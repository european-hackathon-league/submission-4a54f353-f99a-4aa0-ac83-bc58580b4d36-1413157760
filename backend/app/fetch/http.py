"""Polite HTTP for the fetch loop: robots.txt, per-host delay, user agent,
retries with backoff, a daily request budget, and the untouched raw response
saved to storage first (content-addressed ``raw_ref``).
"""

from __future__ import annotations

import hashlib
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app import storage
from app.config import get_settings


class BudgetExhausted(RuntimeError):
    pass


class Disallowed(RuntimeError):
    """robots.txt forbids the URL."""


@dataclass
class Response:
    url: str
    status: int
    content: bytes
    content_type: str
    raw_ref: str
    fetched_at: float

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        import json

        return json.loads(self.content)


class PoliteClient:
    def __init__(self, *, budget: int | None = None, default_delay: float = 2.0, client: httpx.Client | None = None,
                 respect_robots: bool = True) -> None:
        s = get_settings()
        self.user_agent = s.fetch_user_agent
        self.budget = budget if budget is not None else s.fetch_daily_request_budget
        self.used = 0
        self.default_delay = default_delay
        self.delays: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.respect_robots = respect_robots
        self.client = client or httpx.Client(timeout=30, follow_redirects=True,
                                             headers={"User-Agent": self.user_agent})

    def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                r = self.client.get(base + "/robots.txt")
                self.used += 1
                if r.status_code >= 400:
                    parser = None
                else:
                    parser.parse(r.text.splitlines())
            except httpx.HTTPError:
                parser = None
            self._robots[base] = parser
        parser = self._robots[base]
        return True if parser is None else parser.can_fetch(self.user_agent, url)

    def _wait(self, host: str) -> None:
        delay = self.delays.get(host, self.default_delay)
        last = self._last.get(host)
        if last is not None:
            gap = time.monotonic() - last
            if gap < delay:
                time.sleep(delay - gap)
        self._last[host] = time.monotonic()

    def get(self, url: str, *, source_id: str, params: dict | None = None, headers: dict | None = None,
            retries: int = 3, official_api: bool = False) -> Response:
        """``official_api``: a documented public API meant for programs (e.g. the MediaWiki
        Action API). robots.txt speaks to crawlers of pages, so it is not consulted there."""
        if self.used >= self.budget:
            raise BudgetExhausted(f"daily request budget of {self.budget} used")
        params = {k: v for k, v in (params or {}).items() if v is not None}  # an unset parameter is not sent
        full = str(httpx.URL(url, params=params)) if params else url
        if not official_api and not self._allowed(full):
            raise Disallowed(full)
        host = urlsplit(full).netloc
        attempt = 0
        while True:
            self._wait(host)
            self.used += 1
            try:
                r = self.client.get(full, headers=headers)
            except httpx.HTTPError:
                if attempt >= retries:
                    raise
                attempt += 1
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                attempt += 1
                retry_after = r.headers.get("retry-after")
                time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt * 2)
                continue
            break
        content = r.content
        digest = hashlib.sha256(content).hexdigest()
        day = time.strftime("%Y-%m-%d", time.gmtime())
        raw_ref = f"{source_id}/{day}/{digest[:2]}/{digest}"
        if not storage.exists(raw_ref):
            storage.put(raw_ref, content, r.headers.get("content-type", "application/octet-stream"))
        return Response(full, r.status_code, content, r.headers.get("content-type", ""), raw_ref, time.time())
