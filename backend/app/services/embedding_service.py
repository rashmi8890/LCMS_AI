"""Multilingual embedding generation for LCMS Phase 3.

Uses intfloat/multilingual-e5-base. E5 requires role prefixes:
- source chunks: passage: ...
- user search strings: query: ...
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Sequence

import numpy as np
from sentence_transformers import SentenceTransformer

from ..config import settings


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    # Lazy loading prevents model download/loading during FastAPI startup.
    return SentenceTransformer(settings.embedding_model, device=settings.embedding_device)


def model_name() -> str:
    return settings.embedding_model


def text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def embed_passages(texts: Sequence[str]) -> list[list[float]]:
    if not texts:
        return []
    prepared = [f"passage: {text}" for text in texts]
    vectors = _get_model().encode(
        prepared,
        batch_size=settings.embedding_batch_size,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    return np.asarray(vectors, dtype=np.float32).tolist()


def embed_query(query: str) -> list[float]:
    if not query or not query.strip():
        raise ValueError("Search query cannot be empty.")
    vector = _get_model().encode(
        [f"query: {query.strip()}"],
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )[0]
    return np.asarray(vector, dtype=np.float32).tolist()


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    # Vectors are normalized on creation; dot product is cosine similarity.
    a = np.asarray(left, dtype=np.float32)
    b = np.asarray(right, dtype=np.float32)
    if a.shape != b.shape:
        raise ValueError(f"Embedding dimension mismatch: {a.shape} vs {b.shape}")
    return float(np.dot(a, b))
