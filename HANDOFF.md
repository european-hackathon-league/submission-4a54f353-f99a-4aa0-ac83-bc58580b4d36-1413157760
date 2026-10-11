# Handoff to the next agent

State on 2026-10-11. Read `CLAUDE.md`, `plan.md` (progress table, open issues), `changes.md` (every decision with reason), `database-design.md`, `backend/API.md` first. The user supervises; Devin reviews every phase commit.

## Done and pushed

Phases 0 to 10 of `plan.md`, each with a "Completed phase N" commit (7, 8, 9, 10 on 2026-10-11). Highlights of the last session:

- **Phase 9 API** (`backend/app/api/`, `backend/API.md`): routers for config / profiles / compare / source filter, signals / quarantine / taxonomy review, cases (board, overview, page, lifecycle, rank, tags, merge, split, predecessor research), requirements (board, page, status, cost, re-target, rank, delete, export), chat, audit trail and audit chain, notifications, runs. Supabase token or `X-User-Id` (dev), one transaction per request, request id on audit events, 409 on stale saves.
- **Phase 8** finished: adapters for every source of steps 1 to 9 (`app/fetch/adapters/sites.py`, `libraries.py`), extra slugs (`backend/seed/slugs_extra.yaml`), on-demand predecessor research (`app/fetch/predecessor.py`).
- **Phase 10** (`app/demo.py`, `app/reports/audit_chain.py`): local rebuild, demo users, end-to-end through the API for G60 / USA and F70 / Europe, dump / restore.

## Not done, needs the user

1. **Supabase build (in progress on 2026-10-11, started from the session before this one).** The project (ref `tqbabtnmxkonjjjuahcp`, eu-west-3) was wiped and all 13 migrations applied; `python -m app.demo build` runs with gpt-6-luna (bulk) and gpt-6.1-sol (requirement texts only). If it is not finished or failed, check `agent_run` on Supabase (the last rows show the step and the error) and re-run `cd backend && uv run python -m app.demo build`: finished seed steps are skipped and LLM classifications are cached in `backend/.data/llm_cache/`. Do not run the end-to-end demo (`app.demo e2e`) on Supabase: it would confirm and approve a case in the real data (user decision: the PM confirms the first real case in the UI).
2. **OpenAI** is set (`backend/.env`). Still open with it: re-tune the case threshold for OpenAI embeddings (`app.cases.tune`, 0.55 is untuned) and the taxonomy eval (`app.signals.eval_taxonomy --split dev`, then `--split test` once). The user does not want local tests that call the AI: tests always run offline.
3. **Phase 8 checkpoint:** `backend/reports/fetch_plan.md` plans about 336 requests per day on average (cadence per source, budget 500) and about 29,000 for the backfill, spread by the budget; ask before the first real run. Reddit is disabled (no API access); the YouTube key is set.
4. Done: the user approved every row of the phase 2 review CSVs (changes.md R2.1).
5. Real users: `app_user` rows with the Supabase auth user ids (demo users have fixed placeholder ids).

## Next: wire the UI

The UI branch is merged into `main` (user, 2026-10-11). The user explains how the UI works; the backend contract is `backend/API.md` and the OpenAPI spec at `/docs`. Auth: the UI sends the Supabase access token (`AUTH_MODE=supabase`), or `X-User-Id` with a demo user id in dev mode (`app/demo.py`, `DEMO_USERS`). Only `notification` is readable by the browser (Realtime); everything else goes through the API.

## How to run

- `cd backend && uv sync`; tests: `uv run pytest` (embedded Postgres via pgserver, no network, local raw storage and dev auth forced by `tests/conftest.py`; about 4 minutes).
- Local database without touching Supabase: prefix commands with `SUPABASE_DB_URL= SUPABASE_URL= STORAGE_BACKEND=local` (environment variables beat `backend/.env`).
- API: `uv run uvicorn app.main:app --reload`, spec at `/docs`.
- Writes always go through `app/audit.py`; migrations are checksum-protected (add a new file, never edit an applied one).
- Windows: if the embedded Postgres hangs after a crash, `localdb.start` now finishes recovery with the log outside the data directory. Stop servers with `pg_ctl -D backend/.data/pg stop` (binary under `.venv/Lib/site-packages/pgserver/pginstall/bin`).
- Shell note: heredocs with apostrophes break in the Git Bash tool; commit with `git commit -F -` and a quoted heredoc. Teammates push to `main`: `git pull --rebase` before pushing; never force-push.
