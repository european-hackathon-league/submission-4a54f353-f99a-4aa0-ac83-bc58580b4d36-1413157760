"""Disk cache and parallel prefetch for LLM calls in batch runs (changes.md 10.12).

A build makes thousands of independent calls (case pages, case tags). Sent one
after another they take hours; ``prefetch`` runs them in parallel threads and
keeps every answer in a JSONL file per task, keyed by prompt version + model +
inputs. A re-run after a crash or a network drop reads the cache instead of
paying again; the pipeline code then calls the same function and gets the
cached answer instantly.
"""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from app.config import BACKEND_DIR

CACHE_DIR = BACKEND_DIR / ".data" / "llm_cache"
WORKERS = 8
_lock = threading.Lock()
_memory: dict[str, dict[str, Any]] = {}


def key(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def _table(task: str) -> dict[str, Any]:
    with _lock:
        if task not in _memory:
            table: dict[str, Any] = {}
            path = CACHE_DIR / f"{task}.jsonl"
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        item = json.loads(line)
                        table[item["key"]] = item["value"]
                    except (ValueError, KeyError):
                        continue  # a line cut off by a crash
            _memory[task] = table
        return _memory[task]


def get(task: str, k: str) -> Any | None:
    return _table(task).get(k)


def put(task: str, k: str, value: Any) -> None:
    table = _table(task)
    with _lock:
        table[k] = value
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_DIR / f"{task}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"key": k, "value": value}, ensure_ascii=False, default=str) + "\n")


def prefetch(task: str, jobs: list[tuple[str, Callable[[], Any]]], workers: int = WORKERS) -> dict[str, int]:
    """Run the uncached jobs (key, fn) in parallel and cache their JSON-safe results.
    A failing job is skipped (the caller falls back to calling it itself)."""
    todo = [(k, fn) for k, fn in dict(jobs).items() if get(task, k) is None]
    done = failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn): k for k, fn in todo}
        for fut in as_completed(futures):
            try:
                put(task, futures[fut], fut.result())
                done += 1
            except Exception:  # noqa: BLE001 - one failed call must not stop the batch
                failed += 1
    return {"cached_before": len(jobs) - len(todo), "called": done, "failed": failed}
