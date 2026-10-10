# Handoff to the next agent

State on 2026-10-10, end of the session. Read `CLAUDE.md`, `plan.md` (progress table), `changes.md` (every decision with reason), `database-design.md` first. The user supervises; Devin reviews every phase commit.

## Done and pushed (each verified, see plan.md)

| Phase | Commit | Summary |
|---|---|---|
| 0 | 8d9d1e8 | Docs aligned, `database-design.md` |
| 1 | 6dfc8e2 | Backend foundation, migrations 0001-0007, audit write path |
| Review fixes 0/1 | 5cac41d, 565efef | Devin findings (migration 0009, atomic audit, write-once storage, ...) |
| 2 | 2463e1b | Seed from the provided data (review CSVs in `backend/seed/review/`, imported as candidates) |
| 3 | 4cf8b03 | Signal contract, validation gate, quarantine, embeddings |
| 4 | 7051b60 | Cases (feature link, clustering, figures, tags, lifecycle) |
| 5 | 1c23f9f | Scoring, boards, profile compare |
| 6 | 8f6b836 | Requirements |

## Work in progress (committed as WIP, NOT yet "Completed")

**Phase 7, RAG sidebar** (`backend/app/rag/`, migration `0010_rag_search.sql`, prompt `rag_agent@v1.md`, `tests/test_rag.py`):
- Code complete; tests pass (3 RAG tests, run together with `tests/test_requirements.py`).
- Dev-database check finished after the WIP commit: `index.rebuild` + `agent.ask` on CASE-0930 ("Why do customers complain about wireless phone charging?") answered extractively in 0.1 s with 8 cited signals out of 12 retrieved, all quotes from G60 / USA verbatims. Record the rebuild chunk counts (re-run `uv run python -m app.rag.index`), then commit `Completed phase 7: RAG sidebar agent` (update `plan.md` row 7 and add a `## Phase 7` section to `changes.md`. Decisions to log: 7.1 the RAG index is derived data, so a rebuild writes one audit event with counts instead of one per chunk; 7.2 hybrid search = full-text + pgvector with reciprocal rank fusion (k = 60); 7.3 without an OpenAI key the agent answers extractively from the search with citations; citations not returned by a search are removed, no valid citation = "No evidence found").

**Phase 8, fetch loop** (`backend/app/fetch/`, prompt `signal_extract@v1.md`, `tests/test_fetch.py`, fixtures in `tests/fixtures/http/`):
- Built: polite HTTP client (robots.txt, per-host delay, retries, budget, content-addressed raw refs), subjects from DB, record builders (fetching-sources 7.2/7.3), adapters: wikipedia, wikidata, nhtsa-vpic, fueleconomy, nhtsa-complaints, nhtsa-recalls, reddit (needs OAuth), youtube-comments (needs key), RSS feeds (step 3), app-store (step 4), XenForo forums tmc / genesis-owners / lucid-owners (step 5), magazines (step 7), kbb, download folder (steps 8, 9). Four agents (`app/fetch/agents.py`, LLM or heuristic). Worker `python -m app.fetch.worker [--backfill] [--steps 1-9] [--dry-run]`.
- 7 fetch tests pass on recorded responses.
- Not built yet (adapters raise NotConfigured, listed in the dry-run report): motor-talk, pistonheads, carcomplaints, autoscout24, ams, autobild, adac-autotest, automobile-magazine, teknikens-varld, google-play, google-trends, press pages without feeds. Add them as open issues or build them.
- **Checkpoint (do not skip):** before the first real backfill, run `uv run python -m app.fetch.worker --dry-run` and show the user `backend/reports/fetch_plan.md` (sources + expected request volume). It needs Reddit OAuth and a YouTube Data API key from the user.
- Wikipedia returned HTTP 403 to a request without contact info in the user agent; `FETCH_USER_AGENT` should contain the repo URL (the recording script used `BMW-PM-Research-Prototype/0.1 (+https://github.com/ghassenzaara/BMW-Product-Management-Challenge)`). Update the default in `app/config.py`.

## Remaining phases

- **Phase 9 API** (not started): FastAPI routers per plan.md phase 9 on top of the service functions that already exist: `app/cases/{lifecycle,pages,cluster}.py` (open / confirm / reject / merge / split / tags / page save), `app/scoring/{score,rank}.py` (boards, compare, manual rank), `app/requirements/{generator,service}.py`, `app/rag/agent.py` (`ask`, `decide_edit`), `app/signals/service.py` (quarantine delete / release). Supabase Auth: every action needs a user id (`pm:<id>` / `engineer:<id>` actors); access-log middleware (views and exports -> `audit.log_access`). `StaleVersion` -> HTTP 409.
- **Phase 10**: F70 / Europe end to end with only a config change; DB dump + seed script + cached raw records for offline demos; audit chain report for one requirement (source record -> signal -> case -> score -> decisions -> requirement).

## How to run

- `cd backend && uv sync`; tests: `uv run pytest` (embedded Postgres via pgserver, no network; the full suite takes about 2.5 min because one fixture seeds a whole database). To run in parallel with another process, start a separate server and pass `TEST_DATABASE_URL`.
- Dev database (local, `backend/.data/pg`): `uv run python -m app.migrate`, `uv run python -m app.seed.run`, `uv run python -m app.signals.embed`, `uv run python -m app.cases.pipeline --initial`, scoring via `app.scoring.score.score_config`.
- Writes always go through `app/audit.py` (`insert`, `insert_many`, `update`, `update_many`, `tombstone`); never write tables directly. Migrations are checksum-protected: never edit an applied file, add a new one.
- Shell note (Windows Git Bash tool): heredocs containing apostrophes break; write files with the Write tool and commit with `git commit -F <file>`. Teammates push to `main` too (a landing page PR was merged): `git pull --rebase` an unpushed commit before pushing; never force-push.

## Still waiting for the user

- `backend/.env`: Supabase URL, service-role key, `SUPABASE_DB_URL`, `OPENAI_API_KEY` (nothing has run against Supabase or OpenAI yet; all LLM steps use offline fallbacks and the local embedder).
- Team review of the phase 2 CSVs in `backend/seed/review/` (option lists, feature catalog, VFC topic -> car area).
- Reddit OAuth app and YouTube Data API key (phase 8 checkpoint).
- With the OpenAI key: re-run `app.signals.reclassify` (666 quarantined + heuristic seed rows), re-embed (`app.signals.embed --provider-change`), re-tune the case threshold (`app.cases.tune`, the OpenAI value 0.55 is untuned), run `app.signals.eval_car_area` for the LLM accuracy.
