from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
# import re
# from rank_bm25 import BM25Okapi
from ..database import get_db
from ..models import Case, Chunk, ChunkEmbedding
from ..config import settings
from ..services.embedding_service import (
    cosine_similarity,
    embed_passages,
    embed_query,
    model_name,
    text_hash,
)
from ..services.retrieval_service import (
    hybrid_search_chunks,
    tokenize,
)

router = APIRouter(tags=["embeddings"])


class SemanticSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=10, ge=1, le=50)

class HybridSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=10, ge=1, le=50)

@router.get("/api/cases/{case_id}/embeddings/status")
def embedding_status(case_id: int, db: Session = Depends(get_db)):
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")

    chunks = (
        db.query(Chunk)
        .join(Chunk.document)
        .filter(Chunk.document.has(case_id=case_id))
        .all()
    )

    chunk_ids = [chunk.id for chunk in chunks]

    records = (
        db.query(ChunkEmbedding)
        .filter(ChunkEmbedding.chunk_id.in_(chunk_ids))
        .all()
    ) if chunk_ids else []

    records_by_chunk = {
        record.chunk_id: record for record in records
    }

    embedded = 0
    stale = 0

    for chunk in chunks:
        record = records_by_chunk.get(chunk.id)

        if record is None:
            continue

        is_valid = (
            record.model_name == model_name()
            and record.text_hash == text_hash(chunk.text)
            and bool(record.vector)
            and record.dimension == len(record.vector)
        )

        if is_valid:
            embedded += 1
        else:
            stale += 1

    total = len(chunks)
    pending = total - embedded
    missing = pending - stale

    return {
        "case_id": case_id,
        "model": model_name(),
        "total_chunks": total,
        "embedded_chunks": embedded,
        "pending_chunks": pending,
        "missing_embeddings": missing,
        "stale_chunks": stale,
        "ready": total > 0 and embedded == total,
    }

@router.post("/api/cases/{case_id}/embeddings/rebuild")
def rebuild_case_embeddings(case_id: int, db: Session = Depends(get_db)):
    """Create/update embeddings for this case only; source chunks are never modified."""
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")

    chunks = (
        db.query(Chunk).join(Chunk.document)
        .filter(Chunk.document.has(case_id=case_id))
        .order_by(Chunk.id).all()
    )
    if not chunks:
        raise HTTPException(400, "No chunks found. Process the case documents first.")

    model = model_name()
    created = updated = skipped = 0
    try:
        # Small batches bound RAM use and make progress commits recoverable.
        batch_size = max(1, settings.embedding_batch_size)
        for offset in range(0, len(chunks), batch_size):
            batch = chunks[offset:offset + batch_size]
            pending = []
            for chunk in batch:
                digest = text_hash(chunk.text)
                record = db.query(ChunkEmbedding).filter_by(chunk_id=chunk.id).first()
                if (record and record.model_name == model and record.text_hash == digest 
                    and record.vector and record.dimension == len(record.vector)
                ):
                    skipped += 1
                    continue
                pending.append((chunk, record, digest))

            vectors = embed_passages([chunk.text for chunk, _, _ in pending])
            if len(vectors) != len(pending):
                raise RuntimeError(
                    "Embedding count does not match pending chunk count."
                )
            for (chunk, record, digest), vector in zip(pending, vectors):
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

        # Verify every current chunk has a valid embedding.
        db.expire_all()

        verified_chunks = (
            db.query(Chunk)
            .join(Chunk.document)
            .filter(Chunk.document.has(case_id=case_id))
            .all()
        )

        verified_ids = [chunk.id for chunk in verified_chunks]

        verified_records = (
            db.query(ChunkEmbedding)
            .filter(
                ChunkEmbedding.chunk_id.in_(verified_ids),
                ChunkEmbedding.model_name == model,
            )
            .all()
        ) if verified_ids else []

        verified_by_chunk = {
            record.chunk_id: record for record in verified_records
        }

        valid_count = 0

        for chunk in verified_chunks:
            record = verified_by_chunk.get(chunk.id)

            if (
                record
                and record.text_hash == text_hash(chunk.text)
                and record.vector
                and record.dimension == len(record.vector)
            ):
                valid_count += 1

        if valid_count != len(verified_chunks):
            raise RuntimeError(
                f"Embedding verification failed: "
                f"{valid_count}/{len(verified_chunks)} chunks have valid embeddings."
            )

        return {
            "case_id": case_id,
            "model": model,
            "total_chunks": len(verified_chunks),
            "created": created,
            "updated": updated,
            "skipped_unchanged": skipped,
            "verified_embeddings": valid_count,
            "status": "complete",
        }
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, f"Embedding generation failed: {exc}") from exc

@router.post("/api/cases/{case_id}/embeddings/search")
def semantic_search(
    case_id: int,
    payload: SemanticSearchRequest,
    db: Session = Depends(get_db)
):
    """Semantic search scoped strictly to chunks belonging to the requested case."""

    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")

    records = (
        db.query(ChunkEmbedding)
        .join(ChunkEmbedding.chunk)
        .join(Chunk.document)
        .filter(
            Chunk.document.has(case_id=case_id),
            ChunkEmbedding.model_name == model_name()
        )
        .all()
    )

    # Keep only embeddings that match the current chunk text.
    valid_records = [
        record
        for record in records
        if (
            record.text_hash == text_hash(record.chunk.text)
            and record.vector
            and record.dimension == len(record.vector)
        )
    ]

    if not valid_records:
        raise HTTPException(
            409,
            "This case has no embeddings. Run the rebuild endpoint first."
        )

    query_vector = embed_query(payload.query)

    ranked = sorted(
        (
            (cosine_similarity(query_vector, record.vector), record.chunk)
            for record in valid_records
        ),
        key=lambda item: item[0],
        reverse=True
    )

    # Apply minimum similarity threshold
    min_score = settings.embedding_min_score

    filtered_results = [
        (score, chunk)
        for score, chunk in ranked
        if score >= min_score
    ][:payload.limit]

    return {
        "case_id": case_id,
        "model": model_name(),
        "query": payload.query,
        "results": [
            {
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "document_name": chunk.document.filename,
                "page_number": chunk.page_number,
                "chunk_index": chunk.chunk_index,
                "score": round(score, 6),
                "text": chunk.text
            }
            for score, chunk in filtered_results
        ]
    }

@router.post("/api/cases/{case_id}/embeddings/hybrid-search")
def hybrid_search(
    case_id: int,
    payload: HybridSearchRequest,
    db: Session = Depends(get_db),
):
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")

    if not tokenize(payload.query):
        raise HTTPException(
            422,
            "Query must contain searchable words or numbers.",
        )

    try:
        result = hybrid_search_chunks(
            db=db,
            case_id=case_id,
            query=payload.query,
            limit=payload.limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    return {
        **{
            key: value
            for key, value in result.items()
            if key != "results"
        },
        "results": [
            {
                "chunk_id": item["chunk"].id,
                "document_id": item["chunk"].document_id,
                "document_name": item["chunk"].document.filename,
                "page_number": item["chunk"].page_number,
                "chunk_index": item["chunk"].chunk_index,
                "semantic_score": (
                    round(item["semantic_score"], 6)
                    if item["semantic_score"] is not None
                    else None
                ),
                "bm25_score": (
                    round(item["bm25_score"], 6)
                    if item["bm25_score"] is not None
                    else None
                ),
                "semantic_rank": item["semantic_rank"],
                "bm25_rank": item["bm25_rank"],
                "rrf_score": round(item["rrf_score"], 8),
                "text": item["chunk"].text,
            }
            for item in result["results"]
        ],
    }