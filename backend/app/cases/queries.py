"""Read models for the UI: case board with filters, overview map, case page."""

from __future__ import annotations

from app.scoring.rank import case_board

STATES = ("observed", "open", "confirmed", "rejected")
# What the PM workspace shows on a case card: the AI-written card texts and the PM's work for the line.
WORK_COLUMNS = ("short_title", "summary", "highlights", "case_type", "change_depth", "brand_fit_reason",
                "brand_fit_source", "brand_fit_confirmed_by", "body_version", "created_at", "created_by",
                "updated_at", "workflow", "outcome", "outcome_note", "owner_id", "co_owner_ids", "watcher_ids",
                "scope")


def board(conn, profile_id, *, states=("open", "confirmed"), car_area: str | None = None,
          release_fit: str | None = None, market: str | None = None, source_type: str | None = None,
          source_id: str | None = None, aspect_group: str | None = None, conflict_only: bool = False,
          q: str | None = None) -> list[dict]:
    """The ranked board of one weight profile (= vehicle + market), with the end filters
    market, car area, state, source type and release fit. Ranks are the board ranks
    before filtering, so a filtered view keeps each case's place."""
    rows = case_board(conn, profile_id, states=states)
    if not rows:
        return rows
    ids = [r["id"] for r in rows]
    meta = {m["id"]: m for m in conn.execute(
        "select c.id, c.aspect_group, c.scope_tag, f.topic_id, f.name as topic, f.car_area,"
        " c.figures->'by_vehicle' as by_vehicle, c.figures->'source_types' as source_types,"
        " c.figures->'sources' as sources, (c.figures->>'signals')::int as signals"
        " from case_item c join feature f on f.id = c.feature_id where c.id = any(%s)", (ids,)).fetchall()}
    others: dict[str, list] = {}
    for o in conn.execute("select case_id, vehicle from case_vehicle where case_id = any(%s) order by vehicle",
                          (ids,)).fetchall():
        others.setdefault(o["case_id"], []).append(o["vehicle"])
    vehicle = rows[0]["vehicle"]
    others = {k: [v for v in vs if v != vehicle] for k, vs in others.items()}
    requirements: dict[str, list] = {}
    for req in conn.execute("select id, case_id from requirement where case_id = any(%s) and vehicle = %s"
                            " and status <> 'deleted' order by id", (ids, vehicle)).fetchall():
        requirements.setdefault(req["case_id"], []).append(req["id"])
    out = []
    for r in rows:
        m = meta[r["id"]]
        vf = (m["by_vehicle"] or {}).get(r["vehicle"], {})
        markets = vf.get("markets", {})
        if car_area and m["car_area"] != car_area:
            continue
        if aspect_group and m["aspect_group"] != aspect_group:
            continue
        if release_fit and r["release_fit"] != release_fit:
            continue
        if market and not markets.get(market, {}).get("customers"):
            continue
        if source_type and not (m["source_types"] or {}).get(source_type):
            continue
        if source_id and not (m["sources"] or {}).get(source_id):
            continue
        if conflict_only and not r["market_conflict"]:
            continue
        if q and q.lower() not in (r["title"] or "").lower() and q.lower() not in m["topic"].lower():
            continue
        out.append({
            **{k: r[k] for k in ("rank", "id", "title", "vehicle", "state", "score", "rationale", "previous_score",
                                 "score_moved", "manual_rank", "manual_rank_reason", "release_fit", "market_conflict",
                                 "flag_new_evidence", "brand_fit", "segment", *WORK_COLUMNS)},
            "factors": {f: v.get("normalized") for f, v in (r["factors"] or {}).items()},
            "contributions": {f: v.get("contribution") for f, v in (r["factors"] or {}).items()},
            "other_vehicles": others.get(r["id"], []),
            "requirement_ids": requirements.get(r["id"], []),
            "markets_full": markets,
            "equipment": vf.get("equipment", {}),
            "competitor_offers": vf.get("competitor_offers", []),
            "trend_signals": vf.get("trend_signals", 0),
            "study": vf.get("study", []),
            "signals": m["signals"],
            "topic_id": m["topic_id"], "topic": m["topic"], "car_area": m["car_area"],
            "aspect_group": m["aspect_group"], "scope_tag": m["scope_tag"],
            "distinct_customers": vf.get("distinct_customers", 0),
            "markets": {k: {"customers": v.get("customers", 0), "negative_share": v.get("negative_share")}
                        for k, v in markets.items()},
            "competitor_signals": vf.get("competitor_evidence", {}).get("signals", 0),
            "source_types": m["source_types"] or {},
        })
    return out


def overview(conn, vehicle: str) -> list[dict]:
    """Overview map: every case of a line (observed included) counted per car area and state."""
    rows = conn.execute(
        "select f.car_area, cv.state, count(*) as cases,"
        " sum(coalesce((c.figures->'by_vehicle'->%s->>'distinct_customers')::int, 0)) as customers"
        " from case_vehicle cv join case_item c on c.id = cv.case_id join feature f on f.id = c.feature_id"
        " where cv.vehicle = %s group by 1, 2 order by 1, 2", (vehicle, vehicle)).fetchall()
    areas: dict[str, dict] = {}
    for r in rows:
        a = areas.setdefault(r["car_area"] or "unknown", {"car_area": r["car_area"], "states": {}, "customers": 0})
        a["states"][r["state"]] = r["cases"]
        a["customers"] += int(r["customers"] or 0)
    return sorted(areas.values(), key=lambda a: -a["customers"])


def page(conn, case_id: str) -> dict | None:
    case = conn.execute(
        "select c.*, f.topic_id, f.name as topic, f.car_area from case_item c join feature f on f.id = c.feature_id"
        " where c.id = %s", (case_id,)).fetchone()
    if case is None:
        return None
    case.pop("embedding", None)
    case["vehicles"] = conn.execute("select * from case_vehicle where case_id = %s order by vehicle",
                                    (case_id,)).fetchall()
    case["lineage"] = conn.execute(
        "select from_case, to_case, op, actor, rationale, created_at from case_lineage"
        " where from_case = %s or to_case = %s order by created_at", (case_id, case_id)).fetchall()
    case["scores"] = conn.execute(
        "select s.weight_profile_id, p.name as profile, c.id as config_id, c.vehicle, c.market, s.score,"
        " s.brand_multiplier, s.factors, s.rationale, s.computed_at from case_score_current s"
        " join weight_profile p on p.id = s.weight_profile_id join config c on c.id = p.config_id"
        " where s.case_id = %s order by c.vehicle, c.market, p.name", (case_id,)).fetchall()
    case["requirements"] = conn.execute(
        "select id, vehicle, market, title, status from requirement where case_id = %s order by id",
        (case_id,)).fetchall()
    case["decisions"] = conn.execute(
        "select vehicle, actor, action, rationale, created_at from decision where object_type = 'case'"
        " and object_id = %s order by created_at desc limit 50", (case_id,)).fetchall()
    # The PM's work on the case: context, questions, notes, hand-overs, flags, proposals.
    rids = [r["id"] for r in case["requirements"]]
    case["stances"] = conn.execute("select * from case_stance where case_id = %s order by updated_at",
                                   (case_id,)).fetchall()
    case["questions"] = conn.execute(
        "select * from open_question where (object_type = 'case' and object_id = %s)"
        " or (object_type = 'requirement' and object_id = any(%s)) order by created_at", (case_id, rids)).fetchall()
    case["meeting_notes"] = conn.execute("select * from meeting_note where case_id = %s order by created_at",
                                         (case_id,)).fetchall()
    case["referrals"] = conn.execute("select * from referral where case_id = %s order by created_at desc",
                                     (case_id,)).fetchall()
    case["alerts"] = conn.execute("select * from alert where case_id = %s or requirement_id = any(%s)"
                                  " order by created_at desc", (case_id, rids)).fetchall()
    case["proposals"] = conn.execute(
        "select * from proposal where (object_type = 'case' and object_id = %s)"
        " or (object_type = 'requirement' and object_id = any(%s)) order by created_at", (case_id, rids)).fetchall()
    case["innovation"] = conn.execute("select * from innovation_item where case_id = %s", (case_id,)).fetchone()
    return case


def signals(conn, *, case_id: str | None = None, vehicle: str | None = None, market: str | None = None,
            kind: str | None = None, evidence_role: str | None = None, source_id: str | None = None,
            content_type: str | None = None, car_area: str | None = None, topic_id: str | None = None,
            validation: str = "valid", unassigned: bool = False, q: str | None = None, limit: int = 50,
            offset: int = 0) -> dict:
    where, params = ["s.validation = %s"], [validation]
    if unassigned:
        where.append("s.case_id is null")
    if q:
        where.append("(s.summary ilike %s or s.quote ilike %s)")
        params += [f"%{q}%", f"%{q}%"]
    for col, val in (("s.case_id", case_id), ("s.vehicle", vehicle), ("s.market", market), ("s.kind", kind),
                     ("s.evidence_role", evidence_role), ("r.source_id", source_id),
                     ("r.content_type", content_type), ("f.topic_id", topic_id)):
        if val:
            where.append(f"{col} = %s")
            params.append(val)
    if car_area:
        where.append("%s = any(s.car_area)")
        params.append(car_area)
    sql_where = " and ".join(where)
    total = conn.execute(
        "select count(*) n from signal s join source_record r using (record_id) left join feature f"
        f" on f.id = s.feature_id where {sql_where}", params).fetchone()["n"]
    rows = conn.execute(
        "select s.id, s.record_id, s.case_id, s.kind, s.severity, s.summary, s.quote, s.quote_en, s.market,"
        " s.vehicle, s.chassis_code, s.vehicle_role, s.evidence_role, s.powertrain, s.reach, s.reach_unit,"
        " s.aspect_group, s.car_area, s.classified_by, s.scope_tag, s.published_at, f.topic_id, f.name as topic,"
        " s.market_basis, s.feature_hint, s.validation, s.created_at,"
        " r.source_id, r.content_type, r.category, r.url, r.title as record_title, r.fetched_at, r.country,"
        " r.language, sr.name as source_name, co.brand || ' ' || co.model as competitor,"
        " case when d.record_id is null then null else jsonb_build_object('name', d.metric, 'value', d.value,"
        " 'unit', d.unit, 'period', d.period) end as metric"
        " from signal s join source_record r using (record_id) left join feature f on f.id = s.feature_id"
        " left join source_registry sr on sr.source_id = r.source_id"
        " left join competitor co on co.id = s.competitor_id"
        " left join data_record d on d.record_id = s.data_record_id and d.value is not null"
        f" where {sql_where} order by s.published_at desc nulls last, s.id limit %s offset %s",
        [*params, limit, offset]).fetchall()
    return {"total": total, "items": rows}
