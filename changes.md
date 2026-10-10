# Changes Log: Decisions Made During Implementation

Every change against `plan.md`, `CLAUDE.md`, `architecture.mmd` or the first database draft that was decided during implementation, with the reason and who decided. Newest phase last. Small implementation details that follow the plan are not listed here; they are in the commit bodies.

## Phase 0: docs aligned and database design

| # | Change | Reason | Decided by |
|---|---|---|---|
| 0.1 | "Finding" renamed to "Signal" in `architecture.mmd`, `data-model.mmd`, `README.md`, `All-context/README.md`, `fetching-sources.md`. BMW challenge transcriptions (`01-challenge/`, `layers.mmd`) and meeting notes keep their original word. | Binding terminology; the BMW material and meeting notes are records of what others wrote or said. | User (rename), Claude (scope of the rename) |
| 0.2 | Case table named `case_item` instead of `case`. | `CASE` is a reserved SQL word; every raw query would need quoting. API and UI still say "case". | User |
| 0.3 | A case is not tied to one vehicle or config. Vehicles are tags in a new link table `case_vehicle`, which holds per-vehicle state, PM rank, release fit, conflict flag and new-evidence flag. Case IDs `CASE-0012`; requirement IDs per line `REQ-G60-0012` (`REQ-G60-USA-0012` when split). | One problem can come from several BMW lines and series (Baukasten, My BMW app); each line has its own PM, so the decision is per vehicle. The first draft (`case.config_id`) also made cross-market conflicts undetectable. | User (cases span vehicles), Claude (per-vehicle state table) |
| 0.4 | `car_area` is a table, not a hard-coded list. | Fixed list that the team confirms in phase 2; configuration, not code. | Claude |
| 0.5 | Option-list pages stored as `source_record` rows (`source_id = option_list`); `vehicle_feature.record_id` points to them. | Equipment facts become quotable and traceable like every other source. | Claude |
| 0.6 | `site_slug` primary key includes the slug. | Some sites have several pages per car (What Car? body types). | Claude |
| 0.7 | `database-design.md` is the source of truth for the schema; `data-model.mmd` is its diagram; the database section of `plan.md` points to it. | One place for the schema instead of three copies. | Claude |

## Phase 1: foundation

| # | Change | Reason | Decided by |
|---|---|---|---|
| 1.1 | Migrations and tests run against a local embedded Postgres 16 + pgvector 0.6 (`pgserver`, dev dependency) when no Supabase URL is configured. Supabase-only parts (roles `anon` / `authenticated`, Realtime publication, Storage bucket) are guarded so the same SQL runs on both. | No Supabase credentials yet, Docker not running; the migrations still get tested. | Claude |
| 1.2 | Embedding provider is pluggable: `openai` (`text-embedding-3-small`, 1536 dims) by default, `local` (hashed character n-grams, 1536 dims, L2-normalized) as offline fallback for tests and demos without network. Every stored embedding records its provider in the audit `after` payload. | Demo fragility risk in `CLAUDE.md`; tests must not call OpenAI. The local provider is weaker and is never used to tune thresholds for production. | Claude |
| 1.3 | Raw-record storage is pluggable: Supabase Storage bucket `raw-records` when configured, else a local folder `backend/.data/raw-records/` (gitignored). `raw_ref` is the same relative path in both. | Seed and fetch must run before Supabase exists; cached raw records double as the offline demo backup (phase 10). | Claude |
