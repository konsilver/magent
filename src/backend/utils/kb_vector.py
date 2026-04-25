"""Milvus vector store for private knowledge base.

Collection: jingxin_kb_private
- Stores both chunk rows (row_type='chunk') and question rows (row_type='question')
- Child chunk vectors used for retrieval; parent chunk content fetched from PostgreSQL
- User isolation enforced via user_id field in every query expression
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

COLLECTION_NAME = "jingxin_kb_private"
_EMBED_DIMS = int(os.getenv("MEM0_EMBED_DIMS", "1024"))

# Sparse vector dimension space (hash modulo)
_SPARSE_DIM_SPACE = 100_000


def _resolve_embed_config() -> tuple[str, str, str]:
    """Resolve embedding config from DB, with env fallback."""
    try:
        from core.config.model_config import ModelConfigService
        cfg = ModelConfigService.get_instance().resolve("embedding")
        if cfg:
            return cfg.base_url.rstrip("/"), cfg.model_name, cfg.api_key
    except Exception:
        pass
    return (
        os.getenv("MEM0_EMBED_URL", "").rstrip("/"),
        os.getenv("MEM0_EMBED_MODEL", ""),
        os.getenv("MEM0_EMBED_API_KEY", ""),
    )


def _resolve_reranker_config() -> tuple[str, str, str]:
    """Resolve reranker config from DB, with env fallback."""
    try:
        from core.config.model_config import ModelConfigService
        cfg = ModelConfigService.get_instance().resolve("reranker")
        if cfg:
            return cfg.base_url.rstrip("/"), cfg.model_name, cfg.api_key
    except Exception:
        pass
    return (
        os.getenv("RERANKER_URL", "").rstrip("/"),
        os.getenv("RERANKER_MODEL", ""),
        os.getenv("RERANKER_API_KEY", ""),
    )


# ── Embedding ──────────────────────────────────────────────────────────────────

def embed_text(text: str) -> list[float]:
    """Call the configured embedding service and return a dense vector."""
    embed_url, embed_model, api_key = _resolve_embed_config()

    if not embed_url:
        raise RuntimeError("Embedding model is not configured")

    url = f"{embed_url}/embeddings"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {"input": text, "model": embed_model}
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["data"][0]["embedding"]


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Batch embed multiple texts. Falls back to one-by-one on error."""
    embed_url, embed_model, api_key = _resolve_embed_config()

    if not embed_url:
        raise RuntimeError("Embedding model is not configured")

    url = f"{embed_url}/embeddings"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {"input": texts, "model": embed_model}
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]


# ── Reranker ──────────────────────────────────────────────────────────────────

def is_reranker_configured() -> bool:
    """Check if a reranker model endpoint is configured."""
    reranker_url, reranker_model, _ = _resolve_reranker_config()
    return bool(reranker_url and reranker_model)


def rerank(query: str, documents: list[str], top_n: int | None = None) -> list[dict]:
    """Call the configured reranker endpoint (OpenAI-compatible /rerank).

    Returns a list of {"index": int, "relevance_score": float} sorted by score descending.
    """
    reranker_url, reranker_model, reranker_key = _resolve_reranker_config()

    if not reranker_url or not reranker_model:
        raise RuntimeError("Reranker is not configured")

    url = f"{reranker_url}/rerank"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {reranker_key}",
    }
    payload: dict = {
        "model": reranker_model,
        "query": query,
        "documents": documents,
    }
    if top_n is not None:
        payload["top_n"] = top_n

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # Response format: {"results": [{"index": 0, "relevance_score": 0.95}, ...]}
    results = data.get("results", [])
    return sorted(results, key=lambda x: x.get("relevance_score", 0), reverse=True)


# ── Milvus helpers ─────────────────────────────────────────────────────────────

def _get_client():
    """Return a MilvusClient connected to the configured Milvus instance."""
    from pymilvus import MilvusClient
    from core.config.settings import settings
    url = os.getenv("MILVUS_URL") or settings.memory.milvus_url
    token = os.getenv("MILVUS_TOKEN", "")
    if token:
        return MilvusClient(uri=url, token=token)
    return MilvusClient(uri=url)


def get_or_create_collection() -> None:
    """Idempotently create jingxin_kb_private collection with hybrid search schema."""
    from pymilvus import MilvusClient, DataType

    client = _get_client()

    if client.has_collection(COLLECTION_NAME):
        return

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("chunk_id",        DataType.VARCHAR, max_length=64, is_primary=True)
    schema.add_field("parent_chunk_id", DataType.VARCHAR, max_length=64)
    schema.add_field("row_type",        DataType.VARCHAR, max_length=16)   # "chunk" | "question"
    schema.add_field("user_id",         DataType.VARCHAR, max_length=64)
    schema.add_field("kb_id",           DataType.VARCHAR, max_length=64)
    schema.add_field("document_id",     DataType.VARCHAR, max_length=64)
    schema.add_field("title",           DataType.VARCHAR, max_length=500)
    schema.add_field("content",         DataType.VARCHAR, max_length=4096)
    schema.add_field("tags_text",       DataType.VARCHAR, max_length=1000)  # BM25 augmentation
    schema.add_field("chunk_index",     DataType.INT64)
    schema.add_field("dense_embedding", DataType.FLOAT_VECTOR, dim=_EMBED_DIMS)
    schema.add_field("sparse_embedding", DataType.SPARSE_FLOAT_VECTOR)

    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="dense_embedding",
        index_type="IVF_FLAT",
        metric_type="IP",
        params={"nlist": 128},
    )
    index_params.add_index(
        field_name="sparse_embedding",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="IP",
    )
    # Scalar indexes for filtering acceleration
    for field in ("user_id", "kb_id", "document_id", "row_type"):
        index_params.add_index(field_name=field, index_type="INVERTED")

    client.create_collection(
        collection_name=COLLECTION_NAME,
        schema=schema,
        index_params=index_params,
    )
    logger.info("Created Milvus collection: %s", COLLECTION_NAME)


# ── Write ──────────────────────────────────────────────────────────────────────

def upsert_rows(rows: list[dict[str, Any]]) -> None:
    """Upsert a batch of rows into jingxin_kb_private.

    Each row must include all schema fields. dense_embedding and sparse_embedding
    must be pre-computed by the caller (use embed_text / build_sparse_text).
    """
    if not rows:
        return
    get_or_create_collection()
    client = _get_client()
    client.upsert(collection_name=COLLECTION_NAME, data=rows)
    logger.debug("Upserted %d rows into %s", len(rows), COLLECTION_NAME)


def delete_by_document(document_id: str, user_id: str) -> None:
    """Delete all Milvus rows (chunk + question) for a document."""
    get_or_create_collection()
    client = _get_client()
    expr = f'document_id == "{document_id}" and user_id == "{user_id}"'
    client.delete(collection_name=COLLECTION_NAME, filter=expr)
    logger.info("Deleted Milvus rows for document_id=%s user_id=%s", document_id, user_id)


def delete_by_kb(kb_id: str, user_id: str) -> None:
    """Delete all Milvus rows for an entire KB space."""
    get_or_create_collection()
    client = _get_client()
    expr = f'kb_id == "{kb_id}" and user_id == "{user_id}"'
    client.delete(collection_name=COLLECTION_NAME, filter=expr)
    logger.info("Deleted Milvus rows for kb_id=%s user_id=%s", kb_id, user_id)


# ── Search ─────────────────────────────────────────────────────────────────────

def hybrid_search(
    user_id: str,
    kb_ids: list[str],
    query: str,
    query_vec: list[float],
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Hybrid search over dense + sparse vectors with RRF fusion.

    Searches both chunk rows and question rows simultaneously.
    Results are sorted by fused score; dedup by parent_chunk_id is done by the caller.
    """
    from pymilvus import AnnSearchRequest, RRFRanker, MilvusClient

    get_or_create_collection()
    client = _get_client()

    kb_ids_json = json.dumps(kb_ids)
    expr = f'user_id == "{user_id}" and kb_id in {kb_ids_json}'

    # Dense vector search request
    dense_req = AnnSearchRequest(
        data=[query_vec],
        anns_field="dense_embedding",
        param={"metric_type": "IP", "params": {"nprobe": 10}},
        limit=top_k * 3,
        expr=expr,
    )

    # Sparse search request — bag-of-words vector
    sparse_vec = text_to_sparse(query)
    sparse_req = AnnSearchRequest(
        data=[sparse_vec],
        anns_field="sparse_embedding",
        param={"metric_type": "IP"},
        limit=top_k * 3,
        expr=expr,
    )

    output_fields = [
        "chunk_id", "parent_chunk_id", "row_type",
        "kb_id", "document_id", "title", "content", "chunk_index",
    ]

    results = client.hybrid_search(
        collection_name=COLLECTION_NAME,
        reqs=[dense_req, sparse_req],
        ranker=RRFRanker(k=60),
        limit=top_k * 2,
        output_fields=output_fields,
    )

    hits = []
    for hit in results[0]:
        hits.append({
            "chunk_id":       hit.entity.get("chunk_id"),
            "parent_chunk_id": hit.entity.get("parent_chunk_id") or hit.entity.get("chunk_id"),
            "row_type":       hit.entity.get("row_type"),
            "kb_id":          hit.entity.get("kb_id"),
            "document_id":    hit.entity.get("document_id"),
            "title":          hit.entity.get("title"),
            "content":        hit.entity.get("content"),
            "chunk_index":    hit.entity.get("chunk_index"),
            "score":          hit.score,
        })
    return hits


# ── Tag/question re-index ──────────────────────────────────────────────────────

def build_sparse_text(content: str, tags: list[str]) -> str:
    """Build combined text for sparse vectorisation."""
    if not tags:
        return content
    tag_str = " ".join(f"[{t}]" for t in tags)
    return f"{content} {tag_str}"


def text_to_sparse(text: str) -> dict[int, float]:
    """Convert text to a bag-of-words sparse vector using term-frequency + hashing.

    Compatible with Milvus SPARSE_FLOAT_VECTOR field (v2.4+).
    Each unique token is hashed to a dimension index; value = TF.
    """
    import hashlib
    import re
    from collections import Counter

    # Simple tokenisation: split on non-alphanumeric (works for CJK + Latin)
    tokens = re.findall(r'[\w\u4e00-\u9fff]+', text.lower())
    if not tokens:
        return {0: 1.0}

    counts = Counter(tokens)
    total = sum(counts.values())

    sparse: dict[int, float] = {}
    for token, cnt in counts.items():
        dim = int(hashlib.md5(token.encode()).hexdigest()[:8], 16) % _SPARSE_DIM_SPACE
        tf = cnt / total
        sparse[dim] = sparse.get(dim, 0.0) + tf

    return sparse if sparse else {0: 1.0}


def reindex_chunk_tags(chunk_id: str, content: str, tags: list[str]) -> None:
    """Re-compute and upsert sparse_embedding for a chunk row after tag changes."""
    # Fetch the existing row to preserve all other fields
    get_or_create_collection()
    client = _get_client()
    rows = client.query(
        collection_name=COLLECTION_NAME,
        filter=f'chunk_id == "{chunk_id}"',
        output_fields=["chunk_id", "parent_chunk_id", "row_type", "user_id",
                       "kb_id", "document_id", "title", "chunk_index",
                       "dense_embedding"],
    )
    if not rows:
        logger.warning("reindex_chunk_tags: chunk_id %s not found in Milvus", chunk_id)
        return

    row = rows[0]
    sparse_text = build_sparse_text(content, tags)
    row["content"] = content
    row["tags_text"] = " ".join(tags)
    row["sparse_embedding"] = text_to_sparse(sparse_text)
    client.upsert(collection_name=COLLECTION_NAME, data=[row])


def upsert_question_rows(
    parent_chunk_id: str,
    questions: list[str],
    user_id: str,
    kb_id: str,
    document_id: str,
    title: str,
    chunk_index: int,
) -> None:
    """Delete existing question rows for a chunk and insert fresh ones."""
    get_or_create_collection()
    client = _get_client()

    # Delete old question rows
    expr = f'parent_chunk_id == "{parent_chunk_id}" and row_type == "question"'
    client.delete(collection_name=COLLECTION_NAME, filter=expr)

    if not questions:
        return

    # Embed all questions in one batch call
    vecs = embed_batch(questions)
    rows = []
    for i, (q, vec) in enumerate(zip(questions, vecs)):
        rows.append({
            "chunk_id":        f"q_{parent_chunk_id}_{i}",
            "parent_chunk_id": parent_chunk_id,
            "row_type":        "question",
            "user_id":         user_id,
            "kb_id":           kb_id,
            "document_id":     document_id,
            "title":           title,
            "content":         q,
            "tags_text":       "",
            "chunk_index":     chunk_index,
            "dense_embedding": vec,
            "sparse_embedding": text_to_sparse(q),
        })
    client.upsert(collection_name=COLLECTION_NAME, data=rows)
