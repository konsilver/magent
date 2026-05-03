"""SQLAlchemy ORM models for database tables."""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, BigInteger, Boolean, Text, TIMESTAMP,
    ForeignKey, CheckConstraint, UniqueConstraint, Index, Numeric, JSON
)
from sqlalchemy.dialects.postgresql import JSONB, INET
from sqlalchemy.orm import relationship, mapped_column
from core.db.engine import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")
INETType = String(45).with_variant(INET(), "postgresql")


class UserShadow(Base):
    """User shadow table - synced from user center."""
    __tablename__ = "users_shadow"

    user_id = Column(String(64), primary_key=True)
    username = Column(String(255), nullable=False)
    email = Column(String(255))
    avatar_url = Column(Text)
    user_center_id = Column(String(64))
    extra_data = Column("metadata", JSONType, default={})  # Map to 'metadata' column in DB
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    last_sync_at = Column(TIMESTAMP(timezone=True))

    # Relationships
    chat_sessions = relationship("ChatSession", back_populates="user", cascade="all, delete-orphan")
    catalog_overrides = relationship("CatalogOverride", back_populates="user", cascade="all, delete-orphan")
    kb_spaces = relationship("KBSpace", back_populates="user", cascade="all, delete-orphan")
    artifacts = relationship("Artifact", back_populates="user", cascade="all, delete-orphan")
    user_agents = relationship("UserAgent", foreign_keys="[UserAgent.user_id]", back_populates="user")

    __table_args__ = (
        Index("idx_users_shadow_user_center_id", "user_center_id"),
        Index("idx_users_shadow_updated_at", "updated_at"),
    )


class ChatSession(Base):
    """Chat session table."""
    __tablename__ = "chat_sessions"

    chat_id = Column(String(64), primary_key=True)
    user_id = Column(String(64), ForeignKey("users_shadow.user_id", ondelete="CASCADE"), nullable=False)
    title = Column(String(500), nullable=False, default="新对话")
    message_count = Column(Integer, default=0)
    pinned = Column(Boolean, default=False)
    favorite = Column(Boolean, default=False)
    archived = Column(Boolean, default=False)
    deleted_at = Column(TIMESTAMP(timezone=True))
    extra_data = Column("metadata", JSONType, default={})
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("UserShadow", back_populates="chat_sessions")
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")
    artifacts = relationship("Artifact", back_populates="session")

    __table_args__ = (
        CheckConstraint("length(title) >= 1", name="chat_sessions_title_length"),
        CheckConstraint("message_count >= 0", name="chat_sessions_message_count_check"),
        Index("idx_chat_sessions_user_id", "user_id"),
        Index("idx_chat_sessions_updated_at", "updated_at"),
        Index("idx_chat_sessions_user_updated", "user_id", "updated_at"),
        Index("idx_chat_sessions_pinned", "user_id", "pinned", "updated_at", postgresql_where=Column("pinned") == True),
        Index("idx_chat_sessions_favorite", "user_id", "favorite", "updated_at", postgresql_where=Column("favorite") == True),
        Index("idx_chat_sessions_deleted", "deleted_at", postgresql_where=Column("deleted_at").isnot(None)),
        Index("idx_chat_sessions_metadata_gin", "metadata", postgresql_using="gin"),
    )


class ChatMessage(Base):
    """Chat message table."""
    __tablename__ = "chat_messages"

    message_id = Column(String(64), primary_key=True)
    chat_id = Column(String(64), ForeignKey("chat_sessions.chat_id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    model = Column(String(100))
    tool_calls = Column(JSONType)
    usage = Column(JSONType)
    error = Column(JSONType)
    extra_data = Column("metadata", JSONType, default={})
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    # Relationships
    session = relationship("ChatSession", back_populates="messages")

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'system', 'tool')", name="chat_messages_role_check"),
        CheckConstraint("length(content) <= 100000", name="chat_messages_content_length"),
        Index("idx_chat_messages_chat_id", "chat_id"),
        Index("idx_chat_messages_chat_created", "chat_id", "created_at"),
        Index("idx_chat_messages_role", "chat_id", "role"),
        Index("idx_chat_messages_created_at", "created_at"),
        Index("idx_chat_messages_tool_calls_gin", "tool_calls", postgresql_using="gin", postgresql_where=Column("tool_calls").isnot(None)),
    )


class CatalogOverride(Base):
    """Catalog override table - user customizations for skills/agents/MCPs."""
    __tablename__ = "catalog_overrides"

    override_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), ForeignKey("users_shadow.user_id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(20), nullable=False)
    item_id = Column(String(100), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    config_data = Column("config", JSONType, default={})
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("UserShadow", back_populates="catalog_overrides")

    __table_args__ = (
        CheckConstraint("kind IN ('skill', 'agent', 'mcp')", name="catalog_overrides_kind_check"),
        UniqueConstraint("user_id", "kind", "item_id", name="catalog_overrides_unique_user_kind_item"),
        Index("idx_catalog_overrides_user_id", "user_id"),
        Index("idx_catalog_overrides_kind", "kind", "enabled"),
    )


class KBSpace(Base):
    """Knowledge base space table."""
    __tablename__ = "kb_spaces"

    kb_id = Column(String(64), primary_key=True)
    user_id = Column(String(64), ForeignKey("users_shadow.user_id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    document_count = Column(Integer, default=0)
    total_size_bytes = Column(BigInteger, default=0)
    visibility = Column(String(16), nullable=False, default="private")
    chunk_method = Column(String(32), nullable=False, default="semantic")
    extra_data = Column("metadata", JSONType, default={})
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(TIMESTAMP(timezone=True))

    # Relationships
    user = relationship("UserShadow", back_populates="kb_spaces")
    documents = relationship("KBDocument", back_populates="kb_space", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("length(name) >= 1 AND length(name) <= 255", name="kb_spaces_name_length"),
        CheckConstraint("document_count >= 0", name="kb_spaces_document_count_check"),
        CheckConstraint("total_size_bytes >= 0", name="kb_spaces_total_size_check"),
        CheckConstraint("visibility IN ('public', 'private')", name="kb_spaces_visibility_check"),
        Index("idx_kb_spaces_user_id", "user_id"),
        Index("idx_kb_spaces_updated_at", "updated_at"),
        Index("idx_kb_spaces_deleted", "deleted_at", postgresql_where=Column("deleted_at").isnot(None)),
        Index("idx_kb_spaces_visibility", "visibility"),
    )


class KBDocument(Base):
    """Knowledge base document table."""
    __tablename__ = "kb_documents"

    document_id = Column(String(64), primary_key=True)
    kb_id = Column(String(64), ForeignKey("kb_spaces.kb_id", ondelete="CASCADE"), nullable=False)
    title = Column(String(500), nullable=False)
    filename = Column(String(500), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    mime_type = Column(String(100), nullable=False)
    storage_key = Column(Text, nullable=False)
    storage_url = Column(Text)
    checksum = Column(String(64))
    indexing_status = Column(String(20), nullable=False, default="processing")  # processing | completed | failed
    extra_data = Column("metadata", JSONType, default={})
    uploaded_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    deleted_at = Column(TIMESTAMP(timezone=True))

    # Relationships
    kb_space = relationship("KBSpace", back_populates="documents")
    chunks = relationship("KBChunk", back_populates="document", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="kb_documents_size_check"),
        CheckConstraint("length(filename) >= 1", name="kb_documents_filename_length"),
        Index("idx_kb_documents_kb_id", "kb_id"),
        Index("idx_kb_documents_uploaded_at", "uploaded_at"),
        Index("idx_kb_documents_kb_uploaded", "kb_id", "uploaded_at"),
        Index("idx_kb_documents_deleted", "deleted_at", postgresql_where=Column("deleted_at").isnot(None)),
        Index("idx_kb_documents_metadata_gin", "metadata", postgresql_using="gin"),
    )


class KBChunk(Base):
    """Knowledge base chunk table - stores parent chunks for context retrieval.

    Each document is split into parent chunks (stored here) and child chunks
    (vectorised in Milvus jingxin_kb_private collection). Retrieval finds child
    chunks via vector search, then fetches the parent content from this table.
    """
    __tablename__ = "kb_chunks"

    chunk_id = Column(String(64), primary_key=True)
    kb_id = Column(String(64), ForeignKey("kb_spaces.kb_id", ondelete="CASCADE"), nullable=False)
    document_id = Column(String(64), ForeignKey("kb_documents.document_id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)       # 父块原文，检索命中后返回给 LLM
    tags = Column(JSONType, default=list)           # 标签列表 ["数字化转型", "申报条件"]
    questions = Column(JSONType, default=list)      # 关联问题列表（字符串数组）
    char_start = Column(Integer)
    char_end = Column(Integer)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    document = relationship("KBDocument", back_populates="chunks")

    __table_args__ = (
        Index("idx_kb_chunks_kb_id", "kb_id"),
        Index("idx_kb_chunks_document_id", "document_id"),
        Index("idx_kb_chunks_kb_doc", "kb_id", "document_id"),
    )


class Artifact(Base):
    """Artifact table - AI-generated files (reports, charts, etc.)."""
    __tablename__ = "artifacts"

    artifact_id = Column(String(64), primary_key=True)
    chat_id = Column(String(64), ForeignKey("chat_sessions.chat_id", ondelete="SET NULL"))
    user_id = Column(String(64), ForeignKey("users_shadow.user_id", ondelete="CASCADE"), nullable=False)
    type = Column(String(50), nullable=False)
    title = Column(String(500), nullable=False)
    filename = Column(String(500), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    mime_type = Column(String(100), nullable=False)
    storage_key = Column(Text, nullable=False)
    storage_url = Column(Text)
    extra_data = Column("metadata", JSONType, default={})
    # Lazy caches for cross-turn file reading (populated by core/content/artifact_reader.py)
    parsed_text = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    parsed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    parse_error = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(TIMESTAMP(timezone=True))

    # Relationships
    user = relationship("UserShadow", back_populates="artifacts")
    session = relationship("ChatSession", back_populates="artifacts")

    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="artifacts_size_check"),
        CheckConstraint("type IN ('report', 'chart', 'document', 'code', 'other')", name="artifacts_type_check"),
        Index("idx_artifacts_user_id", "user_id"),
        Index("idx_artifacts_chat_id", "chat_id"),
        Index("idx_artifacts_type", "type"),
        Index("idx_artifacts_created_at", "created_at"),
        Index("idx_artifacts_user_created", "user_id", "created_at"),
        Index("idx_artifacts_deleted", "deleted_at", postgresql_where=Column("deleted_at").isnot(None)),
    )


class AuditLog(Base):
    """Audit log table - record all critical operations."""
    __tablename__ = "audit_logs"

    log_id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), ForeignKey("users_shadow.user_id", ondelete="SET NULL"))
    action = Column(String(100), nullable=False)
    resource_type = Column(String(50))
    resource_id = Column(String(64))
    details = Column(JSONType, default={})
    ip_address = Column(INETType)
    user_agent = Column(Text)
    trace_id = Column(String(64))
    status = Column(String(20), default="success")
    error_code = Column(Integer)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("status IN ('success', 'failure', 'error')", name="audit_logs_status_check"),
        Index("idx_audit_logs_user_id", "user_id"),
        Index("idx_audit_logs_action", "action"),
        Index("idx_audit_logs_resource", "resource_type", "resource_id"),
        Index("idx_audit_logs_created_at", "created_at"),
        Index("idx_audit_logs_user_created", "user_id", "created_at"),
        Index("idx_audit_logs_trace_id", "trace_id"),
        Index("idx_audit_logs_status", "status", "created_at", postgresql_where=Column("status") != "success"),
    )


class MessageFeedback(Base):
    """Message feedback table - stores like/dislike ratings and optional comments."""
    __tablename__ = "message_feedback"

    feedback_id = Column(BigInteger, primary_key=True, autoincrement=True)
    message_id = Column(String(64), ForeignKey("chat_messages.message_id", ondelete="CASCADE"), nullable=False)
    chat_id = Column(String(64), ForeignKey("chat_sessions.chat_id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(64), ForeignKey("users_shadow.user_id", ondelete="SET NULL"), nullable=True)
    rating = Column(String(10), nullable=False)   # 'like' or 'dislike'
    comment = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("rating IN ('like', 'dislike')", name="message_feedback_rating_check"),
        Index("idx_message_feedback_message_id", "message_id"),
        Index("idx_message_feedback_chat_id", "chat_id"),
        Index("idx_message_feedback_user_id", "user_id"),
    )


class ContentBlock(Base):
    """Content blocks for editable frontend sections (功能更新 / 能力中心)."""
    __tablename__ = "content_blocks"

    id = Column(String(64), primary_key=True)          # e.g. 'docs_updates', 'docs_capabilities'
    payload = Column(JSONType, nullable=False, default=[])
    updated_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(String(64), nullable=True)


class ModelProvider(Base):
    """Model provider — an OpenAI-compatible model endpoint."""
    __tablename__ = "model_providers"

    provider_id = Column(String(64), primary_key=True)
    display_name = Column(String(255), nullable=False)
    provider_type = Column(String(20), nullable=False)  # chat / embedding / reranker
    base_url = Column(Text, nullable=False)
    api_key = Column(Text, nullable=False)
    model_name = Column(String(255), nullable=False)
    extra_config = Column(JSONType, default={})
    is_active = Column(Boolean, default=True, nullable=False)
    last_tested_at = Column(TIMESTAMP(timezone=True))
    last_test_status = Column(String(20))  # success / failure / null
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    role_assignments = relationship("ModelRoleAssignment", back_populates="provider", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "provider_type IN ('chat', 'embedding', 'reranker')",
            name="model_providers_type_check",
        ),
        CheckConstraint(
            "last_test_status IS NULL OR last_test_status IN ('success', 'failure')",
            name="model_providers_test_status_check",
        ),
        Index("idx_model_providers_type", "provider_type"),
        Index("idx_model_providers_active", "is_active"),
    )


class SystemConfig(Base):
    """Key-value store for external service configurations (DB query, KB, industry, file parser)."""
    __tablename__ = "system_configs"

    config_key = Column(String(100), primary_key=True)
    config_value = Column(Text)
    display_name = Column(String(255), nullable=False)
    description = Column(Text)
    group_key = Column(String(50), nullable=False)
    is_secret = Column(Boolean, default=False, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(String(64))

    __table_args__ = (
        Index("idx_system_configs_group_key", "group_key"),
    )


class AdminSkill(Base):
    """Admin-managed skills stored in DB (replaces filesystem storage)."""
    __tablename__ = "admin_skills"

    skill_id      = Column(String(100), primary_key=True)
    skill_content = Column(Text, nullable=False)           # 完整 SKILL.md 原文
    display_name  = Column(String(255), nullable=False)    # 冗余字段，避免重复解析
    description   = Column(Text, nullable=False)
    version       = Column(String(50), nullable=False, default="1.0.0")
    tags          = Column(JSONType, default=list)
    allowed_tools = Column(JSONType, default=list)
    extra_files   = Column(JSONType, default=dict)             # {filename: content}
    is_enabled    = Column(Boolean, nullable=False, default=True)
    created_at    = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at    = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by    = Column(String(64))

    __table_args__ = (
        Index("idx_admin_skills_is_enabled", "is_enabled"),
        Index("idx_admin_skills_updated_at", "updated_at"),
    )


class AdminPromptPart(Base):
    """Admin-managed prompt parts stored in DB (overrides filesystem prompts)."""
    __tablename__ = "admin_prompt_parts"

    part_id       = Column(String(100), primary_key=True)   # e.g. "system/00_role"
    content       = Column(Text, nullable=False)
    display_name  = Column(String(255), nullable=False)
    sort_order    = Column(Integer, nullable=False, default=0)
    is_enabled    = Column(Boolean, nullable=False, default=True)
    created_at    = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at    = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by    = Column(String(64))

    __table_args__ = (
        Index("idx_admin_prompt_parts_sort_order", "sort_order"),
        Index("idx_admin_prompt_parts_is_enabled", "is_enabled"),
    )


class AdminPromptVersion(Base):
    """Version history for admin prompt parts."""
    __tablename__ = "admin_prompt_versions"

    version_id    = Column(BigInteger, primary_key=True, autoincrement=True)
    part_id       = Column(String(100), nullable=False)
    content       = Column(Text, nullable=False)
    display_name  = Column(String(255))
    sort_order    = Column(Integer)
    is_enabled    = Column(Boolean)
    created_at    = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    created_by    = Column(String(64))

    __table_args__ = (
        Index("idx_admin_prompt_versions_part_id", "part_id"),
        Index("idx_admin_prompt_versions_created_at", "created_at"),
    )


class AdminMcpServer(Base):
    """Admin-managed MCP server configurations stored in DB."""
    __tablename__ = "admin_mcp_servers"

    server_id    = Column(String(100), primary_key=True)
    display_name = Column(String(255), nullable=False)
    description  = Column(Text, nullable=False, default="")
    transport    = Column(String(20), nullable=False, default="stdio")
    command      = Column(String(500))
    args         = Column(JSONType, default=list)
    url          = Column(Text)
    env_vars     = Column(JSONType, default=dict)
    env_inherit  = Column(JSONType, default=list)
    headers      = Column(JSONType, default=dict)
    is_stable    = Column(Boolean, nullable=False, default=True)
    is_enabled   = Column(Boolean, nullable=False, default=True)
    sort_order   = Column(Integer, nullable=False, default=0)
    extra_config = Column(JSONType, default=dict)
    tools_json   = Column(JSONType, default=list)        # cached tool list from discovery
    created_at   = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at   = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by   = Column(String(64))

    __table_args__ = (
        CheckConstraint(
            "transport IN ('stdio', 'streamable_http', 'sse')",
            name="admin_mcp_servers_transport_check",
        ),
        Index("idx_admin_mcp_servers_is_enabled", "is_enabled"),
        Index("idx_admin_mcp_servers_sort_order", "sort_order"),
    )


class ModelRoleAssignment(Base):
    """Role → provider mapping.  Each role_key can have at most one provider."""
    __tablename__ = "model_role_assignments"

    role_key = Column(String(50), primary_key=True)
    provider_id = Column(
        String(64),
        ForeignKey("model_providers.provider_id", ondelete="CASCADE"),
        nullable=False,
    )
    updated_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(String(64))

    # Relationships
    provider = relationship("ModelProvider", back_populates="role_assignments")

    __table_args__ = (
        Index("idx_model_role_assignments_provider", "provider_id"),
    )


class ModelPricing(Base):
    """Model pricing configuration for token billing."""
    __tablename__ = "model_pricing"

    pricing_id   = Column(String(64), primary_key=True)
    model_name   = Column(String(255), nullable=False, unique=True)
    display_name = Column(String(255))
    input_price  = Column(Numeric(12, 6), nullable=False, default=0)
    output_price = Column(Numeric(12, 6), nullable=False, default=0)
    currency     = Column(String(10), nullable=False, default="CNY")
    is_active    = Column(Boolean, default=True, nullable=False)
    created_at   = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at   = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_model_pricing_model_name", "model_name"),
        Index("idx_model_pricing_active", "is_active"),
    )


class UserAgent(Base):
    """Custom sub-agent (admin-created or user-created)."""
    __tablename__ = "user_agents"

    agent_id        = Column(String(64), primary_key=True)
    owner_type      = Column(String(10), nullable=False)               # "admin" | "user"
    user_id         = Column(String(64), ForeignKey("users_shadow.user_id", ondelete="CASCADE"))
    name            = Column(String(255), nullable=False)
    avatar          = Column(Text)
    description     = Column(Text, default="")

    # Core config
    system_prompt       = Column(Text, nullable=False, default="")
    welcome_message     = Column(Text, default="")
    suggested_questions = Column(JSONType, default=list)

    # Capability bindings
    mcp_server_ids  = Column(JSONType, default=list)
    skill_ids       = Column(JSONType, default=list)
    kb_ids          = Column(JSONType, default=list)

    # Model config
    model_provider_id = Column(String(64), ForeignKey("model_providers.provider_id", ondelete="SET NULL"))
    temperature     = Column(Numeric(3, 2))
    max_tokens      = Column(Integer)

    # Runtime controls
    max_iters       = Column(Integer, default=10)
    timeout         = Column(Integer, default=120)
    code_exec_enabled = Column(Boolean, default=False)
    is_enabled      = Column(Boolean, default=True)
    sort_order      = Column(Integer, default=0)

    # Advanced config
    extra_config    = Column(JSONType, default=dict)

    # Metadata
    created_at      = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at      = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by      = Column(String(64))

    # Relationships
    user            = relationship("UserShadow", foreign_keys=[user_id], back_populates="user_agents")
    model_provider  = relationship("ModelProvider")

    __table_args__ = (
        CheckConstraint("owner_type IN ('admin', 'user')", name="user_agents_owner_type_check"),
        Index("idx_user_agents_owner_type", "owner_type"),
        Index("idx_user_agents_user_id", "user_id"),
        Index("idx_user_agents_is_enabled", "is_enabled"),
        Index("idx_user_agents_sort_order", "sort_order"),
        Index("idx_user_agents_updated_at", "updated_at"),
    )


class Plan(Base):
    """计划模式 - 计划表"""
    __tablename__ = "plans"

    plan_id         = Column(String(64), primary_key=True)
    user_id         = Column(String(64), ForeignKey("users_shadow.user_id", ondelete="CASCADE"), nullable=False)
    title           = Column(String(500), nullable=False)
    description     = Column(Text, default="")
    task_input      = Column(Text, nullable=False)
    status          = Column(String(20), nullable=False, default="draft")
    total_steps     = Column(Integer, default=0)
    completed_steps = Column(Integer, default=0)
    result_summary  = Column(Text)
    extra_data      = Column("metadata", JSONType, default={})
    created_at      = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at      = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    steps = relationship("PlanStep", back_populates="plan",
                         cascade="all, delete-orphan", order_by="PlanStep.step_order")

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'approved', 'running', 'completed', 'failed', 'cancelled')",
            name="plans_status_check",
        ),
        Index("idx_plans_user_id", "user_id"),
        Index("idx_plans_status", "status"),
    )


class PlanStep(Base):
    """计划模式 - 步骤表"""
    __tablename__ = "plan_steps"

    step_id         = Column(String(64), primary_key=True)
    plan_id         = Column(String(64), ForeignKey("plans.plan_id", ondelete="CASCADE"), nullable=False)
    step_order      = Column(Integer, nullable=False)
    title           = Column(String(500), nullable=False)
    description     = Column(Text, default="")
    expected_tools  = Column(JSONType, default=list)
    expected_skills = Column(JSONType, default=list)
    expected_agents = Column(JSONType, default=list)
    status          = Column(String(20), nullable=False, default="pending")
    result_summary  = Column(Text)
    tool_calls_log  = Column(JSONType, default=list)
    ai_output       = Column(Text)
    error_message   = Column(Text)
    started_at      = Column(TIMESTAMP(timezone=True))
    completed_at    = Column(TIMESTAMP(timezone=True))
    # Constraint-driven QA fields
    step_goal             = Column(Text, nullable=True)
    check_result          = Column(JSONType, nullable=True)
    local_constraint      = Column(JSONType, nullable=True)
    next_step_instruction = Column(JSONType, nullable=True)

    plan = relationship("Plan", back_populates="steps")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed', 'skipped')",
            name="plan_steps_status_check",
        ),
        Index("idx_plan_steps_plan_id", "plan_id"),
    )


class ScheduledTask(Base):
    """自动化 — 定时任务表"""
    __tablename__ = "scheduled_tasks"

    task_id           = Column(String(64), primary_key=True)
    user_id           = Column(String(64), ForeignKey("users_shadow.user_id", ondelete="CASCADE"), nullable=False)

    # 任务内容 — prompt 或 plan 二选一
    task_type         = Column(String(20), nullable=False)              # "prompt" | "plan"
    prompt            = Column(Text)
    plan_id           = Column(String(64), ForeignKey("plans.plan_id", ondelete="SET NULL"))

    # 调度配置
    cron_expression   = Column(String(100), nullable=False)
    recurring         = Column(Boolean, nullable=False, default=True)
    timezone          = Column(String(50), nullable=False, default="Asia/Shanghai")
    schedule_type     = Column(String(20), nullable=False, default="recurring")  # "recurring" | "once" | "manual"

    # 执行能力配置
    enabled_mcp_ids   = Column(JSONType, default=list)
    enabled_skill_ids = Column(JSONType, default=list)
    enabled_kb_ids    = Column(JSONType, default=list)
    enabled_agent_ids = Column(JSONType, default=list)

    # 状态
    status            = Column(String(20), nullable=False, default="active")
    next_run_at       = Column(TIMESTAMP(timezone=True))
    last_run_at       = Column(TIMESTAMP(timezone=True))
    run_count         = Column(Integer, default=0)
    max_runs          = Column(Integer)

    # 失败追踪
    consecutive_failures = Column(Integer, default=0)
    max_failures      = Column(Integer, default=3)
    last_error        = Column(Text)

    # 元信息
    name              = Column(String(200))
    description       = Column(Text, default="")
    extra_data        = Column("metadata", JSONType, default={})
    sidebar_activated = Column(Boolean, default=False, nullable=False)
    created_at        = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at        = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    user        = relationship("UserShadow")
    plan        = relationship("Plan")
    run_history = relationship("ScheduledTaskRun", back_populates="task",
                               cascade="all, delete-orphan",
                               order_by="ScheduledTaskRun.started_at.desc()")

    __table_args__ = (
        CheckConstraint("task_type IN ('prompt', 'plan')", name="scheduled_tasks_type_check"),
        CheckConstraint(
            "status IN ('active', 'paused', 'disabled', 'completed', 'expired')",
            name="scheduled_tasks_status_check",
        ),
        CheckConstraint(
            "schedule_type IN ('recurring', 'once', 'manual')",
            name="scheduled_tasks_schedule_type_check",
        ),
        Index("idx_scheduled_tasks_user_id", "user_id"),
        Index("idx_scheduled_tasks_status", "status"),
        Index("idx_scheduled_tasks_user_status", "user_id", "status"),
    )


class ToolCallLog(Base):
    """工具调用日志 — 每一次 MCP / 内置工具执行都落一行。"""
    __tablename__ = "tool_call_logs"

    id               = Column(String(64), primary_key=True)
    trace_id         = Column(String(64))
    chat_id          = Column(String(64), index=True)
    message_id       = Column(String(64))
    user_id          = Column(String(64), index=True)
    user_name        = Column(String(255))
    tool_name        = Column(String(128), nullable=False)
    tool_display_name= Column(String(255))
    tool_call_id     = Column(String(64))
    mcp_server       = Column(String(64))
    tool_args        = Column(JSONType)
    tool_result      = Column(JSONType)
    result_truncated = Column(Boolean, default=False, nullable=False)
    status           = Column(String(20), nullable=False, default="success")
    error_message    = Column(Text)
    duration_ms      = Column(Integer)
    source           = Column(String(20), nullable=False, default="main_agent")
    subagent_log_id  = Column(String(64), index=True)
    skill_log_id     = Column(String(64), index=True)
    started_at       = Column(TIMESTAMP(timezone=True))
    created_at       = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('success', 'failed', 'timeout')",
            name="tool_call_logs_status_check",
        ),
        CheckConstraint(
            "source IN ('main_agent', 'subagent', 'skill', 'automation')",
            name="tool_call_logs_source_check",
        ),
        Index("idx_tool_call_logs_created_at", "created_at"),
        Index("idx_tool_call_logs_user_created", "user_id", "created_at"),
        Index("idx_tool_call_logs_chat_created", "chat_id", "created_at"),
        Index("idx_tool_call_logs_tool_name", "tool_name", "created_at"),
        Index("idx_tool_call_logs_status", "status", "created_at"),
        Index("idx_tool_call_logs_trace_id", "trace_id"),
    )


class SubAgentCallLog(Base):
    """子智能体调用日志 — 一次子智能体 / 计划步骤的完整执行记录。"""
    __tablename__ = "subagent_call_logs"

    id                     = Column(String(64), primary_key=True)
    trace_id               = Column(String(64))
    chat_id                = Column(String(64), index=True)
    message_id             = Column(String(64))
    user_id                = Column(String(64), index=True)
    user_name              = Column(String(255))
    subagent_id            = Column(String(64))
    subagent_name          = Column(String(128), nullable=False)
    subagent_type          = Column(String(32))  # plan_mode / report_generator / user_agent ...
    plan_id                = Column(String(64))
    step_id                = Column(String(64))
    step_index             = Column(Integer)
    step_title             = Column(String(500))
    model                  = Column(String(128))
    input_messages         = Column(JSONType)
    output_content         = Column(Text)
    intermediate_steps     = Column(JSONType)
    token_usage            = Column(JSONType)
    tool_calls_count       = Column(Integer, default=0)
    skill_calls_count      = Column(Integer, default=0)
    status                 = Column(String(20), nullable=False, default="running")
    error_message          = Column(Text)
    duration_ms            = Column(Integer)
    parent_subagent_log_id = Column(String(64), index=True)
    started_at             = Column(TIMESTAMP(timezone=True))
    completed_at           = Column(TIMESTAMP(timezone=True))
    created_at             = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'success', 'failed', 'cancelled')",
            name="subagent_call_logs_status_check",
        ),
        Index("idx_subagent_logs_created_at", "created_at"),
        Index("idx_subagent_logs_user_created", "user_id", "created_at"),
        Index("idx_subagent_logs_chat_created", "chat_id", "created_at"),
        Index("idx_subagent_logs_subagent_name", "subagent_name", "created_at"),
        Index("idx_subagent_logs_status", "status", "created_at"),
        Index("idx_subagent_logs_plan_id", "plan_id"),
        Index("idx_subagent_logs_trace_id", "trace_id"),
    )


class SkillCallLog(Base):
    """技能调用日志 — view / run_script / auto_load 三种触发方式都记录。"""
    __tablename__ = "skill_call_logs"

    id                = Column(String(64), primary_key=True)
    trace_id          = Column(String(64))
    chat_id           = Column(String(64), index=True)
    message_id        = Column(String(64))
    user_id           = Column(String(64), index=True)
    user_name         = Column(String(255))
    skill_id          = Column(String(128), nullable=False)
    skill_name        = Column(String(255))
    skill_version     = Column(String(50))
    skill_source      = Column(String(20))          # filesystem / database
    invocation_type   = Column(String(20), nullable=False, default="auto_load")
    script_name       = Column(String(255))
    script_language   = Column(String(32))
    script_args       = Column(JSONType)
    script_stdin      = Column(Text)
    script_stdout     = Column(Text)
    script_stderr     = Column(Text)
    output_truncated  = Column(Boolean, default=False, nullable=False)
    exit_code         = Column(Integer)
    status            = Column(String(20), nullable=False, default="success")
    error_message     = Column(Text)
    duration_ms       = Column(Integer)
    source            = Column(String(20), nullable=False, default="main_agent")
    subagent_log_id   = Column(String(64), index=True)
    started_at        = Column(TIMESTAMP(timezone=True))
    created_at        = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "invocation_type IN ('view', 'run_script', 'auto_load')",
            name="skill_call_logs_invocation_check",
        ),
        CheckConstraint(
            "status IN ('success', 'failed', 'timeout')",
            name="skill_call_logs_status_check",
        ),
        CheckConstraint(
            "source IN ('main_agent', 'subagent', 'automation')",
            name="skill_call_logs_source_check",
        ),
        Index("idx_skill_call_logs_created_at", "created_at"),
        Index("idx_skill_call_logs_user_created", "user_id", "created_at"),
        Index("idx_skill_call_logs_chat_created", "chat_id", "created_at"),
        Index("idx_skill_call_logs_skill_name", "skill_name", "created_at"),
        Index("idx_skill_call_logs_invocation", "invocation_type", "created_at"),
        Index("idx_skill_call_logs_status", "status", "created_at"),
        Index("idx_skill_call_logs_trace_id", "trace_id"),
    )


class ScheduledTaskRun(Base):
    """自动化 — 执行记录表"""
    __tablename__ = "scheduled_task_runs"

    run_id          = Column(String(64), primary_key=True)
    task_id         = Column(String(64), ForeignKey("scheduled_tasks.task_id", ondelete="CASCADE"), nullable=False)
    status          = Column(String(20), nullable=False, default="running")
    chat_id         = Column(String(64))
    result_summary  = Column(Text)
    error_message   = Column(Text)
    started_at      = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at    = Column(TIMESTAMP(timezone=True))
    duration_ms     = Column(Integer)
    usage           = Column(JSONType, default={})

    task = relationship("ScheduledTask", back_populates="run_history")

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'success', 'failed')",
            name="scheduled_task_runs_status_check",
        ),
        Index("idx_scheduled_task_runs_task_id", "task_id"),
        Index("idx_scheduled_task_runs_started_at", "started_at"),
    )
