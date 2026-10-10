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
| 1.2 | Embedding provider is pluggable: `openai` (`text-embedding-3-small`, 1536 dims) by default, `local` (hashed character n-grams, 1536 dims, L2-normalized) as offline fallback for tests and demos without network. The provider name is stored next to the embeddings (`agent_run.params`, `signal.extra.embedding_provider`) so vectors of different providers are never compared. | Demo fragility risk in `CLAUDE.md`; tests must not call OpenAI. The local provider is weaker and is never used to tune thresholds for production. | Claude |
| 1.3 | Raw-record storage is pluggable: Supabase Storage bucket `raw-records` when configured, else a local folder `backend/.data/raw-records/` (gitignored). `raw_ref` is the same relative path in both. | Seed and fetch must run before Supabase exists; cached raw records double as the offline demo backup (phase 10). | Claude |

## Phase 2: seed

| # | Change | Reason | Decided by |
|---|---|---|---|
| 2.1 | The feature catalog, the VFC topic -> feature / car area mapping, the study attribute mapping and the option item -> feature mapping were proposed by the implementing agent (Claude Code) as explicit tables in `app/seed/catalog_proposal.py` and `app/seed/option_load.py`, not by an OpenAI batch call. Every proposal row carries `proposed_by = agent:claude-code (seed proposal)`. | No OpenAI key yet; explicit tables are also easier to review than an LLM batch. | Claude |
| 2.2 | **Checkpoint handling:** the review CSVs in `backend/seed/review/` are imported before the team review, but unconfirmed rows load as `feature.status = candidate` and `feature_mapping.confirmed_by = null`. After the review (column `confirmed = yes`, corrections in place) the seed is re-run on a fresh database. | The user asked not to stop at checkpoints; importing as "candidate" keeps "AI proposes, PM decides" visible in the data instead of pretending the mapping is confirmed. | User (do not stop), Claude (candidate status) |
| 2.3 | The study's G68 (China long-wheelbase 5 Series) and the `G60_G68` sales sheet belong to the vehicle line `G60`; the model label (`BMW 5 Series G68`) is kept in `study_score.raw`. | G60, G61 and G68 are one 5 Series generation (fetching-sources 2.1); a separate G68 line would split China evidence from the line it belongs to. | Claude |
| 2.4 | Feedback rows without a Feedback Type (2,500) are classified by the LLM prompt `feedback_classify@v1` when a key is configured, otherwise by a keyword heuristic (`heuristic@v1`, marked `extra.kind_source = heuristic`). Rows the heuristic cannot decide are quarantined (666 rows) rather than guessed. | The pipeline must run offline; no guessed kinds; an LLM re-run replaces heuristic verdicts later. | Claude |
| 2.5 | Every Excel topic row becomes exactly one signal (13,771), every verbatim one source record (10,128). Distinct customers are counted by verbatim ID (`customer_ref`). | 1:1 traceability between Excel rows and signals; counts can be checked against the data README. | Claude |
| 2.6 | Signals from BMW-labelled rows get a label-based summary ("Difficult to use: Seats (Not user-friendly)") instead of an LLM summary. | Uses only what the source says; no LLM needed for 11,271 already classified rows. | Claude |
| 2.7 | Survey question prefixes (Source C) are kept in `text_original`; the signal quote is the answer only, an exact substring of the record. | Raw text unchanged, quote still verifiable. | Claude |
| 2.8 | Option-list prices: the printed base price is the reference price; a per-variant price is stored only for unconditional price lines; conditional prices ("In Verbindung mit M Sportpaket ...") go to the item description. Variant names are kept as printed (G70: `i7 50xDrive`). | Conditional prices depend on other options and cannot be one number per variant. | Claude |
| 2.9 | `vehicle.on_sale_since` uses the first day of the stated period where the sources file gives only a year (F70 2024, G60 2023); the precision is kept in `registry.yaml`. | Do not invent a launch month; fetch step 1 (Wikipedia) can refine it. | Claude |
| 2.10 | Competitor markets = markets of the BMW line minus explicit "not sold in" notes (Lucid Air not in China, Maextro S800 not in Europe); Rest of World is not a competitor market. | The sources file gives no per-market list; the PM confirms the competitor set per config. | Claude |
| 2.11 | Sales cell `F70!C3` holds a formula (`=-D3-C10`); it is stored as `value_text` with `suspect = true`. Identical Rest of World series of F70 and G60_G68 are flagged `suspect`. | Data caveats from the README stay visible instead of being silently fixed. | Claude |
| 2.12 | BMW feedback category: Source B (dealer / call-center notes) = `A` (complaints), Sources A, C, D = `B` (reviews and surveys). VFC topics about price-performance get `scope_tag = out_of_scope`. | One category per record (fetching-sources 7.2); pricing is out of scope (CLAUDE.md). | Claude |
| 2.13 | Four default weight profiles per config: balanced (default weights), customer voice, competition, budget (cost multipliers S 1.0 / M 0.7 / L 0.4). One config per vehicle x market with 2030 sales > 0 (10 configs). | Gives the profile compare view something to compare from day one. | Claude |
| 2.14 | `audit.insert_many` writes in batches (multi-row INSERT ... RETURNING plus one multi-row audit insert per batch). | About 25,000 seed rows; one round trip per row would take tens of minutes on a remote Supabase. | Claude |
