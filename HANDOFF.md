# Handoff to the next agent

State on 2026-10-11. Read `CLAUDE.md`, `plan.md` (progress table, open issues), `changes.md` (every decision with reason), `database-design.md`, `backend/API.md` first. The user supervises; Devin reviews every phase commit.

## Done and pushed

Phases 0 to 10 of `plan.md`, each with a "Completed phase N" commit (7, 8, 9, 10 on 2026-10-11). Highlights of the last session:

- **Phase 9 API** (`backend/app/api/`, `backend/API.md`): routers for config / profiles / compare / source filter, signals / quarantine / taxonomy review, cases (board, overview, page, lifecycle, rank, tags, merge, split, predecessor research), requirements (board, page, status, cost, re-target, rank, delete, export), chat, audit trail and audit chain, notifications, runs. Supabase token or `X-User-Id` (dev), one transaction per request, request id on audit events, 409 on stale saves.
- **Phase 8** finished: adapters for every source of steps 1 to 9 (`app/fetch/adapters/sites.py`, `libraries.py`), extra slugs (`backend/seed/slugs_extra.yaml`), on-demand predecessor research (`app/fetch/predecessor.py`).
- **Phase 10** (`app/demo.py`, `app/reports/audit_chain.py`): local rebuild, demo users, end-to-end through the API for G60 / USA and F70 / Europe, dump / restore.

## Not done, needs the user

1. **Supabase build.** `backend/.env` is set and both the database (session pooler, eu-west-3) and storage were reached; the database is still empty. Run `cd backend && uv run python -m app.demo build` (no `--fresh`: it is refused on Supabase anyway), then `uv run python -m app.demo e2e "G60 / USA"`. About 10 minutes. Ask the user before running it if they have not said go.
2. **OpenAI key** (`OPENAI_API_KEY`): then `uv run python -m app.cases.pipeline` (classifies the 666 quarantined and 2,557 topic-less rows first), re-embed (`app.signals.embed --provider-change`), re-tune the case threshold (`app.cases.tune`), `app.signals.eval_taxonomy --split dev` (then `--split test` once), rebuild the RAG index.
3. **Phase 8 checkpoint:** show `backend/reports/fetch_plan.md` (about 4,500 requests per incremental run, about 150,000 for the backfill) before the first real `python -m app.fetch.worker --backfill`. Reddit OAuth and a YouTube Data API key are missing.
4. Team review of the phase 2 CSVs in `backend/seed/review/`.
5. Real users: `app_user` rows with the Supabase auth user ids (demo users have fixed placeholder ids).

## How to run

- `cd backend && uv sync`; tests: `uv run pytest` (embedded Postgres via pgserver, no network, local raw storage and dev auth forced by `tests/conftest.py`; about 4 minutes).
- Local database without touching Supabase: prefix commands with `SUPABASE_DB_URL= SUPABASE_URL= STORAGE_BACKEND=local` (environment variables beat `backend/.env`).
- API: `uv run uvicorn app.main:app --reload`, spec at `/docs`.
- Writes always go through `app/audit.py`; migrations are checksum-protected (add a new file, never edit an applied one).
- Windows: if the embedded Postgres hangs after a crash, `localdb.start` now finishes recovery with the log outside the data directory. Stop servers with `pg_ctl -D backend/.data/pg stop` (binary under `.venv/Lib/site-packages/pgserver/pginstall/bin`).
- Shell note: heredocs with apostrophes break in the Git Bash tool; commit with `git commit -F -` and a quoted heredoc. Teammates push to `main`: `git pull --rebase` before pushing; never force-push.
