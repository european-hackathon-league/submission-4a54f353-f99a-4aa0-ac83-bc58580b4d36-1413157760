"""The signal contract: one statement about one topic from one source record.

Fixed core fields plus ``extra`` (source-specific), versioned by SCHEMA_VERSION.
Agents produce ``SignalIn``; the validation gate turns it into a stored row
(valid or quarantined). Field names follow fetching-sources.md section 7.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "signal@2"

Market = Literal["USA", "China", "Europe", "Rest of World", "unknown"]
Kind = Literal["complaint", "wish", "like", "competitor_fact", "trend", "innovation"]
Severity = Literal["defect", "difficult_to_use", "wish", "none"]
VehicleRole = Literal["current", "predecessor", "competitor", "unknown"]
EvidenceRole = Literal["bmw_customer", "competitor", "market"]
Powertrain = Literal["ICE", "PHEV", "BEV", "unknown"]
ReachUnit = Literal["views", "upvotes", "likes", "replies", "none"]

MAX_SUMMARY_WORDS = 20


class SignalIn(BaseModel):
    """What an agent (or the seed) proposes. Validated by the gate before storage."""

    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1)
    kind: Kind
    severity: Severity = "none"
    summary: str = Field(min_length=3, description="English, at most 20 words")
    quote: str = Field(min_length=2, description="exact excerpt of the record text_original")
    quote_en: str | None = None
    # Closed taxonomy: the gate checks both against the stored lists and derives the
    # car area from the topic. No topic = waits for the LLM classifier.
    topic_id: str | None = None
    aspect_group: str | None = None
    classification_confidence: Literal["high", "medium", "low"] | None = None
    classified_by: Literal["bmw_label", "llm", "pm"] | None = None
    feature_hint: str | None = None
    evidence_role: EvidenceRole = "bmw_customer"
    market: Market = "unknown"
    market_basis: Literal["stated", "inferred", "unknown"] = "unknown"
    vehicle: str | None = None
    chassis_code: str | None = None
    competitor_id: str | None = None
    vehicle_role: VehicleRole = "unknown"
    powertrain: Powertrain = "unknown"
    customer_ref: str | None = None
    reach: int | None = Field(default=None, ge=0)
    reach_unit: ReachUnit = "none"
    data_record_id: str | None = None
    extra: dict = Field(default_factory=dict)

    @field_validator("summary")
    @classmethod
    def _short_summary(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v.split()) > MAX_SUMMARY_WORDS:
            raise ValueError(f"summary has more than {MAX_SUMMARY_WORDS} words")
        return v

    @field_validator("severity")
    @classmethod
    def _severity_known(cls, v: str) -> str:
        return v


def severity_fits_kind(kind: str, severity: str) -> bool:
    if kind == "complaint":
        return severity in ("defect", "difficult_to_use")
    if kind == "wish":
        return severity in ("wish", "none")
    return severity == "none"
