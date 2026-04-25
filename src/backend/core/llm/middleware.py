"""Runtime context for agent execution.

After AgentScope migration, middleware classes have been replaced by hooks
(see core.llm.hooks). This module retains ModelContext for backward
compatibility with code that imports it from here.
"""

from __future__ import annotations

# Re-export ModelContext from its new home in hooks.py
from core.llm.hooks import ModelContext  # noqa: F401

__all__ = ["ModelContext"]
