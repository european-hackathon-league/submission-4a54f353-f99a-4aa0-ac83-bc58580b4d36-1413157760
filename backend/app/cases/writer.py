"""Case title and page body: the problem as customers see it, never the solution.

With an OpenAI key the LLM writes them (prompt ``case_writer``) from a sample of
the case's signals and must cite them as ``[signal:<id>]``; citations to signals
outside the sample are removed. Without a key a template is used that only
restates counts, labels and verbatim quotes.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from pydantic import BaseModel, Field
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

from app.llm import prompts
from app.llm.client import LLMUnavailable, get_llm, llm_available

CITATION = re.compile(r"\[signal:([0-9a-f-]{36})\]")
_WORD = re.compile(r"[a-z][a-z-]{3,}")
_GENERIC = {"customer", "stated", "vehicle", "car", "advised", "writer", "really", "would", "like", "also",
            "just", "very", "much", "bmw", "series", "feature", "great", "good", "nice", "love", "likes",
            "problem", "problems", "issue", "issues", "mentioned", "explained", "states", "said", "says", "time",
            "times", "when", "they", "there", "them", "than", "then", "dealer", "service", "requested"}


class CaseText(BaseModel):
    title: str = Field(description="the problem in at most 12 words, never a solution")
    body_md: str


def _terms(signals: list[dict], feature: str, k: int = 2) -> list[str]:
    stop = ENGLISH_STOP_WORDS | _GENERIC | set(feature.lower().split())
    counts = Counter()
    for s in signals:
        counts.update(set(w for w in _WORD.findall((s.get("quote") or "").lower()) if w not in stop))
    return [w for w, _ in counts.most_common(k)]


def _sample(signals: list[dict], n: int = 12) -> list[dict]:
    """Prefer complaints and wishes, spread over markets."""
    order = {"complaint": 0, "wish": 1, "like": 2}
    ranked = sorted(signals, key=lambda s: (order.get(s["kind"], 3), str(s["id"])))
    seen_markets: Counter = Counter()
    picked = []
    for s in ranked:
        if seen_markets[s["market"]] < n // 2:
            picked.append(s)
            seen_markets[s["market"]] += 1
        if len(picked) >= n:
            break
    return picked


def template(feature: str, signals: list[dict]) -> CaseText:
    kinds = Counter(s["kind"] for s in signals)
    severities = Counter(s["severity"] for s in signals)
    customers = len({s["customer_ref"] for s in signals if s["evidence_role"] == "bmw_customer"})
    terms = _terms(signals, feature)
    dominant = "complaints" if kinds["complaint"] >= max(kinds["wish"], kinds["like"]) and kinds["complaint"] else (
        "wishes" if kinds["wish"] >= kinds["like"] and kinds["wish"] else "likes")
    title = f"{feature}: {dominant}" + (f" about {', '.join(terms)}" if terms else "")
    markets = Counter(s["market"] for s in signals)
    lines = [
        "## Problem",
        "",
        f"{len(signals)} signals from {customers} distinct BMW customers about **{feature}**: "
        f"{kinds['complaint']} complaints ({severities['defect']} defects, {severities['difficult_to_use']} "
        f"difficult to use), {kinds['wish']} wishes, {kinds['like']} likes.",
        "",
        "Markets: " + ", ".join(f"{m} {c}" for m, c in markets.most_common()) + ".",
        "",
        "_Text generated from signal labels and quotes (no LLM summary yet)._",
        "",
        "## What customers say",
        "",
    ]
    for s in _sample(signals, 6):
        quote = " ".join((s["quote"] or "").split())[:280]
        lines.append(f"> \"{quote}\" [signal:{s['id']}] ({s['kind']}, {s['market']}, {s.get('vehicle') or 'brand'})")
        lines.append("")
    return CaseText(title=title[:120], body_md="\n".join(lines).strip() + "\n")


def write(feature: str, signals: list[dict]) -> tuple[CaseText, str | None, str | None]:
    """(text, model, prompt_version)."""
    if llm_available():
        sample = _sample(signals)
        payload = [{"id": str(s["id"]), "kind": s["kind"], "severity": s["severity"], "market": s["market"],
                    "vehicle": s.get("vehicle"), "quote": (s["quote"] or "")[:500]} for s in sample]
        try:
            result = get_llm().structured(
                prompts.load("case_writer"), CaseText, feature=feature, guard=prompts.INJECTION_GUARD,
                n_signals=str(len(signals)), signals=prompts.wrap_source(json.dumps(payload, ensure_ascii=False)),
            )
            allowed = {str(s["id"]) for s in sample}
            body = CITATION.sub(lambda m: m.group(0) if m.group(1) in allowed else "", result.data.body_md)
            return CaseText(title=result.data.title[:120], body_md=body), result.model, result.prompt_version
        except LLMUnavailable:
            pass
    return template(feature, signals), None, "case_template@v1"
