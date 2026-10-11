"""The API on the LOCAL database, offline: python -m app.dev_local [port]

For wiring and testing the UI without touching Supabase or OpenAI, whatever
backend/.env says: embedded Postgres (backend/.data/pg), local raw records, dev
auth (X-User-Id), no LLM (template texts, extractive assistant), local embeddings.
"""

import os
import sys

for key, value in {"SUPABASE_DB_URL": "", "SUPABASE_URL": "", "STORAGE_BACKEND": "local", "AUTH_MODE": "dev",
                   "OPENAI_API_KEY": "", "EMBEDDING_PROVIDER": "local"}.items():
    os.environ[key] = value

if __name__ == "__main__":
    import uvicorn

    from app.config import get_settings

    s = get_settings()
    assert not s.db_url and not s.has_openai, "refusing to start: Supabase or OpenAI is still configured"
    uvicorn.run("app.main:app", host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8000)
