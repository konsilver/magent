"""API request/response models."""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any, List


class AttachmentItem(BaseModel):
    """单个文件附件"""
    name: str = Field(..., description="文件名")
    content: str = Field("", description="文件文本内容（供模型读取）")
    mime_type: str = Field("", description="MIME 类型")
    file_id: str = Field("", description="OSS 持久化后的文件 ID（供下载）")
    download_url: str = Field("", description="下载路径，如 /files/{file_id}")


class QuotedFollowUpItem(BaseModel):
    """追问引用信息"""
    text: str = Field(..., description="被引用的原始文本", min_length=1, max_length=8000)
    ts: Optional[int] = Field(None, description="前端消息时间戳（可选）")

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Quoted text cannot be empty")
        return v.strip()


class ChatRequest(BaseModel):
    """聊天请求模型"""
    chat_id: str = Field(..., description="会话ID，用于维持对话上下文", max_length=100)
    message: str = Field(..., description="用户消息内容", min_length=1, max_length=10000)
    model_name: Optional[str] = Field("qwen", description="使用的模型名称（qwen/deepseek）", max_length=50)
    user_id: Optional[str] = Field(None, description="用户ID（可选）", max_length=100)
    enable_thinking: bool = Field(True, description="是否启用思考模式；False 时切换为快速模式")
    attachments: List[AttachmentItem] = Field(
        default_factory=list,
        description="上传的文件附件列表",
    )
    enabled_kbs: Optional[List[str]] = Field(
        default=None,
        description="当前会话中启用的知识库 ID 列表（前端运行时注入）",
    )
    enabled_skills: Optional[List[str]] = Field(
        default=None,
        description="本次请求启用的 skill ID 列表（不传则使用用户/系统默认配置）",
    )
    enabled_mcps: Optional[List[str]] = Field(
        default=None,
        description="本次请求启用的 MCP 工具 ID 列表（不传则使用用户/系统默认配置）",
    )
    enabled_agents: Optional[List[str]] = Field(
        default=None,
        description="本次请求启用的子智能体 ID 列表（不传则使用用户/系统默认配置）",
    )
    agent_id: Optional[str] = Field(
        default=None,
        description="子智能体 ID，传入时使用该智能体配置对话（不传则使用主智能体）",
        max_length=64,
    )
    code_exec: bool = Field(
        default=False,
        description="是否启用代码执行能力（从实验室入口创建的对话）",
    )
    plan_chat: bool = Field(
        default=False,
        description="是否为计划模式对话（从应用中心入口创建的对话）",
    )
    skill_id: Optional[str] = Field(
        default=None,
        description="显式调用的技能 ID（斜杠命令选择）",
        max_length=64,
    )
    quoted_follow_up: Optional[QuotedFollowUpItem] = Field(
        default=None,
        description="追问场景下引用的原始文本，用于增强上下文理解",
    )

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        """Validate message is not just whitespace."""
        if not v or not v.strip():
            raise ValueError("Message cannot be empty or whitespace only")
        return v.strip()

    @field_validator("model_name")
    @classmethod
    def validate_model_name(cls, v: Optional[str]) -> Optional[str]:
        """Validate model name is in allowed list."""
        if v is None:
            return "qwen"
        allowed_models = ["qwen", "deepseek", "gpt-4", "claude"]
        if v not in allowed_models:
            raise ValueError(f"Invalid model name. Allowed: {', '.join(allowed_models)}")
        return v


class ChatResponse(BaseModel):
    """聊天响应模型"""
    chat_id: str = Field(..., description="会话ID")
    response: str = Field(..., description="AI响应内容")
    timestamp: str = Field(..., description="响应时间戳")
    is_markdown: bool = Field(False, description="响应是否为Markdown格式")
    route: Optional[str] = Field(None, description="路由信息（main/subagent）")
    sources: List[Dict[str, Any]] = Field(default_factory=list, description="数据来源列表")
    artifacts: List[Dict[str, Any]] = Field(default_factory=list, description="生成的附件列表")
    warnings: List[str] = Field(default_factory=list, description="警告信息列表")


class SessionInfo(BaseModel):
    """会话信息模型"""
    chat_id: str
    message_count: int
    created_at: str
    last_updated: str


class SessionDetail(SessionInfo):
    """会话详细信息模型"""
    messages: List[Dict[str, Any]]


class EnabledPatch(BaseModel):
    """能力启用/禁用模型"""
    enabled: bool = Field(..., description="是否启用")


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str
    service: str
    timestamp: str
