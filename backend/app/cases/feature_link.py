"""Similarity thresholds per embedding provider (used when a large case group is split).

Topics are never matched by similarity: BMW rows get their topic from the VFC
label and everything else from the LLM classifier (``app.signals.reclassify``).
"""

from __future__ import annotations

from app.llm.embeddings import get_embedder


def provider_key() -> str:
    return get_embedder().name.split(":", 1)[0]


def threshold(thresholds: dict, name: str, default: float) -> float:
    value = thresholds.get(name, default)
    if isinstance(value, dict):
        return float(value.get(provider_key(), default))
    return float(value)
