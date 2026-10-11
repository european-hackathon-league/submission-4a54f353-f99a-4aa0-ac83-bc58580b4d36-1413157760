"""Adapter contract: one adapter per source (or family of sources).

``fetch(ctx, source, subject)`` returns text records (7.2), data records (7.3)
and the new cursor. Incremental runs stop at items already seen (``cursor``);
the backfill walks every page.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.fetch.http import PoliteClient
from app.fetch.subjects import Subject


class NotConfigured(RuntimeError):
    """The adapter needs credentials or a library that is not available."""


@dataclass
class FetchContext:
    client: PoliteClient
    backfill: bool
    run_id: object = None
    max_pages: int = 200  # safety cap per subject even in a backfill


@dataclass
class FetchResult:
    records: list[dict] = field(default_factory=list)
    data_records: list[dict] = field(default_factory=list)
    cursor: dict = field(default_factory=dict)
    requests: int = 0
    notes: list[str] = field(default_factory=list)


ADAPTERS: dict[str, object] = {}


def adapter(*source_ids: str):
    def register(fn):
        for sid in source_ids:
            ADAPTERS[sid] = fn
        return fn
    return register


def cursor_key(subject: Subject | None) -> str:
    return subject.id if subject else "_brand"
