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

from app.config import get_settings
from app.llm import cache, prompts
from app.llm.client import LLMUnavailable, get_llm, llm_available

CITATION = re.compile(r"\[signal:([0-9a-f-]{36})\]")
_WORD = re.compile(r"[a-z][a-z-]{3,}")
_GENERIC = {"customer", "stated", "vehicle", "car", "advised", "writer", "really", "would", "like", "also",
            "just", "very", "much", "bmw", "series", "feature", "great", "good", "nice", "love", "likes",
            "problem", "problems", "issue", "issues", "mentioned", "explained", "states", "said", "says", "time",
            "times", "when", "they", "there", "them", "than", "then", "dealer", "service", "requested"}


class CaseText(BaseModel):
    title: str = Field(description="the problem in at most 12 words, never a solution")
    short_title: str = Field(default="", description="2 to 4 words for cards and diagrams")
    summary: str = Field(default="", description="one or two sentences: the problem and who has it")
    highlights: list[str] = Field(default_factory=list,
                                  description="three short bullets: the problem, who is affected, the competition")
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
    competitor = sum(1 for s in signals if s["evidence_role"] == "competitor")
    summary = (f"{customers} BMW customers on {feature}: {kinds['complaint']} complaints, {kinds['wish']} wishes, "
               f"{kinds['like']} likes.")
    highlights = [
        f"{kinds['complaint']} complaints ({severities['defect']} defects), {kinds['wish']} wishes, "
        f"{kinds['like']} likes about {feature}.",
        f"{customers} distinct BMW customers; signals by market: "
        + ", ".join(f"{m} {c}" for m, c in markets.most_common(3)) + ".",
        f"{competitor} competitor signals in the case." if competitor else "No competitor evidence in the signals.",
    ]
    return CaseText(title=title[:120], short_title=_short(feature), summary=summary, highlights=highlights,
                    body_md="\n".join(lines).strip() + "\n")


def _short(text: str, words: int = 4) -> str:
    """At most four words of a label: the fallback short title."""
    return " ".join(text.replace("(", " ").replace(")", " ").replace(",", " ").split()[:words])


def _cache_key(feature: str, signals: list[dict]) -> str:
    return cache.key(prompts.load("case_writer").prompt_version, get_settings().llm_model_for("case_writer"),
                     get_settings().llm_reasoning_for("case_writer"),
                     feature, len(signals), sorted(str(s["id"]) for s in _sample(signals)))


def _llm_text(feature: str, signals: list[dict]) -> dict:
    sample = _sample(signals)
    payload = [{"id": str(s["id"]), "kind": s["kind"], "severity": s["severity"], "market": s["market"],
                "vehicle": s.get("vehicle"), "quote": (s["quote"] or "")[:500]} for s in sample]
    result = get_llm().structured(
        prompts.load("case_writer"), CaseText, feature=feature, guard=prompts.INJECTION_GUARD,
        n_signals=str(len(signals)), signals=prompts.wrap_source(json.dumps(payload, ensure_ascii=False)),
    )
    allowed = {str(s["id"]) for s in sample}
    body = CITATION.sub(lambda m: m.group(0) if m.group(1) in allowed else "", result.data.body_md)
    d = result.data
    return {"title": d.title[:120], "short_title": " ".join(d.short_title.split()[:5]) or _short(feature),
            "summary": CITATION.sub("", d.summary).strip(),
            "highlights": [CITATION.sub("", h).strip() for h in d.highlights][:3],
            "body_md": body, "model": result.model, "prompt_version": result.prompt_version}


def prefetch(groups: list[tuple[str, list[dict]]]) -> dict:
    """Write the pages of many new cases in parallel (cached); ``write`` then reads the cache."""
    if not llm_available():
        return {}
    return cache.prefetch("case_writer", [(_cache_key(f, s), (lambda f=f, s=s: _llm_text(f, s)))
                                          for f, s in groups])


def write(feature: str, signals: list[dict]) -> tuple[CaseText, str | None, str | None]:
    """(text, model, prompt_version)."""
    if llm_available():
        k = _cache_key(feature, signals)
        hit = cache.get("case_writer", k)
        try:
            if hit is None:
                hit = _llm_text(feature, signals)
                cache.put("case_writer", k, hit)
            return (CaseText(title=hit["title"], short_title=hit.get("short_title") or _short(feature),
                             summary=hit.get("summary") or "", highlights=hit.get("highlights") or [],
                             body_md=hit["body_md"]), hit["model"], hit["prompt_version"])
        except LLMUnavailable:
            pass
    return template(feature, signals), None, "case_template@v1"
