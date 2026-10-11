"""RAG sidebar agent on case and requirement pages.

Tools: search_signals, get_case, get_requirement, score_breakdown,
compare_markets, what_if_weights, propose_edit. Every factual claim cites a
signal as [signal:<id>]. Citation check: a cited ID must exist and must have
been returned by a search in this conversation turn, otherwise the citation is
removed; an answer left without evidence becomes "No evidence found". Edits are
only proposals (diffs) that the PM accepts or rejects.

Without an OpenAI key the agent answers extractively from the hybrid search
(changes.md 7.3).
"""

from __future__ import annotations

import difflib
import json
import re
import uuid
from dataclasses import dataclass, field

from app import audit
from app.config import get_settings
from app.llm import prompts
from app.llm.client import llm_available
from app.llm.embeddings import get_embedder
from app.scoring import score as scoring

CITATION = re.compile(r"\[signal:([0-9a-f-]{36})\]")
NO_EVIDENCE = "No evidence found in the stored sources."
MAX_STEPS = 6


@dataclass
class Turn:
    object_type: str
    object_id: str
    retrieved: set[str] = field(default_factory=set)
    proposal: dict | None = None


def search(conn, query: str, *, k: int = 12, vehicle: str | None = None, case_id: str | None = None,
           types: list[str] | None = None) -> list[dict]:
    qv = get_embedder().embed([query])[0]
    rows = conn.execute("select * from rag_search(%s, %s, %s, %s, %s, %s)",
                        (query, qv, k, vehicle, case_id, types)).fetchall()
    return [{**r, "score": float(r["score"])} for r in rows]


# ---- tools -----------------------------------------------------------------------

def tool_search_signals(conn, turn: Turn, query: str, case_only: bool = True, vehicle: str | None = None) -> list:
    case_id = turn.object_id if turn.object_type == "case" and case_only else None
    if turn.object_type == "requirement" and case_only:
        case_id = conn.execute("select case_id from requirement where id = %s", (turn.object_id,)).fetchone()["case_id"]
    hits = search(conn, query, vehicle=vehicle, case_id=case_id, types=["signal"])
    if not hits and case_id:
        hits = search(conn, query, vehicle=vehicle, types=["signal"])
    turn.retrieved.update(h["object_id"] for h in hits)
    return [{"signal_id": h["object_id"], "vehicle": h["vehicle"], "market": h["market"], "case_id": h["case_id"],
             "text": h["text"][:600]} for h in hits]


def tool_get_case(conn, turn: Turn, case_id: str) -> dict:
    c = conn.execute("select id, title, body_md, body_version, brand_fit, segment, figures from case_item where id = %s",
                     (case_id,)).fetchone()
    if not c:
        return {"error": f"{case_id} not found"}
    states = conn.execute("select vehicle, state, release_fit, market_conflict from case_vehicle where case_id = %s",
                          (case_id,)).fetchall()
    return {**{k: c[k] for k in ("id", "title", "body_md", "body_version", "brand_fit", "segment")},
            "per_vehicle": states,
            "figures": {v: {"distinct_customers": f.get("distinct_customers"), "markets": f.get("markets")}
                        for v, f in (c["figures"] or {}).get("by_vehicle", {}).items()}}


def tool_get_requirement(conn, turn: Turn, requirement_id: str) -> dict:
    r = conn.execute("select id, case_id, vehicle, market, title, body_md, body_version, status, cost_size,"
                     " release_target, evidence_level from requirement where id = %s", (requirement_id,)).fetchone()
    return r or {"error": f"{requirement_id} not found"}


def tool_score_breakdown(conn, turn: Turn, case_id: str) -> list:
    return conn.execute(
        "select p.name as profile, c.vehicle, c.market, s.score, s.rationale from case_score_current s"
        " join weight_profile p on p.id = s.weight_profile_id join config c on c.id = p.config_id"
        " where s.case_id = %s order by s.score desc limit 12", (case_id,)).fetchall()


def tool_compare_markets(conn, turn: Turn, case_id: str, vehicle: str) -> dict:
    c = conn.execute("select figures from case_item where id = %s", (case_id,)).fetchone()
    return ((c or {}).get("figures") or {}).get("by_vehicle", {}).get(vehicle, {}).get("markets", {})


def tool_what_if_weights(conn, turn: Turn, case_id: str, weights: dict) -> list:
    case = conn.execute("select * from case_item where id = %s", (case_id,)).fetchone()
    out = []
    for cfg in conn.execute("select c.* from config c join case_vehicle cv on cv.vehicle = c.vehicle"
                            " where cv.case_id = %s", (case_id,)).fetchall():
        cases = conn.execute("select c.* from case_item c join case_vehicle cv on cv.case_id = c.id"
                             " where cv.vehicle = %s", (cfg["vehicle"],)).fetchall()
        refs = scoring.references(conn, cfg, cases)
        result = scoring.compute(case, {"name": "what-if", "case_weights": weights}, cfg, refs)
        out.append({"config": cfg["name"], "score": result["score"], "rationale": result["rationale"]})
    return out


def tool_propose_edit(conn, turn: Turn, new_body_md: str, reason: str) -> dict:
    table = "case_item" if turn.object_type == "case" else "requirement"
    current = conn.execute(f"select body_md, body_version from {table} where id = %s", (turn.object_id,)).fetchone()
    diff = "\n".join(difflib.unified_diff((current["body_md"] or "").splitlines(), new_body_md.splitlines(),
                                          "current", "proposed", lineterm=""))
    turn.proposal = {"base_version": current["body_version"], "body_md": new_body_md, "diff": diff, "reason": reason}
    return {"status": "proposed; the PM accepts or rejects it", "diff_lines": diff.count("\n") + 1}


TOOLS = {
    "search_signals": (tool_search_signals, {"query": "string", "case_only": "boolean", "vehicle": "string"},
                       "Hybrid search over signals (quotes from customers, reviews, competitor facts)."),
    "get_case": (tool_get_case, {"case_id": "string"}, "Case page, per-vehicle state and figures."),
    "get_requirement": (tool_get_requirement, {"requirement_id": "string"}, "Requirement page and fields."),
    "score_breakdown": (tool_score_breakdown, {"case_id": "string"}, "Current scores and rationales per profile."),
    "compare_markets": (tool_compare_markets, {"case_id": "string", "vehicle": "string"},
                        "Opinions and customers per market for one BMW line."),
    "what_if_weights": (tool_what_if_weights, {"case_id": "string", "weights": "object"},
                        "Score of the case under hypothetical factor weights."),
    "propose_edit": (tool_propose_edit, {"new_body_md": "string", "reason": "string"},
                     "Propose a new page body; the PM must accept it."),
}


def _tool_specs() -> list[dict]:
    specs = []
    for name, (_, params, desc) in TOOLS.items():
        props = {k: ({"type": t} if t != "object" else {"type": "object", "additionalProperties": {"type": "number"}})
                 for k, t in params.items()}
        specs.append({"type": "function", "function": {"name": name, "description": desc, "parameters": {
            "type": "object", "properties": props, "required": [k for k in params if k not in ("case_only", "vehicle")]}}})
    return specs


def check_citations(conn, answer: str, retrieved: set[str]) -> tuple[str, list[str]]:
    cited = list(dict.fromkeys(CITATION.findall(answer)))
    existing = {str(r["id"]) for r in conn.execute("select id from signal where id = any(%s::uuid[])"
                                                   " and validation = 'valid'", (cited,)).fetchall()} if cited else set()
    valid = [c for c in cited if c in retrieved and c in existing]
    cleaned = CITATION.sub(lambda m: m.group(0) if m.group(1) in valid else "", answer)
    if not valid:
        return NO_EVIDENCE, []
    return cleaned.strip(), valid


def _extractive(conn, turn: Turn, question: str) -> str:
    hits = tool_search_signals(conn, turn, question)
    if not hits:
        return NO_EVIDENCE
    lines = [f"Signals matching the question ({len(hits)} shown, extractive answer without an LLM):"]
    for h in hits[:8]:
        quote = h["text"].split("Quote: ", 1)[-1].split("\nSource:")[0][:200]
        lines.append(f"- {h['market'] or 'unknown market'}, {h['vehicle'] or 'brand'}: \"{quote}\" [signal:{h['signal_id']}]")
    return "\n".join(lines)


def _llm_answer(conn, turn: Turn, question: str, history: list[dict]) -> tuple[str, str]:
    from openai import OpenAI

    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key, max_retries=3, timeout=180)
    prompt = prompts.load("rag_agent")
    system, _ = prompt.render(object_type=turn.object_type, object_id=turn.object_id, guard=prompts.INJECTION_GUARD)
    messages = [{"role": "system", "content": system}] + history + [{"role": "user", "content": question}]
    for _ in range(MAX_STEPS):
        response = client.chat.completions.create(model=settings.openai_chat_model, messages=messages,
                                                  tools=_tool_specs())
        msg = response.choices[0].message
        if not msg.tool_calls:
            return msg.content or "", prompt.prompt_version
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name,
                                                           "arguments": tc.function.arguments}}
            for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            fn = TOOLS.get(tc.function.name, (None,))[0]
            try:
                args = json.loads(tc.function.arguments or "{}")
                result = fn(conn, turn, **args) if fn else {"error": "unknown tool"}
            except Exception as exc:  # tool errors go back to the model, not to the PM
                result = {"error": str(exc)[:300]}
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": prompts.wrap_source(json.dumps(result, default=str)[:12000])})
    return "I could not finish within the step limit. " + NO_EVIDENCE, prompt.prompt_version


def ask(conn, object_type: str, object_id: str, question: str, *, actor: str, request_id: str | None = None) -> dict:
    turn = Turn(object_type, object_id)
    history = [{"role": "user" if m["role"] == "pm" else "assistant", "content": m["content"]}
               for m in conn.execute("select role, content from chat_message where object_type = %s and object_id = %s"
                                     " order by created_at desc limit 8", (object_type, object_id)).fetchall()][::-1]
    audit.insert(conn, "chat_message", {"object_type": object_type, "object_id": object_id, "role": "pm",
                                        "author": actor, "content": question}, actor=actor, request_id=request_id)
    model = None
    if llm_available():
        raw, prompt_version = _llm_answer(conn, turn, question, history)
        model = get_settings().openai_chat_model
    else:
        raw, prompt_version = _extractive(conn, turn, question), "rag_extractive@v1"
    answer, cited = check_citations(conn, raw, turn.retrieved)
    row = audit.insert(conn, "chat_message", {
        "object_type": object_type, "object_id": object_id, "role": "agent", "author": "agent:rag",
        "content": answer, "cited_signals": [uuid.UUID(c) for c in cited],
        "retrieved_signals": [uuid.UUID(r) for r in sorted(turn.retrieved)],
        "proposed_diff": turn.proposal, "diff_status": "proposed" if turn.proposal else "none",
        "model": model, "prompt_version": prompt_version,
    }, actor="agent:rag", request_id=request_id, rationale="RAG answer (citations checked)")
    return row


def decide_edit(conn, message_id: str, accept: bool, *, actor: str, request_id: str | None = None) -> dict:
    """PM accepts (applies with the stale-save check) or rejects a proposed edit."""
    from app.cases.pages import save_case_page
    from app.requirements.service import save_page

    msg = audit.fetch_for_update(conn, "chat_message", {"id": message_id})
    if msg["diff_status"] != "proposed":
        raise ValueError("no open edit proposal on this message")
    if accept:
        p = msg["proposed_diff"]
        if msg["object_type"] == "case":
            save_case_page(conn, msg["object_id"], p["body_md"], p["base_version"], actor=actor, request_id=request_id)
        else:
            save_page(conn, msg["object_id"], p["body_md"], p["base_version"], actor=actor, request_id=request_id)
    return audit.update(conn, "chat_message", {"id": message_id},
                        {"diff_status": "accepted" if accept else "rejected"}, actor=actor,
                        rationale="PM decision on the proposed edit", request_id=request_id)
