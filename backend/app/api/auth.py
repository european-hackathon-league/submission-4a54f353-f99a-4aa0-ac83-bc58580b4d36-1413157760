"""Who is calling: Supabase Auth in production, a user header in local development.

Every write carries an actor derived from the user: ``pm:<id>``, ``engineer:<id>``
or ``admin:<id>``. The user must exist in ``app_user`` (role and owned lines).

- ``AUTH_MODE=supabase``: ``Authorization: Bearer <access token>`` from Supabase
  Auth. Verified with ``SUPABASE_JWT_SECRET`` (HS256, legacy projects) or with the
  project's JWKS (asymmetric signing keys). The ``sub`` claim is ``app_user.id``.
- ``AUTH_MODE=dev``: header ``X-User-Id: <app_user.id>``, no token. Only for local
  development and tests; refused when Supabase is configured unless set explicitly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException

from app import db
from app.config import get_settings


@dataclass(frozen=True)
class User:
    id: str
    name: str
    role: str
    vehicles: tuple[str, ...]

    @property
    def actor(self) -> str:
        return f"{self.role}:{self.id}"

    def owns(self, vehicle: str | None) -> bool:
        """PMs work on their own lines; an empty list or the admin role means all lines."""
        return self.role == "admin" or not self.vehicles or vehicle is None or vehicle in self.vehicles


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url)


def verify_token(token: str) -> str:
    """Return the user id (``sub``) of a valid Supabase access token."""
    settings = get_settings()
    try:
        if settings.supabase_jwt_secret:
            claims = jwt.decode(token, settings.supabase_jwt_secret, algorithms=["HS256"], audience="authenticated")
        else:
            if not settings.supabase_url:
                raise HTTPException(500, "SUPABASE_URL or SUPABASE_JWT_SECRET is needed to verify tokens")
            key = _jwks_client(f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json")
            signing_key = key.get_signing_key_from_jwt(token)
            claims = jwt.decode(token, signing_key.key, algorithms=["RS256", "ES256"], audience="authenticated")
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"invalid token: {exc}") from exc
    return claims["sub"]


def load_user(user_id: str) -> User:
    try:
        uid = uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(401, "user id is not a uuid") from exc
    with db.transaction() as conn:
        row = conn.execute("select id, name, role, vehicles from app_user where id = %s", (uid,)).fetchone()
    if row is None:
        raise HTTPException(403, "unknown user: add the user to app_user first")
    return User(str(row["id"]), row["name"], row["role"], tuple(row["vehicles"] or ()))


def current_user(authorization: str | None = Header(default=None),
                 x_user_id: str | None = Header(default=None)) -> User:
    mode = get_settings().auth_mode
    if mode == "dev":
        if not x_user_id:
            raise HTTPException(401, "X-User-Id header missing (AUTH_MODE=dev)")
        return load_user(x_user_id)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Authorization: Bearer <token> missing")
    return load_user(verify_token(authorization.split(" ", 1)[1]))


def require_pm(user: User, vehicle: str | None = None) -> None:
    if user.role not in ("pm", "admin"):
        raise HTTPException(403, "only a PM can do this")
    if not user.owns(vehicle):
        raise HTTPException(403, f"{vehicle} is not one of your lines")


def require_editor(user: User, vehicle: str | None = None) -> None:
    """PMs and engineers edit requirements."""
    if user.role not in ("pm", "engineer", "admin"):
        raise HTTPException(403, "only PMs and engineers can do this")
    if user.role == "pm" and not user.owns(vehicle):
        raise HTTPException(403, f"{vehicle} is not one of your lines")
