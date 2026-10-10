"""Case tags proposed by the AI (the PM can override every one):
brand fit, segment, release fit (current car update, next generation, OTA).

With an OpenAI key the LLM judges them from the case (prompt ``case_tags``),
with a reason. Without one, transparent rules propose segment and release fit;
brand fit stays empty (unknown), because brand taste cannot be guessed from
keywords. Tags set by the PM (``*_source = pm``) are never overwritten.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from app.llm import prompts
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


class Tags(BaseModel):
    brand_fit: Literal["fits", "neutral", "off_brand"]
    brand_fit_reason: str
    segment: Literal["base", "premium", "luxury"]
    release_fit: Literal["current_car", "next_gen", "ota"]
    release_fit_reason: str


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


def propose(case: dict, feature: dict, figures: dict, vehicle: str, brand_profile: str) -> dict:
    v = figures["by_vehicle"].get(vehicle, {})
    defects = sum(m["defects"] for m in v.get("markets", {}).values())
    customers = v.get("distinct_customers", 0)
    if llm_available():
        try:
            result = get_llm().structured(
                prompts.load("case_tags"), Tags, guard=prompts.INJECTION_GUARD, brand_profile=brand_profile,
                vehicle=vehicle, feature=feature["name"], car_area=feature["car_area"], title=case["title"],
                body=prompts.wrap_source(case["body_md"][:4000]), customers=str(customers), defects=str(defects),
            )
            t = result.data
            return {"brand_fit": t.brand_fit, "brand_fit_reason": t.brand_fit_reason, "segment": t.segment,
                    "release_fit": t.release_fit, "release_fit_reason": t.release_fit_reason,
                    "source": f"llm:{result.prompt_version}"}
        except LLMUnavailable:
            pass
    fit, reason = rule_release_fit(feature["name"], feature["car_area"], defects, customers)
    return {"brand_fit": None, "brand_fit_reason": None, "segment": rule_segment(feature["name"]),
            "release_fit": fit, "release_fit_reason": reason, "source": "rules@v1"}
