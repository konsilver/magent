-- Jingxin-Agent 数据库模式设计
-- PostgreSQL 14+
-- 字符集: UTF-8
-- 时区: UTC

-- ==================== 用户影子表 ====================
CREATE TABLE users_shadow (
    user_id VARCHAR(64) PRIMARY KEY,
    username VARCHAR(255) NOT NULL,
    email VARCHAR(255),
    avatar_url TEXT,
    user_center_id VARCHAR(64),  -- 用户中心的原始 ID
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_sync_at TIMESTAMP WITH TIME ZONE,  -- 最后同步用户中心时间

    CONSTRAINT users_shadow_email_check CHECK (email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
);

CREATE INDEX idx_users_shadow_user_center_id ON users_shadow(user_center_id);
CREATE INDEX idx_users_shadow_updated_at ON users_shadow(updated_at DESC);

COMMENT ON TABLE users_shadow IS '用户影子表 - 从用户中心同步的用户基本信息';
COMMENT ON COLUMN users_shadow.user_center_id IS '用户中心的原始 ID';
COMMENT ON COLUMN users_shadow.last_sync_at IS '最后一次从用户中心同步的时间';

-- ==================== 聊天会话表 ====================
CREATE TABLE chat_sessions (
    chat_id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL REFERENCES users_shadow(user_id) ON DELETE CASCADE,
    title VARCHAR(500) NOT NULL DEFAULT '新对话',
    message_count INTEGER DEFAULT 0,
    pinned BOOLEAN DEFAULT FALSE,
    favorite BOOLEAN DEFAULT FALSE,
    archived BOOLEAN DEFAULT FALSE,
    deleted_at TIMESTAMP WITH TIME ZONE,  -- 软删除标记
    metadata JSONB DEFAULT '{}'::jsonb,  -- 包含 theme, tags, model_preference 等
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chat_sessions_title_length CHECK (char_length(title) >= 1),
    CONSTRAINT chat_sessions_message_count_check CHECK (message_count >= 0)
);

-- 索引策略
CREATE INDEX idx_chat_sessions_user_id ON chat_sessions(user_id);
CREATE INDEX idx_chat_sessions_updated_at ON chat_sessions(updated_at DESC);
CREATE INDEX idx_chat_sessions_user_updated ON chat_sessions(user_id, updated_at DESC);  -- 复合索引优化列表查询
CREATE INDEX idx_chat_sessions_pinned ON chat_sessions(user_id, pinned, updated_at DESC) WHERE pinned = TRUE;
CREATE INDEX idx_chat_sessions_favorite ON chat_sessions(user_id, favorite, updated_at DESC) WHERE favorite = TRUE;
CREATE INDEX idx_chat_sessions_deleted ON chat_sessions(deleted_at) WHERE deleted_at IS NOT NULL;  -- 部分索引优化软删除查询
CREATE INDEX idx_chat_sessions_metadata_gin ON chat_sessions USING GIN (metadata jsonb_path_ops);  -- GIN 索引支持 JSONB 查询

COMMENT ON TABLE chat_sessions IS '聊天会话表 - 保存每个聊天会话的元数据';
COMMENT ON COLUMN chat_sessions.deleted_at IS '软删除时间戳，NULL 表示未删除';
COMMENT ON COLUMN chat_sessions.metadata IS 'JSONB 字段存储扩展信息：{"theme": "light", "tags": ["工作"], "model": "gpt-4"}';

-- ==================== 聊天消息表 ====================
CREATE TABLE chat_messages (
    message_id VARCHAR(64) PRIMARY KEY,
    chat_id VARCHAR(64) NOT NULL REFERENCES chat_sessions(chat_id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,  -- 'user', 'assistant', 'system', 'tool'
    content TEXT NOT NULL,
    model VARCHAR(100),  -- 使用的模型名称
    tool_calls JSONB,  -- 工具调用信息 [{"id": "tool_1", "name": "search", "args": {...}, "result": {...}}]
    usage JSONB,  -- token 使用统计 {"prompt_tokens": 100, "completion_tokens": 50}
    error JSONB,  -- 错误信息 {"code": 50001, "message": "..."}
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chat_messages_role_check CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    CONSTRAINT chat_messages_content_length CHECK (char_length(content) <= 100000)
);

-- 索引策略
CREATE INDEX idx_chat_messages_chat_id ON chat_messages(chat_id);
CREATE INDEX idx_chat_messages_chat_created ON chat_messages(chat_id, created_at DESC);  -- 复合索引优化消息列表查询
CREATE INDEX idx_chat_messages_role ON chat_messages(chat_id, role);
CREATE INDEX idx_chat_messages_created_at ON chat_messages(created_at DESC);
CREATE INDEX idx_chat_messages_tool_calls_gin ON chat_messages USING GIN (tool_calls jsonb_path_ops) WHERE tool_calls IS NOT NULL;

COMMENT ON TABLE chat_messages IS '聊天消息表 - 保存每条聊天消息的完整内容';
COMMENT ON COLUMN chat_messages.tool_calls IS 'JSONB 数组存储工具调用: [{"id": "...", "name": "search", "args": {...}, "result": {...}}]';
COMMENT ON COLUMN chat_messages.usage IS 'Token 使用统计: {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}';

-- ==================== 能力目录覆盖表 ====================
CREATE TABLE catalog_overrides (
    override_id SERIAL PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL REFERENCES users_shadow(user_id) ON DELETE CASCADE,
    kind VARCHAR(20) NOT NULL,  -- 'skill', 'agent', 'mcp'
    item_id VARCHAR(100) NOT NULL,  -- skill ID, agent ID, 或 MCP server ID
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    config JSONB DEFAULT '{}'::jsonb,  -- 用户自定义配置
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT catalog_overrides_kind_check CHECK (kind IN ('skill', 'agent', 'mcp')),
    CONSTRAINT catalog_overrides_unique_user_kind_item UNIQUE (user_id, kind, item_id)
);

CREATE INDEX idx_catalog_overrides_user_id ON catalog_overrides(user_id);
CREATE INDEX idx_catalog_overrides_kind ON catalog_overrides(kind, enabled);

COMMENT ON TABLE catalog_overrides IS '能力目录覆盖表 - 保存用户对 skills/agents/MCPs 的个性化配置';
COMMENT ON COLUMN catalog_overrides.kind IS '能力类型: skill/agent/mcp';
COMMENT ON COLUMN catalog_overrides.config IS '用户自定义配置（覆盖默认配置）';

-- ==================== 知识库空间表 ====================
CREATE TABLE kb_spaces (
    kb_id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL REFERENCES users_shadow(user_id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    document_count INTEGER DEFAULT 0,
    total_size_bytes BIGINT DEFAULT 0,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP WITH TIME ZONE,  -- 软删除

    CONSTRAINT kb_spaces_name_length CHECK (char_length(name) >= 1 AND char_length(name) <= 255),
    CONSTRAINT kb_spaces_document_count_check CHECK (document_count >= 0),
    CONSTRAINT kb_spaces_total_size_check CHECK (total_size_bytes >= 0)
);

CREATE INDEX idx_kb_spaces_user_id ON kb_spaces(user_id);
CREATE INDEX idx_kb_spaces_updated_at ON kb_spaces(updated_at DESC);
CREATE INDEX idx_kb_spaces_deleted ON kb_spaces(deleted_at) WHERE deleted_at IS NOT NULL;

COMMENT ON TABLE kb_spaces IS '知识库空间表 - 用户创建的知识库';
COMMENT ON COLUMN kb_spaces.total_size_bytes IS '所有文档的总大小（字节）';

-- ==================== 知识库文档表 ====================
CREATE TABLE kb_documents (
    document_id VARCHAR(64) PRIMARY KEY,
    kb_id VARCHAR(64) NOT NULL REFERENCES kb_spaces(kb_id) ON DELETE CASCADE,
    title VARCHAR(500) NOT NULL,
    filename VARCHAR(500) NOT NULL,
    size_bytes BIGINT NOT NULL,
    mime_type VARCHAR(100) NOT NULL,
    storage_key TEXT NOT NULL,  -- S3 对象键
    storage_url TEXT,  -- 可选：CDN URL
    checksum VARCHAR(64),  -- SHA256 校验和
    metadata JSONB DEFAULT '{}'::jsonb,  -- {"tags": [], "source": "upload"}
    uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP WITH TIME ZONE,  -- 软删除

    CONSTRAINT kb_documents_size_check CHECK (size_bytes > 0),
    CONSTRAINT kb_documents_filename_length CHECK (char_length(filename) >= 1)
);

CREATE INDEX idx_kb_documents_kb_id ON kb_documents(kb_id);
CREATE INDEX idx_kb_documents_uploaded_at ON kb_documents(uploaded_at DESC);
CREATE INDEX idx_kb_documents_kb_uploaded ON kb_documents(kb_id, uploaded_at DESC);
CREATE INDEX idx_kb_documents_deleted ON kb_documents(deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX idx_kb_documents_metadata_gin ON kb_documents USING GIN (metadata jsonb_path_ops);

COMMENT ON TABLE kb_documents IS '知识库文档表 - 保存上传到知识库的文档元数据';
COMMENT ON COLUMN kb_documents.storage_key IS 'S3 对象键，格式：{env}/{user_id}/{kb_id}/{timestamp}_{filename}';
COMMENT ON COLUMN kb_documents.checksum IS 'SHA256 校验和，用于去重和完整性校验';

-- ==================== 工件表 ====================
CREATE TABLE artifacts (
    artifact_id VARCHAR(64) PRIMARY KEY,
    chat_id VARCHAR(64) REFERENCES chat_sessions(chat_id) ON DELETE SET NULL,  -- 可选，允许 NULL
    user_id VARCHAR(64) NOT NULL REFERENCES users_shadow(user_id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL,  -- 'report', 'chart', 'document', 'code', etc.
    title VARCHAR(500) NOT NULL,
    filename VARCHAR(500) NOT NULL,
    size_bytes BIGINT NOT NULL,
    mime_type VARCHAR(100) NOT NULL,
    storage_key TEXT NOT NULL,
    storage_url TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,  -- {"format": "pdf", "generated_by": "report_export_mcp"}
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP WITH TIME ZONE,  -- 软删除

    CONSTRAINT artifacts_size_check CHECK (size_bytes > 0),
    CONSTRAINT artifacts_type_check CHECK (type IN ('report', 'chart', 'document', 'code', 'other'))
);

CREATE INDEX idx_artifacts_user_id ON artifacts(user_id);
CREATE INDEX idx_artifacts_chat_id ON artifacts(chat_id);
CREATE INDEX idx_artifacts_type ON artifacts(type);
CREATE INDEX idx_artifacts_created_at ON artifacts(created_at DESC);
CREATE INDEX idx_artifacts_user_created ON artifacts(user_id, created_at DESC);
CREATE INDEX idx_artifacts_deleted ON artifacts(deleted_at) WHERE deleted_at IS NOT NULL;

COMMENT ON TABLE artifacts IS '工件表 - 保存 AI 生成的报告、图表等文件';
COMMENT ON COLUMN artifacts.type IS '工件类型：report/chart/document/code/other';
COMMENT ON COLUMN artifacts.chat_id IS '关联的会话 ID（可选）';

-- ==================== 审计日志表 ====================
CREATE TABLE audit_logs (
    log_id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(64) REFERENCES users_shadow(user_id) ON DELETE SET NULL,
    action VARCHAR(100) NOT NULL,  -- 'chat.create', 'session.delete', 'file.download', etc.
    resource_type VARCHAR(50),  -- 'chat_session', 'artifact', 'kb_document'
    resource_id VARCHAR(64),
    details JSONB DEFAULT '{}'::jsonb,  -- 操作详情
    ip_address INET,
    user_agent TEXT,
    trace_id VARCHAR(64),
    status VARCHAR(20) DEFAULT 'success',  -- 'success', 'failure', 'error'
    error_code INTEGER,  -- 错误码
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT audit_logs_status_check CHECK (status IN ('success', 'failure', 'error'))
);

-- 索引策略（审计日志表经常用于查询和分析）
CREATE INDEX idx_audit_logs_user_id ON audit_logs(user_id);
CREATE INDEX idx_audit_logs_action ON audit_logs(action);
CREATE INDEX idx_audit_logs_resource ON audit_logs(resource_type, resource_id);
CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at DESC);
CREATE INDEX idx_audit_logs_user_created ON audit_logs(user_id, created_at DESC);
CREATE INDEX idx_audit_logs_trace_id ON audit_logs(trace_id);
CREATE INDEX idx_audit_logs_status ON audit_logs(status, created_at DESC) WHERE status != 'success';  -- 部分索引优化错误查询

COMMENT ON TABLE audit_logs IS '审计日志表 - 记录所有关键操作用于审计和排查';
COMMENT ON COLUMN audit_logs.action IS '操作类型：chat.create, session.delete, file.download 等';
COMMENT ON COLUMN audit_logs.details IS 'JSONB 存储操作详情：{"old_value": ..., "new_value": ...}';
COMMENT ON COLUMN audit_logs.trace_id IS '关联的请求 trace_id';

-- ==================== 触发器 ====================

-- 更新 updated_at 触发器函数
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 为所有有 updated_at 字段的表添加触发器
CREATE TRIGGER update_users_shadow_updated_at BEFORE UPDATE ON users_shadow
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_chat_sessions_updated_at BEFORE UPDATE ON chat_sessions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_catalog_overrides_updated_at BEFORE UPDATE ON catalog_overrides
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_kb_spaces_updated_at BEFORE UPDATE ON kb_spaces
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 自动更新 chat_sessions.message_count 触发器
CREATE OR REPLACE FUNCTION update_chat_session_message_count()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE chat_sessions SET message_count = message_count + 1 WHERE chat_id = NEW.chat_id;
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE chat_sessions SET message_count = GREATEST(0, message_count - 1) WHERE chat_id = OLD.chat_id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_message_count_on_insert AFTER INSERT ON chat_messages
    FOR EACH ROW EXECUTE FUNCTION update_chat_session_message_count();

CREATE TRIGGER update_message_count_on_delete AFTER DELETE ON chat_messages
    FOR EACH ROW EXECUTE FUNCTION update_chat_session_message_count();

-- 自动更新 kb_spaces.document_count 和 total_size_bytes 触发器
CREATE OR REPLACE FUNCTION update_kb_space_stats()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE kb_spaces
        SET document_count = document_count + 1,
            total_size_bytes = total_size_bytes + NEW.size_bytes
        WHERE kb_id = NEW.kb_id;
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE kb_spaces
        SET document_count = GREATEST(0, document_count - 1),
            total_size_bytes = GREATEST(0, total_size_bytes - OLD.size_bytes)
        WHERE kb_id = OLD.kb_id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_kb_stats_on_insert AFTER INSERT ON kb_documents
    FOR EACH ROW EXECUTE FUNCTION update_kb_space_stats();

CREATE TRIGGER update_kb_stats_on_delete AFTER DELETE ON kb_documents
    FOR EACH ROW EXECUTE FUNCTION update_kb_space_stats();

-- ==================== 分区策略（可选，用于大数据量场景）====================

-- 审计日志表按月分区（示例，生产环境按需启用）
-- CREATE TABLE audit_logs_2026_02 PARTITION OF audit_logs
--     FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');

-- ==================== 数据保留策略 ====================

COMMENT ON TABLE chat_sessions IS '
数据保留策略:
- 软删除保留期: 30 天
- 归档条件: deleted_at < NOW() - INTERVAL ''30 days''
- 硬删除: 归档后 90 天
';

COMMENT ON TABLE kb_documents IS '
数据保留策略:
- 软删除保留期: 30 天
- S3 生命周期: 90 天归档, 1 年删除
';

COMMENT ON TABLE audit_logs IS '
数据保留策略:
- 在线保留期: 90 天
- 归档策略: 90 天后移至冷存储
- 最终删除: 1 年后
';

-- ==================== 性能优化建议 ====================

-- 1. 定期 VACUUM 和 ANALYZE
-- VACUUM ANALYZE chat_sessions;
-- VACUUM ANALYZE chat_messages;

-- 2. 连接池配置
-- 推荐 pgBouncer 或 SQLAlchemy 连接池
-- Pool size: 20-50 (根据并发量调整)

-- 3. 慢查询监控
-- ALTER DATABASE jingxin_prod SET log_min_duration_statement = 1000;  -- 记录超过 1s 的查询

-- 4. 定期清理软删除数据
-- DELETE FROM chat_sessions WHERE deleted_at < NOW() - INTERVAL '30 days';
