"""Kind / severity / topic / aspect group for BMW feedback rows that lack a
Feedback Type or a usable VFC topic (``no_class_found``, general enquiry).

With an OpenAI key the LLM classifies (prompt ``feedback_classify``); topic and
aspect group are enums of the fixed taxonomy, so it cannot invent one. Without a
key, a transparent keyword heuristic decides kind and severity only (changes.md
2.4); its verdicts are marked ``kind_source = heuristic`` and can be re-run with
the LLM later. The heuristic never guesses a topic: those rows wait for the LLM.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import asdict, dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app import taxonomy
from app.config import BACKEND_DIR, get_settings
from app.llm import prompts
from app.llm.client import LLMUnavailable, get_llm, llm_available

HEURISTIC_VERSION = "heuristic@v1"


@dataclass
class Verdict:
    kind: str | None
    severity: str | None
    summary: str | None
    source: str
    model: str | None
    prompt_version: str | None
    topic_id: str | None = None
    aspect_group: str | None = None
    confidence: str | None = None
    other_suggestion: str | None = None
    about_car: bool = False  # heuristic only: the text names a part of the car


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

# Words that name a part of the car. Only a relevance filter for the offline
# heuristic; it never sets a topic or a car area.
_CAR_WORDS = re.compile(
    r"\b(range|charg\w*|battery|kwh|wallbox|screen|display|idrive|infotainment|navigation|nav|carplay|"
    r"android auto|bluetooth|audio|sound system|speaker|radio|voice|head-?up|hud|menu|app|my bmw|digital key|"
    r"remote|update|ota|connected|subscription|cruise|lane|assist\w*|parking|camera|sensor|blind spot|seat\w*|"
    r"lumbar|massage|headrest|air ?con\w*|a/c|climate|heater|heating|ventilat\w*|cool\w*|handl\w*|ride|"
    r"steer\w*|brak\w*|suspension|corner\w*|grip|chassis|engine|power|accelerat\w*|gear\w*|transmission|"
    r"fuel|mpg|exhaust|torque|boot|trunk|luggage|door\w*|key|lock\w*|cargo|interior|cabin|material\w*|leather|"
    r"storage|cupholder\w*|space|legroom|dashboard|exterior|design|looks|paint|wheel\w*|tyre\w*|tire\w*|"
    r"mirror\w*|light\w*|headlight\w*|lamp\w*|quality|noise|reliab\w*)\b", re.I)


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
    return Verdict(kind, severity, None, "heuristic", None, HEURISTIC_VERSION,
                   about_car=bool(_CAR_WORDS.search(text)))


class _Item(BaseModel):
    row: int
    kind: Literal["complaint", "wish", "like", "none"]
    severity: Literal["defect", "difficult_to_use", "wish", "none"]
    topic_id: taxonomy.TopicId
    aspect_group: taxonomy.AspectGroup
    confidence: taxonomy.Confidence
    other_suggestion: str = Field(description="short proposed name when topic_id is other, else empty")
    summary: str = Field(description="English, at most 20 words, one statement about one feature")


class _Batch(BaseModel):
    items: list[_Item]


def _llm_batch(rows) -> dict[int, Verdict]:
    prompt = prompts.load("feedback_classify")
    payload = [{"row": r.excel_row, "text": r.text} for r in rows]
    result = get_llm().structured(
        prompt, _Batch, topics=taxonomy.prompt_topics(), aspect_groups=taxonomy.prompt_aspects(),
        guard=prompts.INJECTION_GUARD,
        items=prompts.wrap_source(json.dumps(payload, ensure_ascii=False)),
    )
    out = {}
    for item in result.data.items:
        kind = None if item.kind == "none" else item.kind
        summary = " ".join(item.summary.split()[:20]) if kind else None
        out[item.row] = Verdict(kind, item.severity if kind else None, summary, "llm", result.model,
                                result.prompt_version, topic_id=item.topic_id, aspect_group=item.aspect_group,
                                confidence=item.confidence,
                                other_suggestion=item.other_suggestion.strip() or None)
    return out


CACHE_DIR = BACKEND_DIR / ".data" / "llm_cache"


def _cache_key(text: str) -> str:
    """Same text, prompt version and model = same answer; a re-run (e.g. after a dropped
    connection) reuses it instead of paying for the call again."""
    prompt = prompts.load("feedback_classify")
    return hashlib.sha256(f"{prompt.prompt_version}|{get_settings().llm_model_for('feedback_classify')}|{text}"
                          .encode()).hexdigest()


def _load_cache() -> dict[str, dict]:
    path = CACHE_DIR / "feedback_classify.jsonl"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
            out[item["key"]] = item["verdict"]
        except (ValueError, KeyError):
            continue  # a line cut off by a crash
    return out


_CACHE_LOCK = threading.Lock()  # parallel batches append to one file


def _save_cache(entries: list[tuple[str, Verdict]]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _CACHE_LOCK, open(CACHE_DIR / "feedback_classify.jsonl", "a", encoding="utf-8") as f:
        for key, verdict in entries:
            f.write(json.dumps({"key": key, "verdict": asdict(verdict)}, ensure_ascii=False) + "\n")


def classify_rows(rows, batch_size: int = 25) -> dict[int, Verdict]:
    """excel_row -> Verdict for every row that needs a classification from its text.
    LLM answers are cached on disk per batch (backend/.data/llm_cache)."""
    verdicts: dict[int, Verdict] = {}
    if llm_available():
        cache = _load_cache()
        todo = []
        for r in rows:
            hit = cache.get(_cache_key(r.text))
            if hit:
                verdicts[r.excel_row] = Verdict(**hit)
            else:
                todo.append(r)
        # Batches run in parallel (changes.md 10.12); each answered batch is cached at once.
        from concurrent.futures import ThreadPoolExecutor

        batches = [todo[i : i + batch_size] for i in range(0, len(todo), batch_size)]

        def run(batch):
            answers = _llm_batch(batch)
            _save_cache([(_cache_key(r.text), answers[r.excel_row]) for r in batch if r.excel_row in answers])
            return answers

        try:
            from app.llm.cache import WORKERS

            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                for answers in pool.map(run, batches):
                    verdicts.update(answers)
        except LLMUnavailable:
            pass
    for r in rows:
        if r.excel_row not in verdicts:
            verdicts[r.excel_row] = heuristic(r.text)
    return verdicts
