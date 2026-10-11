"""Download sources (price lists, KBA, IEA and other reports; steps 8 and 9).

These files are fetched by hand or by a script into
``backend/.data/downloads/<source_id>/`` (fetching-sources.md: access
"Download"). The adapter stores each PDF page or text file as a source record
with the file in raw storage, so the agents can extract signals from it.
"""

from __future__ import annotations

import hashlib
import time

from app import storage
from app.config import BACKEND_DIR
from app.fetch.adapters.base import FetchContext, FetchResult
from app.fetch.http import Response
from app.fetch.records import text_record

DOWNLOAD_DIR = BACKEND_DIR / ".data" / "downloads"


def _pdf_pages(path) -> list[str]:
    from app.seed.option_lists import open_pdf

    with open_pdf(path) as pdf:
        return [p.extract_text() or "" for p in pdf.pages]


def fetch_downloads(ctx: FetchContext, source: dict, subjects: list, cursor: dict) -> FetchResult:
    out = FetchResult()
    folder = DOWNLOAD_DIR / source["source_id"]
    if not folder.exists():
        out.notes.append(f"no files in {folder} (download them by hand, see fetching-sources.md)")
        return out
    seen = set(cursor.get("_files", []))
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in (".pdf", ".txt", ".html", ".md"):
            out.notes.append(f"{path.name}: unsupported file type, skipped")
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen and not ctx.backfill:
            continue
        seen.add(digest)
        raw_ref = f"{source['source_id']}/files/{digest}{path.suffix.lower()}"
        if not storage.exists(raw_ref):
            storage.put(raw_ref, data)
        response = Response(url=path.name, status=200, content=b"", content_type="", raw_ref=raw_ref,
                            fetched_at=time.time())
        pages = _pdf_pages(path) if path.suffix.lower() == ".pdf" else [data.decode("utf-8", errors="replace")]
        for i, text in enumerate(pages, start=1):
            if len(text.strip()) < 100:
                continue
            out.records.append(text_record(
                source=source, subject=None, record_id=f"{source['source_id']}-{digest[:16]}-p{i}",
                response=response, text=text, url=f"{path.name}#page={i}",
                category=(source.get("categories") or ["F"])[0], content_type="reference", level="market",
                title=f"{path.name}, page {i}",
                country=source["country"] if len(source.get("country") or "") == 2 else None, run_id=ctx.run_id))
    out.cursor["_files"] = sorted(seen)
    return out
