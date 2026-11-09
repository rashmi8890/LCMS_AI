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
from ..models import ChunkEmbedding
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

def embed_chunks(db, chunks) -> dict:
    """
    Generate and persist embeddings for the supplied chunks.

    Skips chunks whose current text already has a valid embedding
    for the configured model.
    Commits each batch so completed batches survive a later failure.
    """
    if not chunks:
        return {
            "total_chunks": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "status": "complete",
        }

    model = model_name()
    created = updated = skipped = 0
    batch_size = max(1, settings.embedding_batch_size)

    try:
        for offset in range(0, len(chunks), batch_size):
            batch = chunks[offset:offset + batch_size]
            chunk_ids = [chunk.id for chunk in batch]

            records = (
                db.query(ChunkEmbedding)
                .filter(ChunkEmbedding.chunk_id.in_(chunk_ids))
                .all()
            )

            records_by_chunk = {
                record.chunk_id: record for record in records
            }

            pending = []

            for chunk in batch:
                digest = text_hash(chunk.text)
                record = records_by_chunk.get(chunk.id)

                if (
                    record
                    and record.model_name == model
                    and record.text_hash == digest
                    and record.vector
                    and record.dimension == len(record.vector)
                ):
                    skipped += 1
                    continue

                pending.append((chunk, record, digest))

            if not pending:
                continue

            vectors = embed_passages(
                [chunk.text for chunk, _, _ in pending]
            )

            if len(vectors) != len(pending):
                raise RuntimeError(
                    "Embedding count does not match pending chunk count."
                )

            for (chunk, record, digest), vector in zip(pending, vectors):
                if not vector:
                    raise RuntimeError(
                        f"Empty embedding generated for chunk {chunk.id}."
                    )

                if record is None:
                    record = ChunkEmbedding(chunk_id=chunk.id)
                    db.add(record)
                    created += 1
                else:
                    updated += 1

                record.model_name = model
                record.dimension = len(vector)
                record.vector = vector
                record.text_hash = digest

            db.commit()

        return {
            "total_chunks": len(chunks),
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "status": "complete",
        }

    except Exception:
        db.rollback()
        raise

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
