# BMW Challenge: AI for Product Decision Making

An AI-powered application that helps BMW product managers derive, justify and prioritize customer requirements for a successor vehicle, with the product manager in control of every decision. This folder holds the challenge material, provided data, meeting notes and team ideas.

## Structure

```
All-context/
├── GLOSSARY.md                Model codes, BMW and process terms, German option-list terms
├── 01-challenge/              What we are asked to build, and what BMW wants
├── 02-data-provided-by-pm/    Provided BMW data: raw/, processed/ (CSV and text), insights, equipment baseline
├── 03-meetings-with-pm/       Meeting summaries and notes (raw/ holds the transcripts)
├── 04-team-notes/             Our early brainstorm, open questions, notes between teammates
├── 05-web-sources/            External sources for the fetch agents
└── 06-solution-concept/       Solution concept from the UI side: scope, end-to-end flow, UX decisions
```

This is the single knowledge base: the former `docs/` folder of the frontend branch was merged into it on 2026-10-11 (identical files dropped, new files moved here). The fixed classification lives in [`../taxonomy/`](../taxonomy/README.md).

The decided design is not in this folder: see [`../architecture.mmd`](../architecture.mmd) and [`../data-model.mmd`](../data-model.mmd).

### 01-challenge: the task
| File | Content |
|---|---|
| [`hackathon-briefing.md`](01-challenge/hackathon-briefing.md) / [`.pdf`](01-challenge/hackathon-briefing.pdf) | The challenge, why it matters, mission, what to demonstrate |
| [`mission-and-deliverables.md`](01-challenge/mission-and-deliverables.md) | Mission sheet: scope, in/out of scope, expected deliverables, audit trail |
| [`process-cycle.md`](01-challenge/process-cycle.md) | BMW's process slide: situation analysis → documentation (also drawn in [`../layers.mmd`](../layers.mmd)) |
| [`findings-to-requirements.md`](01-challenge/findings-to-requirements.md) | 1 billion findings → 1,000 requirements → one car |
| [`provided-data-overview.md`](01-challenge/provided-data-overview.md) | What each provided dataset is good for |
| [`evaluation-criteria.md`](01-challenge/evaluation-criteria.md) | The 5 judging criteria, "we reward honesty", what to show for each |
| [`stakeholder-needs.md`](01-challenge/stakeholder-needs.md) | Consolidated needs from all documents and interviews, with source tags |
| [`smart-requirements.md`](01-challenge/smart-requirements.md) | How to write a requirement (SMART), good and bad examples, the requirement record template |
| [`product-development-process.md`](01-challenge/product-development-process.md) | Generic 13-phase automotive process with gates, and where our tool sits |
| `assets/` | The source slide images |

### 02-data-provided-by-pm: the data
Start with [`02-data-provided-by-pm/README.md`](02-data-provided-by-pm/README.md) (columns, counts, caveats). Files in `raw/`: customer feedback (F70, G60, G70), customer study 2025, sales volumes 2024 to 2030, and option lists (PDF). Never modify `raw/`.

| File | Content |
|---|---|
| [`insights.md`](02-data-provided-by-pm/insights.md) | Pre-computed findings: coverage, cross-model themes, per-model tables, conflicting signals |
| [`equipment-baseline.md`](02-data-provided-by-pm/equipment-baseline.md) | English summary of the option lists: line-ups, prices, standard and optional equipment |
| [`external-sources.md`](02-data-provided-by-pm/external-sources.md) | Competitors named, suggested benchmarks, evidence trust levels (the source list itself is in `05-web-sources/`) |
| `processed/` | Derived, agent-readable files built from `raw/`: `feedback_rows.csv` (13,771 rows, one per verbatim x topic; read by `taxonomy/build.py`), `feedback_verbatims.csv` (10,128), `feedback_topic_counts.csv`, `study_cn_eu_2025.csv`, `study_us_2025.csv`, `sales_volumes.csv`, `option-lists/*.txt` |
| [`scripts/build_processed.py`](02-data-provided-by-pm/scripts/build_processed.py) | Rebuilds `processed/` from `raw/` |

### 03-meetings-with-pm: what was said
Files are named `date_time_topic_type`. Read the summaries and notes first; raw transcripts are in `raw/`.

| Meeting | Transcript | Summary / notes |
|---|---|---|
| Team discussion, 14:24 (German) | [transcript](03-meetings-with-pm/raw/2026-10-10_1424_team-discussion-de_transcript.md) | [summary](03-meetings-with-pm/2026-10-10_1424_team-discussion-de_summary.md) |
| Team discussion with BMW contact, 14:39 (English) | [transcript](03-meetings-with-pm/raw/2026-10-10_1439_team-discussion-en_transcript.md) | [summary](03-meetings-with-pm/2026-10-10_1439_team-discussion-en_summary.md) |
| Product manager interview 1 (about 24 min) | [transcript](03-meetings-with-pm/raw/2026-10-10_pm-interview_transcript.md) | [summary](03-meetings-with-pm/2026-10-10_pm-interview_summary.md) |
| Product manager interview 2, 16:05 to 16:46 | not stored | [notes](03-meetings-with-pm/2026-10-10_1605_pm-interview-2_notes.md) |
| Demo feedback from Thomas (BMW AI engineer), 23:08 (about 26 min): fixed taxonomy, one kanban for cases, less is more | [transcript](03-meetings-with-pm/raw/2026-10-10_2308_thomas-demo-feedback_transcript.md) | [summary](03-meetings-with-pm/2026-10-10_2308_thomas-demo-feedback_summary.md) |
| Lifecycle voice note, 18:04 (about 1.5 min) | [transcript](03-meetings-with-pm/raw/2026-10-10_1804_lifecycle-voice-note_transcript.md) | [summary](03-meetings-with-pm/2026-10-10_1804_lifecycle-voice-note_summary.md) |

The two PM interviews are the most important user input.

### 04-team-notes: our early thinking
[`brainstorm-ideas.md`](04-team-notes/brainstorm-ideas.md): data sources, markets, architecture layers, agents, perspectives. Superseded by the architecture files where they differ.
[`open-design-questions.md`](04-team-notes/open-design-questions.md): two open prompts on the PM workflow and UX.
[`notes-to-ghassen/2026-10-11_taxonomy-categories.md`](04-team-notes/notes-to-ghassen/2026-10-11_taxonomy-categories.md): the note that introduced the fixed taxonomy (it says "finding"; that is our "signal").

### 05-web-sources: external data
[`fetching-sources.md`](05-web-sources/fetching-sources.md): what to search for, the list of web sources with URLs (including sources for new launches and competitor generations), coverage per model and market, the fetch order, and what to save with every record.

### 06-solution-concept: the concept from the UI side
[`scope-and-focus.md`](06-solution-concept/scope-and-focus.md), [`end-to-end-flow.md`](06-solution-concept/end-to-end-flow.md), [`ux-and-collaboration.md`](06-solution-concept/ux-and-collaboration.md), [`web-intelligence-loop.md`](06-solution-concept/web-intelligence-loop.md). Written on the frontend branch; where they differ from `CLAUDE.md`, `architecture.mmd` or `database-design.md`, those win for the backend.

## Recurring themes across the material
- **Human in the loop, no black box:** every recommendation shows its source, market and evidence. The product manager accepts, edits or rejects.
- **Traceability and audit trail:** from signal, to case, to requirement, to score, to decision, including who changed what, when and why.
- **Evidence vs. assumption:** make clear which conclusions rest on data and which on forward-looking assumptions.
- **Continuous loop:** competitor and feedback agents re-check requirements and flag outdated ones. They do not overwrite.
- **Brand fit and market differences:** volume of mentions is not relevance. China, the US and the Middle East need separate treatment.
- **Prioritization signals:** count distinct customers, not reach or rows. Treat functional complaints and removed features as serious.
- **Interview 2 additions:** the PM sets priorities (weight profiles) and wants to see how the ranking changes; skip sources; surface market conflicts instead of deciding them.
- **Lifecycle:** a new generation can change everything, a mid-life update only small things (colours, rims); too big for an update means next generation, not "no".
- **Output is a shortlist:** PMs hand roughly the top 10 requirements to engineering (codebeamer), each with its arguments.
- **Scope:** customer value only. No regulatory, engineering or business-case work.
