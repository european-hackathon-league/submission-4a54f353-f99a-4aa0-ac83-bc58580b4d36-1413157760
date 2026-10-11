"""Shared API plumbing: one transaction per request, the request id, error mapping."""

from __future__ import annotations

import contextvars
import uuid
from typing import Iterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import audit, db
from app.cases.lifecycle import InvalidTransition
from app.requirements.service import InvalidAction

REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def request_id() -> str | None:
    return REQUEST_ID.get()


def conn() -> Iterator:
    """A connection inside one transaction: everything a request writes, including
    its audit events, commits together or not at all."""
    with db.transaction() as c:
        yield c


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def _request_id(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = REQUEST_ID.set(rid)
        try:
            response = await call_next(request)
        finally:
            REQUEST_ID.reset(token)
        response.headers["x-request-id"] = rid
        return response

    def _error(status: int):
        async def handler(request: Request, exc: Exception):
            return JSONResponse(status_code=status, content={"detail": str(exc)})
        return handler

    app.add_exception_handler(audit.NotFound, _error(404))
    app.add_exception_handler(audit.StaleVersion, _error(409))
    app.add_exception_handler(InvalidTransition, _error(409))
    app.add_exception_handler(InvalidAction, _error(409))
    app.add_exception_handler(PermissionError, _error(403))
    app.add_exception_handler(ValueError, _error(422))
