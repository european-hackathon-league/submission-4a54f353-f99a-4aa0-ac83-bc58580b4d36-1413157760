"""Requirement generation: the answer to a case the PM confirmed for one BMW line.

One requirement per confirmed case and line; one per market only when the
markets clearly differ (market conflict flag). Content: description, metric and
target (today's value from the seed, competitor values where known), acceptance
criteria, release target (from the case's release fit), assumptions and
uncertainties, evidence level (display only), cost (option price -> S/M/L, else
an AI estimate). With an OpenAI key the LLM writes it (prompt
``requirement_writer``); without one a template states only what the data says
and marks every target as an assumption.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from app import audit
from app.llm import prompts
from app.llm.client import LLMUnavailable, get_llm, llm_available
from app.scoring.rank import requirement_rank

ACTOR = "agent:requirements"
MARKET_CODE = {"USA": "USA", "China": "CN", "Europe": "EU", "Rest of World": "RoW"}


class MetricTarget(BaseModel):
    metric: str
    today: str = Field(description="today's value with its source, or 'unknown'")
    target: str
    competitor_reference: str = Field(description="competitor value with source, or 'none known'")


class RequirementText(BaseModel):
    title: str
    description: str = Field(description="what BMW will build or change, customer-facing, no engineering specs")
    metric_target: MetricTarget
    acceptance_criteria: list[str]
    assumptions: list[str] = Field(description="future-facing assumptions and uncertainties")
    cost_size: Literal["S", "M", "L"]
    cost_reason: str


def cost_band(price_eur: float) -> str:
    """Option price as a rough cost proxy (CLAUDE.md: cost only as a rough factor)."""
    if price_eur < 500:
        return "S"
    if price_eur < 2000:
        return "M"
    return "L"


def requirement_id(vehicle: str, case_id: str, market: str | None) -> str:
    number = case_id.split("-", 1)[1]
    return f"REQ-{vehicle}-{MARKET_CODE[market]}-{number}" if market else f"REQ-{vehicle}-{number}"


def split_markets(figures: dict, vehicle: str, conflict: bool, min_customers: int) -> list[str | None]:
    if not conflict:
        return [None]
    markets = figures.get("by_vehicle", {}).get(vehicle, {}).get("markets", {})
    eligible = [m for m, f in markets.items() if m != "unknown" and f["customers"] >= min_customers]
    return sorted(eligible) or [None]


def evidence_level(conn, case_id: str) -> str:
    row = conn.execute(
        "select count(*) filter (where r.source_id like 'bmw_%%' or r.source_id = 'option_list') as bmw,"
        " count(*) as total from signal s join source_record r using (record_id)"
        " where s.case_id = %s and s.validation = 'valid'", (case_id,)).fetchone()
    if row["bmw"]:
        return "bmw_data"
    return "web" if row["total"] else "assumption"


def _context(conn, case: dict, vehicle: str, market: str | None) -> dict:
    vf = case["figures"].get("by_vehicle", {}).get(vehicle, {})
    feature = conn.execute("select name, car_area from feature where id = %s", (case["feature_id"],)).fetchone()
    tech = conn.execute(
        "select record_id, model, metric, value_text, unit from data_record where source_id = 'option_list'"
        " and chassis_code = %s order by metric, model", (vehicle,)).fetchall()
    return {"feature": feature, "vehicle_figures": vf, "market": market, "tech": tech}


def _template(case: dict, ctx: dict, vehicle: str, market: str | None) -> RequirementText:
    vf = ctx["vehicle_figures"]
    markets = vf.get("markets", {})
    scope = {market: markets[market]} if market and market in markets else markets
    complaints = sum(m["complaints"] for m in scope.values())
    wishes = sum(m["wishes"] for m in scope.values())
    customers = sum(m["customers"] for m in scope.values())
    study = [s for s in vf.get("study", []) if not market or s["market"] == market]
    feature = ctx["feature"]["name"]
    if study:
        worst = min(study, key=lambda s: float(s["normalized"]))
        today = (f"{worst['attribute']}: {float(worst['normalized']):.2f} normalized satisfaction "
                 f"({worst['sheet']}, {worst['market']})")
        metric = f"Customer satisfaction with '{worst['attribute']}'"
        target = "at least the best value of the line in the same study (assumption, to be agreed)"
    else:
        today = f"{complaints} complaints and {wishes} wishes from {customers} customers (BMW feedback)"
        metric = f"Share of customer feedback on {feature} that is a complaint"
        target = "halve the complaint share in the successor's first customer feedback wave (assumption)"
    where = market or ", ".join(sorted(m for m in markets if m != "unknown")) or "all markets"
    return RequirementText(
        title=f"Successor {vehicle}: improve {feature}"[:120],
        description=(f"The successor of the {vehicle} addresses the problem '{case['title']}' raised by "
                     f"{customers} customers ({where}). Customer-facing outcome: customers no longer report this "
                     f"problem; the exact solution is agreed with engineering."),
        metric_target=MetricTarget(metric=metric, today=today, target=target, competitor_reference="none known"),
        acceptance_criteria=[f"Customer clinic or study shows the {feature} problem of {case['id']} is solved",
                             f"Complaints about {feature} drop in post-launch feedback compared with today"],
        assumptions=["Generated from a template without an LLM: targets are assumptions until the PM sets them.",
                     "Markets other than those with signals are not covered by evidence."],
        cost_size="M",
        cost_reason="no option price known; middle band as a placeholder estimate",
    )


def _llm(case: dict, ctx: dict, vehicle: str, market: str | None):
    payload = {
        "case_id": case["id"], "title": case["title"], "feature": ctx["feature"]["name"],
        "car_area": ctx["feature"]["car_area"], "market": market or "all markets of the line",
        "figures": ctx["vehicle_figures"], "today_tech_data": ctx["tech"][:40],
    }
    result = get_llm().structured(
        prompts.load("requirement_writer"), RequirementText, guard=prompts.INJECTION_GUARD, vehicle=vehicle,
        case_page=prompts.wrap_source(case["body_md"][:5000]),
        data=prompts.wrap_source(json.dumps(payload, ensure_ascii=False, default=str)[:12000]),
    )
    return result.data, result.model, result.prompt_version


def generate(conn, case_id: str, vehicle: str, *, actor: str = ACTOR, request_id: str | None = None) -> list[dict]:
    cv = conn.execute("select * from case_vehicle where case_id = %s and vehicle = %s", (case_id, vehicle)).fetchone()
    if cv is None or cv["state"] != "confirmed":
        raise ValueError("requirements are generated only for confirmed cases")
    case = conn.execute("select * from case_item where id = %s", (case_id,)).fetchone()
    cfgs = conn.execute("select thresholds from config where vehicle = %s and active", (vehicle,)).fetchall()
    min_customers = min([int((c["thresholds"] or {}).get("conflict_min_customers", 5)) for c in cfgs] or [5])
    created = []
    for market in split_markets(case["figures"] or {}, vehicle, cv["market_conflict"], min_customers):
        rid = requirement_id(vehicle, case_id, market)
        if conn.execute("select 1 from requirement where id = %s", (rid,)).fetchone():
            continue
        ctx = _context(conn, case, vehicle, market)
        model = prompt_version = None
        text = None
        if llm_available():
            try:
                text, model, prompt_version = _llm(case, ctx, vehicle, market)
            except LLMUnavailable:
                text = None
        if text is None:
            text, prompt_version = _template(case, ctx, vehicle, market), "requirement_template@v1"
        prices = ctx["vehicle_figures"].get("equipment", {}).get("option_prices_eur", [])
        if prices:
            cost_size, cost_source, price = cost_band(min(prices)), "option_price", min(prices)
            cost_note = f"option price {min(prices):.0f} EUR (DE option list) -> {cost_size}"
        else:
            cost_size, cost_source, price = text.cost_size, "ai_estimate", None
            cost_note = f"AI estimate {text.cost_size}: {text.cost_reason}"
        body = _body(text, case, cost_note)
        default = conn.execute(
            "select s.score, p.cost_multipliers from case_score_current s join weight_profile p"
            " on p.id = s.weight_profile_id join config c on c.id = p.config_id"
            " where s.case_id = %s and c.vehicle = %s and p.is_default" + (" and c.market = %s" if market else "")
            + " order by s.score desc limit 1",
            (case_id, vehicle, market) if market else (case_id, vehicle)).fetchone()
        row = audit.insert(conn, "requirement", {
            "id": rid, "case_id": case_id, "vehicle": vehicle, "market": market, "title": text.title,
            "body_md": body, "metric_target": text.metric_target.model_dump(),
            "acceptance_criteria": text.acceptance_criteria, "release_target": cv["release_fit"] or "next_gen",
            "evidence_level": evidence_level(conn, case_id),
            "assumptions": text.assumptions + [cost_note], "cost_size": cost_size, "cost_source": cost_source,
            "cost_price_eur": price, "status": "draft",
            "rank_score": requirement_rank(default["score"], cost_size, default["cost_multipliers"]) if default else None,
            "model": model, "prompt_version": prompt_version,
        }, actor=ACTOR, request_id=request_id,
            rationale=f"generated from confirmed case {case_id} ({prompt_version})"
                      + (f", split for market {market} (market conflict)" if market else ""))
        audit.insert(conn, "page_version", {"object_type": "requirement", "object_id": rid, "version": 1,
                                            "body_md": body, "author": ACTOR},
                     actor=ACTOR, key=("object_type", "object_id", "version"))
        audit.insert(conn, "notification", {"type": "requirement_generated", "object_type": "requirement",
                                            "object_id": rid, "vehicle": vehicle, "market": market,
                                            "message": f"{rid} generated from {case_id}"}, actor=ACTOR)
        created.append(row)
    return created


def _body(text: RequirementText, case: dict, cost_note: str) -> str:
    mt = text.metric_target
    lines = [
        f"## Requirement", "", text.description, "",
        f"Answers case **{case['id']}**: {case['title']}", "",
        "## Metric and target", "",
        f"- Metric: {mt.metric}", f"- Today: {mt.today}", f"- Target: {mt.target}",
        f"- Competitor reference: {mt.competitor_reference}", "",
        "## Acceptance criteria", "", *[f"- {c}" for c in text.acceptance_criteria], "",
        "## Assumptions and uncertainties", "", *[f"- {a}" for a in text.assumptions], "",
        "## Cost", "", f"- {cost_note}", "",
    ]
    return "\n".join(lines)
