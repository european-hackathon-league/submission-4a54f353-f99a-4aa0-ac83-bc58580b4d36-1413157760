"""Case tags proposed by the AI (the PM can override every one):
brand fit, segment, release fit (current car update, next generation, OTA),
case type and change depth.

With an OpenAI key the LLM judges them from the case (prompt ``case_tags``),
with a reason. Without one, transparent rules propose segment and release fit;
brand fit stays empty (unknown), because brand taste cannot be guessed from
keywords. Tags set by the PM (``*_source = pm``) are never overwritten.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from app.config import get_settings
from app.llm import cache, prompts
from app.llm.client import LLMUnavailable, get_llm, llm_available

# Features that are mostly software and can change over the air (hardware such as
# wireless charging pads, speakers, displays or USB ports cannot).
SOFTWARE = re.compile(r"app|digital key|remote|software|ota|esim|wifi|connected|navigation|map|traffic|voice|"
                      r"assistant|operating concept|idrive|carplay|android auto|mirroring|bluetooth|driver profile|"
                      r"in-car apps|my modes|functions on demand|radio|instrument cluster|wake-up", re.I)
HARDWARE = re.compile(r"charging|audio|speaker|display|head-up|usb|camera|controller|gesture|seat|steering", re.I)
SMALL_ITEMS = re.compile(r"\b(colou?r|paint|wheel|rim|trim|upholster|material|cupholder|floor mat|usb|"
                         r"storage|mirror|light)\w*", re.I)
LUXURY = re.compile(r"massage|rear seat entertainment|automatic doors|executive|theatre|bowers|night vision|"
                    r"rear touch|panel heating", re.I)
BASE = re.compile(r"usb|cupholder|radio|storage|floor mat|wipers|horn|12 v|tire repair|towing", re.I)


CaseType = Literal["new_feature", "fix_improve", "performance_target", "quality_perception", "market_variant",
                   "brand_differentiator", "regression"]
ChangeDepth = Literal["trim_option", "part_module", "platform_powertrain", "software_ota"]


class Tags(BaseModel):
    brand_fit: Literal["fits", "neutral", "off_brand"]
    brand_fit_reason: str
    segment: Literal["base", "premium", "luxury"]
    release_fit: Literal["current_car", "next_gen", "ota"]
    release_fit_reason: str
    case_type: CaseType
    change_depth: ChangeDepth


def rule_change_depth(release_fit: str) -> str:
    """Fallback: the size of the change follows the release fit."""
    return {"ota": "software_ota", "current_car": "trim_option"}.get(release_fit, "part_module")


def rule_case_type(complaints: int, wishes: int, likes: int, removed: bool) -> str:
    """Fallback from the opinion counts: what kind of problem the case is."""
    if removed:
        return "regression"
    if likes > complaints + wishes:
        return "brand_differentiator"
    return "new_feature" if wishes > complaints else "fix_improve"


def rule_segment(feature: str) -> str:
    if LUXURY.search(feature):
        return "luxury"
    if BASE.search(feature):
        return "base"
    return "premium"


def rule_release_fit(feature: str, car_area: str, defects: int, customers: int) -> tuple[str, str]:
    """Software-only areas -> OTA; small equipment changes -> current car update;
    everything else (hardware, packaging, chassis) -> next generation."""
    if SOFTWARE.search(feature) and not HARDWARE.search(feature) and car_area in (
            "infotainment_hmi", "connectivity_digital", "driver_assistance", "dynamics_powertrain"):
        return "ota", f"rule-based fallback: {feature} is mostly software, can ship over the air"
    if SMALL_ITEMS.search(feature) and defects == 0:
        return "current_car", "rule-based fallback: small equipment or trim change fits a mid-life update"
    return "next_gen", (f"rule-based fallback: {car_area} change touches hardware; "
                        f"{customers} customers, {defects} defects")


def _inputs(case: dict, feature: dict, figures: dict, vehicle: str, brand_profile: str) -> dict:
    v = figures["by_vehicle"].get(vehicle, {})
    return {"brand_profile": brand_profile, "vehicle": vehicle, "feature": feature["name"],
            "car_area": feature["car_area"], "title": case["title"], "body": (case["body_md"] or "")[:4000],
            "customers": str(v.get("distinct_customers", 0)),
            "defects": str(sum(m["defects"] for m in v.get("markets", {}).values()))}


def _cache_key(inputs: dict) -> str:
    return cache.key(prompts.load("case_tags").prompt_version, get_settings().llm_model_for("case_tags"),
                     get_settings().llm_reasoning_for("case_tags"), inputs)


def _llm_tags(inputs: dict) -> dict:
    result = get_llm().structured(prompts.load("case_tags"), Tags, guard=prompts.INJECTION_GUARD,
                                  **{**inputs, "body": prompts.wrap_source(inputs["body"])})
    t = result.data
    return {"brand_fit": t.brand_fit, "brand_fit_reason": t.brand_fit_reason, "segment": t.segment,
            "release_fit": t.release_fit, "release_fit_reason": t.release_fit_reason,
            "case_type": t.case_type, "change_depth": t.change_depth,
            "source": f"llm:{result.prompt_version}"}


def prefetch(jobs: list[tuple]) -> dict:
    """Judge the tags of many (case, feature, figures, vehicle, brand profile) in parallel (cached)."""
    if not llm_available():
        return {}
    items = [_inputs(*j) for j in jobs]
    return cache.prefetch("case_tags", [(_cache_key(i), (lambda i=i: _llm_tags(i))) for i in items])


def propose(case: dict, feature: dict, figures: dict, vehicle: str, brand_profile: str) -> dict:
    v = figures["by_vehicle"].get(vehicle, {})
    defects = sum(m["defects"] for m in v.get("markets", {}).values())
    customers = v.get("distinct_customers", 0)
    if llm_available():
        inputs = _inputs(case, feature, figures, vehicle, brand_profile)
        k = _cache_key(inputs)
        try:
            hit = cache.get("case_tags", k)
            if hit is None:
                hit = _llm_tags(inputs)
                cache.put("case_tags", k, hit)
            return hit
        except LLMUnavailable:
            pass
    fit, reason = rule_release_fit(feature["name"], feature["car_area"], defects, customers)
    markets = v.get("markets", {}).values()
    return {"brand_fit": None, "brand_fit_reason": None, "segment": rule_segment(feature["name"]),
            "release_fit": fit, "release_fit_reason": reason,
            "case_type": rule_case_type(sum(m["complaints"] for m in markets), sum(m["wishes"] for m in markets),
                                        sum(m["likes"] for m in markets),
                                        bool(v.get("equipment", {}).get("removed"))),
            "change_depth": rule_change_depth(fit), "source": "rules@v2"}
