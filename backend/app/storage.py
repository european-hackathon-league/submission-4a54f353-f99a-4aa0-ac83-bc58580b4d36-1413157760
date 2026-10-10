"""Raw record storage: the untouched response or file behind every record.

``raw_ref`` is a relative path (e.g. ``reddit/2026-10-10/abc.json``) that is the
same in Supabase Storage (bucket raw-records) and in the local fallback folder.
"""

import hashlib
from pathlib import Path

from app.config import get_settings


def _local_path(raw_ref: str) -> Path:
    root = get_settings().raw_local_dir
    path = (root / raw_ref).resolve()
    if root.resolve() not in path.parents:
        raise ValueError(f"raw_ref escapes the storage root: {raw_ref}")
    return path


def _supabase():
    from supabase import create_client

    s = get_settings()
    return create_client(s.supabase_url, s.supabase_service_role_key).storage.from_(s.raw_bucket)


def put(raw_ref: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """Store raw bytes under raw_ref (idempotent) and return raw_ref."""
    settings = get_settings()
    if settings.storage_backend == "supabase":
        _supabase().upload(raw_ref, data, {"content-type": content_type, "upsert": "true"})
    else:
        path = _local_path(raw_ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return raw_ref


def get(raw_ref: str) -> bytes:
    if get_settings().storage_backend == "supabase":
        return _supabase().download(raw_ref)
    return _local_path(raw_ref).read_bytes()


def exists(raw_ref: str) -> bool:
    if get_settings().storage_backend == "supabase":
        try:
            _supabase().download(raw_ref)
            return True
        except Exception:
            return False
    return _local_path(raw_ref).exists()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
