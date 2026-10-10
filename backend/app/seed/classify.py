"""Kind / severity / car area for BMW feedback rows without a Feedback Type.

With an OpenAI key the LLM classifies (prompt ``feedback_classify``). Without
one, a transparent keyword heuristic runs (changes.md 2.4); its verdicts are
marked ``kind_source = heuristic`` and can be re-run with the LLM later. Rows
the heuristic cannot decide stay quarantined instead of being guessed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.llm import prompts
from app.llm.client import LLMUnavailable, get_llm, llm_available
from app.seed.catalog_proposal import CAR_AREAS

HEURISTIC_VERSION = "heuristic@v1"
CAR_AREA_CODES = [c for c, _, _ in CAR_AREAS]


@dataclass
class Verdict:
    kind: str | None
    severity: str | None
    car_area: str | None
    summary: str | None
    source: str
    model: str | None
    prompt_version: str | None


_DEFECT = re.compile(r"\b(broke|broken|fail(ed|s|ure)?|malfunction\w*|error|not work\w*|doesn'?t work|stopped "
                     r"working|rattl\w*|squeak\w*|leak\w*|crash\w*|freez\w*|warning light|recall|fault\w*|defect\w*|"
                     r"dead|died|won'?t (start|charge|connect))\b", re.I)
_DIFFICULT = re.compile(r"\b(difficult|hard to|confus\w*|annoy\w*|not intuitive|unintuitive|complicated|can'?t "
                        r"find|cumbersome|frustrat\w*|awkward|too many menus|distracting|clunky)\b", re.I)
_WISH = re.compile(r"\b(wish\w*|would (like|love|be nice)|should (have|be|offer)|missing|lack\w*|need(s|ed)? (a|an|"
                   r"more|to)|want(s|ed)? (a|an|more)|please add|it would be|could be improved|bring back)\b", re.I)
_LIKE = re.compile(r"\b(love\w*|great|excellent|happy|enjoy\w*|amazing|pleased|impress\w*|fantastic|perfect|"
                   r"best|awesome|comfortable|smooth|beautiful|good|nice|satisfied|wonderful|brilliant|superb)\b", re.I)
_NEGATION = re.compile(r"\b(not|n't|never|no)\s+(\w+\s+){0,2}$", re.I)

_AREA_WORDS = [
    ("range_charging", r"\b(range|charg\w*|battery|kwh|wallbox|supercharg\w*|ev)\b"),
    ("infotainment", r"\b(screen|display|idrive|infotainment|navigation|nav|carplay|android auto|bluetooth|"
                     r"audio|sound system|speaker|radio|voice|head-?up|hud|menu)\b"),
    ("connected_services", r"\b(app|my bmw|digital key|remote|update|ota|connected|subscription)\b"),
    ("driver_assistance", r"\b(cruise|lane|assist\w*|parking|camera|sensor|blind spot|autopilot)\b"),
    ("seats", r"\b(seat\w*|lumbar|massage|headrest)\b"),
    ("climate", r"\b(air ?con\w*|a/c|climate|heater|heating|ventilat\w*|cool\w*)\b"),
    ("driving_dynamics", r"\b(handl\w*|ride|steer\w*|brak\w*|suspension|cornering|corner|grip|chassis)\b"),
    ("powertrain", r"\b(engine|power|acceleration|accelerat\w*|gear\w*|transmission|fuel|mpg|exhaust|torque)\b"),
    ("access_cargo", r"\b(boot|trunk|luggage|door\w*|key|lock\w*|cargo)\b"),
    ("interior", r"\b(interior|cabin|material\w*|leather|storage|cupholder\w*|space|legroom|dashboard)\b"),
    ("exterior", r"\b(exterior|design|looks|paint|wheel\w*|tyre\w*|tire\w*|mirror\w*)\b"),
    ("lighting", r"\b(light\w*|headlight\w*|lamp\w*)\b"),
    ("quality", r"\b(build quality|quality|noise|reliab\w*)\b"),
]


def _hits(pattern: re.Pattern, text: str) -> int:
    count = 0
    for m in pattern.finditer(text):
        if not _NEGATION.search(text[: m.start()][-30:]):
            count += 1
    return count


def heuristic(text: str) -> Verdict:
    defect, difficult, wish, like = (_hits(p, text) for p in (_DEFECT, _DIFFICULT, _WISH, _LIKE))
    if defect:
        kind, severity = "complaint", "defect"
    elif difficult:
        kind, severity = "complaint", "difficult_to_use"
    elif wish:
        kind, severity = "wish", "wish"
    elif like:
        kind, severity = "like", "none"
    else:
        kind = severity = None
    area = next((code for code, pat in _AREA_WORDS if re.search(pat, text, re.I)), None)
    return Verdict(kind, severity, area, None, "heuristic", None, HEURISTIC_VERSION)


class _Item(BaseModel):
    row: int
    kind: Literal["complaint", "wish", "like", "none"]
    severity: Literal["defect", "difficult_to_use", "wish", "none"]
    car_area: str = Field(description="one code from the fixed car-area list")
    summary: str = Field(description="English, at most 20 words, one statement about one feature")


class _Batch(BaseModel):
    items: list[_Item]


def _llm_batch(rows) -> dict[int, Verdict]:
    prompt = prompts.load("feedback_classify")
    payload = [{"row": r.excel_row, "text": r.text} for r in rows]
    result = get_llm().structured(
        prompt, _Batch, car_areas=", ".join(CAR_AREA_CODES), guard=prompts.INJECTION_GUARD,
        items=prompts.wrap_source(json.dumps(payload, ensure_ascii=False)),
    )
    out = {}
    for item in result.data.items:
        kind = None if item.kind == "none" else item.kind
        area = item.car_area if item.car_area in CAR_AREA_CODES else None
        summary = " ".join(item.summary.split()[:20]) if kind else None
        out[item.row] = Verdict(kind, item.severity if kind else None, area, summary, "llm", result.model,
                                result.prompt_version)
    return out


def classify_rows(rows, batch_size: int = 25) -> dict[int, Verdict]:
    """excel_row -> Verdict for every unclassified row."""
    verdicts: dict[int, Verdict] = {}
    if llm_available():
        try:
            for i in range(0, len(rows), batch_size):
                verdicts.update(_llm_batch(rows[i : i + batch_size]))
        except LLMUnavailable:
            pass
    for r in rows:
        if r.excel_row not in verdicts:
            verdicts[r.excel_row] = heuristic(r.text)
    return verdicts
