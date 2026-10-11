"""RSS sources (news and press feeds, step 3) and App Store reviews (step 4).

News feeds are shared by all subjects: one fetch per feed, entries kept only if
they mention a collision-safe search term of a subject.
"""

from __future__ import annotations

import hashlib
import html
import re

import feedparser

from app.fetch.adapters.base import FetchContext, FetchResult
from app.fetch.records import text_record
from app.fetch.subjects import Subject, author_hash

FEEDS = {
    "motor1": "https://www.motor1.com/rss/news/all/",
    "insideevs": "https://insideevs.com/rss/news/all/",
    "electrek": "https://electrek.co/feed/",
    "thedrive": "https://www.thedrive.com/feed",
    "carscoops": "https://www.carscoops.com/feed/",
    "autoevolution-feed": "https://www.autoevolution.com/rss/backend.xml",
    "bmwblog": "https://www.bmwblog.com/feed/",
    "bimmertoday": "https://bimmertoday.de/feed/",
    "carnewschina": "https://carnewschina.com/feed/",
    "cnevpost": "https://cnevpost.com/feed/",
    "press-bmw": "https://www.press.bmwgroup.com/global/rss",
    "lucid-owners": "https://lucidowners.com/forums/-/index.rss",
}
FEED_LANGUAGE = {"bimmertoday": "de"}
_TAG = re.compile(r"<[^>]+>")

# App IDs from fetching-sources.md 3.7 (brand level): (subject brand, app store id, countries).
APPS = [
    ("BMW", "1519457734", ["us"]),
    ("BMW", "1519034860", ["gb", "de"]),
    ("BMW", "1527576076", ["cn"]),
    ("Mercedes-Benz", "1397873833", ["us"]),
    ("Audi", "440464115", ["us", "de"]),
    ("Tesla", "582007913", ["us", "gb", "de"]),
    ("Genesis", "867941329", ["us"]),
    ("Lexus", "1468484450", ["us"]),
]


def _clean(text: str) -> str:
    return html.unescape(_TAG.sub(" ", text or "")).replace("\xa0", " ").strip()


def _mentions(text: str, terms: list[str]) -> str | None:
    lowered = text.lower()
    for t in terms:
        if re.search(r"(?<![\w-])" + re.escape(t.lower()) + r"(?![\w-])", lowered):
            return t
    return None


def fetch_feed(ctx: FetchContext, source: dict, subjects: list[Subject], cursor: dict) -> FetchResult:
    """One feed for all subjects (called by the worker with every subject)."""
    out = FetchResult()
    url = FEEDS.get(source["source_id"])
    if not url:
        return out
    r = ctx.client.get(url, source_id=source["source_id"])
    out.requests += 1
    feed = feedparser.parse(r.content)
    seen = set(cursor.get("_feed", []))
    language = FEED_LANGUAGE.get(source["source_id"], "en")
    single_brand = source["source_id"] in ("bmwblog", "bimmertoday", "press-bmw")
    for entry in feed.entries:
        key = entry.get("id") or entry.get("link")
        if not key or (key in seen and not ctx.backfill):
            continue
        seen.add(key)
        body = _clean(" ".join(c.get("value", "") for c in entry.get("content", [])) or entry.get("summary", ""))
        text = f"{_clean(entry.get('title', ''))}\n\n{body}".strip()
        for subject in subjects:
            term = _mentions(text, subject.safe_terms(language, single_brand=single_brand and subject.brand == "BMW")
                             or subject.safe_terms(None, single_brand=False))
            if not term:
                continue
            out.records.append(text_record(
                source=source, subject=subject, record_id=f"{source['source_id']}-{hashlib.sha1(key.encode()).hexdigest()[:16]}-{subject.id[:8]}",
                response=r, text=text, url=entry.get("link", url), category=(source["categories"] or ["J"])[0],
                content_type="press" if source["source_id"].startswith("press-") else "news",
                title=_clean(entry.get("title", "")), published_at=entry.get("published") or entry.get("updated"),
                search_term=term, thread_id=key, language=language, extra={"feed_entry_id": key},
                run_id=ctx.run_id))
    out.cursor["_feed"] = sorted(seen)[-2000:]
    return out


def fetch_app_store(ctx: FetchContext, source: dict, subjects: list[Subject], cursor: dict) -> FetchResult:
    """App reviews are brand level; each review is attached to the BMW lines (BMW
    app) or to every competitor subject of that brand."""
    out = FetchResult()
    for brand, app_id, countries in APPS:
        targets = [s for s in subjects if s.brand == brand]
        if not targets:
            continue
        for country in countries:
            newest = cursor.get(f"{app_id}-{country}")
            top = None
            for page in range(1, 11):
                r = ctx.client.get(f"https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={app_id}"
                                   "/sortby=mostrecent/json", source_id="app-store")
                out.requests += 1
                entries = r.json().get("feed", {}).get("entry", []) if r.status == 200 else []
                if isinstance(entries, dict):
                    entries = [entries]
                reviews = [e for e in entries if "im:rating" in e]
                if not reviews:
                    break
                stop = False
                for e in reviews:
                    rid = e["id"]["label"]
                    if newest and rid == newest and not ctx.backfill:
                        stop = True
                        break
                    top = top or rid  # newest review first (sortby=mostrecent)
                    text = f"{e['title']['label']}\n\n{e['content']['label']}"
                    for s in targets[:1] if brand != "BMW" else targets:
                        out.records.append(text_record(
                            source=source, subject=s, record_id=f"app-store-{rid}-{s.id[:8]}", response=r,
                            text=text, url=f"https://apps.apple.com/{country}/app/id{app_id}", category="H",
                            content_type="owner", level="brand", title=e["title"]["label"],
                            published_at=e.get("updated", {}).get("label"), search_term=app_id,
                            author_hash=author_hash("app-store", e.get("author", {}).get("name", {}).get("label")),
                            country=country.upper(), rating_value=float(e["im:rating"]["label"]), rating_scale=5,
                            engagement={"likes": int(e.get("im:voteSum", {}).get("label", 0) or 0)},
                            extra={"app_id": app_id, "app_version": e.get("im:version", {}).get("label")},
                            run_id=ctx.run_id))
                if stop:
                    break
            if top:
                out.cursor[f"{app_id}-{country}"] = top
    return out


# Feed-type sources are handled per source, not per subject.
SHARED = {sid: fetch_feed for sid in FEEDS} | {"app-store": fetch_app_store}

