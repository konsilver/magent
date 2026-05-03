"""Implementation for MCP tools: retrieve_dataset_content, list_datasets."""

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
