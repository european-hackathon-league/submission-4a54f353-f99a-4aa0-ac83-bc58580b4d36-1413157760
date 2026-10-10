# BMW Product Management Challenge

TUM.ai hackathon project for the BMW Group challenge "AI for Product Decision Making": a platform that helps BMW product managers derive, justify and prioritize customer-facing requirements for the successor of a vehicle line, with the product manager in control of every decision and a full audit trail.

## How it works

**Finding → Case (problem) → PM confirms → Requirement (solution) → PM approves → engineering.**

- **Finding:** one statement about one feature from one source (BMW feedback, a review, a competitor fact, a trend), with the exact quote and source link.
- **Case:** a problem as customers and the market see it, for example "front seat ventilation too weak". It groups many findings, keeps all opinions per market, and has an importance score but no cost.
- **Requirement:** generated after the PM confirms a case. It states what BMW will build, with a measurable target, a release target (next generation, current car update or OTA) and a rough cost. One per case, or one per market when markets differ.

A daily background loop fetches web evidence; the PM works the cases and requirements on Notion-like pages with a RAG assistant that cites its sources.

## Repository

| Path | Content |
|---|---|
| [`architecture.mmd`](architecture.mmd) | Target architecture (Mermaid) |
| [`data-model.mmd`](data-model.mmd) | Data model for Supabase Postgres + pgvector (Mermaid ER) |
| [`layers.mmd`](layers.mmd) | BMW's process slide as a diagram, for reference |
| [`All-context/`](All-context/README.md) | Challenge material, provided data, meeting notes, team notes, web sources |
| [`web/`](web/README.md) | **Clickable demo** (Next.js): signals → cases → requirements → vehicle plan, 4 personas, mock backend on synthetic data. Run with `npm install && npm run dev` in `web/` |
| [`contracts/`](contracts/openapi.yaml) | REST contract between UI and backend (OpenAPI 3.1 + JSON Schemas), generated from `web/src/contracts` |
| [`taxonomy/`](taxonomy/README.md) | **Fixed taxonomy** for classifying signals: 11 car areas → 219 topics (BMW VFC level 2) + aspect groups, classifier schema and prompt, eval against BMW labels. Hand-off spec for the agents |
| [`docs/`](docs/README.md) | Amine's knowledge base: data insights, processed CSVs (used by the demo seed), solution design, UX decisions |

In the UI, "findings" are called **signals**.
