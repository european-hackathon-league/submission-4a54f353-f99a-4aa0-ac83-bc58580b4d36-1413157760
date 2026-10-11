"""Remove every case so the pipeline can group again under new rules:
python -m app.cases.reset --yes

For a database where no person has worked on a case yet (a first build, or a
build under an old opening rule). It refuses when a case has an owner, a PM
decision, a requirement, a stance, a question or a note: those are someone's
work and never removed by a script.

Signals are kept; they only lose their case. Case pages written by the LLM stay
in the disk cache (backend/.data/llm_cache), so the same group is not paid for
again. The reset itself is one audit event with the counts; the earlier audit
events of the removed cases stay (the log is append-only).
"""

from __future__ import annotations

import argparse

from app import audit, db
from app.seed.run import DEFAULT_THRESHOLDS

ACTOR = "system"
OPEN_RULE = ("open_min_customers", "open_min_defects")


def touched_by_people(conn) -> dict:
    """What a person has done on cases. Any non-zero count blocks the reset."""
    one = lambda sql: conn.execute(sql).fetchone()["n"]  # noqa: E731
    return {
        "cases with an owner": one("select count(*) n from case_vehicle where owner_id is not null"),
        "PM decisions on cases": one("select count(*) n from decision where object_type = 'case'"),
        "requirements": one("select count(*) n from requirement"),
        "stances": one("select count(*) n from case_stance"),
        "open questions": one("select count(*) n from open_question"),
        "meeting notes": one("select count(*) n from meeting_note"),
        "referrals": one("select count(*) n from referral"),
        "chat messages": one("select count(*) n from chat_message"),
    }


def reset(conn, *, thresholds: dict | None = None) -> dict:
    blocked = {k: v for k, v in touched_by_people(conn).items() if v}
    if blocked:
        raise SystemExit(f"refused: people have worked on cases ({blocked}). Nothing was changed.")
    counts = {
        "cases": conn.execute("select count(*) n from case_item").fetchone()["n"],
        "case_vehicle": conn.execute("select count(*) n from case_vehicle").fetchone()["n"],
        "signals released": conn.execute("select count(*) n from signal where case_id is not null").fetchone()["n"],
    }
    conn.execute("update signal set case_id = null where case_id is not null")
    for table in ("alert", "proposal", "innovation_item", "case_score", "case_lineage", "case_vehicle"):
        conn.execute(f"delete from {table}")
    conn.execute("delete from page_version where object_type = 'case'")
    conn.execute("delete from notification where object_type = 'case'")
    conn.execute("delete from rag_chunk")
    conn.execute("delete from case_item")
    conn.execute("alter sequence case_number_seq restart with 1")
    # The opening rule of every config (20 customers, 10 customers reporting a defect by default).
    rule = {k: (thresholds or DEFAULT_THRESHOLDS)[k] for k in OPEN_RULE}
    for cfg in conn.execute("select id, thresholds from config").fetchall():
        audit.update(conn, "config", {"id": cfg["id"]}, {"thresholds": {**(cfg["thresholds"] or {}), **rule}},
                     actor=ACTOR, rationale=f"opening rule set to {rule}")
    audit.write(conn, action="reset", object_type="case", object_id="all", actor=ACTOR, after={**counts, **rule},
                rationale="every case removed before grouping again: no person had worked on a case; a group below "
                          "the opening rule becomes no case")
    return {**counts, **rule}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="really remove every case")
    args = parser.parse_args()
    with db.transaction() as conn:
        if not args.yes:
            print("Dry run (add --yes to remove the cases).")
            print({"people worked on": touched_by_people(conn),
                   "cases": conn.execute("select count(*) n from case_item").fetchone()["n"]})
        else:
            print(reset(conn))
