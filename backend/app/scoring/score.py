"""Case importance score (no cost: a problem has no cost).

score = brand multiplier x sum(weight_i x factor_i) / sum(weight_i), every
factor in 0..1, computed for one weight profile, i.e. one config (vehicle +
market). Each computation is stored as a new case_score row with the full
breakdown and a rationale generated from it.
"""

from __future__ import annotations

import math

from app import audit
from app.cases import figures

FACTORS = ("distinct_customers", "severity", "satisfaction_gap", "competitor_gap", "market_volume",
           "removed_vs_predecessor", "reach", "trend")
SEVERITY_WEIGHT = {"defects": 1.0, "difficult_to_use": 0.7, "wishes": 0.4}
ACTOR = "agent:scoring"


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def normalize_weights(weights: dict) -> dict:
    """Missing factors count as 0. A profile with no positive weight cannot score."""
    w = {f: float(weights.get(f, 0) or 0) for f in FACTORS}
    if any(not math.isfinite(v) or v < 0 for v in w.values()):
        raise ValueError("weights must be finite and >= 0")
    total = sum(w.values())
    if total <= 0:
        raise ValueError("weights must have a positive sum")
    return {f: v / total for f, v in w.items()}


def factor_customers(n: int, reference: int) -> float:
    return _clip(math.log1p(n) / math.log1p(max(reference, 1)))


def factor_severity(m: dict) -> float:
    opinions = m.get("complaints", 0) + m.get("wishes", 0) + m.get("likes", 0)
    if not opinions:
        return 0.0
    complaints_other = max(0, m.get("complaints", 0) - m.get("defects", 0) - m.get("difficult_to_use", 0))
    weighted = (m.get("defects", 0) * 1.0 + m.get("difficult_to_use", 0) * 0.7 + complaints_other * 0.7
                + m.get("wishes", 0) * 0.4)
    return _clip(weighted / opinions)


def factor_satisfaction_gap(study: list[dict], market: str) -> tuple[float, str]:
    """1 - satisfaction, rescaled so that 7/10 (normalized 0.7) or worse is a full gap."""
    rows = [s for s in study if s["market"] == market]
    if not rows:
        return 0.0, "no study attribute for this feature and market"
    gaps = []
    for s in rows:
        gap = _clip((1 - float(s["normalized"])) / 0.3)
        if s.get("dissatisfied_share") is not None:
            gap = max(gap, _clip(float(s["dissatisfied_share"]) / 0.2))
        gaps.append(gap)
    worst = max(gaps)
    return worst, f"{len(rows)} study attribute(s), worst gap {worst:.2f}"


def factor_competitor_gap(vehicle_figures: dict) -> tuple[float, str]:
    evidence = vehicle_figures.get("competitor_evidence", {}).get("likes_or_facts", 0)
    offers = vehicle_figures.get("competitor_offers", [])
    if not evidence and not offers:
        return 0.0, "no competitor evidence"
    eq = vehicle_figures.get("equipment", {})
    if not eq.get("listed") or (not eq.get("standard") and not eq.get("optional")):
        return 1.0, "rivals offer it, not in our option list"
    if not eq.get("standard"):
        return 0.6, "rivals offer it, optional here"
    if eq.get("absent") or eq.get("optional"):
        return 0.4, "rivals offer it, standard only on some variants"
    return 0.2, "rivals praised for it, standard here"


def compute(case: dict, profile: dict, config: dict, refs: dict) -> dict:
    """Pure function: factor breakdown, score and rationale for one case and profile."""
    vehicle, market = config["vehicle"], config["market"]
    vf = (case["figures"] or {}).get("by_vehicle", {}).get(vehicle, {})
    m = vf.get("markets", {}).get(market, {})
    weights = normalize_weights(profile["case_weights"])
    cap = int((config.get("thresholds") or {}).get("reach_cap", 100000))
    sat, sat_note = factor_satisfaction_gap(vf.get("study", []), market)
    comp, comp_note = factor_competitor_gap(vf)
    eq = vf.get("equipment", {})
    removed = 1.0 if eq.get("removed") or "removed_vs_predecessor" in (case.get("tags") or []) else 0.0
    raw = {
        "distinct_customers": m.get("customers", 0),
        "severity": {k: m.get(k, 0) for k in ("complaints", "wishes", "likes", "defects", "difficult_to_use")},
        "satisfaction_gap": sat_note,
        "competitor_gap": comp_note,
        "market_volume": refs.get("volume", 0),
        "removed_vs_predecessor": bool(removed),
        "reach": m.get("reach_capped", 0),
        "trend": vf.get("trend_signals", 0),
    }
    values = {
        "distinct_customers": factor_customers(m.get("customers", 0), refs.get("max_customers", 1)),
        "severity": factor_severity(m),
        "satisfaction_gap": sat,
        "competitor_gap": comp,
        "market_volume": _clip(refs.get("volume", 0) / max(refs.get("max_volume", 1), 1)),
        "removed_vs_predecessor": removed,
        "reach": _clip(math.log1p(min(m.get("reach_capped", 0), cap)) / math.log1p(cap)) if cap else 0.0,
        "trend": _clip(vf.get("trend_signals", 0) / 5),
    }
    factors = {f: {"raw": raw[f], "normalized": round(values[f], 4), "weight": round(weights[f], 4),
                   "contribution": round(values[f] * weights[f], 4)} for f in FACTORS}
    fit = case.get("brand_fit")
    multipliers = profile.get("brand_multipliers") or {"fits": 1.0, "neutral": 0.8, "off_brand": 0.5}
    brand = float(multipliers.get(fit or "neutral", 0.8))
    base = sum(f["contribution"] for f in factors.values())
    score = round(base * brand, 4)
    return {"factors": factors, "brand_multiplier": brand, "score": score,
            "rationale": rationale(case, config, profile, factors, brand, fit, score)}


def rationale(case, config, profile, factors, brand, fit, score) -> str:
    top = sorted(factors.items(), key=lambda kv: kv[1]["contribution"], reverse=True)
    parts = []
    labels = {
        "distinct_customers": lambda f: f"{f['raw']} distinct customers",
        "severity": lambda f: (f"{f['raw']['defects']} defects, {f['raw']['difficult_to_use']} difficult to use, "
                               f"{f['raw']['wishes']} wishes, {f['raw']['likes']} likes"),
        "satisfaction_gap": lambda f: f"study: {f['raw']}",
        "competitor_gap": lambda f: f"competition: {f['raw']}",
        "market_volume": lambda f: f"2030 volume {int(f['raw'] or 0):,} units",
        "removed_vs_predecessor": lambda f: "removed vs predecessor" if f["raw"] else "not removed",
        "reach": lambda f: f"capped reach {int(f['raw'] or 0):,}",
        "trend": lambda f: f"{f['raw']} trend signals",
    }
    for name, f in top:
        if f["weight"] == 0:
            continue
        parts.append(f"{labels[name](f)} ({f['normalized']:.2f} x {f['weight']:.2f} = {f['contribution']:.3f})")
    fit_text = fit or "unknown, treated as neutral"
    text = (f"Score {score:.3f} for {config['vehicle']} / {config['market']}, profile '{profile['name']}'. "
            f"Brand fit {fit_text} (x{brand:.2f}). Factors, largest first: " + "; ".join(parts) + ".")
    sf = config.get("source_filter") or {}
    if figures.has_filter(sf):
        skipped = [f"{k.removeprefix('exclude_')}: {', '.join(v)}" for k, v in sf.items() if v]
        text += " Source filter skips " + "; ".join(skipped) + "."
    return text


def references(conn, config: dict, cases: list[dict]) -> dict:
    vol = conn.execute(
        "select market, units from sales_volume where vehicle = %s and year = 2030", (config["vehicle"],)
    ).fetchall()
    volumes = {r["market"]: r["units"] or 0 for r in vol}
    max_customers = max([((c["figures"] or {}).get("by_vehicle", {}).get(config["vehicle"], {})
                          .get("markets", {}).get(config["market"], {}).get("customers", 0)) for c in cases] + [1])
    return {"volume": volumes.get(config["market"], 0), "max_volume": max(volumes.values() or [1]),
            "max_customers": max_customers}


def score_config(conn, config_id, *, profile_ids: list | None = None, states=("open", "confirmed", "observed"),
                 run_id=None, case_ids: list[str] | None = None) -> int:
    """Score every case of the config's vehicle under each (or the given) weight profile."""
    config = conn.execute("select * from config where id = %s", (config_id,)).fetchone()
    profiles = conn.execute("select * from weight_profile where config_id = %s" +
                            (" and id = any(%s)" if profile_ids else ""),
                            (config_id, profile_ids) if profile_ids else (config_id,)).fetchall()
    cases = conn.execute(
        "select c.*, cv.manual_rank from case_item c join case_vehicle cv on cv.case_id = c.id"
        " where cv.vehicle = %s and cv.state = any(%s)", (config["vehicle"], list(states))).fetchall()
    if figures.has_filter(config["source_filter"]):
        cap = int((config["thresholds"] or {}).get("reach_cap", 100000))
        cases = [{**c, "figures": figures.compute(conn, c["id"], cap, config["source_filter"])} for c in cases]
    refs = references(conn, config, cases)
    rows = []
    for profile in profiles:
        for case in cases:
            if case_ids is not None and case["id"] not in case_ids:
                continue
            result = compute(case, profile, config, refs)
            rows.append({"case_id": case["id"], "weight_profile_id": profile["id"], **result})
    audit.insert_many(conn, "case_score", rows, actor=ACTOR, run_id=run_id,
                      rationale="case score from the factor breakdown")
    return len(rows)


def compare_profiles(conn, config_id, top_n: int = 10) -> dict:
    """Which cases stay in the top N under every profile of the config."""
    rows = conn.execute(
        "select s.case_id, s.score, p.name from case_score_current s join weight_profile p on p.id = s.weight_profile_id"
        " where p.config_id = %s", (config_id,)).fetchall()
    by_profile: dict[str, list] = {}
    for r in rows:
        by_profile.setdefault(r["name"], []).append(r)
    tops = {name: [r["case_id"] for r in sorted(v, key=lambda r: -float(r["score"]))[:top_n]]
            for name, v in by_profile.items()}
    always = set.intersection(*(set(t) for t in tops.values())) if tops else set()
    return {"top_n": top_n, "profiles": tops, "in_top_under_every_profile": sorted(always)}
