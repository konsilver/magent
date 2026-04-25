"""Conversation summary API routes."""

from typing import List, Dict, Any
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.auth.backend import get_current_user, UserContext
from core.infra.responses import success_response
from core.llm.summarizer import get_summarizer
from core.infra.exceptions import BadRequestError

router = APIRouter(prefix="/v1/summary", tags=["Summary"])


class SummarizeRequest(BaseModel):
    """Request model for conversation summarization."""
    messages: List[Dict[str, str]] = Field(
        ...,
        description="List of conversation messages with 'role' and 'content'"
    )


@router.post("", summary="生成对话摘要")
async def summarize_conversation(
    request: SummarizeRequest,
    user: UserContext = Depends(get_current_user)
):
    """
    Generate a concise summary/title for a conversation.

    This endpoint uses an LLM to analyze the conversation and generate
    a short, meaningful title (max 20 characters).

    Args:
        request: List of messages to summarize
        user: Current authenticated user

    Returns:
        Summary title or fallback if generation fails
    """
    if not request.messages:
        raise BadRequestError(
            message="Messages list cannot be empty",
            data={"provided_messages": len(request.messages)}
        )

    # Get summarizer and generate summary
    summarizer = get_summarizer()

    # If feature is disabled, return early without calling LLM
    if not summarizer.enabled:
        return success_response(
            data={"summary": None, "enabled": False},
            message="Summary feature is disabled"
        )

    summary = await summarizer.summarize_conversation(request.messages)

    # Fallback: use first user message if summarization fails
    if not summary:
        for msg in request.messages:
            if msg.get("role") == "user" and msg.get("content"):
                summary = msg["content"][:18]
                break
        summary = summary or "新对话"

    return success_response(
        data={"summary": summary, "enabled": True},
        message="Summary generated successfully"
    )
