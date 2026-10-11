# Agent Guide: BMW Challenge Knowledge Base

This folder is the knowledge base for a hackathon: building an AI tool that helps BMW product managers derive, justify and prioritize **customer requirements for a successor vehicle**. [`README.md`](README.md) is the full map. This file tells an agent how to use it well.

## Reading order
1. [`01-challenge/hackathon-briefing.md`](01-challenge/hackathon-briefing.md) and [`mission-and-deliverables.md`](01-challenge/mission-and-deliverables.md): the official task and deliverables.
2. [`01-challenge/evaluation-criteria.md`](01-challenge/evaluation-criteria.md): how we are judged.
3. [`01-challenge/stakeholder-needs.md`](01-challenge/stakeholder-needs.md): everything BMW people asked for, with source tags. **The design checklist.**
4. [`02-data/insights.md`](02-data/insights.md): what the data says, including coverage gaps and conflicts.
5. [`05-solution/scope-and-focus.md`](05-solution/scope-and-focus.md), [`end-to-end-flow.md`](05-solution/end-to-end-flow.md) and [`web-intelligence-loop.md`](05-solution/web-intelligence-loop.md): what we are building, how it flows, and our differentiator.
6. As needed: [`02-data/README.md`](02-data/README.md) (schemas), [`02-data/equipment-baseline.md`](02-data/equipment-baseline.md) (what the cars offer), [`02-data/external-sources.md`](02-data/external-sources.md), meeting summaries in [`03-meetings/`](03-meetings/), [`GLOSSARY.md`](GLOSSARY.md).

## Where to find what
| Question | Go to |
|---|---|
| What exactly must we deliver? | `01-challenge/mission-and-deliverables.md` §4 |
| What do PMs want the tool to do or not do? | `01-challenge/stakeholder-needs.md` |
| How should prioritization work? | `stakeholder-needs.md` §4, Patrick summary §4, `process-cycle.md` §4 |
| How to phrase a requirement and which fields it needs | `01-challenge/smart-requirements.md` |
| Customer pains, wants and likes per model | `02-data/insights.md` §1–2, then `02-data/processed/feedback_*.csv` |
| Satisfaction scores (EU/China means, US distributions) | `02-data/processed/study_cn_eu_2025.csv`, `study_us_2025.csv` |
| Which markets matter commercially | `02-data/processed/sales_volumes.csv` |
| Is a feature already offered, and at what price? | `02-data/equipment-baseline.md`, then grep `02-data/processed/option-lists/*.txt` (German) |
| Competitors and future trends | `02-data/external-sources.md` (nothing fetched yet, so research is needed) |
| What we build, what to focus on | `05-solution/scope-and-focus.md` |
| Pipeline stages, gates, data objects, scoring formula, screens | `05-solution/end-to-end-flow.md` |
| Web fetching loop, external sources, monitoring alerts, China PM / RoW PM workspaces | `05-solution/web-intelligence-loop.md`, `02-data/external-sources.md` §4 |
| What a term or code means | `GLOSSARY.md` |

## Ground rules for answers and requirements
- **Cite sources.** Point to verbatim IDs (e.g. `G60-0878`), study attribute and market, sales row, option-list page or section, meeting summary, or URL. Every requirement needs linked findings.
- **Use the processed CSVs** (`02-data/processed/`), not the xlsx or PDF files. Count **distinct verbatims** (`id`), never rows. CSV `text` fields contain line breaks, so use a CSV parser.
- **Separate evidence from assumption.** Provided data describes today's cars. Statements about 3 to 5 years ahead are assumptions unless backed by an external source. Use the trust levels in `external-sources.md`.
- **Respect coverage.** No feedback from China. G60/G70 verbatims are 90 to 95% US. F70 is UK/Japan, ICE. Say so whenever you generalize, and weight by market volume, not verbatim volume.
- **Surface conflicts.** Market or source disagreements go to the PM; don't average them away (see `insights.md` §3).
- **Stay in scope.** Customer-facing value only. No regulation, engineering specs or business case. Cost is a rough factor only, and engineering decides feasibility.
- **Brand fit.** BMW = driving dynamics, sportiness, design, premium, efficiency. Frequency alone is not relevance.
- **Don't invent data.** If a number isn't in the files, say it is missing. Prices in `equipment-baseline.md` marked `~` are best-effort.
- **Ignore `knowledge-base.zip`.** It is a stale snapshot.
- Meeting transcripts are noisy ASR. Prefer the summaries and treat quotes as paraphrase.

## Maintaining this knowledge base
- Never edit files in `02-data/raw/`. Regenerate `processed/` with `python 02-data/scripts/build_processed.py` (needs pandas, openpyxl, and poppler's `pdftotext`).
- If you add or rename a file, update the map in `README.md` (and its rename table).
- Meeting files follow `YYYY-MM-DD_HHMM_topic_type.md` (`type` = summary or transcript). Transcripts go in `03-meetings/raw/`.
- Put fetched external research in a new `02-data/external/` folder, one file per source, with URL, retrieval date and quoted passages.
