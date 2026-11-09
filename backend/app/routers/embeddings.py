from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import re
from rank_bm25 import BM25Okapi
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

router = APIRouter(tags=["embeddings"])


class SemanticSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=10, ge=1, le=50)

class HybridSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=10, ge=1, le=50)

def tokenize(text: str) -> list[str]:
    """
    Unicode-aware tokenizer for English and Hindi.
    Converts text to lowercase and extracts words/numbers.
    """
    return re.findall(
        r"[^\W_]+",
        (text or "").casefold(),
        flags=re.UNICODE
    )


def reciprocal_rank_fusion(
    semantic_results: list,
    bm25_results: list,
    k: int = 60
) -> list:
    """
    Merge semantic and BM25 rankings using RRF.

    Each result contains:
        chunk: Chunk object
        rank: 1-based rank
        score: original retriever score
    """
    fused = {}

    for results, source in (
        (semantic_results, "semantic"),
        (bm25_results, "bm25")
    ):
        for item in results:
            chunk = item["chunk"]
            chunk_id = chunk.id

            if chunk_id not in fused:
                fused[chunk_id] = {
                    "chunk": chunk,
                    "rrf_score": 0.0,
                    "semantic_score": None,
                    "bm25_score": None,
                    "semantic_rank": None,
                    "bm25_rank": None,
                }

            fused[chunk_id]["rrf_score"] += (
                1.0 / (k + item["rank"])
            )

            fused[chunk_id][f"{source}_score"] = item["score"]
            fused[chunk_id][f"{source}_rank"] = item["rank"]

    return sorted(
        fused.values(),
        key=lambda item: item["rrf_score"],
        reverse=True
    )

@router.get("/api/cases/{case_id}/embeddings/status")
def embedding_status(case_id: int, db: Session = Depends(get_db)):
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")

    total = (
        db.query(Chunk).join(Chunk.document)
        .filter(Chunk.document.has(case_id=case_id)).count()
    )
    embedded = (
        db.query(ChunkEmbedding).join(ChunkEmbedding.chunk).join(Chunk.document)
        .filter(Chunk.document.has(case_id=case_id),
                ChunkEmbedding.model_name == model_name()).count()
    )
    return {
        "case_id": case_id,
        "model": model_name(),
        "total_chunks": total,
        "embedded_chunks": embedded,
        "pending_chunks": max(total - embedded, 0),
        "ready": total > 0 and total == embedded,
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
                if record and record.model_name == model and record.text_hash == digest:
                    skipped += 1
                    continue
                pending.append((chunk, record, digest))

            vectors = embed_passages([chunk.text for chunk, _, _ in pending])
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

        return {
            "case_id": case_id,
            "model": model,
            "total_chunks": len(chunks),
            "created": created,
            "updated": updated,
            "skipped_unchanged": skipped,
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

    if not records:
        raise HTTPException(
            409,
            "This case has no embeddings. Run the rebuild endpoint first."
        )

    query_vector = embed_query(payload.query)

    ranked = sorted(
        (
            (cosine_similarity(query_vector, record.vector), record.chunk)
            for record in records
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
    db: Session = Depends(get_db)
):
    """
    Experimental hybrid retrieval:
    BM25 keyword search + E5 semantic search + RRF fusion.

    Results are strictly scoped to the requested case.
    """

    # 1. Validate case
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")

    # 2. Load chunks belonging only to this case
    chunks = (
        db.query(Chunk)
        .join(Chunk.document)
        .filter(Chunk.document.has(case_id=case_id))
        .order_by(Chunk.id)
        .all()
    )

    if not chunks:
        raise HTTPException(
            400,
            "No chunks found. Process the case documents first."
        )

    # 3. Tokenize query and corpus
    query_tokens = tokenize(payload.query)

    if not query_tokens:
        raise HTTPException(
            422,
            "Query must contain searchable words or numbers."
        )

    tokenized_corpus = [
        tokenize(chunk.text)
        for chunk in chunks
    ]

    # Ignore chunks with no searchable tokens
    valid_pairs = [
        (chunk, tokens)
        for chunk, tokens in zip(chunks, tokenized_corpus)
        if tokens
    ]

    if not valid_pairs:
        raise HTTPException(
            400,
            "No searchable text found in this case."
        )

    # 4. BM25 retrieval
    bm25 = BM25Okapi(
        [tokens for _, tokens in valid_pairs]
    )

    bm25_scores = bm25.get_scores(query_tokens)

    bm25_ranked = sorted(
        [
            {
                "chunk": chunk,
                "score": float(score)
            }
            for (chunk, _), score in zip(valid_pairs, bm25_scores)
            if score > 0
        ],
        key=lambda item: item["score"],
        reverse=True
    )

    # 5. Generate semantic candidates
    # No 0.76 threshold here:
    # semantic and BM25 scores are not comparable.
    embedding_records = (
        db.query(ChunkEmbedding)
        .join(ChunkEmbedding.chunk)
        .join(Chunk.document)
        .filter(
            Chunk.document.has(case_id=case_id),
            ChunkEmbedding.model_name == model_name()
        )
        .all()
    )

    semantic_ranked = []

    if embedding_records:
        query_vector = embed_query(payload.query)

        semantic_ranked = sorted(
            [
                {
                    "chunk": record.chunk,
                    "score": cosine_similarity(
                        query_vector,
                        record.vector
                    )
                }
                for record in embedding_records
            ],
            key=lambda item: item["score"],
            reverse=True
        )

    # 6. Limit candidate lists before fusion
    candidate_limit = min(
        len(chunks),
        max(payload.limit * 3, 20)
    )

    semantic_candidates = [
        {
            "chunk": item["chunk"],
            "score": item["score"],
            "rank": rank
        }
        for rank, item in enumerate(
            semantic_ranked[:candidate_limit],
            start=1
        )
    ]

    bm25_candidates = [
        {
            "chunk": item["chunk"],
            "score": item["score"],
            "rank": rank
        }
        for rank, item in enumerate(
            bm25_ranked[:candidate_limit],
            start=1
        )
    ]

    # 7. Fuse rankings using RRF
    fused_results = reciprocal_rank_fusion(
        semantic_candidates,
        bm25_candidates
    )

    final_results = fused_results[:payload.limit]

    # 8. Return results with source information
    return {
        "case_id": case_id,
        "query": payload.query,
        "retrieval_method": "hybrid_bm25_rrf",
        "embedding_model": model_name(),
        "total_case_chunks": len(chunks),
        "semantic_candidates": len(semantic_candidates),
        "bm25_candidates": len(bm25_candidates),
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
                "text": item["chunk"].text
            }
            for item in final_results
        ]
    }
