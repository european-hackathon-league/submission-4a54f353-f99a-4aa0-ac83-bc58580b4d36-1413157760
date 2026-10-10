"""Validation gate: every proposed signal passes these checks before it counts.

Order (plan.md phase 3): schema -> record exists -> quote found in the record ->
kind / severity / car area consistent -> duplicate -> source known -> competitor
fact dated -> vehicle role from on_sale_since -> scope tag.

A failed check quarantines the signal with the reason; it is stored (visible in
the quarantine view), never silently dropped. The per-config source filter is
applied when reading (case figures, boards), not here, because one signal can
serve several configs.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.signals.schema import SCHEMA_VERSION, SignalIn, severity_fits_kind

OUT_OF_SCOPE = re.compile(
    r"\b(homologation|type approval|regulat\w*|emission standard|euro 7|price|pricing|msrp|cost(s)? too|"
    r"lease rate|financing|insurance premium)\b",
    re.I,
)

_QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "}


@dataclass
class GateContext:
    records: dict[str, dict]
    car_areas: set[str]
    sources: set[str]
    on_sale_since: dict[str, dt.date]
    competitors: set[str]
    existing: set[tuple] = field(default_factory=set)  # (record_id, kind, quote, feature_hint)
    authored: dict[tuple, str] = field(default_factory=dict)  # (source_id, author_hash, quote) -> record_id


@dataclass
class GateResult:
    row: dict | None  # None = not storable (no record to attach the quarantine to)
    valid: bool
    reason: str | None = None


def load_context(conn, record_ids: set[str]) -> GateContext:
    records = {}
    if record_ids:
        for r in conn.execute(
            "select record_id, source_id, text_original, published_at, author_hash, market, country,"
            " vehicle_role, deleted_at from source_record where record_id = any(%s)",
            (list(record_ids),),
        ).fetchall():
            records[r["record_id"]] = r
    existing = set()
    authored = {}
    if record_ids:
        for s in conn.execute(
            "select s.record_id, s.kind, s.quote, s.feature_hint, r.source_id, r.author_hash from signal s"
            " join source_record r using (record_id)"
            " where s.validation = 'valid' and (s.record_id = any(%s) or r.author_hash in"
            " (select author_hash from source_record where record_id = any(%s) and author_hash is not null))",
            (list(record_ids), list(record_ids)),
        ).fetchall():
            existing.add((s["record_id"], s["kind"], normalize(s["quote"] or ""), normalize(s["feature_hint"] or "")))
            if s["author_hash"]:
                authored[(s["source_id"], s["author_hash"], normalize(s["quote"] or ""))] = s["record_id"]
    return GateContext(
        records=records,
        car_areas={r["code"] for r in conn.execute("select code from car_area").fetchall()},
        sources={r["source_id"] for r in conn.execute("select source_id from source_registry").fetchall()},
        on_sale_since={r["code"]: r["on_sale_since"] for r in conn.execute(
            "select code, on_sale_since from vehicle where on_sale_since is not null").fetchall()},
        competitors={str(r["id"]) for r in conn.execute("select id from competitor").fetchall()},
        existing=existing,
        authored=authored,
    )


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(_QUOTES.get(ch, ch) for ch in text)
    return " ".join(text.split()).casefold()


def find_quote(quote: str, text: str) -> str | None:
    """Return the exact substring of text that matches quote (whitespace, quote marks
    and case may differ), or None. The returned span is what gets stored."""
    if quote in text:
        return quote
    target = normalize(quote)
    if not target:
        return None
    # Map each normalized character back to its position in the original text.
    norm_chars: list[str] = []
    positions: list[int] = []
    prev_space = True
    for i, ch in enumerate(unicodedata.normalize("NFKC", text)):
        ch = _QUOTES.get(ch, ch)
        if ch.isspace():
            if prev_space:
                continue
            ch, prev_space = " ", True
        else:
            prev_space = False
        for c in ch.casefold():
            norm_chars.append(c)
            positions.append(i)
    joined = "".join(norm_chars)
    idx = joined.find(target)
    if idx < 0 or len(unicodedata.normalize("NFKC", text)) != len(text):
        return None
    start, end = positions[idx], positions[idx + len(target) - 1] + 1
    return text[start:end]


def _quarantine(row: dict, reason: str) -> GateResult:
    row["validation"] = "quarantined"
    row["reject_reason"] = reason
    return GateResult(row, False, reason)


def check(raw: dict, ctx: GateContext, *, run_id=None, prompt_version=None, model=None) -> GateResult:
    record_id = raw.get("record_id")
    base = {
        "record_id": record_id, "run_id": run_id, "schema_version": SCHEMA_VERSION,
        "prompt_version": prompt_version, "model": model,
    }
    record = ctx.records.get(record_id) if record_id else None

    # 1. Schema.
    try:
        sig = SignalIn.model_validate(raw)
    except ValidationError as exc:
        if record is None:
            return GateResult(None, False, f"schema: {exc.errors()[0]['msg']} (no stored record)")
        row = {**base, "evidence_role": raw.get("evidence_role") if raw.get("evidence_role") in (
            "bmw_customer", "competitor", "market") else "market", "extra": {"raw": _safe(raw)}}
        first = exc.errors()[0]
        return _quarantine(row, f"schema: {'.'.join(map(str, first['loc']))}: {first['msg']}")

    row = {**base, **sig.model_dump()}
    row["validation"] = "valid"
    row["reject_reason"] = None

    # 2. Record exists and is not deleted.
    if record is None:
        return GateResult(None, False, "record not stored")
    if record["deleted_at"] is not None or record["text_original"] is None:
        return _quarantine(row, "record deleted")

    # 3. Quote found in the stored record.
    span = find_quote(sig.quote, record["text_original"])
    if span is None:
        return _quarantine(row, "quote not found in record")
    row["quote"] = span
    row["quote_start"] = record["text_original"].find(span)

    # 4. Consistency of kind, severity and car area.
    if not severity_fits_kind(sig.kind, sig.severity):
        return _quarantine(row, f"severity {sig.severity} does not fit kind {sig.kind}")
    unknown = [a for a in sig.car_area if a not in ctx.car_areas]
    if unknown:
        return _quarantine(row, f"unknown car area {unknown[0]}")
    if sig.evidence_role == "competitor" and (not sig.competitor_id or sig.competitor_id not in ctx.competitors):
        # Stored as market evidence so it can never count as a competitor fact by accident.
        row["extra"] = {**row["extra"], "proposed_evidence_role": "competitor"}
        row["evidence_role"], row["competitor_id"] = "market", None
        return _quarantine(row, "competitor signal without a known competitor_id")

    # 5. Duplicates: same record, kind, quote and feature; or the same author posting the same text elsewhere.
    key = (record_id, sig.kind, normalize(span), normalize(sig.feature_hint or ""))
    if key in ctx.existing:
        return _quarantine(row, "duplicate of an existing signal on the same record")
    if record["author_hash"]:
        akey = (record["source_id"], record["author_hash"], normalize(span))
        other = ctx.authored.get(akey)
        if other and other != record_id:
            return _quarantine(row, f"near-duplicate: same author and text as {other}")
        ctx.authored[akey] = record_id
    ctx.existing.add(key)

    # 6. Source known.
    if record["source_id"] not in ctx.sources:
        return _quarantine(row, f"unknown source {record['source_id']}")

    # 7. Competitor facts must be dated.
    if sig.kind == "competitor_fact" and record["published_at"] is None and not sig.data_record_id:
        return _quarantine(row, "competitor fact without a date")

    # Market from the record when the agent did not state one.
    if sig.market == "unknown" and record["market"] != "unknown":
        row["market"] = record["market"]
        row["market_basis"] = "stated" if record["country"] else "inferred"

    # 8. Vehicle role: records published before the line went on sale are about the predecessor.
    published = record["published_at"]
    since = ctx.on_sale_since.get(sig.vehicle or "")
    if published and since and sig.vehicle_role in ("current", "unknown") and published.date() < since:
        row["vehicle_role"] = "predecessor"
        row["extra"] = {**row["extra"], "role_reason": f"published {published.date()} before on_sale_since {since}"}
    elif row["vehicle_role"] == "unknown" and record["vehicle_role"] != "unknown":
        row["vehicle_role"] = record["vehicle_role"]

    # 9. Scope tag (kept valid, tagged).
    row["scope_tag"] = "out_of_scope" if OUT_OF_SCOPE.search(f"{sig.summary} {span}") else "in_scope"
    row["published_at"] = published
    return GateResult(row, True)


def _safe(raw: dict) -> dict:
    return {k: (v if isinstance(v, (str, int, float, bool, type(None), list, dict)) else str(v)) for k, v in raw.items()}
