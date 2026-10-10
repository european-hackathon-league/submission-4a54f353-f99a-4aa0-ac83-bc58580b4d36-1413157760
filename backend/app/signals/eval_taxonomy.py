"""Eval: topic classification from text vs BMW's own labels (taxonomy/eval).

python -m app.signals.eval_taxonomy [--split dev|test] [--limit N]

Classifies the verbatims of ``taxonomy/eval/gold.csv`` from their text alone,
writes ``predictions`` (``id,topic_ids``) and scores them with
``taxonomy/eval/score.py``. It runs the LLM classifier (the prompt the pipeline
uses) and needs an OpenAI key. Tune on dev only and run test once at the end
(taxonomy/README.md). Report: backend/reports/taxonomy_eval.md.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from dataclasses import dataclass

from app import taxonomy
from app.config import BACKEND_DIR
from app.llm.client import llm_available
from app.seed import classify

REPORTS = BACKEND_DIR / "reports"
EVAL_DIR = taxonomy.TAXONOMY_DIR / "eval"


@dataclass
class _Row:
    excel_row: int
    text: str


def gold(split: str, limit: int | None) -> list[dict]:
    with open(EVAL_DIR / "gold.csv", newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["split"] == split]
    return rows[:limit] if limit else rows


def run(split: str = "dev", limit: int | None = None) -> str:
    if not llm_available():
        raise SystemExit("eval_taxonomy needs OPENAI_API_KEY in backend/.env (it measures the LLM classifier)")
    rows = gold(split, limit)
    verdicts = classify.classify_rows([_Row(i, r["text"]) for i, r in enumerate(rows)])
    predicted = [verdicts[i].topic_id or taxonomy.OTHER for i in range(len(rows))]
    name = f"LLM classifier ({verdicts[0].prompt_version}, {verdicts[0].model})" if rows else "LLM classifier"
    REPORTS.mkdir(parents=True, exist_ok=True)
    predictions = REPORTS / f"taxonomy_predictions_{split}.csv"
    with open(predictions, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "topic_ids"])
        w.writerows((r["id"], p) for r, p in zip(rows, predicted))
    scored = subprocess.run([sys.executable, str(EVAL_DIR / "score.py"), str(predictions), "--split", split],
                            capture_output=True, text=True, check=True).stdout
    report = "\n".join([
        "# Topic classification eval",
        "",
        f"Taxonomy `{taxonomy.version()}`, split `{split}`, {len(rows)} BMW-labelled verbatims "
        "(`taxonomy/eval/gold.csv`). Predicted from the verbatim text alone; expected = BMW's VFC labels.",
        "",
        f"Classifier: **{name}**.",
        "",
        "```",
        scored.strip(),
        "```",
        "",
        "BMW tagged some topics per verbatim, not necessarily all, so precision is a lower bound "
        "(taxonomy/README.md).",
    ]) + "\n"
    (REPORTS / "taxonomy_eval.md").write_text(report, encoding="utf-8")
    return scored


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    print(run(args.split, args.limit))
