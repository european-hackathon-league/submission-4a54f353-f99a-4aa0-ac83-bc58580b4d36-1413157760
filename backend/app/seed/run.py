"""Seed the database from the provided BMW data: python -m app.seed.run

Steps run in order, each in its own transaction with an agent_run row; a step
that already finished (agent_run status ok) is skipped, so the command is safe
to re-run. ``--review`` only (re)writes the review CSVs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json

from app import audit, db, taxonomy
from app.llm.client import Usage, current_usage
from app.seed import catalog, catalog_proposal, loaders, option_load, readers, registry

DEFAULT_WEIGHTS = {
    "distinct_customers": 0.25, "severity": 0.20, "satisfaction_gap": 0.15, "competitor_gap": 0.15,
    "market_volume": 0.10, "removed_vs_predecessor": 0.05, "reach": 0.05, "trend": 0.05,
}
PROFILES = [
    ("balanced", DEFAULT_WEIGHTS, {"S": 1.0, "M": 0.85, "L": 0.7}, True),
    ("customer voice", {**DEFAULT_WEIGHTS, "distinct_customers": 0.35, "severity": 0.25, "competitor_gap": 0.05,
                        "market_volume": 0.05, "trend": 0.0}, {"S": 1.0, "M": 0.85, "L": 0.7}, False),
    ("competition", {**DEFAULT_WEIGHTS, "competitor_gap": 0.30, "distinct_customers": 0.15, "severity": 0.15,
                     "trend": 0.10}, {"S": 1.0, "M": 0.85, "L": 0.7}, False),
    ("budget", DEFAULT_WEIGHTS, {"S": 1.0, "M": 0.7, "L": 0.4}, False),
]
DEFAULT_THRESHOLDS = {
    "open_min_customers": 10, "conflict_min_customers": 5, "conflict_negative_share": 0.6,
    "similarity_case": {"openai": 0.55, "local": 0.15},
    "case_split_min_signals": 50, "reach_cap": 100000, "competitor_gap_min_rivals": 1,
}
BRAND_PROFILE_V1 = """# BMW brand profile (assumption, version 1)

Status: **assumption**, written from the PM interviews on 2026-10-10. BMW has no single documented source of truth for brand taste; the PM edits this profile and every change is versioned.

- **Sporty, driving dynamics:** rear-wheel-drive character, precise steering, "Freude am Fahren".
- **Design:** distinctive exterior and interior design; evolution, not reinvention.
- **Efficiency:** efficient powertrains, range and charging that keep up with the best rivals.
- **Premium quality and technology:** high-quality materials, intuitive operation, digital services that work.

Off-brand examples: features that trade driving dynamics for gimmicks, or luxury-only items far above the segment of the line.
"""


def _run(agent: str, fn, *args, force: bool = False):
    with db.transaction() as conn:
        done = conn.execute(
            "select 1 from agent_run where agent = %s and status = 'ok' limit 1", (agent,)
        ).fetchone()
        if done and not force:
            print(f"skip {agent} (already seeded)")
            return None
        run = conn.execute(
            "insert into agent_run (agent, mode) values (%s, 'seed') returning id", (agent,)
        ).fetchone()["id"]
    usage = Usage()
    token = current_usage.set(usage)
    try:
        with db.transaction() as conn:
            result = fn(conn, run, *args)
            records = result.get("records", 0) if isinstance(result, dict) else (result or 0)
            conn.execute(
                "update agent_run set status = 'ok', ended_at = now(), records_fetched = %s, signals_valid = %s,"
                " signals_quarantined = %s, llm_tokens = %s, llm_cost_usd = %s, params = %s where id = %s",
                (records, (result or {}).get("signals", 0) - (result or {}).get("quarantined", 0)
                 if isinstance(result, dict) else 0,
                 (result or {}).get("quarantined", 0) if isinstance(result, dict) else 0,
                 usage.tokens, usage.cost_usd, json.dumps({"result": result, "llm": usage.by_prompt}, default=str),
                 run),
            )
    except Exception as exc:
        with db.transaction() as conn:
            conn.execute("update agent_run set status = 'failed', ended_at = now(), error = %s where id = %s",
                         (repr(exc)[:2000], run))
        raise
    finally:
        current_usage.reset(token)
    print(f"{agent}: {result}")
    return result


def step_reference(conn, run):
    n_areas = catalog.load_car_areas(conn)
    counts = registry.load(conn)
    return {"car_areas": n_areas, "taxonomy": taxonomy.version(), **counts}


def step_features(conn, run):
    ids = catalog.load_features(conn)
    return {"topics": len(ids)}


def step_configs(conn, run):
    """Competitor markets, brand profile, one config per valid vehicle x market, weight profiles."""
    vehicles = conn.execute("select code, markets_sold::text[] as markets_sold from vehicle where is_current").fetchall()
    data = registry.REGISTRY_YAML.read_text(encoding="utf-8")
    import yaml

    not_sold = {(c["bmw_vehicle"], c["brand"], c["model"]): c["not_sold_in"]
                for c in yaml.safe_load(data)["competitors"]}
    sold = {v["code"]: v["markets_sold"] for v in vehicles}
    for c in conn.execute("select id, bmw_vehicle, brand, model from competitor").fetchall():
        markets = [m for m in sold.get(c["bmw_vehicle"], []) if m not in not_sold.get(
            (c["bmw_vehicle"], c["brand"], c["model"]), []) and m != "Rest of World"]
        audit.update(conn, "competitor", {"id": c["id"]}, {"markets": markets}, actor="seed:configs",
                     rationale="markets of the BMW line minus 'not sold in' notes (fetching-sources 2.3)")
    profile = audit.insert(conn, "brand_profile", {"version": 1, "body_md": BRAND_PROFILE_V1,
                                                   "created_by": "seed:configs"},
                           actor="seed:configs", rationale="initial brand profile (assumption)")
    configs = 0
    for v in vehicles:
        for market in v["markets_sold"]:
            comps = [str(r["id"]) for r in conn.execute(
                "select id from competitor where bmw_vehicle = %s and %s = any(markets) and confirmed",
                (v["code"], market)).fetchall()]
            cfg = audit.insert(conn, "config", {
                "name": f"{v['code']} / {market}", "vehicle": v["code"], "market": market,
                "competitor_ids": comps, "brand_profile_id": profile["id"], "source_filter": {},
                "thresholds": DEFAULT_THRESHOLDS,
            }, actor="seed:configs", rationale="default configuration for a vehicle and market with sales")
            for name, weights, cost, default in PROFILES:
                audit.insert(conn, "weight_profile", {
                    "config_id": cfg["id"], "name": name, "case_weights": weights,
                    "brand_multipliers": {"fits": 1.0, "neutral": 0.8, "off_brand": 0.5},
                    "cost_multipliers": cost, "is_default": default, "created_by": "seed:configs",
                }, actor="seed:configs", rationale="default weight profile")
            configs += 1
    return {"configs": configs}


def write_review_files() -> dict:
    attrs = list(dict.fromkeys(s.attribute for s in readers.read_study()))
    return {"catalog": catalog_proposal.write_review_files(attrs), "options": option_load.write_review()}


def seed_all(force: bool = False) -> dict:
    write_review_files()
    out = {}
    out["reference"] = _run("seed:reference", step_reference, force=force)
    out["sales"] = _run("seed:sales", loaders.load_sales, force=force)
    out["features"] = _run("seed:features", step_features, force=force)
    out["study"] = _run("seed:study", loaders.load_study, force=force)

    def options(conn, run):
        return option_load.load(conn, run, catalog.topic_ids(conn))

    out["options"] = _run("seed:option_list", options, force=force)
    out["feedback"] = _run("seed:feedback", loaders.load_feedback, force=force)
    out["configs"] = _run("seed:configs", step_configs, force=force)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", action="store_true", help="only write the review CSVs")
    args = parser.parse_args()
    started = dt.datetime.now()
    if args.review:
        print(write_review_files())
        return
    seed_all()
    print(f"seed finished in {(dt.datetime.now() - started).seconds} s")


if __name__ == "__main__":
    main()
