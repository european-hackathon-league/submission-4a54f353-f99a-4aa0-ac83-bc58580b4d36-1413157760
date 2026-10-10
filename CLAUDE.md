# BMW Product Management Challenge (TUM.ai Hackathon Final)

## Mission
Build an AI-powered platform that helps BMW product managers (PMs) derive, justify and prioritize **customer-facing requirements for the successor** of an existing vehicle line, 3 to 5 years ahead. It ingests BMW data plus web research, turns it into cases (problems) and requirements (solutions), prioritizes them with explainable logic, keeps the PM in control, and records everything in an audit trail.

The platform is generic: vehicle and market are configuration. The demo shows at least one derivative in one market.

BMW values: trustworthy sources, transparent reasoning, thoughtful prioritization, clear handling of uncertainty and conflicting evidence, evidence vs. assumption made explicit.

## Scope
**In:** features, equipment, comfort, space, usability, infotainment and digital services, design and quality perception, driving experience, range and charging as experienced by the customer, variant and package offering.
**Out:** regulatory / homologation, technical / engineering specs, pricing and business case (cost or effort only as a rough prioritization factor).

## Deliverables
1. Concept + demonstrable prototype.
2. Sample requirement list. Each requirement: unique ID, clear actionable description, linked signals and sources, priority / score, prioritization rationale, evidence level, future-facing assumptions and uncertainties, decision status.
3. End-to-end workflow: what the AI does autonomously, where human review is mandatory.
4. Audit trail: source, signal, consolidation, scoring, prioritization, human decision. Records changes to content, scores, priorities, evidence and status with actor (AI or human), timestamp and rationale.

## Repository map
- `README.md`: short project overview.
- `plan.md`: approved implementation plan (phases 0 to 10, database design). Progress table at the top.
- `architecture.mmd`: target architecture (Mermaid). **Source of truth for the pipeline.**
- `data-model.mmd`: data model (Mermaid ER) for Supabase Postgres + pgvector. **Source of truth for tables and fields.**
- `layers.mmd`: BMW's process slide as a diagram, for reference only (not our architecture).
- `All-context/README.md`: index of all material. Read first.
- `All-context/01-challenge/`: briefing, mission sheet, BMW process slides (md transcriptions + original PDF/PNG).
- `All-context/02-data-provided-by-pm/raw/`: provided data. Its `README.md` documents columns, counts and caveats.
- `All-context/03-meetings-with-pm/`: meeting summaries and notes, transcripts in `raw/`. **The two PM interviews are the primary user research.**
- `All-context/04-team-notes/`: early brainstorm (superseded by the architecture where they differ), original in `raw/`.
- `All-context/05-web-sources/fetching-sources.md`: web sources, fetch order and record format for the fetch agents (owned by a teammate).

## Provided data (short)
| Dataset | Vehicles | Key facts |
|---|---|---|
| Customer feedback (xlsx, 3 files) | F70 1 Series, G60 5 Series, G70 7 Series | Free-text English verbatims, classified by type (Likes, Difficult to Use, Defect, Wants) and topic. One row per topic, so count unique IDs. F70: 2,128 verbatims, UK/JP, ICE only. G60 and G70: 4,000 each, ~90% US, ICE/PHEV/BEV. Sources A to D differ in style (source C has only pain points, source D is unclassified). Some BMW topic labels are wrong. No dates. |
| Customer study 2025 (xlsx) | all, incl. G68 (China 5 Series) | `CN_EU_2025`: mean scores (10-point) per category. `US_2025`: G60/G70 only, ~100 attributes as 7-level rating distributions. Not directly comparable. No sample sizes. |
| Sales volumes (xlsx) | all | EU / CN / US / RoW for 2024, 2025, 2030 forecast. F70 not sold in CN or US. No powertrain split. RoW values for F70 and G60 identical (likely placeholder). |
| Option lists (PDF, German) | F70, G60, G70 | German market, July 2026. Encrypted: `pypdf` + `reader.decrypt('')`. Equipment x variant grid (■ standard, □ optional); plain text extraction loses the columns. Baseline of what the car offers today. |

Competitors and 3-to-5-year trends come only from web research, labelled as external or assumption.

Environment note: system Python 3.12 has `pypdf`; `pandas` is broken (numpy ABI mismatch) and `openpyxl` is missing. Use a project venv for implementation. The xlsx files use inline strings (no sharedStrings part).

## User insights (PM interviews, team meetings, lifecycle voice note)
- PMs today use benchmarking, customer studies, social listening, sentiment reports and car clinics. Tools: Excel, PowerPoint, Jira, Confluence, SharePoint. **Research is the bottleneck.**
- Funnel: see everything first, then narrow down. Handover is roughly the **top 10 requirements to engineering via codebeamer**, each with arguments: customer complaint, customer wish, competition, then rough cost / sellability.
- Prioritization signals: number of mentions (not enough alone), functional complaints weigh heavily, features removed vs. predecessor, cost, competitor offering, segment fit (base, premium, luxury).
- The PM sets priorities (e.g. speed vs. comfort vs. budget) and wants to see how the ranking changes and which items stay on top regardless.
- Reach is not relevance: one influencer is one opinion. Brand fit filters off-brand wishes. BMW identity: sporty, driving dynamics, design, efficiency; no single documented source of truth for brand taste.
- Markets are separate worlds (China, US, Middle East have their own PMs). Market conflicts must be shown with statistics, never decided by the AI.
- PMs want to skip sources (e.g. social media users who are not the target audience).
- Cars evolve, they are not reinvented. A new model fixes predecessor complaints and adds a few innovations; innovation comes mostly from competitors, other industries and suppliers.
- Lifecycle: a new generation can change everything; a mid-life update (~year 3) only small things (colours, rims, options). Too big for an update means next generation, not "no".
- No black box: every proposal shows source type, market and evidence. AI proposes, PM decides. Agents flag, never overwrite.
- Target vision: one shared platform for all PMs (same baseline and logic), several PMs can work on it. It is a PM tool; engineering feasibility is the next scope.
- Main competitors: German premium (above all Mercedes) and Chinese brands (e.g. Xiaomi).

## Core concepts: Signal, Case, Requirement
Chain: **Signal → Case (problem) → PM confirms → Requirement (solution) → PM approves → engineering.** Every step is logged with actor, timestamp, before/after and rationale.

**Signal** (every piece of evidence; formerly called "finding"): one statement about one feature from one source record (a customer verbatim, a review sentence, a competitor fact, a trend). Versioned JSON with exact quote (must be found in the stored source record), short English summary (≤ 20 words), market, car area, vehicle role, author hash and reach. Invalid signals go to a visible quarantine.

Competitor signals (e.g. complaints about the E-Class) attach to the BMW case of the same feature as competitor evidence; they feed the competitor gap and never count as BMW customers.

**Case = a problem, composed of a group of signals.** One specific topic about one feature, as customers and the market see it: what is wrong, missing or valued. Never what to build.
- Example: "Front seat ventilation too weak" (feature: seat ventilation, car area: Interior).
- Usually groups many signals, each linked to its quote and source.
- Holds all opinions (complaints, wishes, likes), counted per market and powertrain, so market conflicts become visible.
- Figures: distinct customers, reach (capped), study satisfaction, equipment today (standard / optional / absent), competitor offer, trend.
- Tags: scope, brand fit, segment (AI proposes, PM can override).
- **No cost.** A problem has no cost.
- Importance score from case factors weighted by the PM's weight profile; PM can override the rank by dragging, with a reason.
- Lifecycle: **observed** (auto-clustered, overview map only) → **open** (threshold reached: enough distinct customers, a defect, a competitor gap or a removed feature; or opened by the PM) → **confirmed** or **rejected** by the PM after meetings and discussion. Rejected cases stay with their reason.
- Notion-like page (markdown body + structured fields) with a RAG sidebar agent that discusses, cites sources and proposes edits as diffs the PM accepts or rejects.
- After confirmation the daily loop still attaches new signals, but the case never changes by itself; it gets a flag ("+40 complaints since confirmation").

**Requirement = the answer to a confirmed case.** What BMW will build or change, customer-facing and measurable, no engineering specs.
- Generated by the requirement agent only after the PM confirms a case.
- One per case. Several only when markets differ clearly: then one per market.
- Content: description; metric and target based on today's value (seed) and competitor values; acceptance criteria; release target (next generation, current car update, OTA software); assumptions and uncertainties; evidence level (BMW data / web / assumption, display only, not in the score); link to its case and through it to every signal and source.
- **Cost lives here**: option price if it exists, else an AI estimate S / M / L; PM and engineers edit it.
- Editable with engineering: PM and engineers edit, re-target or delete (delete = tombstone in the audit log). Same Notion-like page with RAG sidebar.
- Rank = case score x cost multiplier (S 1.0, M 0.85, L 0.7); PM can override by dragging.
- PM approves; the approved top 10 go to engineering.

## Pipeline (steps in `architecture.mmd`)
0. **Configuration:** vehicle + market (valid only if sales > 0), competitor set (default, AI suggests, PM confirms), brand profile (versioned assumption, PM-editable), source filter, several weight profiles per vehicle with a compare view.
1. **Seed (one-time, logged):** option lists (extracted once by the team and verified, page as source), portfolio (segment, generation, markets), sales (2030 forecast = market weight), customer study (raw + normalized 0..1 + US dissatisfied share), BMW feedback Excel converted to signals (car area via topic mapping), feature catalog linking topics, option codes and study attributes (mappings confirmed by the team; later new features go to the PM). Predecessor-generation research only on demand when the PM flags a removed feature.
2. **Daily fetch loop:** Python worker + cron, four agents (competitor, customer/public feedback, trends, innovation scout), daily budget with priority (features with open cases first, rest rotates). Sources, categories A to J and record format: `fetching-sources.md` (sections 3 and 7). Scope v1: fetch-order steps 1 to 9 (steps 10 to 13 later). Depth: one backfill over all pages, then daily incremental runs that stop at already-stored records. A general search provider is only needed for discovery and stays pluggable.
3. **Signal JSON + validation gate:** schema, stored record, quote found, duplicates, source filter, dated competitor facts. Rejected → quarantine (PM can delete: content purged, tombstone kept). Agents tag car area from a fixed list (no separate classifier). Older-generation signals kept and tagged. Original quote + English translation for non-English sources. Schema has fixed core fields + `extra`, versioned.
4. **Cases:** link signal to feature (vector search on feature hint), then to a case inside the feature (summary embeddings, OpenAI embeddings, threshold tuned on seed data), stable IDs, merges/splits logged. Figures and tags per case. Conflict flag when one market is mostly negative and another mostly positive, each with ≥ 5 distinct customers (PM-tunable).
5. **Case score** (no cost), defaults per weight profile: distinct customers 0.25, severity 0.20, satisfaction gap 0.15, competitor gap 0.15, market volume 0.10, removed vs predecessor 0.05, reach 0.05 (capped, log, per market), trend 0.05; brand fit multiplier (fits 1.0, neutral 0.8, off-brand 0.5). Rationale text generated from the factor breakdown.
6. **PM workspace:** notification toast (new open case, new evidence, conflict, failed run), case board, case page with RAG sidebar, PM confirms case.
7. **Requirement generation** from confirmed cases (see concept above).

End filters on the case and requirement boards: market, car area / category, state, source type, and **release fit** (current car update, next generation, OTA), judged by an LLM from how big the case is; the PM can override.
8. **Requirement ranking, discussion with engineering, approval.** Export format (codebeamer / Jira) is a later feature.

## Technical decisions
- Storage: Supabase Postgres + pgvector, relational with link tables (no graph DB). Raw records in storage, referenced by `raw_ref`.
- Audit: append-only event table enforced in the DB (no update / delete); actor (AI agent or PM), timestamp, before/after, rationale; views and exports logged too. Deletions are tombstones.
- Field names and market values follow `fetching-sources.md` section 7: markets `USA`, `China`, `Europe`, `Rest of World`, `unknown`; `vehicle_role` current / predecessor / competitor / unknown; `author_hash` for distinct web customers.
- LLM: OpenAI (ChatGPT) models for agents, case pages and RAG; OpenAI embeddings. Every agent output stores model and prompt version.
- RAG agent: hybrid keyword + vector search over case and requirement pages, signals with quotes, source records, decisions and audit events. Every factual claim cites a signal ID; no source means "no evidence found". Edits are proposals the PM accepts.
- Pages: markdown body stored in the DB with versions; structured fields (score, status, market, cost) separate; stale saves rejected by version check.
- UI (Notion-like pages, boards) is built by a teammate; the backend provides data, scores and the RAG agent.

## Rejected options (do not re-propose)
- A separate confidence axis in the score (evidence level is display only).
- A graph database.
- A separate car-area classifier step (Jev); agents tag car area.
- Treating the case as the solution; the case is the problem.
- An "exploration level" setting and a "project type" setting (replaced by: no case without signals; the release-fit filter).

## Open items
- Export format for codebeamer / Jira (later feature).
- Fetch steps 10 to 13 (native Chinese sites, browser-only, login) after v1.

## Risks
- **Market mismatch:** feedback mostly US (G60/G70) or UK/JP (F70), option lists German, study markets differ by sheet.
- **Skewed coverage:** F70 one source and ICE only; no study sample sizes; no feedback dates.
- **Hallucinated sources** would destroy traceability: every claim must point to a stored source record.
- **Clustering noise:** unclassified and mislabelled BMW rows; threshold must be tuned and measured.
- **Volume bias:** loud or off-brand signals ranked too high; distinct customers, capped reach and brand fit counter it.
- **Scope drift** into engineering specs when setting targets.
- **Arbitrary-looking scores** unless every factor is shown and tunable.
- **Prompt injection** from scraped pages: page content is data; agents can only write signals, never cases.
- **Demo fragility:** network, free-tier pausing, API quotas; keep a seed backup and cached records.
- **Hardcoding** a vehicle or market.

## Principles
- Every artifact traces to a real source. Never fabricate sources or citations.
- Audit log is append-only and part of the core from day 1.
- Every change records actor (AI agent or human), timestamp and rationale.
- Vehicle and market are configuration, never hardcoded.
- AI proposes and flags; the PM decides.
- Raw data in `All-context/02-data-provided-by-pm/raw/` is never modified; derived data goes elsewhere.

## Current phase
Implementation plan approved (`plan.md`). Next: Phase 0, then phases 1 to 10. Every phase ends with a commit "Completed phase N: <title>" pushed to `main`.
