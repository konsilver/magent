"""Shared authentication dependencies."""

import os
from typing import Optional

from fastapi import Header, HTTPException

from core.config.settings import settings


def _require_token(env_var: str, settings_attr: str, label: str):
    """Factory for Bearer-token auth dependencies."""
    def dependency(authorization: Optional[str] = Header(None)) -> None:
        token = os.getenv(env_var) or getattr(settings.auth, settings_attr, "")
        if not token:
            raise HTTPException(status_code=503, detail=f"{label} access not configured ({env_var} not set)")
        if not authorization or authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="Unauthorized")
    return dependency


require_admin = _require_token("ADMIN_TOKEN", "admin_token", "Admin")
require_config = _require_token("CONFIG_TOKEN", "config_token", "Config")
