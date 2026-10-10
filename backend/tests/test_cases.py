"""Phase 4: cases on a small synthetic data set (the fixture is reused by later phases)."""

import pytest

from app import audit
from app.cases import cluster, feature_link, figures, lifecycle, pipeline
from app.llm.embeddings import get_embedder
from app.signals import service

VENT = "Seat heating and ventilation"
FACTORS = ("distinct_customers", "severity", "satisfaction_gap", "competitor_gap", "market_volume",
           "removed_vs_predecessor", "reach", "trend")


@pytest.fixture
def world(conn):
    for code in ("seats", "infotainment", "general"):
        audit.insert(conn, "car_area", {"code": code, "name": code}, actor="seed:test", key="code")
    for code in ("reddit", "bmw_sales"):
        audit.insert(conn, "source_registry", {"source_id": code, "name": code}, actor="seed:test", key="source_id")
    for v in ("G60", "G70"):
        audit.insert(conn, "vehicle", {"code": v, "series": v, "is_current": True}, actor="seed:test", key="code")
        for market, units in (("USA", 80000), ("China", 75000)):
            audit.insert(conn, "data_record", {
                "record_id": f"bmw_sales-{v}-{market}-2030", "source_id": "bmw_sales", "chassis_code": v,
                "market": market, "period": "2030 forecast", "metric": "sales units", "value": units,
            }, actor="seed:test", key="record_id")
    emb = get_embedder()
    ids = {}
    for name, area in ((VENT, "seats"), ("Navigation system", "infotainment")):
        ids[name] = audit.insert(conn, "feature", {"name": name, "car_area": area, "status": "confirmed",
                                                   "embedding": emb.embed([name])[0]}, actor="seed:test")["id"]
    competitor = audit.insert(conn, "competitor", {"bmw_vehicle": "G60", "brand": "Mercedes-Benz",
                                                   "model": "E-Class"}, actor="seed:test")
    cfg = audit.insert(conn, "config", {"name": "G60 / USA", "vehicle": "G60", "market": "USA",
                                        "thresholds": {"open_min_customers": 10, "conflict_min_customers": 5,
                                                       "similarity_case": {"local": 0.3},
                                                       "similarity_feature": {"local": 0.3}}},
                       actor="seed:test")
    profiles = {}
    for name, weights in (("balanced", FACTORS), ("customers only", ("distinct_customers",))):
        profiles[name] = audit.insert(conn, "weight_profile", {
            "config_id": cfg["id"], "name": name, "case_weights": {f: 1.0 for f in weights},
            "is_default": name == "balanced", "created_by": "seed:test"}, actor="seed:test")["id"]

    proposals = []

    def add(n, vehicle, market, text, kind, severity, feature, role="bmw_customer", **extra):
        for i in range(n):
            rid = f"reddit-{vehicle}-{market}-{kind}-{feature[:3]}-{i}"
            audit.insert(conn, "source_record", {"record_id": rid, "source_id": "reddit", "text_original": text,
                                                 "content_hash": rid, "author_hash": rid, "market": market},
                         actor="agent:feedback", key="record_id")
            proposals.append({"record_id": rid, "kind": kind, "severity": severity, "summary": text[:60],
                              "quote": text, "car_area": ["seats" if feature == VENT else "infotainment"],
                              "feature_hint": feature, "vehicle": vehicle, "market": market,
                              "customer_ref": rid, "evidence_role": role, **extra})

    add(12, "G60", "USA", "The front seat ventilation is too weak in hot weather", "complaint", "difficult_to_use", VENT)
    add(6, "G60", "China", "The front seat ventilation works great in hot weather", "like", "none", VENT)
    add(3, "G70", "USA", "Seat ventilation in front too weak in hot weather", "complaint", "defect", VENT)
    add(2, "G60", "USA", "Rivals have strong front seat ventilation for hot weather", "like", "none", VENT,
        role="competitor", competitor_id=str(competitor["id"]))
    add(3, "G70", "USA", "Navigation map routing loses the route on highways", "complaint", "defect",
        "Navigation system")
    out = service.ingest(conn, proposals, actor="agent:feedback")
    assert out["valid"] == len(proposals), out
    return {"conn": conn, "features": ids, "config": cfg, "profiles": profiles}


def _run_cases(conn):
    feature_link.link(conn, min_similarity=0.3)
    cluster.initial(conn, 0.3)
    for r in conn.execute("select id from case_item").fetchall():
        pipeline.update_case(conn, r["id"])


def test_feature_link_and_case_spans_lines(world):
    conn = world["conn"]
    _run_cases(conn)
    vent_case = conn.execute(
        "select c.id from case_item c where c.feature_id = %s order by c.id limit 1", (world["features"][VENT],)
    ).fetchone()["id"]
    vehicles = {r["vehicle"] for r in conn.execute("select vehicle from case_vehicle where case_id = %s",
                                                   (vent_case,)).fetchall()}
    assert vehicles == {"G60", "G70"}
    unassigned = conn.execute("select count(*) n from signal where case_id is null").fetchone()["n"]
    assert unassigned == 0


def test_figures_conflict_and_open_rule(world):
    conn = world["conn"]
    _run_cases(conn)
    case = conn.execute("select * from case_item where feature_id = %s order by id limit 1",
                        (world["features"][VENT],)).fetchone()
    g60 = case["figures"]["by_vehicle"]["G60"]
    assert g60["markets"]["USA"]["customers"] == 12  # competitor signals never count as customers
    assert g60["competitor_evidence"]["likes_or_facts"] == 2
    states = {r["vehicle"]: r for r in conn.execute("select * from case_vehicle where case_id = %s",
                                                     (case["id"],)).fetchall()}
    assert states["G60"]["state"] == "open" and states["G60"]["opened_by"].startswith("rule:min_customers")
    assert states["G60"]["market_conflict"] is True
    assert states["G70"]["state"] == "open" and "defect" in states["G70"]["state_reason"]
    assert states["G60"]["release_fit"] == "next_gen" and states["G60"]["release_fit_source"] == "ai"
    kinds = {r["type"] for r in conn.execute("select type from notification").fetchall()}
    assert {"case_opened", "market_conflict"} <= kinds


def test_conflict_needs_enough_customers_each_side():
    markets = {"USA": {"customers": 12, "negative_share": 1.0}, "China": {"customers": 4, "negative_share": 0.0}}
    assert figures.conflict(markets, 5)[0] is False
    markets["China"]["customers"] = 5
    assert figures.conflict(markets, 5)[0] is True


def test_lifecycle_rules(world):
    conn = world["conn"]
    _run_cases(conn)
    nav = conn.execute("select id from case_item where feature_id = %s", (world["features"]["Navigation system"],)
                       ).fetchone()["id"]
    with pytest.raises(lifecycle.InvalidTransition):
        lifecycle.transition(conn, nav, "G70", "reject", actor="pm:anna", rationale=None)
    lifecycle.transition(conn, nav, "G70", "confirm", actor="pm:anna", rationale="discussed in PM meeting")
    with pytest.raises(lifecycle.InvalidTransition):
        lifecycle.transition(conn, nav, "G70", "confirm", actor="pm:anna", rationale="again")
    decision = conn.execute("select action, actor from decision where object_id = %s", (nav,)).fetchone()
    assert decision == {"action": "confirm", "actor": "pm:anna"}


def test_new_evidence_on_confirmed_case_only_flags(world):
    conn = world["conn"]
    _run_cases(conn)
    nav = conn.execute("select id, title, body_md from case_item where feature_id = %s",
                       (world["features"]["Navigation system"],)).fetchone()
    lifecycle.transition(conn, nav["id"], "G70", "confirm", actor="pm:anna", rationale="ok")
    audit.insert(conn, "source_record", {"record_id": "reddit-new", "source_id": "reddit", "content_hash": "new",
                                         "text_original": "Navigation map routing loses the route on highways"},
                 actor="agent:feedback", key="record_id")
    service.ingest(conn, [{"record_id": "reddit-new", "kind": "complaint", "severity": "defect",
                           "summary": "Navigation loses route", "quote": "Navigation map routing loses the route",
                           "car_area": ["infotainment"], "feature_hint": "Navigation system", "vehicle": "G70",
                           "customer_ref": "new"}], actor="agent:feedback")
    feature_link.link(conn, min_similarity=0.3)
    result = cluster.incremental(conn, 0.3)
    assert result["attached"] == 1
    after = conn.execute("select title, body_md from case_item where id = %s", (nav["id"],)).fetchone()
    assert after == {"title": nav["title"], "body_md": nav["body_md"]}
    cv = conn.execute("select flag_new_evidence, state from case_vehicle where case_id = %s", (nav["id"],)).fetchone()
    assert cv == {"flag_new_evidence": 1, "state": "confirmed"}


def test_merge_and_split_keep_ids(world):
    conn = world["conn"]
    _run_cases(conn)
    vent = [r["id"] for r in conn.execute("select id from case_item where feature_id = %s order by id",
                                          (world["features"][VENT],)).fetchall()]
    target = vent[0]
    some = [str(r["id"]) for r in conn.execute("select id from signal where case_id = %s limit 2",
                                               (target,)).fetchall()]
    new = cluster.split(conn, target, some, actor="pm:anna", rationale="different aspect")
    cluster.merge(conn, new["id"], target, actor="pm:anna", rationale="same problem after all")
    ops = [r["op"] for r in conn.execute("select op from case_lineage order by created_at").fetchall()]
    assert ops == ["split", "merge"]
    assert conn.execute("select count(*) n from signal where case_id = %s", (new["id"],)).fetchone()["n"] == 0
    assert conn.execute("select count(*) n from case_item where id = %s", (new["id"],)).fetchone()["n"] == 1
