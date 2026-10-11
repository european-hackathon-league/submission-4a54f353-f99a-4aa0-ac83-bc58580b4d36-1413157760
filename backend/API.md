# API for the UI

The OpenAPI spec is the contract: run the API and open `http://localhost:8000/docs` (or `/openapi.json` for code generation).

```bash
cd backend
uv run uvicorn app.main:app --reload
```

## Auth

- **Production** (`AUTH_MODE=supabase`, the default when `SUPABASE_URL` is set): send `Authorization: Bearer <Supabase access token>`. The token's user id must exist in `app_user` (role `pm`, `engineer` or `admin`, and the lines a PM owns).
- **Local development** (`AUTH_MODE=dev`): send `X-User-Id: <app_user.id>`. `python -m app.demo` creates demo users and prints their ids.

Every write is audited with the user as actor; send `X-Request-Id` to correlate a click with its audit events (one is generated otherwise and returned in the response header).

## Errors

`401` no or invalid credentials · `403` wrong role or not your line · `404` unknown object · `409` stale page save (reload and merge) or invalid state transition · `422` invalid input.

## Main flows

| Screen | Calls |
|---|---|
| Config picker | `GET /configs` (each with its weight profiles), `GET /me` |
| Case board | `GET /cases?profile_id=…` with filters `state` (repeat), `car_area`, `release_fit`, `market`, `source_type`, `source_id`, `aspect_group`, `conflict_only`, `q` |
| Overview map | `GET /cases/overview?vehicle=G60` |
| Case page | `GET /cases/{id}` (logged view), `PUT /cases/{id}/page` `{body_md, expected_version, title?}`, `GET /cases/{id}/versions`, `GET /signals?case_id=…` |
| Case actions | `POST /cases/{id}/vehicles/{vehicle}/transition` `{action: open\|confirm\|reject\|reopen, rationale}` (confirm returns the generated requirement ids), `…/rank` `{rank, reason}`, `POST /cases/{id}/tags`, `/merge`, `/split` |
| Requirement board | `GET /requirements?profile_id=…` (filters `status`, `release_target`, `car_area`, `market`) |
| Requirement page | `GET /requirements/{id}`, `PUT …/page`, `POST …/status`, `…/retarget`, `…/cost`, `…/rank`, `…/delete` |
| Handover | `GET /requirements/export?profile_id=…&format=json\|csv` (approved top 10, logged) |
| RAG sidebar | `GET /chat/{case\|requirement}/{id}`, `POST /chat/{type}/{id}` `{question}`, `POST /chat/messages/{id}/decision` `{accept}` |
| Weights | `POST /configs/{id}/profiles`, `PATCH /profiles/{id}`, `GET /configs/{id}/compare?top_n=10`, `PATCH /configs/{id}` (source filter, thresholds, competitors) |
| Evidence | `GET /signals/{id}` (with its source record), `GET /quarantine`, `POST /quarantine/{id}/delete\|release`, `GET /taxonomy/review`, `POST /taxonomy/review/{id}` |
| Trail | `GET /audit/{object_type}/{object_id}`, `GET /audit-chain/{requirement_id}` |
| Toasts | `GET /notifications?unread=true`, `POST /notifications/{id}/read`; live updates through Supabase Realtime on `notification` |
| Runs | `GET /runs` (fetch and pipeline runs, failures) |
