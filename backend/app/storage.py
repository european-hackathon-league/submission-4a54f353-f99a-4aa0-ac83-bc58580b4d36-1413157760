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


class RawRecordConflict(RuntimeError):
    """raw_ref already holds different bytes. Raw evidence is immutable: use a
    content-addressed or versioned raw_ref (e.g. include sha256 or the fetch date)."""


def put(raw_ref: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """Store raw bytes under raw_ref, write-once, and return raw_ref.

    Same ref with identical bytes is accepted (idempotent re-runs); same ref
    with different bytes raises RawRecordConflict and never overwrites.
    """
    settings = get_settings()
    if settings.storage_backend == "supabase":
        bucket = _supabase()
        try:
            bucket.upload(raw_ref, data, {"content-type": content_type, "upsert": "false"})
        except Exception as upload_error:  # most likely "already exists": compare instead of overwriting
            try:
                existing = bucket.download(raw_ref)
            except Exception:
                raise upload_error
            _check_same(raw_ref, existing, data)
        return raw_ref
    path = _local_path(raw_ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "xb") as fh:  # exclusive create: never replaces an existing file
            fh.write(data)
    except FileExistsError:
        _check_same(raw_ref, path.read_bytes(), data)
    return raw_ref


def _check_same(raw_ref: str, existing: bytes, data: bytes) -> None:
    if existing != data:
        raise RawRecordConflict(
            f"raw_ref {raw_ref} already holds different bytes (sha256 {sha256(existing)[:12]} vs"
            f" {sha256(data)[:12]}); raw evidence is immutable, use a versioned raw_ref"
        )


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
