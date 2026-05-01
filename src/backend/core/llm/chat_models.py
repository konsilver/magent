"""Model factory utilities (AgentScope backend).

Important: do NOT construct model instances at import time.
This keeps the FastAPI app importable even when the DB has no rows.

All model configuration is resolved from the DB via ModelConfigService.
"""

from __future__ import annotations

import logging
import time
from typing import Optional, Tuple

from agentscope.model import OpenAIChatModel

from prompts.prompt_config import ModelConfig

logger = logging.getLogger(__name__)

# TTL cache for model instances — avoids repeated DB queries on every bare-agent creation.
# Key: (role_key, disable_thinking, stream). TTL: 60 seconds.
_MODEL_CACHE: dict[str, Tuple[OpenAIChatModel, float]] = {}
_MODEL_CACHE_TTL = 60.0


def make_chat_model(
    *,
    model: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    base_url: str,
    api_key: str,
    max_input_tokens: Optional[int] = None,
    disable_thinking: bool = False,
    stream: bool = False,
) -> OpenAIChatModel:
    """Construct an OpenAIChatModel with safe defaults."""

    generate_kwargs: dict = {
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if disable_thinking:
        generate_kwargs["extra_body"] = {
            "chat_template_kwargs": {"enable_thinking": False}
        }

    instance = OpenAIChatModel(
        model_name=model or "dummy-model",
        api_key=api_key or "DUMMY",
        stream=stream,
        client_kwargs={
            "base_url": base_url or "https://api.openai.com/v1",
            "timeout": timeout,
        },
        generate_kwargs=generate_kwargs,
    )
    # Store max_input_tokens as a custom attribute for summarization logic
    instance._max_input_tokens = max_input_tokens  # type: ignore[attr-defined]
    return instance


def _resolve_or_dummy(role_key: str):
    """Resolve config from DB, return None if not available."""
    try:
        from core.config.model_config import ModelConfigService
        return ModelConfigService.get_instance().resolve(role_key)
    except Exception as exc:
        logger.warning("ModelConfigService unavailable for role '%s': %s", role_key, exc)
        return None


def get_default_model(
    cfg: ModelConfig | None = None,
    disable_thinking: bool = False,
    stream: bool = False,
) -> OpenAIChatModel:
    _cache_key = f"main_agent:{disable_thinking}:{stream}"
    _cached = _MODEL_CACHE.get(_cache_key)
    if _cached and (time.monotonic() - _cached[1]) < _MODEL_CACHE_TTL:
        return _cached[0]

    cfg = cfg or ModelConfig()
    resolved = _resolve_or_dummy("main_agent")
    if resolved:
        model = make_chat_model(
            model=resolved.model_name,
            temperature=resolved.temperature,
            max_tokens=resolved.max_tokens,
            timeout=resolved.timeout,
            base_url=resolved.base_url,
            api_key=resolved.api_key,
            disable_thinking=disable_thinking,
            stream=stream,
        )
    else:
        # Fallback: dummy model so the app can still start
        model = make_chat_model(
            model="dummy-model",
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
            base_url="",
            api_key="",
            disable_thinking=disable_thinking,
            stream=stream,
        )
    _MODEL_CACHE[_cache_key] = (model, time.monotonic())
    return model


def get_summarize_model(cfg: ModelConfig | None = None) -> OpenAIChatModel:
    cfg = cfg or ModelConfig()
    resolved = _resolve_or_dummy("summarizer")
    if resolved:
        model_name = resolved.model_name
        if "openai:" not in model_name:
            model_name = "openai:" + model_name
        return make_chat_model(
            model=model_name,
            temperature=resolved.temperature,
            max_tokens=resolved.max_tokens,
            timeout=resolved.timeout,
            base_url=resolved.base_url,
            api_key=resolved.api_key,
            max_input_tokens=128000,
        )
    return make_chat_model(
        model="dummy-model",
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        timeout=cfg.timeout,
        base_url="",
        api_key="",
        max_input_tokens=128000,
    )
