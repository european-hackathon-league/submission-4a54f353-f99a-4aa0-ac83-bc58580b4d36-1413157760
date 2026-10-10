# BMW Product Management Challenge

TUM.ai hackathon project for the BMW Group challenge "AI for Product Decision Making": a platform that helps BMW product managers derive, justify and prioritize customer-facing requirements for the successor of a vehicle line, with the product manager in control of every decision and a full audit trail.

## How it works

**Signal → Case (problem) → PM confirms → Requirement (solution) → PM approves → engineering.**

- **Signal:** every piece of evidence: one statement about one feature from one source (BMW feedback, a review, a competitor fact, a trend), with the exact quote and source link. Competitor signals (e.g. complaints about the E-Class) attach to the BMW case of the same feature as competitor evidence and never count as BMW customers.
- **Case:** a problem as customers and the market see it, for example "front seat ventilation too weak". A case is composed of a group of signals, keeps all opinions per market, can apply to several BMW lines, and has an importance score but no cost.
- **Requirement:** generated after the PM confirms a case. It states what BMW will build, with a measurable target, a release target (next generation, current car update or OTA) and a rough cost. One per case, or one per market when markets differ.

A daily background loop fetches web evidence (v1: fetch steps 1 to 9 of the source list; one backfill over all pages, then daily incremental runs). The PM works the cases and requirements on Notion-like pages with a RAG assistant that cites its sources. Boards filter by market, car area / category, state, source type and release fit (current car update, next generation, OTA; judged by an LLM from the case size, the PM can override).

## Repository

| Path | Content |
|---|---|
| [`architecture.mmd`](architecture.mmd) | Target architecture (Mermaid) |
| [`database-design.md`](database-design.md) | Database design: tables, columns, rules |
| [`data-model.mmd`](data-model.mmd) | Data model for Supabase Postgres + pgvector (Mermaid ER) |
| [`plan.md`](plan.md) | Implementation plan and progress |
| [`layers.mmd`](layers.mmd) | BMW's process slide as a diagram, for reference |
| [`All-context/`](All-context/README.md) | Challenge material, provided data, meeting notes, team notes, web sources |
