# Implementation Plan: BMW Requirements Platform

## Progress

| Phase | Title | Status | Commit | Verification result |
|---|---|---|---|---|
| 0 | Align docs, database design | done | 8d9d1e8 | Both Mermaid files render with mmdc 11.4.2; no "finding" left in design docs (BMW challenge transcriptions and meeting notes keep their original wording); database-design.md covers all tables |
| 1 | Foundation | done | see next phase | 14 tests passed (all tables + RLS, HNSW indexes, audit_event refuses UPDATE/DELETE/TRUNCATE, service_role has no UPDATE/DELETE grant, audited diff, stale save, tombstone, config sales check, strict schema, prompts, local embedder); 7 migrations apply on local Postgres 16 + pgvector; not yet applied to Supabase (no credentials) |
| 2 | Seed | todo | | |
| 3 | Signal contract and validation gate | todo | | |
| 4 | Cases | todo | | |
| 5 | Scoring | todo | | |
| 6 | Requirements | todo | | |
| 7 | RAG sidebar agent | todo | | |
| 8 | Daily fetch loop (steps 1 to 9) | todo | | |
| 9 | API for the UI teammate | todo | | |
| 10 | Hardening and demo readiness | todo | | |

Every phase ends with a commit `Completed phase N: <title>` pushed to `main`, and its row updated here.

## Open issues

- Supabase credentials and OpenAI key not provided yet: migrations are tested on a local embedded Postgres 16 + pgvector 0.6 only; LLM steps run with the offline fallbacks until the key is in `backend/.env`.


## Context
The design phase is finished: concepts, pipeline and data model are agreed and live in `CLAUDE.md`, `architecture.mmd` and `data-model.mmd`. The repo holds no code yet. Claude implements the whole backend ("the brain"); the user supervises. A teammate builds the UI (Notion-like pages, boards). The web sources are specified in `All-context/05-web-sources/fetching-sources.md` (source registry with URL patterns, categories A to J, fetch order steps 1 to 13, competitor list, slugs per site, record format). No timeline: phases are ordered by dependency and each ends in a runnable, verifiable state.

Decisions added in this round:
- **Signal** = every piece of evidence (previously "finding"). **Case** = a problem, composed of a group of signals. **Requirement** = the answer to a confirmed case.
- End filters: car area / category and **release fit** (current car update, next generation, OTA), judged by an LLM from how big the case is; the PM can override.
- **Competitor signals** (e.g. E-Class complaints, Tesla forum) attach to the BMW case of the same feature as competitor evidence; they feed the competitor gap and never count as BMW customers.
- **Fetch scope v1: steps 1 to 9** of the fetch order (reference APIs, structured APIs, news/press feeds, app reviews, forums, owner review sites, expert reviews, price lists, market/trend data). Steps 10 to 13 (native Chinese sites, browser-only, login) come later.
- **Depth:** one backfill run fetches all pages; daily runs are incremental (newest first, stop at already-stored records).
- The sources file gives URL patterns and APIs directly, so a general web-search provider is only needed for discovery (e.g. new launches); it stays pluggable.

Stack: Python 3.12, FastAPI, Supabase (Postgres + pgvector, Storage, Realtime, Auth), OpenAI (chat + embeddings), `uv` venv, httpx + selectolax for scraping, feedparser for RSS, pdfplumber for PDFs, openpyxl/pandas inside the venv.

## Database design (built in phase 1, documented in phase 0)
Full design: [`database-design.md`](database-design.md) (diagram: `data-model.mmd`). Table groups: reference and configuration (`vehicle`, `competitor`, `source_registry`, `site_slug`, `search_term`, `car_area`, `brand_profile`, `config`, `weight_profile`, `app_user`), baseline (`feature`, `feature_mapping`, `vehicle_feature`, `study_score`, `data_record` + view `sales_volume`), evidence (`agent_run`, `source_record`, `signal`), cases and requirements (`case_item`, `case_vehicle`, `case_lineage`, `case_score`, `requirement`, `page_version`), collaboration and audit (`decision`, `chat_message`, `notification`, `rag_chunk`, `audit_event`).

Changes agreed with the user in phase 0 against the first draft of this plan:
- The case table is `case_item` (`CASE` is reserved in SQL); API and UI still say "case".
- A case is not tied to one vehicle or config: one problem can apply to several BMW lines. Vehicles are tags in `case_vehicle`, which holds the per-vehicle state, PM rank, release fit, conflict flag and new-evidence flag. Case IDs are `CASE-0012`; requirements are per line (`REQ-G60-0012`, `REQ-G60-USA-0012` when split). Scores stay per weight profile, so per config.
- `car_area` is a table (fixed list, confirmed in phase 2) instead of a hard-coded list.
- Option-list pages are stored as `source_record` rows (`source_id = option_list`), so equipment facts are quotable like every other source.
- `site_slug` key includes the slug (some sites have several pages per car).

## Phase 0 — Align docs and write the database design
- `CLAUDE.md` already uses Signal and holds this round's decisions. Rename Finding → Signal in `architecture.mmd`, `data-model.mmd`, `README.md`; add "a case is composed of a group of signals", the two end filters, competitor signals, fetch scope steps 1 to 9, backfill + incremental.
- Update `data-model.mmd` to the database design above (SOURCE → `source_record`, sales folded into `data_record`, new `competitor`, `site_slug`, `search_term`, `source_registry`, `feature_mapping`, `case_lineage`, `page_version`, `rag_chunk`, `notification`, `app_user`, `brand_profile`).
- Write `database-design.md` (the tables above, with rules) to the repo root. `plan.md` (this file) already exists; keep its progress table current.
- Commit `Completed phase 0: docs aligned and database design` and push.

## Phase 1 — Foundation
- `backend/` skeleton: `uv` project, FastAPI app, `config.py` (pydantic-settings), `db.py` (psycopg pool + Supabase storage client), `.env.example` (keys never committed).
- `backend/supabase/migrations/`: one migration per table group above, extensions (`vector`), indexes, append-only trigger on `audit_event`, Realtime on `notification`, storage bucket `raw-records`.
- `audit.py`: `write(object_type, object_id, before, after, actor, rationale)` in the same transaction as the change; actors `seed:<script>`, `agent:<name>`, `pm:<user>`, `system`.
- `llm/`: OpenAI wrapper with structured outputs (JSON schema), retries, cost logging; `prompts/` as versioned files.
- Test: update/delete on `audit_event` fails.

## Phase 2 — Seed
- Pre-seeding: everything in `All-context/02-data-provided-by-pm/raw/` (3 feedback files, customer study, sales volumes, 3 option-list PDFs) is imported into the tables created in phase 1, read-only from `raw/`, each file also stored in the `raw-records` bucket.
- **Registry seed** (parse `fetching-sources.md` once into YAML, reviewed, then load): `source_registry`, `competitor`, `site_slug`, `search_term`, vehicle generations / `on_sale_since`.
- **Portfolio** → `vehicle`. **Sales** → `data_record` (identical RoW values flagged `suspect`).
- **Study** → `study_score` (CN_EU means normalized; US distributions → normalized mean + dissatisfied share).
- **BMW feedback** → `source_record` per verbatim ID + one `signal` per topic row (kind/severity from Feedback Type, market via fetching-sources 7.4, `BEVE` → `BEV`, source C prefix stripped, `evidence_role = bmw_customer`). Unclassified rows: LLM fills kind/severity.
- **VFC topic → car area mapping** (team-confirmed CSV): car area for seed signals and test set for agent tagging.
- **Option lists** (once, together with the user): pdfplumber word positions + LLM vision on hard pages + checks (cells = variants, prices, unique codes) → review CSV → `vehicle_feature`; tech data → `data_record`.
- **Feature catalog**: LLM proposes from VFC topics + option items + study attributes → review CSV → team confirms → `feature` + `feature_mapping`.
- Checks: 2,128 / 4,000 / 4,000 verbatims; per-type counts match `02-data-provided-by-pm/README.md`; every row audited.

## Phase 3 — Signal contract and validation gate
- Pydantic `Signal` (fixed core + `extra`, versioned).
- Gate: schema → record exists + hash → quote found in `text_original` → duplicate / near-duplicate → source filter (hidden) → competitor fact dated → vehicle role from `on_sale_since` (predecessor tagged) → scope tag.
- Quarantine view with counts per agent; PM delete = tombstone.
- Summary embeddings (OpenAI). Eval: agent car-area tagging accuracy vs the VFC mapping.

## Phase 4 — Cases
- Feature link (feature_hint embedding → catalog; no match → candidate feature for the PM).
- Initial clustering of seed signals per feature (HDBSCAN); LLM writes case title + problem body.
- Incremental assignment of new signals; stable IDs; merge / split via `case_lineage`.
- Threshold tuning script with a report on the seed data.
- Figures: distinct BMW customers per market + powertrain, opinions, capped reach, study score, equipment today, competitor offer (`data_record`) and competitor evidence (`evidence_role = competitor` signals), trend.
- Tags (scope, brand fit, segment); conflict flag (≥ 5 distinct customers per side).
- **Release fit**: LLM judges current car update / next generation / OTA from case size, with reason; PM override.
- Lifecycle observed → open (threshold or PM) → confirmed / rejected; notifications.

## Phase 5 — Scoring
- Eight factor normalizers + brand multiplier; `case_score` versions with breakdown and rationale.
- Manual rank override with reason; rescoring flags instead of moving overridden cases.
- Profile compare: cases in the top N under every profile.
- Requirement rank = case score × cost multiplier.

## Phase 6 — Requirements
- On case confirm: generator (description, metric + target from seed value and competitor `data_record`, acceptance criteria, assumptions, evidence level, release target from release fit).
- One per case; one per market when markets clearly differ.
- Cost: option price → S/M/L bands, else LLM estimate (`ai_estimate`); edits by PM / engineers audited.
- Status flow, tombstone delete, versioned body with stale-save rejection.

## Phase 7 — RAG sidebar agent
- `rag_chunk` index over case/requirement bodies, signals (summary + quote), source texts, decisions, audit events.
- Hybrid search SQL function (full-text + pgvector, rank fusion) with vehicle / market / case filters.
- Tool-calling agent: `search_signals`, `get_case`, `get_requirement`, `score_breakdown`, `compare_markets`, `what_if_weights`, `propose_edit`.
- Citation check (cited signal IDs must exist and be retrieved, else "no evidence found"); diffs applied only on PM accept.

## Phase 8 — Daily fetch loop (steps 1 to 9)
- `python -m app.fetch.worker [--backfill] [--steps 1-9]`, cron daily; `agent_run` per run.
- Adapters by access type, driven by `source_registry`:
  - **API**: wikipedia, wikidata, nhtsa-vpic, fueleconomy, nhtsa-complaints / recalls, reddit (OAuth), youtube-comments (Data API v3; transcripts skipped).
  - **RSS**: news and press feeds (step 3), app-store reviews (step 4).
  - **Scraping**: httpx + selectolax with pagination rules from 3.9 and slugs from 3.10 (steps 5 to 7, step 3 press pages, auto-data, ev-database).
  - **Library**: google-play reviews, google-trends.
  - **Download**: price lists, KBA, IEA and other reports (step 8 and 9) → `data_record` / `source_record`.
- Polite fetching: robots.txt respected, per-site delay, user agent, retries; raw response to storage first (`raw_ref`).
- Backfill once over all pages; daily incremental via `last_cursor` (stop at known records).
- Four agents extract signals from records (competitor: J, E · feedback: A, B, C, D, H · trends: F, G, I · innovation scout: I + J on other industries / shows); vehicle and search terms from `competitor`, `search_term`, collision rules 2.4; market mapping from section 4 and 7.4; non-English text translated.
- Signals → gate → feature / case linking → figures → rescoring → notifications; new evidence on confirmed cases only flags.
- Prompt-injection guard: page text is data; agents create signals only.
- On-demand predecessor research endpoint.

## Phase 9 — API for the UI teammate
- FastAPI routers with OpenAPI spec as the contract: config / profiles (+ compare), signals + quarantine, cases (board with filters market, car area, release fit, state, source type; page get / save; open / confirm / reject / reorder / merge / split), requirements (board, page, approve / reject / delete / reorder), chat + proposed edits, audit trail per object, notifications.
- Supabase Auth: every action carries a user; access logging middleware (views, exports → audit).

## Phase 10 — Hardening and demo readiness
- Second configuration (F70 / Europe) runs end-to-end with only a config change.
- DB dump + seed script + cached raw records for offline demos / paused Supabase.
- Audit chain report for one requirement: source record → signal → case → score → PM decisions → requirement → approval.

## Verification
- `pytest` units: normalizers, gate checks, quote matching, cost bands, rank math, append-only audit, stale-save rejection.
- Seed count checks against the data README; registry load checked against the sources file (number of source IDs, competitors).
- Eval scripts with numbers: car-area tagging accuracy, clustering threshold report.
- Adapter smoke tests per access type on one BMW model + one competitor (recorded responses for CI).
- End-to-end on G60 / USA: seed → cases → scores → confirm a case via API → requirement → RAG answer with citations → audit chain printed; repeat on F70 / Europe with only a config change.

## Prerequisites from the user
- OpenAI API key and Supabase project (URL, service key) in `backend/.env`; Reddit OAuth app and YouTube Data API key for step 2.
- One joint session for the option-list review and the feature-catalog / car-area mapping confirmation.
