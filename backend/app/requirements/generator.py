"""Requirement generation: the answer to a case the PM confirmed for one BMW line.

One requirement per confirmed case and line; one per market only when the
markets clearly differ (market conflict flag), or the regional variant the PM
asks for. Content: description, metrics and targets (today's value from the
seed, competitor values where known), acceptance criteria, release target (from
the case's release fit), assumptions and uncertainties, evidence level (display
only), cost (option price -> S/M/L, else an AI estimate). With an OpenAI key the
LLM writes it (prompt ``requirement_writer``); without one a template states
only what the data says and marks every target as an assumption.

The PM's context shapes the draft: the series brief (layer 1), the stance on
the case (layer 2) and the answers to the shape questions (layer 3). Every part
of the result carries its provenance: evidence (with signal ids), PM context
(with the layer) or assumption.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app import audit
from app.llm import prompts
from app.llm.client import LLMUnavailable, get_llm, llm_available
from app.scoring.rank import requirement_rank

ACTOR = "agent:requirements"
MARKET_CODE = {"USA": "USA", "China": "CN", "Europe": "EU", "Rest of World": "RoW"}


SCOPE_CODE = {"CN": "CN", "RoW": "XCN"}   # id part of a regional variant (XCN = every market except China)
LAYERS = {"brief": "series_brief", "stance": "case_stance", "shape": "requirement_shape"}
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


class Sentence(BaseModel):
    text: str
    basis: Literal["evidence", "pm_context", "assumption"]
    signal_ids: list[str] = Field(description="signals the sentence rests on (basis evidence), else empty")
    context_ref: str = Field(description="e.g. brief.costEnvelope, stance.ambition, shape.benchmark; else empty")


class Metric(BaseModel):
    metric: str = Field(description="what is measured, customer-facing")
    today: str = Field(description="today's value with its source, or 'unknown'")
    target: str
    unit: str = Field(description="unit of the target, or empty")
    basis: Literal["evidence", "assumption"]
    benchmark: str = Field(description="competitor value with source from the data, or empty")


class RequirementText(BaseModel):
    title: str
    short_title: str = Field(description="2 to 4 words for lists")
    description: list[Sentence] = Field(description="what BMW will build or change, one sentence per item")
    metrics: list[Metric]
    acceptance_criteria: list[str]
    assumptions: list[str] = Field(description="future-facing assumptions and uncertainties")
    cost_size: Literal["S", "M", "L"]
    cost_reason: str


def sentences(text: str) -> list[str]:
    return [t.strip() for t in SENTENCE.split(text or "") if t.strip()]


def cost_band(price_eur: float) -> str:
    """Option price as a rough cost proxy (CLAUDE.md: cost only as a rough factor)."""
    if price_eur < 500:
        return "S"
    if price_eur < 2000:
        return "M"
    return "L"


def requirement_id(vehicle: str, case_id: str, market: str | None, scope: str | None = None) -> str:
    number = case_id.split("-", 1)[1]
    part = MARKET_CODE[market] if market else SCOPE_CODE.get(scope or "")
    return f"REQ-{vehicle}-{part}-{number}" if part else f"REQ-{vehicle}-{number}"


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
        short_title=" ".join(f"Improve {feature}".replace(",", " ").split()[:4]),
        description=[
            Sentence(text=f"The successor of the {vehicle} addresses the problem '{case['title']}' raised by "
                          f"{customers} customers ({where}).", basis="evidence", signal_ids=[], context_ref=""),
            Sentence(text="Customer-facing outcome: customers no longer report this problem; the exact solution "
                          "is agreed with engineering.", basis="assumption", signal_ids=[], context_ref=""),
        ],
        metrics=[Metric(metric=metric, today=today, target=target, unit="", basis="assumption", benchmark="")],
        acceptance_criteria=[f"Customer clinic or study shows the {feature} problem of {case['id']} is solved",
                             f"Complaints about {feature} drop in post-launch feedback compared with today"],
        assumptions=["Generated from a template without an LLM: targets are assumptions until the PM sets them.",
                     "Markets other than those with signals are not covered by evidence."],
        cost_size="M",
        cost_reason="no option price known; middle band as a placeholder estimate",
    )


def _llm(case: dict, ctx: dict, vehicle: str, market: str | None, pm_context: dict):
    payload = {
        "case_id": case["id"], "title": case["title"], "feature": ctx["feature"]["name"],
        "car_area": ctx["feature"]["car_area"], "market": market or "all markets of the line",
        "figures": ctx["vehicle_figures"], "today_tech_data": ctx["tech"][:40],
    }
    result = get_llm().structured(
        prompts.load("requirement_writer"), RequirementText, guard=prompts.INJECTION_GUARD, vehicle=vehicle,
        case_page=prompts.wrap_source(case["body_md"][:5000]),
        data=prompts.wrap_source(json.dumps(payload, ensure_ascii=False, default=str)[:12000]),
        pm_context=json.dumps(pm_context, ensure_ascii=False, default=str)[:6000] if pm_context else "none given",
    )
    return result.data, result.model, result.prompt_version


def pm_context(conn, case_id: str, vehicle: str, user, answers: list[dict] | None) -> tuple[dict, str | None]:
    """The PM's context for a draft and the brief it comes from: resolved series
    brief, stance on the case, shape answers. Empty without a user."""
    from app.collab import briefs, casework

    if user is None:
        return {}, None
    out: dict = {}
    brief = briefs.resolve(conn, vehicle, user.id)
    if brief:
        out["brief"] = brief["values"]
    stance = casework.stance(conn, case_id, user.id)
    if stance:
        out["stance"] = {k: stance[k] for k in ("overrides", "override_reasons", "evidence_takes", "inside",
                                                 "ambition")}
    if answers:
        out["shape"] = {a["key"]: a["value"] for a in answers}
    return out, (brief["overlayId"] or brief["sharedId"]) if brief else None


def provenance(text: RequirementText, *, case_signals: set[str], cost_source: str, scope_source: str,
               release_source: str) -> list[dict]:
    """Where every part comes from, in the UI contract's shape (path, source, layer, ref, signalIds, label)."""
    def tag(path, source, label, layer=None, ref=None, ids=()):
        return {"path": path, "source": source, "layer": layer, "ref": ref, "signalIds": list(ids), "label": label}

    out = [tag("title", "assumption", "Written by the AI from the case")]
    for i, part in enumerate(text.description):
        ids = [s for s in part.signal_ids if s in case_signals]
        prefix = part.context_ref.split(".", 1)[0]
        if part.basis == "evidence" and ids:
            out.append(tag(f"description#{i}", "evidence", f"Evidence · {len(ids)} signals", ids=ids))
        elif part.basis == "pm_context" and prefix in LAYERS:
            out.append(tag(f"description#{i}", "pm_context", f"Your context · {part.context_ref}", LAYERS[prefix],
                           part.context_ref))
        elif part.basis == "evidence":
            out.append(tag(f"description#{i}", "evidence", "Evidence · case figures"))
        else:
            out.append(tag(f"description#{i}", "assumption", "Assumption"))
    for i, m in enumerate(text.metrics, start=1):
        out.append(tag(f"metrics.m{i}.target", "evidence" if m.basis == "evidence" else "assumption",
                       "Evidence · today's value or a competitor value" if m.basis == "evidence"
                       else "Assumption · target to be agreed"))
    out += [tag(f"acceptanceCriteria#{i}", "assumption", "Proposed by the AI")
            for i in range(len(text.acceptance_criteria))]
    out += [tag(f"assumptions#{i}", "assumption", "Assumption") for i in range(len(text.assumptions))]
    for path, source in (("marketScope", scope_source), ("releaseTarget", release_source)):
        if source == "shape":
            key = "scope" if path == "marketScope" else "release"
            out.append(tag(path, "pm_context", f"Your answer · {key}", "requirement_shape", f"shape.{key}"))
        elif source == "evidence":
            out.append(tag(path, "evidence", "Evidence · market conflict in the case"))
        elif source == "pm":
            out.append(tag(path, "pm_context", "Your release fit on the case", "case_stance", "case.releaseFit"))
        else:
            out.append(tag(path, "assumption", "AI proposal · release fit" if path == "releaseTarget"
                           else "Default · all markets of the line"))
    out.append(tag("cost", "evidence" if cost_source == "option_price" else "assumption",
                   "Evidence · option price (DE list)" if cost_source == "option_price" else "AI estimate"))
    return out


def generate(conn, case_id: str, vehicle: str, *, actor: str = ACTOR, request_id: str | None = None, user=None,
             answers: list[dict] | None = None, market_scope: str | None = None, release_target: str | None = None,
             variant_of: str | None = None) -> list[dict]:
    """Draft the requirement(s) of a confirmed case for one line. With ``market_scope``
    (the PM's answer to the scope question) exactly one requirement is drafted for
    that scope; otherwise one per market when the markets conflict."""
    cv = conn.execute("select * from case_vehicle where case_id = %s and vehicle = %s", (case_id, vehicle)).fetchone()
    if cv is None or cv["state"] != "confirmed":
        raise ValueError("requirements are generated only for confirmed cases")
    case = conn.execute("select * from case_item where id = %s", (case_id,)).fetchone()
    cfgs = conn.execute("select thresholds from config where vehicle = %s and active", (vehicle,)).fetchall()
    min_customers = min([int((c["thresholds"] or {}).get("conflict_min_customers", 5)) for c in cfgs] or [5])
    context, brief_id = pm_context(conn, case_id, vehicle, user, answers)
    case_signals = {str(r["id"]) for r in conn.execute(
        "select id from signal where case_id = %s and validation = 'valid'", (case_id,)).fetchall()}
    if market_scope:
        targets = [("China" if market_scope == "CN" else None, market_scope, "shape")]
    else:
        targets = [(m, "global" if m is None else ("CN" if m == "China" else "RoW"),
                    "evidence" if m else "default")
                   for m in split_markets(case["figures"] or {}, vehicle, cv["market_conflict"], min_customers)]
    owner = uuid.UUID(user.id) if user is not None else cv["owner_id"]
    created = []
    for market, scope, scope_source in targets:
        rid = requirement_id(vehicle, case_id, market, scope if market_scope else None)
        if conn.execute("select 1 from requirement where id = %s", (rid,)).fetchone():
            continue
        ctx = _context(conn, case, vehicle, market)
        model = prompt_version = None
        text = None
        if llm_available():
            try:
                text, model, prompt_version = _llm(case, ctx, vehicle, market, context)
            except LLMUnavailable:
                text = None
        if text is None:
            text, prompt_version = _template(case, ctx, vehicle, market), "requirement_template@v2"
        prices = ctx["vehicle_figures"].get("equipment", {}).get("option_prices_eur", [])
        if prices:
            cost_size, cost_source, price = cost_band(min(prices)), "option_price", min(prices)
            cost_note = f"option price {min(prices):.0f} EUR (DE option list) -> {cost_size}"
            band = f"{min(prices):.0f} EUR option price (DE)"
        else:
            cost_size, cost_source, price = text.cost_size, "ai_estimate", None
            cost_note = f"AI estimate {text.cost_size}: {text.cost_reason}"
            band = ""
        release = release_target or cv["release_fit"] or "next_gen"
        release_source = "shape" if release_target else ("pm" if cv["release_fit_source"] == "pm" else "ai")
        parts = [p.text.strip() for p in text.description if p.text.strip()]
        metrics = [{"id": f"m{i}", "question": m.metric, "today": m.today, "target": m.target,
                    "unit": m.unit or None, "basis": m.basis, "benchmark": m.benchmark or None}
                   for i, m in enumerate(text.metrics, start=1)]
        first = text.metrics[0] if text.metrics else None
        fields = {
            "title": text.title, "short_title": " ".join(text.short_title.split()[:5]),
            "description": " ".join(parts), "description_parts": parts, "metrics": metrics,
            "acceptance_criteria": text.acceptance_criteria, "assumptions": text.assumptions,
            "release_target": release, "evidence_level": evidence_level(conn, case_id), "market_scope": scope,
            "cost_size": cost_size, "cost_source": cost_source, "cost_band": band, "cost_note": cost_note,
            "provenance": provenance(text, case_signals=case_signals, cost_source=cost_source,
                                     scope_source=scope_source, release_source=release_source),
            "shape_answers": [{"key": a["key"], "value": a["value"], "byDefault": bool(a.get("by_default"))}
                              for a in answers or []],
        }
        body = render_body(fields, case)
        default = conn.execute(
            "select s.score, p.cost_multipliers from case_score_current s join weight_profile p"
            " on p.id = s.weight_profile_id join config c on c.id = p.config_id"
            " where s.case_id = %s and c.vehicle = %s and p.is_default" + (" and c.market = %s" if market else "")
            + " order by s.score desc limit 1",
            (case_id, vehicle, market) if market else (case_id, vehicle)).fetchone()
        row = audit.insert(conn, "requirement", {
            "id": rid, "case_id": case_id, "vehicle": vehicle, "market": market, **fields, "body_md": body,
            "metric_target": {"metric": first.metric, "today": first.today, "target": first.target,
                              "competitor_reference": first.benchmark or "none known"} if first else {},
            "cost_price_eur": price, "status": "draft", "variant_of": variant_of,
            "change_depth": case["change_depth"], "brief_id": brief_id, "owner_id": owner,
            "drafter": prompt_version + (f" ({model})" if model else ""),
            "rank_score": requirement_rank(default["score"], cost_size, default["cost_multipliers"]) if default else None,
            "model": model, "prompt_version": prompt_version,
        }, actor=ACTOR, request_id=request_id,
            rationale=f"generated from confirmed case {case_id} ({prompt_version})"
                      + (f", split for market {market} (market conflict)" if market and not market_scope else "")
                      + (f", {scope} variant asked by the PM" if market_scope and scope != "global" else "")
                      + (f", with the PM context of {user.actor}" if context else ""))
        audit.insert(conn, "page_version", {"object_type": "requirement", "object_id": rid, "version": 1,
                                            "body_md": body, "author": ACTOR},
                     actor=ACTOR, key=("object_type", "object_id", "version"))
        audit.insert(conn, "requirement_version", {"requirement_id": rid, "version": 1, "snapshot": snapshot(row),
                                                   "cause": "draft", "rationale": f"Drafted from {case_id}",
                                                   "created_by": ACTOR},
                     actor=ACTOR, key=("requirement_id", "version"))
        audit.insert(conn, "notification", {"type": "requirement_generated", "object_type": "requirement",
                                            "object_id": rid, "vehicle": vehicle, "market": market,
                                            "message": f"{rid} generated from {case_id}"}, actor=ACTOR)
        created.append(row)
    return created


SNAPSHOT_FIELDS = ("title", "short_title", "description", "description_parts", "market_scope", "release_target",
                   "metrics", "acceptance_criteria", "assumptions", "evidence_level", "cost_size", "cost_band",
                   "cost_source", "cost_note", "provenance", "shape_answers")


def snapshot(row: dict) -> dict:
    """The parts of a requirement that change between versions."""
    return {k: row.get(k) for k in SNAPSHOT_FIELDS}


def render_body(r: dict, case: dict) -> str:
    """The requirement page (markdown), always rendered from the structured fields."""
    lines = ["## Requirement", "", r.get("description") or "", "",
             f"Answers case **{case['id']}**: {case['title']}", "", "## Metrics and targets", ""]
    for m in r.get("metrics") or []:
        lines.append(f"- **{m.get('question')}**: target {m.get('target')}"
                     + (f" {m['unit']}" if m.get("unit") else "")
                     + (f"; today {m['today']}" if m.get("today") else "")
                     + (f"; benchmark {m['benchmark']}" if m.get("benchmark") else "")
                     + f" ({m.get('basis', 'assumption')})")
    lines += ["", "## Acceptance criteria", "", *[f"- {c}" for c in r.get("acceptance_criteria") or []], "",
              "## Assumptions and uncertainties", "", *[f"- {a}" for a in r.get("assumptions") or []], "",
              "## Scope, release and cost", "",
              f"- Scope: {r.get('market_scope') or 'global'}", f"- Release: {r.get('release_target')}",
              f"- Cost: {r.get('cost_size') or 'unknown'}"
              + (f" ({r['cost_note']})" if r.get("cost_note") else ""), ""]
    return "\n".join(lines)
