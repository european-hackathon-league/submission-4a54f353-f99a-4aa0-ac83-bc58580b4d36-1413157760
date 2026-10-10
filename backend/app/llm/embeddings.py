"""Text embeddings: OpenAI by default, a local hashed n-gram fallback offline.

The local provider (changes.md 1.2) is deterministic and needs no network. It
captures surface similarity only; similarity thresholds are tuned per provider.
"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol, Sequence

import numpy as np

from app.config import get_settings


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: Sequence[str]) -> np.ndarray: ...


class OpenAIEmbedder:
    def __init__(self) -> None:
        from openai import OpenAI

        from app.llm.client import LLMUnavailable

        s = get_settings()
        if not s.openai_api_key:
            raise LLMUnavailable("OPENAI_API_KEY is not set")
        self._client = OpenAI(api_key=s.openai_api_key, max_retries=5, timeout=120)
        self.model = s.embedding_model
        self.dim = s.embedding_dim
        self.name = f"openai:{self.model}"

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), 256):
            batch = [t if t.strip() else " " for t in texts[i : i + 256]]
            response = self._client.embeddings.create(model=self.model, input=batch, dimensions=self.dim)
            out.extend(d.embedding for d in response.data)
        return np.asarray(out, dtype=np.float32).reshape(len(texts), self.dim)


_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an the and or but of to in on for with is are was were be been it its this that i my we our "
    "they he she you me at by from as so very too not no".split()
)


class LocalEmbedder:
    """Hashed word unigrams + bigrams + character trigrams, L2-normalized."""

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or get_settings().embedding_dim
        self.name = f"local:hash{self.dim}"

    def _features(self, text: str) -> list[tuple[str, float]]:
        words = [w for w in _TOKEN.findall(text.lower()) if w not in _STOP]
        feats = [("w:" + w, 1.0) for w in words]
        feats += [("b:" + a + "_" + b, 0.7) for a, b in zip(words, words[1:])]
        for w in words:
            padded = f"#{w}#"
            feats += [("c:" + padded[i : i + 3], 0.3) for i in range(len(padded) - 2)]
        return feats

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feat, weight in self._features(text):
                h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "little")
                sign = 1.0 if (h >> 63) & 1 else -1.0
                matrix[row, h % self.dim] += sign * weight
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return matrix / norms


_override: Embedder | None = None


def set_embedder(embedder: Embedder | None) -> None:
    global _override
    _override = embedder


def get_embedder() -> Embedder:
    if _override is not None:
        return _override
    s = get_settings()
    if s.embedding_provider == "local" or not s.openai_api_key:
        return LocalEmbedder()
    return OpenAIEmbedder()


def as_np(value) -> np.ndarray:
    """pgvector returns Vector objects from the database; convert to float32 numpy."""
    if hasattr(value, "to_numpy"):
        value = value.to_numpy()
    return np.asarray(value, dtype=np.float32)


def as_matrix(values) -> np.ndarray:
    return np.vstack([as_np(v) for v in values]) if len(values) else np.zeros((0, 0), dtype=np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na and nb else 0.0
