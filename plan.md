# Implementation Plan: BMW Requirements Platform

## Progress

| Phase | Title | Status | Commit | Verification result |
|---|---|---|---|---|
| 0 | Align docs, database design | todo | | |
| 1 | Foundation | todo | | |
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

- none yet


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
All tables in schema `public`, UUID or text keys, `created_at` default `now()`. The backend uses the service role; the UI talks to the FastAPI API (plus Realtime for notifications). Every write goes through `audit.py`.

**Reference and configuration**
| Table | Key columns | Notes |
|---|---|---|
| `vehicle` | `code` PK, series, segment, generation, `on_sale_since` date, `predecessor_code`, `markets_sold` text[] | From fetching-sources 2.1 + sales; records published before `on_sale_since` belong to the predecessor |
| `competitor` | id, `bmw_vehicle` FK, brand, model, generation, since, `names` jsonb (de/fr/zh), `wikipedia_page`, `markets` text[] | From fetching-sources 2.3; new launches (category J) add rows |
| `site_slug` | (`subject_type`, `subject_id`, `source_id`) PK, `slug` | Fetching-sources 3.10 |
| `search_term` | subject, `term`, `language` | Incl. collision-safe terms from 2.4 and names per language 2.2 |
| `source_registry` | `source_id` PK, name, `url_pattern`, `categories` text[], country, level, access, `check_status`, `fetch_step`, `enabled`, `last_cursor` jsonb | Parsed once from the sources file; `last_cursor` drives incremental fetching |
| `brand_profile` | id, version, `body_md` | Versioned assumption, PM-editable |
| `config` | id, `vehicle` FK, market, `competitor_ids` uuid[], `brand_profile_id`, `source_filter` jsonb, `thresholds` jsonb (open-case minimum, conflict minimum 5, similarity threshold) | Check: vehicle/market valid only if 2030 sales > 0 |
| `weight_profile` | id, `config_id` FK, name, `case_weights` jsonb, `brand_multipliers` jsonb, `cost_multipliers` jsonb, `created_by` | Defaults: 0.25/0.20/0.15/0.15/0.10/0.05/0.05/0.05; 1.0/0.8/0.5; S 1.0 / M 0.85 / L 0.7 |
| `app_user` | id = Supabase auth uid, name, role (pm, engineer, admin) | Several PMs |

**Baseline**
| Table | Key columns | Notes |
|---|---|---|
| `feature` | id, name, `car_area`, status (confirmed, candidate), `embedding` vector | Feature catalog |
| `feature_mapping` | `feature_id` FK, kind (vfc_topic, option_code, study_attribute), value | Normalized links instead of arrays |
| `vehicle_feature` | vehicle FK, `feature_id` FK, variant, market, availability (standard, optional, absent, removed), `sa_code`, `price_eur`, `record_id` | Option lists (market DE) |
| `study_score` | id, vehicle FK, market, attribute, method (mean_10pt, dist_7level), raw jsonb, normalized, `dissatisfied_share`, `feature_id` | Customer study |
| `data_record` | `record_id` PK, `source_id`, brand, model, `chassis_code`, powertrain, country, market, period, metric, value, unit, `source_location`, `raw_ref`, `fetched_at` | Fetching-sources 7.3; also holds BMW sales (`source_id` = `bmw_sales`), tech data from option lists and competitor specs. View `sales_volume` on top |

**Evidence**
| Table | Key columns | Notes |
|---|---|---|
| `agent_run` | id, agent, started/ended, counts (records, signals valid, quarantined), `llm_cost`, status, error | One per run |
| `source_record` | `record_id` PK + all fields of fetching-sources 7.2 (source_id, category, content_type, level, url, raw_ref, fetched_at, published_at, search_term, thread_id, author_hash, brand, model, chassis_code, model_year, vehicle_role, powertrain, country, market, language, title, text_original, text_en, rating_value, rating_scale, engagement jsonb), `content_hash` unique, `run_id` | Text records; BMW feedback verbatims too (`source_id` = `bmw_feedback`) |
| `signal` | id, `record_id` FK, `run_id`, schema/prompt version, `customer_ref`, market + basis, vehicle, `vehicle_role`, `evidence_role` (bmw_customer, competitor, market), powertrain, kind, severity, `car_area` text[], `feature_hint`, `feature_id`, `case_id`, summary, quote, `quote_en`, reach + unit, `data_record_id` (competitor fact), `extra` jsonb, `validation` (valid, quarantined, deleted), `reject_reason`, `embedding` vector | Signal = evidence; competitor signals have `evidence_role = competitor` |

**Cases and requirements**
| Table | Key columns | Notes |
|---|---|---|
| `case` | `id` text PK (`CASE-G60-0012`), `config_id`, `feature_id`, title, state (observed, open, confirmed, rejected), `body_md`, `body_version`, `figures` jsonb, `market_conflict`, scope / brand_fit / segment tags, `release_fit` (current_car, next_gen, ota) + reason + `release_fit_source` (ai, pm), `manual_rank` + reason, `embedding` vector, `flag_new_evidence` int | Case = group of signals |
| `case_lineage` | `from_case`, `to_case`, op (merge, split), actor | Stable IDs |
| `case_score` | id, `case_id`, `weight_profile_id`, factors jsonb, score, rationale, `computed_at` | One row per version |
| `requirement` | `id` text PK (`REQ-G60-USA-0012`), `case_id` FK, market (only when split), `body_md`, `body_version`, `metric_target`, `acceptance_criteria`, `release_target`, `evidence_level`, assumptions, `cost_size`, `cost_source`, status (draft, in_discussion, approved, rejected, deleted), `rank_score`, `manual_rank` + reason | |
| `page_version` | `object_type`, `object_id`, version, `body_md`, author, `created_at` | History of Notion-like pages; stale save rejected |

**Collaboration and audit**
| Table | Key columns | Notes |
|---|---|---|
| `decision` | id, `object_type`, `object_id`, actor, action, rationale | PM / engineer actions |
| `chat_message` | id, `object_type`, `object_id`, role, content, `cited_signals` uuid[], `proposed_diff` jsonb, `diff_status` | RAG sidebar |
| `notification` | id, type, `object_type`, `object_id`, message, `read_by` uuid[] | Realtime-enabled |
| `rag_chunk` | id, `object_type`, `object_id`, text, `embedding` vector, `tsv` tsvector | Hybrid search |
| `audit_event` | `seq` bigserial PK, at, actor, `actor_type` (ai, human, system), action, `object_type`, `object_id`, before jsonb, after jsonb, rationale | Append-only: UPDATE/DELETE revoked + trigger raises |

**Indexes and rules**: HNSW on every `embedding`; GIN on `rag_chunk.tsv`; btree on `signal(case_id)`, `signal(feature_id)`, `signal(market)`, `source_record(source_id, published_at)`, `case(config_id, state)`; unique `source_record.content_hash`. Deletes are tombstones: content columns nulled, row kept, audit event written. RLS on with deny-all for `anon`; `authenticated` may only read `notification`.

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
