"""The fixed taxonomy (``taxonomy/`` at the repo root, see its README).

Three closed facets per signal: topic (BMW VFC level 2), aspect group (grouped
from VFC level 3) and car area (always derived from the topic, never predicted).
Only a human changes the lists (edit the CSVs, run ``taxonomy/build.py``); the
agents can only pick from them, and ``other`` is the single way out.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from app.config import REPO_DIR

TAXONOMY_DIR = REPO_DIR / "taxonomy"
OTHER = "other"
CONFIDENCE = ("high", "medium", "low")


@lru_cache
def load() -> dict:
    return json.loads((TAXONOMY_DIR / "taxonomy.json").read_text(encoding="utf-8"))


def version() -> str:
    return load()["version"]


@lru_cache
def topics() -> dict[str, dict]:
    """topic_id -> topic (label, car_area, hint, vfc_level2), without the fallback."""
    return {t["id"]: t for t in load()["topics"]}


def car_area_of(topic_id: str | None) -> str | None:
    topic = topics().get(topic_id or "")
    return topic["car_area"] if topic else None


def aspect_groups() -> list[str]:
    return [a["id"] for a in load()["aspect_groups"]]


def topic_for_label(vfc_level2: str | None) -> str | None:
    """BMW VFC level 2 label -> topic ID. None for meta labels and unknown labels."""
    return load()["vfc_level2_to_topic"].get(vfc_level2 or "")


def aspect_for_label(vfc_level3: str | None) -> str | None:
    return load()["vfc_level3_to_aspect_group"].get(vfc_level3 or "")


@lru_cache
def prompt_topics() -> str:
    return (TAXONOMY_DIR / "prompt_topics.md").read_text(encoding="utf-8")


def prompt_aspects() -> str:
    return "; ".join(f"{a['id']} ({a['description']})" for a in load()["aspect_groups"])


# Enums for structured LLM output: an invented topic or aspect group cannot be returned.
TopicId = Literal[tuple([*topics(), OTHER])]  # type: ignore[valid-type]
AspectGroup = Literal[tuple(aspect_groups())]  # type: ignore[valid-type]
Confidence = Literal["high", "medium", "low"]
