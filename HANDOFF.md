# Handoff to the next session

State on 2026-10-11, about 06:30. Read `CLAUDE.md` first, then `changes.md` section "UI alignment" (U.1 to U.24): it records every decision of this session with its reason. **Nothing of this session is committed.**

## 1. Check first: the Supabase build

A build was started on Supabase (`cd backend && LLM_WORKERS=12 uv run python -m app.demo build`, log in `%TEMP%\build-supabase.log`). When this file was written it was writing the case pages. It may have finished, failed or been stopped with the session.

- Look at the last rows of `agent_run` on Supabase (step, status, error). A row left on `running` with no session behind it is a dead run: set it to `failed`.
- If it did not finish, run the same command again. It is safe: finished seed steps are skipped, signals that have an embedding are skipped, and every LLM answer is cached in `backend/.data/llm_cache/` (key = prompt version + model + thinking effort + inputs), so nothing is paid twice.
- Known state before the case step: seed complete (10,128 verbatims, 13,771 signals, 174 quarantined, 407 in the taxonomy review queue, 10 configs), 13,404 signal embeddings. Migrations 0001 to 0016 are applied.
- After it finishes, check: number of cases and how many opened per line, a few case pages for quality (short title, summary, three highlights, citations), `select pg_size_pretty(pg_database_size(current_database()))`. The free plan allows 500 MB; the estimate for this data is about 340 MB (never measured on Supabase).
- Do not run `app.demo e2e` on Supabase: it confirms and approves a case in the real data.

## 2. What this session changed

**Schema (new migrations, never edit an applied one):**
- `0014_ui_alignment.sql`: 15 tables and many columns for what the UI shows (briefs, stances, questions, meeting notes, proposals, referrals, innovation pool, comments, alerts, vehicle plan, requirement versions / feedback / checks, owners and workflow per case and line, sign-off statuses).
- `0015_rag_pm_context.sql`: the search index may hold PM context.
- `0016_size_budget.sql`: two vector indexes dropped, embeddings stored with 768 dimensions (`EMBEDDING_DIM`), `rag_search` takes a vector of any size.
- Supabase has all 16. **The local database has 0001 to 0015 only.** Applying 0016 locally clears its embeddings and its search index; they must be rebuilt after (`python -m app.signals.embed`, `python -m app.rag.index`).

**Pipeline and agents:**
- Prompts `case_writer@v2`, `case_tags@v2`, `requirement_writer@v2`, `rag_agent@v2`. Tried against OpenAI: case writer and case tags (good output on three real cases). **Never run against OpenAI: `requirement_writer@v2` and `rag_agent@v2`.**
- Models and thinking effort (`app/config.py`): gpt-6-luna with effort `low` for `feedback_classify`, `signal_extract`, `case_writer`, `case_tags`; gpt-6-luna with the default effort for the RAG agent; gpt-6.1-sol with the default effort for `requirement_writer`. gpt-6-luna has no `minimal` effort (the API accepts none, low, medium, high, xhigh).
- The LLM never works inside a database transaction any more (seed, case pipeline, backfill). The three earlier Supabase attempts failed in `seed:feedback` with dropped connections while the connection waited on the LLM.
- Observed cases are not scored; a case is scored when it opens. Machine events are not indexed for the RAG agent; the PM context is.
- Fetch loop: a record the LLM finds nothing in keeps only a marker row (relevance filter). YouTube: videos of the last 60 days, the 10 most relevant comments per video. Wikipedia and Wikidata are called as official APIs (robots.txt refused them before).

**API and UI:**
- About 40 new endpoints in `backend/app/api/routers/workspace.py`, services in `backend/app/collab/`.
- The adapter in `web/src/backend/` serves all 67 endpoints of the UI contract from the API (`DATA_SOURCE=backend`). The JSON sidecar and the persona table are gone; users are `app_user` rows.
- UI fixes: brief page on a brief pre-filled from data, drag and drop on the case board, readable audit lines and proposal cards (`web/src/lib/audit.ts`).

## 3. Decisions made in this session

Each one is also a row of `changes.md` (U.1 to U.24). "User" = decided by the user in the session; "Claude" = proposed and applied by the assistant, not objected to.

| Decision | Why | By |
|---|---|---|
| Fit schema, agents and API to the UI before the Supabase migration and the paid run | Otherwise the case pages would be paid for twice | User |
| The adapter serves the whole UI contract from the API; mock and real data are never mixed; the sidecar file is gone | The earlier adapter showed mock cases next to real signals | Claude |
| The case lifecycle keeps its four states; the PM's steps are `workflow`, the decision is `outcome` | The UI has six states and six outcomes; the documented lifecycle stays the source of truth | Claude |
| Cases are owned per line; handing a case over never copies it | A copy would split the evidence | Claude |
| Requirements hold structured fields, the page is rendered from them, every change is a version | The UI edits SMART fields, not markdown | User (guide 8.5) |
| Statuses signed off, planned and exported exist in the backend; only the head signs off | Guide 8.5 | User (guide 8.5) |
| Confirming a case does not draft from the UI; the shape questions come first | UI layer 3 | Claude |
| Brand fit stays a multiplier, `reach` stays a factor; the UI's "project type" setting is not stored | Scoring decisions of `CLAUDE.md` are not changed for the UI | Claude |
| The RAG agent knows the PM context and cites it apart from customer evidence | It could not say what engineering answered | User |
| On a case that spans lines the agent uses only the brief of the asking PM's line | The 7 Series brief appeared on a 5 Series case | User |
| Load the database from BMW's original files, not from the processed CSVs | Same content; the originals keep sheet and row; the processed option lists lose the columns | User, after the assistant's advice |
| BMW feedback that is not relevant is kept out of cases, scores and the index, not deleted | All verbatims are 2.2 MB; deleting breaks the counts and the transparency | Assistant's advice; the user raised the point and did not overrule it |
| A fetched web record without a relevant statement keeps only a marker row | Space and noise | User |
| Size budget for the free plan: no vector index on `signal` and `rag_chunk`, 768 dimensions, no scores for observed cases, no machine events in the search index | The local build was 789 MB, the plan allows 500 MB | User asked for a solution; measures by Claude |
| gpt-6-luna with thinking `low` for classification, extraction, case pages and tags; default thinking for the RAG agent; gpt-6.1-sol with default thinking for requirements | Reading tasks need little reasoning. The user asked for "minimal", the model does not offer it; "none" was tried and the user chose "low" | User |
| YouTube: videos of the last 2 months, 10 comments per video, comments and transcripts | A few strong, recent voices per video | User |
| Keep the mock in the repository as the offline fallback | Demo safety; the adapter reuses its deterministic logic | Assistant's advice, not objected to |
| Migrations 0014 to 0016 applied on Supabase and the build started with OpenAI | "Now try to migrate the work to Supabase"; "run the Supabase migration using OpenAI" | User |

Not decided yet (ask the user): running the YouTube fetch into Supabase; what to do about the 1 GB storage limit before a web backfill; whether `case_writer.jsonl` of the LLM cache is shared in git; real user accounts instead of the eight demo people.

## 4. Not verified

- **The full backend suite was last run before the final edits.** 122 tests passed after the pipeline and size changes. Edited after that and covered only by partial runs (fetch, foundation and signal tests pass): relevance filter, YouTube window and comment limit, thinking effort, cache keys. Run `cd backend && uv run pytest` (about 4 minutes, offline).
- **`npm run adapter-flow` passed (34 calls) before** the RAG, scoring and size changes. Run it again against a local database (see section 7).
- The screens were opened on real data once (login, brief, desk, case board, case page, requirement page, signals, audit). The vehicle plan page was only checked through its endpoint. The readable audit lines were checked in mock mode only.
- Database size on Supabase after the size changes: estimated, not measured.

## 5. Open work, in order

1. Finish and check the Supabase build (section 1).
2. Run the full test suite, then commit. Ask the user before committing. Include `backend/.data/llm_cache/feedback_classify.jsonl` (the shared cache now holds the real classifications) and decide with the user whether `case_writer.jsonl` is shared too.
3. Update the design documents. Only `changes.md` reflects this session. Still describing the old state: `database-design.md` (source of truth for tables), `data-model.mmd`, `INTEGRATION-GUIDE.md`, `backend/API.md`, `CLAUDE.md` (current phase, models, embedding size), `plan.md`, `web/README.md`.
4. Switch the UI to the Supabase-backed API: run the API with `AUTH_MODE=dev` (or Supabase tokens), `web/.env.local` with `DATA_SOURCE=backend` and `BACKEND_URL`. The demo users have fixed ids (`backend/app/demo.py`).
5. YouTube comments into Supabase. Proposed to the user, **not confirmed**: `uv run python -m app.fetch.worker --sources youtube-comments` (about 3,000 of 10,000 daily quota units, at most 1,500 comments). Ask first.
6. Try `requirement_writer@v2` and `rag_agent@v2` with OpenAI on one confirmed case before the demo.

## 6. Known problems

- **Fetch sources** (`backend/reports/fetch_smoke.md`, live check with `python -m app.fetch.smoke`): 32 of 81 returned records. 34 were reachable but returned nothing for the 5 Series; it is not known whether the parser misses the content or no article named the car. Blocked: `kbb`, `whatcar` (403), `autoscout24` (robots.txt). Broken: `press-lexus` (TLS handshake), `google-trends` (the library fails with the installed urllib3, `method_whitelist`). YouTube transcripts: every download answered `IpBlocked` from this machine.
- **Raw web pages** go to the Supabase storage bucket, 1 GB on the free plan. The planned backfill (about 29,000 requests) would exceed it. Decide before the first real fetch.
- **UI limits on real data:** the Refresh button returns 501 (the fetch loop is started by the worker only); the Signals diagram reads at most 2,000 signals (an aggregate endpoint `GET /signals/flow` exists, the diagram does not use it); a brief change lists affected requirements but does not redraft them; the scripted chat shortcuts of the mock (draft a requirement, questions for engineering) are not ported; the brand-fit slider has no effect and `reach` has no slider; the head overview covers the caller's line only.
- **Local database** (`backend/.data/pg`): holds test writes of this session (two confirmed cases, `REQ-G60-0273`, `REQ-G60-1160`, Lena's brief) and template texts, not LLM texts. Rebuild it with `python -m app.demo build --fresh` when a clean local copy is needed.
- `web/package-lock.json` changed: `npm ci` failed on a lockfile out of step with `package.json`, so `npm install` was run.

## 7. How to run

- **Tests:** `cd backend && uv run pytest` (embedded Postgres, no network, no OpenAI). Tests no longer write to the shared LLM cache.
- **API on the local database, offline:** `cd backend && uv run python -m app.dev_local` (port 8000; no Supabase, no OpenAI, whatever `backend/.env` says).
- **UI:** `cd web && npm run dev`. Mock data by default; real data with `web/.env.local` containing `DATA_SOURCE=backend` and `BACKEND_URL=http://127.0.0.1:8000`. That file exists now and points at the local API: delete it to return to the mock.
- **End-to-end check of the adapter** (writes, local database only): `API_BASE=http://localhost:3000/api npm run adapter-flow`.
- **Live check of the fetch sources** (real requests, nothing stored): `cd backend && uv run python -m app.fetch.smoke [--sources a,b] [--cap 20]`.
- Commands that read `backend/.env` talk to Supabase and OpenAI. For local work prefix them with `SUPABASE_DB_URL= SUPABASE_URL= OPENAI_API_KEY= EMBEDDING_PROVIDER=local STORAGE_BACKEND=local`.
- Git Bash: a heredoc that contains an apostrophe breaks. Write the script to a file and run it.

## 8. How the user wants to work

- Ask before running backend processes, tests, builds, fetches or anything on Supabase or OpenAI; say what will run and what it costs. The user stopped a test run that was started without being asked.
- Text in the UI is readable prose, never raw JSON.
- BMW's provided data is never dropped; irrelevant rows are kept out of cases, scores and the index (quarantine, out of scope), not deleted. Web records without a relevant statement are not kept.
- Load the database from BMW's original files, not from the processed CSVs.
