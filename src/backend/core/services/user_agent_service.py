"""Business logic for custom sub-agents (UserAgent)."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from core.db.repository import UserAgentRepository, AuditLogRepository
from core.db.models import UserAgent

logger = logging.getLogger(__name__)

MAX_USER_AGENTS = 20
DEFAULT_AGENT_VERSION = "V1.0"
MAX_CHANGE_HISTORY = 30
NON_VERSIONED_FIELDS = {"is_enabled"}
VERSIONED_FIELDS = {
    "name": "名称",
    "description": "简介",
    "system_prompt": "角色设定",
    "welcome_message": "开场白",
    "suggested_questions": "推荐问题",
    "mcp_server_ids": "绑定工具",
    "skill_ids": "绑定技能",
    "kb_ids": "绑定知识库",
    "model_provider_id": "模型",
    "temperature": "温度",
    "max_tokens": "最大输出长度",
    "max_iters": "最大推理轮次",
    "timeout": "超时时间",
    "is_enabled": "启用状态",
}


class UserAgentService:
    """Service for user agent CRUD and permission checks."""

    def __init__(self, db: Session):
        self.db = db
        self.repo = UserAgentRepository(db)

    # ── Queries ──────────────────────────────────────────────────────

    def list_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        agents = self.repo.list_for_user(user_id)
        return [self._serialize(a) for a in agents]

    def list_admin(self) -> List[Dict[str, Any]]:
        agents = self.repo.list_admin()
        return [self._serialize(a) for a in agents]

    def get_by_id(self, agent_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        agent = self.repo.get_by_id(agent_id)
        if not agent:
            raise LookupError(f"Agent {agent_id} not found")
        if user_id and not self._is_accessible(agent, user_id):
            raise PermissionError("No access to this agent")
        return self._serialize(agent)

    def get_raw_by_id(self, agent_id: str, user_id: Optional[str] = None) -> UserAgent:
        """返回 ORM 对象（供 workflow/factory 直接使用）。"""
        agent = self.repo.get_by_id(agent_id)
        if not agent:
            raise LookupError(f"Agent {agent_id} not found")
        if user_id and not self._is_accessible(agent, user_id):
            raise PermissionError("No access to this agent")
        return agent

    # ── Mutations ────────────────────────────────────────────────────

    def create(
        self,
        user_id: Optional[str],
        operator_name: Optional[str],
        owner_type: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        if owner_type == "user":
            if not user_id:
                raise ValueError("user_id required for user agents")
            count = self.repo.count_user_agents(user_id)
            if count >= MAX_USER_AGENTS:
                raise ValueError(f"Maximum {MAX_USER_AGENTS} agents per user reached")

        agent_id = f"ua_{uuid.uuid4().hex[:16]}"
        created_at = self._now_iso()
        creation_history = [{
            "version": DEFAULT_AGENT_VERSION,
            "timestamp": created_at,
            "content": "创建了子智能体",
            "operator_name": operator_name or user_id or "未知用户",
            "details": [],
        }]
        extra_config = self._merge_extra_config(
            current_extra=None,
            incoming_extra=data.get("extra_config"),
            version=DEFAULT_AGENT_VERSION,
            change_history=creation_history,
        )

        record = {
            "agent_id": agent_id,
            "owner_type": owner_type,
            "user_id": user_id if owner_type == "user" else None,
            "created_by": user_id,
            **data,
            "extra_config": extra_config,
        }
        agent = self.repo.create(record)
        self._audit(user_id, "agent.create", agent_id, {"owner_type": owner_type, "name": data.get("name")})
        return self._serialize(agent)

    def update(
        self,
        agent_id: str,
        user_id: Optional[str],
        operator_name: Optional[str],
        owner_type: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        agent = self.repo.get_by_id(agent_id)
        if not agent:
            raise LookupError(f"Agent {agent_id} not found")
        self._check_ownership(agent, user_id, owner_type)

        current_extra = dict(agent.extra_config or {})
        changed_fields = self._collect_changed_fields(agent, data)
        versioned_fields = [field for field in changed_fields if field not in NON_VERSIONED_FIELDS]
        changed_labels = [VERSIONED_FIELDS[field] for field in changed_fields]
        next_version = self._read_version(current_extra)
        change_history = self._read_change_history(current_extra)

        if changed_labels:
            change_summary = self._build_change_summary(changed_fields, data)
            change_details = self._build_change_details(agent, changed_fields, data)
            entry_version = next_version
            if versioned_fields:
                next_version = self._increment_version(next_version)
                entry_version = next_version
            change_history.append({
                "version": entry_version,
                "timestamp": self._now_iso(),
                "content": change_summary,
                "operator_name": operator_name or user_id or "未知用户",
                "details": change_details,
            })
            change_history = change_history[-MAX_CHANGE_HISTORY:]

        payload = dict(data)
        payload["extra_config"] = self._merge_extra_config(
            current_extra=current_extra,
            incoming_extra=data.get("extra_config"),
            version=next_version,
            change_history=change_history,
        )

        agent = self.repo.update(agent_id, payload)
        audit_details = {"fields": list(data.keys())}
        if changed_labels:
            audit_details["change_summary"] = change_summary
            audit_details["version"] = next_version
        self._audit(user_id, "agent.update", agent_id, audit_details)
        return self._serialize(agent)

    def delete(
        self,
        agent_id: str,
        user_id: Optional[str],
        owner_type: str,
    ) -> bool:
        agent = self.repo.get_by_id(agent_id)
        if not agent:
            raise LookupError(f"Agent {agent_id} not found")
        self._check_ownership(agent, user_id, owner_type)

        ok = self.repo.delete(agent_id)
        self._audit(user_id, "agent.delete", agent_id)
        return ok

    def toggle_enabled(self, agent_id: str) -> Dict[str, Any]:
        agent = self.repo.get_by_id(agent_id)
        if not agent:
            raise LookupError(f"Agent {agent_id} not found")
        new_val = not agent.is_enabled
        agent = self.repo.update(agent_id, {"is_enabled": new_val})
        return self._serialize(agent)

    # ── Available resources ──────────────────────────────────────────

    def list_available_resources(self) -> Dict[str, Any]:
        """Return MCP servers, skills, and KB spaces that can be bound to agents."""
        from core.db.models import AdminMcpServer, KBSpace

        mcp_servers = self.db.query(AdminMcpServer).filter(
            AdminMcpServer.is_enabled == True
        ).order_by(AdminMcpServer.sort_order).all()

        # Skills: merge DB-managed + filesystem-discovered
        skill_list: List[Dict[str, Any]] = []
        try:
            from core.db.models import AdminSkill
            db_skills = self.db.query(AdminSkill).filter(
                AdminSkill.is_enabled == True
            ).order_by(AdminSkill.updated_at.desc()).all()
            seen_ids = set()
            for s in db_skills:
                skill_list.append({"id": s.skill_id, "name": s.display_name, "description": s.description or ""})
                seen_ids.add(s.skill_id)
        except Exception:
            seen_ids = set()

        # Filesystem-discovered skills (agent_skills.loader)
        try:
            from agent_skills.loader import get_skill_loader
            loader = get_skill_loader()
            for sid, meta in loader.load_all_metadata().items():
                if sid not in seen_ids:
                    skill_list.append({
                        "id": sid,
                        "name": getattr(meta, "name", sid),
                        "description": getattr(meta, "description", ""),
                    })
        except Exception as exc:
            logger.debug("Failed to load filesystem skills: %s", exc)

        # KB spaces
        kb_list: List[Dict[str, Any]] = []
        try:
            kb_spaces = self.db.query(KBSpace).filter(
                KBSpace.deleted_at.is_(None),
            ).order_by(KBSpace.created_at.desc()).all()
            kb_list = [{"id": s.kb_id, "name": s.name, "description": s.description or ""} for s in kb_spaces]
        except Exception:
            pass

        return {
            "mcp_servers": [
                {"id": s.server_id, "name": s.display_name, "description": s.description}
                for s in mcp_servers
            ],
            "skills": skill_list,
            "kb_spaces": kb_list,
        }

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _is_accessible(agent: UserAgent, user_id: str) -> bool:
        if agent.owner_type == "admin" and agent.is_enabled:
            return True
        if agent.owner_type == "user" and agent.user_id == user_id:
            return True
        return False

    @staticmethod
    def _check_ownership(agent: UserAgent, user_id: Optional[str], owner_type: str) -> None:
        if owner_type == "admin":
            if agent.owner_type != "admin":
                raise PermissionError("Admin can only modify admin agents")
        else:
            if agent.owner_type != "user" or agent.user_id != user_id:
                raise PermissionError("You can only modify your own agents")

    @staticmethod
    def _serialize(agent: UserAgent) -> Dict[str, Any]:
        extra_config = agent.extra_config or {}
        return {
            "agent_id": agent.agent_id,
            "owner_type": agent.owner_type,
            "user_id": agent.user_id,
            "name": agent.name,
            "avatar": agent.avatar,
            "description": agent.description,
            "system_prompt": agent.system_prompt,
            "welcome_message": agent.welcome_message,
            "suggested_questions": agent.suggested_questions or [],
            "mcp_server_ids": agent.mcp_server_ids or [],
            "skill_ids": agent.skill_ids or [],
            "kb_ids": agent.kb_ids or [],
            "model_provider_id": agent.model_provider_id,
            "temperature": float(agent.temperature) if agent.temperature is not None else None,
            "max_tokens": agent.max_tokens,
            "max_iters": agent.max_iters,
            "timeout": agent.timeout,
            "is_enabled": agent.is_enabled,
            "sort_order": agent.sort_order,
            "extra_config": extra_config,
            "version": UserAgentService._read_version(extra_config),
            "change_history": UserAgentService._read_change_history(extra_config),
            "created_at": agent.created_at.isoformat() if agent.created_at else None,
            "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
            "created_by": agent.created_by,
        }

    def _audit(self, user_id: Optional[str], action: str, resource_id: str, details: Dict = None) -> None:
        try:
            audit_repo = AuditLogRepository(self.db)
            audit_repo.create({
                "user_id": user_id,
                "action": action,
                "resource_type": "user_agent",
                "resource_id": resource_id,
                "details": details or {},
                "status": "success",
            })
        except Exception as exc:
            logger.warning("Audit log failed: %s", exc)

    @staticmethod
    def _normalize_value(value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, list):
            return list(value)
        return value

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().replace(microsecond=0).isoformat()

    @classmethod
    def _collect_changed_fields(cls, agent: UserAgent, data: Dict[str, Any]) -> List[str]:
        fields: List[str] = []
        for field in VERSIONED_FIELDS:
            if field not in data:
                continue
            old_value = cls._normalize_value(getattr(agent, field, None))
            new_value = cls._normalize_value(data.get(field))
            if old_value != new_value:
                fields.append(field)
        return fields

    @staticmethod
    def _read_version(extra_config: Optional[Dict[str, Any]]) -> str:
        if not isinstance(extra_config, dict):
            return DEFAULT_AGENT_VERSION
        raw = extra_config.get("version")
        return UserAgentService._normalize_version(raw if isinstance(raw, str) else "")

    @staticmethod
    def _read_change_history(extra_config: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
        if not isinstance(extra_config, dict):
            return []
        raw_history = extra_config.get("change_history")
        if not isinstance(raw_history, list):
            return []

        history: List[Dict[str, str]] = []
        for item in raw_history:
            if not isinstance(item, dict):
                continue
            timestamp = item.get("timestamp")
            content = item.get("content")
            version = item.get("version")
            operator_name = item.get("operator_name")
            details = item.get("details")
            if not isinstance(timestamp, str) or not isinstance(content, str):
                continue
            history.append({
                "timestamp": timestamp,
                "content": content,
                "version": UserAgentService._normalize_version(version if isinstance(version, str) else ""),
                "operator_name": operator_name if isinstance(operator_name, str) and operator_name.strip() else "未知用户",
                "details": UserAgentService._normalize_change_details(details),
            })
        return history

    @staticmethod
    def _increment_version(version: str) -> str:
        normalized = UserAgentService._normalize_version(version)
        match = re.match(r"^[Vv](\d+)\.(\d+)$", normalized)
        if not match:
            return "V1.1"
        major, minor = (int(part) for part in match.groups())
        return f"V{major}.{minor + 1}"

    @staticmethod
    def _build_change_summary(changed_fields: List[str], data: Dict[str, Any]) -> str:
        if changed_fields == ["is_enabled"]:
            return "启用了子智能体" if bool(data.get("is_enabled")) else "停用了子智能体"

        changed_labels = [VERSIONED_FIELDS[field] for field in changed_fields]
        if not changed_labels:
            return "更新了智能体配置"
        if len(changed_labels) <= 3:
            return f"修改了{'、'.join(changed_labels)}"
        preview = "、".join(changed_labels[:3])
        return f"修改了{preview}等{len(changed_labels)}项"

    @classmethod
    def _build_change_details(cls, agent: UserAgent, changed_fields: List[str], data: Dict[str, Any]) -> List[Dict[str, str]]:
        details: List[Dict[str, str]] = []
        for field in changed_fields:
            old_value = cls._stringify_detail_value(field, getattr(agent, field, None))
            new_value = cls._stringify_detail_value(field, data.get(field))
            details.append({
                "field": VERSIONED_FIELDS[field],
                "before": old_value,
                "after": new_value,
            })
        return details

    @staticmethod
    def _stringify_detail_value(field: str, value: Any) -> str:
        if field == "is_enabled":
            return "启用" if bool(value) else "关闭"
        if value is None:
            return "未填写"
        if isinstance(value, list):
            return "、".join(str(item) for item in value) if value else "未填写"
        if isinstance(value, bool):
            return "是" if value else "否"
        text = str(value).strip()
        return text if text else "未填写"

    @staticmethod
    def _normalize_change_details(details: Any) -> List[Dict[str, str]]:
        if not isinstance(details, list):
            return []
        normalized: List[Dict[str, str]] = []
        for item in details:
            if not isinstance(item, dict):
                continue
            field = item.get("field")
            before = item.get("before")
            after = item.get("after")
            if not isinstance(field, str):
                continue
            normalized.append({
                "field": field,
                "before": before if isinstance(before, str) else "未填写",
                "after": after if isinstance(after, str) else "未填写",
            })
        return normalized

    @staticmethod
    def _normalize_version(version: str) -> str:
        if not isinstance(version, str) or not version.strip():
            return DEFAULT_AGENT_VERSION
        raw = version.strip()
        if re.match(r"^[Vv]\d+\.\d+$", raw):
            return f"V{raw[1:]}"
        legacy_patch = re.match(r"^(\d+)\.(\d+)\.(\d+)$", raw)
        if legacy_patch:
            major, minor, patch = (int(part) for part in legacy_patch.groups())
            return f"V{major}.{minor + patch}"
        legacy_minor = re.match(r"^(\d+)\.(\d+)$", raw)
        if legacy_minor:
            major, minor = (int(part) for part in legacy_minor.groups())
            return f"V{major}.{minor}"
        return DEFAULT_AGENT_VERSION

    @staticmethod
    def _merge_extra_config(
        current_extra: Optional[Dict[str, Any]],
        incoming_extra: Optional[Dict[str, Any]],
        *,
        version: str,
        change_history: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        merged = dict(current_extra or {})
        if isinstance(incoming_extra, dict):
            merged.update(incoming_extra)
        merged["version"] = version
        merged["change_history"] = change_history
        return merged
