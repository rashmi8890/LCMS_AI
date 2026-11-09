
# import re
import unicodedata
from rank_bm25 import BM25Okapi

from ..models import Chunk, ChunkEmbedding
from .embedding_service import (
    cosine_similarity,
    embed_query,
    model_name,
    text_hash,
)


def tokenize(text: str) -> list[str]:
    """
    Tokenize Hindi, English, and mixed-language text.

    Keeps Unicode letters, numbers, and combining marks together.
    This preserves Devanagari characters such as matras and nukta.
    """
    if not text:
        return []

    tokens = []
    current_token = []

    for char in text.casefold():
        category = unicodedata.category(char)

        # L = Letter, N = Number, M = Combining Mark
        if category[0] in ("L", "N", "M") and (
            category[0] != "M" or current_token
        ):
            current_token.append(char)

        else:
            if current_token:
                tokens.append("".join(current_token))
                current_token = []

    if current_token:
        tokens.append("".join(current_token))

    return tokens

def rank_bm25_candidates(valid_pairs, query_tokens, bm25_scores):
    """
    Keep documents containing at least one query token.
    Rank them by BM25 score, including zero and negative scores.
    """
    query_token_set = set(query_tokens)

    candidates = [
        {
            "chunk": chunk,
            "score": float(score),
        }
        for (chunk, tokens), score in zip(valid_pairs, bm25_scores)
        if query_token_set.intersection(tokens)
    ]

    return sorted(
        candidates,
        key=lambda item: item["score"],
        reverse=True,
    )

def reciprocal_rank_fusion(
    semantic_results: list,
    bm25_results: list,
    k: int = 60,
) -> list:
    """Combine semantic and BM25 rankings using RRF."""

    fused = {}

    for results, source in (
        (semantic_results, "semantic"),
        (bm25_results, "bm25"),
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
        reverse=True,
    )


def hybrid_search_chunks(
    db,
    case_id: int,
    query: str,
    limit: int = 10,
):
    """
    Perform case-scoped hybrid retrieval.

    Combines:
    1. BM25 keyword retrieval
    2. Multilingual E5 semantic retrieval
    3. Reciprocal Rank Fusion

    Returns original Chunk objects so citations can retain
    their original database chunk IDs.
    """

    if not query or not query.strip():
        raise ValueError("Search query cannot be empty.")

    if limit < 1:
        raise ValueError("Search limit must be at least 1.")

    # 1. Load chunks strictly belonging to this case.
    chunks = (
        db.query(Chunk)
        .join(Chunk.document)
        .filter(Chunk.document.has(case_id=case_id))
        .order_by(Chunk.id)
        .all()
    )

    if not chunks:
        raise ValueError(
            "No chunks found. Process the case documents first."
        )

    # 2. Tokenize query and document corpus.
    query_tokens = tokenize(query)

    if not query_tokens:
        raise ValueError(
            "Query must contain searchable words or numbers."
        )

    valid_pairs = [
        (chunk, tokens)
        for chunk in chunks
        if (tokens := tokenize(chunk.text))
    ]

    if not valid_pairs:
        raise ValueError(
            "No searchable text found in this case."
        )

    # 3. BM25 retrieval.
    bm25 = BM25Okapi(
        [tokens for _, tokens in valid_pairs]
    )

    bm25_scores = bm25.get_scores(query_tokens)

    bm25_ranked = rank_bm25_candidates(
        valid_pairs,
        query_tokens,
        bm25_scores,
    )

    # 4. Load embeddings belonging only to this case.
    embedding_records = (
        db.query(ChunkEmbedding)
        .join(ChunkEmbedding.chunk)
        .join(Chunk.document)
        .filter(
            Chunk.document.has(case_id=case_id),
            ChunkEmbedding.model_name == model_name(),
        )
        .all()
    )

    # Keep only valid embeddings matching current chunk text.
    valid_embedding_records = [
        record
        for record in embedding_records
        if (
            record.text_hash == text_hash(record.chunk.text)
            and record.vector
            and record.dimension == len(record.vector)
        )
    ]

    # 5. Semantic retrieval.
    semantic_ranked = []

    if valid_embedding_records:
        query_vector = embed_query(query)

        semantic_ranked = sorted(
            [
                {
                    "chunk": record.chunk,
                    "score": cosine_similarity(
                        query_vector,
                        record.vector,
                    ),
                }
                for record in valid_embedding_records
            ],
            key=lambda item: item["score"],
            reverse=True,
        )
        
    # 6. Limit candidates before fusion.
    candidate_limit = min(
        len(chunks),
        max(limit * 3, 20),
    )

    semantic_candidates = [
        {
            **item,
            "rank": rank,
        }
        for rank, item in enumerate(
            semantic_ranked[:candidate_limit],
            start=1,
        )
    ]

    bm25_candidates = [
        {
            **item,
            "rank": rank,
        }
        for rank, item in enumerate(
            bm25_ranked[:candidate_limit],
            start=1,
        )
    ]

    # 7. Fuse rankings.
    fused_results = reciprocal_rank_fusion(
        semantic_candidates,
        bm25_candidates,
    )

    return {
        "case_id": case_id,
        "query": query,
        "retrieval_method": "hybrid_bm25_rrf",
        "embedding_model": model_name(),
        "total_case_chunks": len(chunks),
        "embedded_chunks": len(valid_embedding_records),
        "embedding_coverage": round(
            len(valid_embedding_records) / len(chunks), 4
        ),
        "semantic_candidates": len(semantic_candidates),
        "bm25_candidates": len(bm25_candidates),
        "results": fused_results[:limit],
    }
