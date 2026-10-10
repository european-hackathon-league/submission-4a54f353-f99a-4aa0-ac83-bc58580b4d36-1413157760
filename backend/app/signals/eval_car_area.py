"""Eval: car-area tagging from text vs the team's VFC topic -> car area mapping.

python -m app.signals.eval_car_area [--n 300]

Takes a deterministic sample of BMW feedback signals whose VFC topic maps to a
specific car area, tags each verbatim from its text alone and reports accuracy:
the LLM tagger (same prompt the classifier uses) when an OpenAI key is set, and
the keyword heuristic as a baseline. Report: backend/reports/car_area_eval.md.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass

from app import db
from app.config import BACKEND_DIR
from app.llm.client import llm_available
from app.seed import classify

REPORT = BACKEND_DIR / "reports" / "car_area_eval.md"


@dataclass
class _Row:
    excel_row: int
    text: str


def sample(conn, n: int) -> list[dict]:
    return conn.execute(
        "select distinct on (s.record_id) s.id, s.record_id, s.car_area[1] as expected, s.quote"
        " from signal s where s.extra->>'kind_source' = 'bmw_label' and s.car_area[1] <> 'general'"
        " and s.validation = 'valid'"
        " order by s.record_id, md5(s.id::text) limit %s",
        (n * 3,),
    ).fetchall()[:: 3][:n]


def score(pairs: list[tuple[str, str | None]]) -> tuple[float, Counter]:
    hits = sum(1 for exp, got in pairs if exp == got)
    confusions = Counter((exp, got) for exp, got in pairs if exp != got)
    return (hits / len(pairs) if pairs else 0.0), confusions


def run(n: int = 300) -> dict:
    with db.transaction() as conn:
        rows = sample(conn, n)
    results = {}
    heuristic = [(r["expected"], classify.heuristic(r["quote"]).car_area) for r in rows]
    results["heuristic@v1"] = score(heuristic)
    if llm_available():
        verdicts = classify.classify_rows([_Row(i, r["quote"]) for i, r in enumerate(rows)])
        llm = [(r["expected"], verdicts[i].car_area) for i, r in enumerate(rows)]
        results["llm:feedback_classify"] = score(llm)
    lines = [
        "# Car-area tagging eval",
        "",
        f"Sample: {len(rows)} BMW feedback verbatims whose VFC topic maps to a specific car area "
        "(one signal per verbatim, deterministic order). Expected = car area of the VFC topic in "
        "`seed/review/vfc_topics.csv`; predicted = tag from the verbatim text alone.",
        "",
        "| Tagger | Accuracy |",
        "|---|---|",
    ]
    for name, (acc, _) in results.items():
        lines.append(f"| {name} | {acc:.1%} |")
    if not llm_available():
        lines += ["", "LLM tagger not run: no `OPENAI_API_KEY` configured."]
    for name, (_, conf) in results.items():
        lines += ["", f"Most frequent confusions ({name}), expected -> predicted:", ""]
        for (exp, got), c in conf.most_common(8):
            lines.append(f"- {exp} -> {got or 'none'}: {c}")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {k: round(v[0], 3) for k, v in results.items()} | {"n": len(rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=300)
    print(run(parser.parse_args().n))
