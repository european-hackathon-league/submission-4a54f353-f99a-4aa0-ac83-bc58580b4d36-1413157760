"""FastAPI app. Routers are added per phase; the OpenAPI spec is the UI contract."""

from fastapi import FastAPI

app = FastAPI(
    title="BMW Requirements Platform API",
    version="0.1.0",
    description="Backend for PMs: signals, cases, scores, requirements, RAG sidebar, audit trail.",
)


@app.get("/health", tags=["system"])
def health() -> dict:
    return {"status": "ok"}
