"""Case figures: everything a PM needs to judge a case, per BMW line and market.

Competitor signals are competitor evidence only: they never count as BMW
customers. Reach is capped per signal (one influencer is one opinion).
"""

from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict

NEGATIVE = ("complaint", "wish")


def excluded(signal: dict, source_filter: dict | None) -> bool:
    """True when the config's source filter skips this signal's source."""
    if not source_filter:
        return False
    return (signal["source_id"] in (source_filter.get("exclude_sources") or [])
            or signal["category"] in (source_filter.get("exclude_categories") or [])
            or signal["content_type"] in (source_filter.get("exclude_content_types") or []))


def has_filter(source_filter: dict | None) -> bool:
    return bool(source_filter) and any(source_filter.get(k) for k in (
        "exclude_sources", "exclude_categories", "exclude_content_types"))


def compute(conn, case_id: str, reach_cap: int = 100000, source_filter: dict | None = None) -> dict:
    """Figures of one case. With a source filter (config, "PMs skip sources") the
    signals of excluded sources are left out; the stored figures use no filter."""
    case = conn.execute("select id, feature_id from case_item where id = %s", (case_id,)).fetchone()
    signals = conn.execute(
        "select s.id, s.kind, s.severity, s.market, s.vehicle, s.powertrain, s.customer_ref, s.evidence_role,"
        " s.reach, s.vehicle_role, r.source_id, r.content_type, r.category"
        " from signal s join source_record r using (record_id)"
        " where s.case_id = %s and s.validation = 'valid'",
        (case_id,),
    ).fetchall()
    signals = [s for s in signals if not excluded(s, source_filter)]
    vehicles = sorted({s["vehicle"] for s in signals if s["vehicle"]})
    by_vehicle = {}
    for v in vehicles:
        own = [s for s in signals if s["vehicle"] == v or s["vehicle"] is None]
        customers = [s for s in own if s["evidence_role"] == "bmw_customer"]
        markets: dict[str, dict] = defaultdict(lambda: {
            "customer_ids": set(), "complaints": 0, "wishes": 0, "likes": 0, "defects": 0, "difficult_to_use": 0,
            "powertrain": defaultdict(set), "reach": 0.0,
        })
        for s in customers:
            m = markets[s["market"]]
            m["customer_ids"].add(s["customer_ref"])
            m["powertrain"][s["powertrain"]].add(s["customer_ref"])
            m[{"complaint": "complaints", "wish": "wishes", "like": "likes"}.get(s["kind"], "likes")] += \
                1 if s["kind"] in ("complaint", "wish", "like") else 0
            if s["severity"] == "defect":
                m["defects"] += 1
            if s["severity"] == "difficult_to_use":
                m["difficult_to_use"] += 1
            if s["reach"]:
                m["reach"] += min(s["reach"], reach_cap)
        market_out = {}
        for name, m in markets.items():
            opinions = m["complaints"] + m["wishes"] + m["likes"]
            market_out[name] = {
                "customers": len(m["customer_ids"]),
                "complaints": m["complaints"], "wishes": m["wishes"], "likes": m["likes"],
                "defects": m["defects"], "difficult_to_use": m["difficult_to_use"],
                "negative_share": round((m["complaints"] + m["wishes"]) / opinions, 3) if opinions else None,
                "powertrain": {p: len(ids) for p, ids in m["powertrain"].items()},
                "reach_capped": m["reach"],
            }
        competitor = [s for s in own if s["evidence_role"] == "competitor"]
        by_vehicle[v] = {
            "distinct_customers": len({s["customer_ref"] for s in customers}),
            "defect_customers": len({s["customer_ref"] for s in customers if s["severity"] == "defect"}),
            "markets": market_out,
            "predecessor_signals": sum(1 for s in own if s["vehicle_role"] == "predecessor"),
            "competitor_evidence": {
                "signals": len(competitor),
                "complaints": sum(1 for s in competitor if s["kind"] == "complaint"),
                "likes_or_facts": sum(1 for s in competitor if s["kind"] in ("like", "competitor_fact")),
            },
            "trend_signals": sum(1 for s in own if s["kind"] in ("trend", "innovation")),
            "study": _study(conn, v, case["feature_id"]),
            "equipment": _equipment(conn, v, case["feature_id"]),
            "competitor_offers": _competitor_offers(conn, v, case["feature_id"]),
        }
    sources = defaultdict(int)
    source_types = defaultdict(int)
    for s in signals:
        sources[s["source_id"]] += 1
        source_types[s["content_type"] or "unknown"] += 1
    return {
        "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "signals": len(signals),
        "distinct_customers": len({s["customer_ref"] for s in signals if s["evidence_role"] == "bmw_customer"}),
        "by_vehicle": by_vehicle,
        "sources": dict(sources),
        "source_types": dict(source_types),
    }


def _study(conn, vehicle: str, feature_id) -> list[dict]:
    rows = conn.execute(
        "select market, sheet, attribute, method, normalized, dissatisfied_share from study_score"
        " where vehicle = %s and feature_id = %s order by market, attribute",
        (vehicle, feature_id),
    ).fetchall()
    return [{k: (float(v) if hasattr(v, "as_integer_ratio") and not isinstance(v, int) else v)
             for k, v in r.items()} for r in rows]


def _equipment(conn, vehicle: str, feature_id) -> dict:
    rows = conn.execute(
        "select variant, availability, item_name, sa_code, price_eur, package from vehicle_feature"
        " where vehicle = %s and feature_id = %s",
        (vehicle, feature_id),
    ).fetchall()
    if not rows:
        return {"listed": False}
    by_variant: dict[str, str] = {}
    rank = {"standard": 3, "optional": 2, "removed": 1, "absent": 0}
    for r in rows:
        if rank[r["availability"]] > rank.get(by_variant.get(r["variant"], "absent"), -1):
            by_variant[r["variant"]] = r["availability"]
    prices = sorted({float(r["price_eur"]) for r in rows if r["price_eur"]})
    items = sorted({(r["item_name"], r["sa_code"] or "", r["package"] or "") for r in rows})
    return {
        "listed": True,
        "market": "DE",
        "standard": sorted(v for v, a in by_variant.items() if a == "standard"),
        "optional": sorted(v for v, a in by_variant.items() if a == "optional"),
        "absent": sorted(v for v, a in by_variant.items() if a == "absent"),
        "removed": any(r["availability"] == "removed" for r in rows),
        "option_prices_eur": prices,
        "items": [{"item": i, "sa_code": c or None, "package": p or None} for i, c, p in items][:20],
    }


def _competitor_offers(conn, vehicle: str, feature_id) -> list[dict]:
    """Competitor equipment facts (data_record) linked through a competitor signal of this feature."""
    rows = conn.execute(
        "select distinct d.record_id, c.brand, c.model, d.metric, d.value, d.value_text, d.unit, d.period"
        " from signal s join data_record d on d.record_id = s.data_record_id"
        " join competitor c on c.id = s.competitor_id"
        " where s.feature_id = %s and c.bmw_vehicle = %s and s.validation = 'valid'",
        (feature_id, vehicle),
    ).fetchall()
    return [{k: (float(v) if k == "value" and v is not None else v) for k, v in r.items()} for r in rows]


def conflict(markets: dict, min_customers: int, negative_share: float = 0.6) -> tuple[bool, str | None]:
    """One market mostly negative, another mostly positive, each with enough customers."""
    eligible = {m: f for m, f in markets.items()
                if m != "unknown" and f["customers"] >= min_customers and f["negative_share"] is not None}
    negative = [m for m, f in eligible.items() if f["negative_share"] >= negative_share]
    positive = [m for m, f in eligible.items() if f["negative_share"] <= 1 - negative_share]
    if negative and positive:
        n, p = negative[0], positive[0]
        return True, (f"{n}: {eligible[n]['negative_share']:.0%} negative of {eligible[n]['customers']} customers; "
                      f"{p}: {1 - eligible[p]['negative_share']:.0%} positive of {eligible[p]['customers']} customers")
    return False, None


def log_reach(value: float, cap: int) -> float:
    return math.log1p(min(value, cap)) / math.log1p(cap) if cap else 0.0
