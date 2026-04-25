"""Implementation for MCP tools: retrieve_dataset_content & retrieve_local_kb."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

# Import safe stream writer from common utilities
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from _common import safe_stream_writer

from utils.dify_kb import get_allowed_dataset_ids
from utils.helpers import clean_retrieve_document, truncate_records_by_tokens

load_dotenv()


def _read_int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        return default


MAX_RETRIEVE_TOKENS = _read_int_env("RETRIEVE_DATASET_TOKEN_LIMIT", 50_000)


def _resolve_dify_config() -> tuple[str, str]:
    base_url = (
        os.getenv("DIFY_URL")
        or os.getenv("DIFY_BASE_URL")
        or ""
    ).strip().rstrip("/")
    auth_token = (
        os.getenv("DIFY_API_KEY")
        or os.getenv("DIFY_AUTH_TOKEN")
        or ""
    ).strip()
    return base_url, auth_token


def _normalize_token_field(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize record token key so token truncation is effective."""
    normalized: List[Dict[str, Any]] = []
    for record in records:
        item = dict(record)
        token_val = item.get("tokens", item.get("token", 0))
        try:
            item["tokens"] = int(token_val or 0)
        except (TypeError, ValueError):
            item["tokens"] = 0
        normalized.append(item)
    return normalized


def _retrieve_single_dataset(
    dataset_id: str,
    query: str,
    top_k: int,
    score_threshold: float,
    search_method: str,
    reranking_enable: bool,
    weights: float,
    base_url: str,
    headers: dict,
    writer,
) -> List[Dict[str, Any]]:
    """Retrieve from a single Dify dataset. Returns cleaned records list."""
    url = f"{base_url}/datasets/{dataset_id}/retrieve"
    payload = {
        "query": query,
        "retrieval_model": {
            "search_method": search_method,
            "reranking_enable": reranking_enable,
            "top_k": top_k,
            "score_threshold_enabled": True,
            "score_threshold": score_threshold,
            "weights": weights,
        },
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        records = data.get("records", [])
        cleaned = clean_retrieve_document(records)
        # If strict threshold yields no results, retry once with threshold disabled.
        if not cleaned and score_threshold > 0:
            retry_payload = dict(payload)
            retrieval_model = dict(retry_payload.get("retrieval_model", {}))
            retrieval_model["score_threshold_enabled"] = False
            retry_payload["retrieval_model"] = retrieval_model
            retry_resp = requests.post(url, headers=headers, json=retry_payload, timeout=10)
            retry_resp.raise_for_status()
            retry_data = retry_resp.json()
            retry_records = retry_data.get("records", [])
            cleaned = clean_retrieve_document(retry_records)
        # 附上 dataset_id，供前端调用详情接口
        for item in cleaned:
            item["dataset_id"] = dataset_id
        return cleaned
    except Exception as exc:
        writer(f"⚠️ 数据集 {dataset_id} 查询失败: {exc}\n")
        return []


def retrieve_dataset_content(
    query: str,
    dataset_id: str = "",
    top_k: int = 10,
    score_threshold: float = 0.4,
    search_method: str = "hybrid_search",
    reranking_enable: bool = False,
    weights: float = 0.6,
    *,
    allowed_dataset_ids: str | None = None,
) -> List[Dict[str, Any]]:
    writer = safe_stream_writer()
    writer(f"正在通过知识库搜索{query}的结果...\n")

    base_url, auth_token = _resolve_dify_config()
    if not base_url or not auth_token:
        writer("❌ 知识库工具配置缺失：请设置 DIFY_URL 与 DIFY_API_KEY\n")
        return [
            {
                "error": "retrieve_dataset_content 配置缺失",
                "hint": "请设置 DIFY_URL 与 DIFY_API_KEY",
            }
        ]

    headers = {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}

    # Resolve allowed set from header (HTTP mode) or env var
    if allowed_dataset_ids is not None:
        _allowed_set = {x.strip() for x in allowed_dataset_ids.split(",") if x.strip()}
    else:
        _allowed_set = get_allowed_dataset_ids()

    # If no allowed set configured, fetch all from Dify
    if not _allowed_set:
        try:
            from utils.dify_kb import list_datasets
            _allowed_set = {str(ds.get("id", "")).strip() for ds in s(timeout=5) if ds.get("id")}
        except Exception:
            pass

    if not _allowed_set:
        writer("❌ 没有可用的知识库数据集\n")
        return []

    # If user specified a dataset_id, only search that one (with validation)
    specified_id = (dataset_id or "").strip()
    if specified_id:
        if _allowed_set and specified_id not in _allowed_set:
            writer(f"❌ dataset_id {specified_id} 不在允许列表中\n")
            return []
        target_ids = [specified_id]
        writer(f"ℹ️ 搜索指定数据集: {specified_id}\n")
    else:
        # Default: search ALL allowed datasets
        target_ids = sorted(_allowed_set)
        writer(f"ℹ️ 正在搜索全部 {len(target_ids)} 个数据集...\n")

    all_cleaned: List[Dict[str, Any]] = []
    for ds_id in target_ids:
        items = _retrieve_single_dataset(
            dataset_id=ds_id,
            query=query,
            top_k=top_k,
            score_threshold=score_threshold,
            search_method=search_method,
            reranking_enable=reranking_enable,
            weights=weights,
            base_url=base_url,
            headers=headers,
            writer=writer,
        )
        all_cleaned.extend(items)

    if all_cleaned:
        # Sort by score descending, keep top_k
        for item in all_cleaned:
            if "score" not in item:
                item["score"] = 0.0
        all_cleaned.sort(key=lambda x: x.get("score", 0), reverse=True)
        all_cleaned = all_cleaned[:top_k]

        all_cleaned = _normalize_token_field(all_cleaned)
        all_cleaned = truncate_records_by_tokens(
            all_cleaned,
            token_threshold=MAX_RETRIEVE_TOKENS,
            writer=writer,
        )
        writer(f"✅ 从知识库找到 {len(all_cleaned)} 条相关记录\n")
    else:
        writer("⚠️ 知识库未找到相关内容\n")
    return all_cleaned


# ── List all datasets ─────────────────────────────────────────────────────────

_list_logger = logging.getLogger(__name__ + ".list_datasets")


def list_all_datasets(
    *,
    allowed_dataset_ids: str | None = None,
    allowed_kb_ids: str | None = None,
    current_user_id: str | None = None,
) -> Dict[str, Any]:
    """List all available public and private knowledge bases with document names."""
    public_datasets: List[Dict[str, Any]] = []
    private_datasets: List[Dict[str, Any]] = []

    # ── Public datasets (Dify) ────────────────────────────────────────────────
    try:
        from utils.dify_kb import is_dify_enabled, list_datasets as dify_list, list_documents as dify_list_docs

        if is_dify_enabled():
            if allowed_dataset_ids is not None:
                _allowed_set = {x.strip() for x in allowed_dataset_ids.split(",") if x.strip()}
            else:
                _allowed_set = get_allowed_dataset_ids()

            datasets = dify_list(page=1, limit=100, timeout=(2, 5))
            for ds in datasets:
                ds_id = str(ds.get("id", "")).strip()
                if not ds_id:
                    continue
                if _allowed_set and ds_id not in _allowed_set:
                    continue

                name = ds.get("name", ds_id)
                desc = ds.get("description") or ds.get("desc") or ""
                doc_count = ds.get("document_count", 0)

                # Fetch document titles (up to 20)
                doc_titles: List[str] = []
                try:
                    docs_result = dify_list_docs(ds_id, page=1, limit=20)
                    for doc in docs_result.get("items", []):
                        title = doc.get("title", "").strip()
                        if title:
                            doc_titles.append(title)
                except Exception as exc:
                    _list_logger.debug("Failed to list docs for dataset %s: %s", ds_id, exc)

                public_datasets.append({
                    "dataset_id": ds_id,
                    "name": name,
                    "description": desc,
                    "document_count": doc_count,
                    "document_titles": doc_titles,
                    "type": "public",
                })
    except Exception as exc:
        _list_logger.warning("Failed to list public datasets: %s", exc)

    # ── Private datasets (local KB) ───────────────────────────────────────────
    try:
        from core.db.engine import SessionLocal
        from core.db.models import KBSpace, KBDocument

        if allowed_kb_ids is not None:
            allowed = {k.strip() for k in allowed_kb_ids.split(",") if k.strip()}
        else:
            allowed = set()

        user_id = (current_user_id or "").strip() or os.getenv("CURRENT_USER_ID", "").strip()

        with SessionLocal() as db:
            query = db.query(KBSpace).filter(KBSpace.deleted_at.is_(None))
            if allowed:
                query = query.filter(KBSpace.kb_id.in_(allowed))
            elif user_id:
                query = query.filter(KBSpace.user_id == user_id)
            spaces = query.all()

            for space in spaces:
                # Fetch document titles
                docs = db.query(KBDocument).filter(
                    KBDocument.kb_id == space.kb_id,
                    KBDocument.deleted_at.is_(None),
                ).order_by(KBDocument.uploaded_at.desc()).limit(20).all()
                doc_titles = [d.title for d in docs if d.title]

                private_datasets.append({
                    "kb_id": space.kb_id,
                    "name": space.name,
                    "description": space.description or "",
                    "document_count": space.document_count or len(docs),
                    "document_titles": doc_titles,
                    "type": "private",
                })
    except Exception as exc:
        _list_logger.warning("Failed to list private KBs: %s", exc)

    return {
        "public_datasets": public_datasets,
        "private_datasets": private_datasets,
        "total": len(public_datasets) + len(private_datasets),
    }


# ── Private (local) KB retrieval ──────────────────────────────────────────────

_local_kb_logger = logging.getLogger(__name__ + ".local_kb")

KB_DETAIL_CONTENT_MAX_CHARS = int(os.getenv("KB_DETAIL_CONTENT_MAX_CHARS", "50000"))


def _get_allowed_kb_ids() -> set[str]:
    raw = os.getenv("LOCAL_KB_ALLOWED_IDS", "").strip()
    if not raw:
        return set()
    return {k.strip() for k in raw.split(",") if k.strip()}


def _get_current_user_id() -> str:
    return os.getenv("CURRENT_USER_ID", "").strip()


def _fetch_parent_contents(parent_ids: list[str]) -> dict[str, str]:
    """Fetch parent chunk content from PostgreSQL by chunk_id list."""
    if not parent_ids:
        return {}
    try:
        from core.db.engine import SessionLocal
        from core.db.models import KBChunk
        db = SessionLocal()
        try:
            chunks = db.query(KBChunk).filter(KBChunk.chunk_id.in_(parent_ids)).all()
            return {c.chunk_id: c.content for c in chunks}
        finally:
            db.close()
    except Exception as exc:
        _local_kb_logger.warning("Failed to fetch parent chunks from DB: %s", exc)
        return {}


def _build_runtime_local_kb_section() -> str:
    """Build runtime private KB list for tool description injection.

    NOTE: 详细的知识库简介和文档列表在系统提示词中动态注入（见 prompt_runtime.py），
    此处仅提供 kb_id 与名称的快速参考。
    """
    allowed_raw = os.getenv("LOCAL_KB_ALLOWED_IDS", "").strip()
    if not allowed_raw:
        return ""

    allowed_ids = [k.strip() for k in allowed_raw.split(",") if k.strip()]
    if not allowed_ids:
        return ""

    # Try to fetch KB names from DB
    kb_names: dict[str, str] = {}
    try:
        from core.db.engine import SessionLocal
        from core.db.models import KBSpace
        db = SessionLocal()
        try:
            spaces = db.query(KBSpace).filter(KBSpace.kb_id.in_(allowed_ids)).all()
            kb_names = {s.kb_id: s.name for s in spaces}
        finally:
            db.close()
    except Exception as exc:
        _local_kb_logger.debug("Could not fetch KB names for tool description: %s", exc)

    lines = []
    for kid in allowed_ids:
        name = kb_names.get(kid, kid)
        lines.append(f"- {kid} | {name}")

    return "\n".join([
        "## 当前可用私有知识库（运行时注入）",
        "调用 `retrieve_local_kb` 时，`kb_id` 应从以下列表中选择（详细简介和文档列表见系统提示词）。",
        "格式：`kb_id | 知识库名称`",
        *lines,
        "## 当前可用私有知识库（运行时注入）结束",
    ]).strip()


def retrieve_local_kb(
    kb_id: str,
    query: str,
    top_k: int = 10,
    *,
    allowed_kb_ids: str | None = None,
    current_user_id: str | None = None,
    reranker_enabled: str | None = None,
) -> list[dict[str, Any]]:
    """Search user's private KB and return ranked result chunks.

    Returns a list of dicts with keys: id, title, content, kb_id, score.
    """
    # ── Auth check ──────────────────────────────────────────────────────────
    if allowed_kb_ids is not None:
        allowed = {k.strip() for k in allowed_kb_ids.split(",") if k.strip()}
    else:
        allowed = _get_allowed_kb_ids()

    user_id = current_user_id if current_user_id is not None else _get_current_user_id()

    # Auto-resolve: if no allowed list, fetch KB spaces from DB
    if not allowed:
        try:
            from core.db.engine import SessionLocal
            from core.db.models import KBSpace
            with SessionLocal() as _db:
                if user_id:
                    spaces = _db.query(KBSpace).filter(
                        KBSpace.user_id == user_id,
                        KBSpace.deleted_at.is_(None),
                    ).all()
                else:
                    # No user_id available, fetch all non-deleted spaces
                    spaces = _db.query(KBSpace).filter(
                        KBSpace.deleted_at.is_(None),
                    ).all()
                allowed = {s.kb_id for s in spaces if s.kb_id}
                _local_kb_logger.info("Auto-resolved %d KB spaces", len(allowed))
        except Exception as exc:
            _local_kb_logger.warning("Auto-resolve KB spaces failed: %s", exc)

    if not allowed:
        _local_kb_logger.warning("retrieve_local_kb: no accessible private KBs")
        return [{"error": "未找到可访问的私有知识库"}]

    kb_id = (kb_id or "").strip()
    # Determine which KBs to search
    if kb_id:
        if kb_id not in allowed:
            _local_kb_logger.warning("retrieve_local_kb: kb_id %s not in allowed list", kb_id)
            return [{"error": f"无权访问知识库 {kb_id}"}]
        search_kb_ids = [kb_id]
    else:
        # Search ALL allowed KBs
        search_kb_ids = sorted(allowed)
        _local_kb_logger.info("Searching all %d allowed KBs: %s", len(search_kb_ids), search_kb_ids)

    # Resolve user_id: when not available from headers, look up KB owner from DB
    if not user_id:
        try:
            from core.db.engine import SessionLocal
            from core.db.models import KBSpace
            with SessionLocal() as _db:
                space = _db.query(KBSpace).filter(KBSpace.kb_id == search_kb_ids[0]).first()
                if space:
                    user_id = space.user_id
        except Exception:
            pass
    if not user_id:
        return [{"error": "未能获取当前用户 ID"}]

    # ── Embed query ──────────────────────────────────────────────────────────
    try:
        from utils.kb_vector import embed_text, hybrid_search
        query_vec = embed_text(query)
    except Exception as exc:
        _local_kb_logger.error("retrieve_local_kb: embed_text failed: %s", exc)
        return [{"error": f"向量化失败：{exc}"}]

    # ── Hybrid search ────────────────────────────────────────────────────────
    try:
        hits = hybrid_search(
            user_id=user_id,
            kb_ids=search_kb_ids,
            query=query,
            query_vec=query_vec,
            top_k=top_k * 3,   # over-fetch before dedup
        )
    except Exception as exc:
        _local_kb_logger.error("retrieve_local_kb: hybrid_search failed: %s", exc)
        return [{"error": f"检索失败：{exc}"}]

    # Build KB metadata for the response
    kb_meta: list[dict[str, str]] = []
    try:
        from core.db.engine import SessionLocal
        from core.db.models import KBSpace
        with SessionLocal() as _db:
            spaces = _db.query(KBSpace).filter(
                KBSpace.kb_id.in_(search_kb_ids),
            ).all()
            kb_meta = [{"kb_id": s.kb_id, "name": s.name, "description": s.description or ""} for s in spaces]
    except Exception:
        pass

    if not hits:
        return {"available_kbs": kb_meta, "items": [], "message": "未找到相关内容"}

    # ── Dedup by parent_chunk_id (keep highest score) ────────────────────────
    seen: dict[str, dict] = {}
    for hit in hits:
        pid = hit.get("parent_chunk_id") or hit.get("chunk_id", "")
        if pid not in seen or hit["score"] > seen[pid]["score"]:
            seen[pid] = hit

    # Sort by score descending, take top_k
    top_hits = sorted(seen.values(), key=lambda x: x["score"], reverse=True)[:top_k]

    # ── Optional reranker step ───────────────────────────────────────────────
    _reranker_flag = reranker_enabled if reranker_enabled is not None else os.getenv("RERANKER_ENABLED", "")
    if (_reranker_flag or "").lower() in ("true", "1"):
        try:
            from utils.kb_vector import rerank, is_reranker_configured
            if is_reranker_configured() and top_hits:
                contents = [hit.get("content", "") for hit in top_hits]
                reranked = rerank(query, contents, top_n=top_k)
                reranked_hits = []
                for item in reranked:
                    idx = item.get("index", 0)
                    if 0 <= idx < len(top_hits):
                        hit = dict(top_hits[idx])
                        hit["score"] = round(item.get("relevance_score", hit["score"]), 4)
                        reranked_hits.append(hit)
                if reranked_hits:
                    top_hits = reranked_hits
                    _local_kb_logger.info("Reranker applied: %d results reranked", len(top_hits))
        except Exception as rerank_exc:
            _local_kb_logger.warning("Reranker failed, falling back to original ranking: %s", rerank_exc)

    # ── Fetch parent content from PostgreSQL ─────────────────────────────────
    parent_ids = [h["parent_chunk_id"] for h in top_hits if h.get("parent_chunk_id")]
    parent_map = _fetch_parent_contents(parent_ids)

    # ── Build results ────────────────────────────────────────────────────────
    results = []
    total_chars = 0
    for i, hit in enumerate(top_hits):
        pid = hit.get("parent_chunk_id") or hit.get("chunk_id", "")
        # Prefer parent content (full context); fall back to child snippet
        content = parent_map.get(pid) or hit.get("content", "")

        if total_chars + len(content) > KB_DETAIL_CONTENT_MAX_CHARS:
            content = content[: max(0, KB_DETAIL_CONTENT_MAX_CHARS - total_chars)]
            if content:
                results.append({
                    "id": pid,
                    "title": hit.get("title", ""),
                    "content": content,
                    "kb_id": hit.get("kb_id", kb_id),
                    "score": round(hit["score"], 4),
                    "chunk_index": hit.get("chunk_index", i),
                })
            break

        total_chars += len(content)
        results.append({
            "id": pid,
            "title": hit.get("title", ""),
            "content": content,
            "kb_id": hit.get("kb_id", kb_id),
            "score": round(hit["score"], 4),
            "chunk_index": hit.get("chunk_index", i),
        })

    return {"available_kbs": kb_meta, "items": results}
