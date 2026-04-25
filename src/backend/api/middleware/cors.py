"""CORS middleware configuration."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config.settings import settings

logger = logging.getLogger(__name__)


def setup_cors(app: FastAPI) -> None:
    """Configure CORS middleware based on environment.

    In production, uses the domain whitelist from CORS_ORIGINS env var.
    In development, allows all origins.
    """
    allowed_origins_str = settings.server.cors_origins
    configured_origins = [
        origin.strip()
        for origin in allowed_origins_str.split(",")
        if origin.strip()
    ]

    if settings.server.is_prod:
        allowed_origins = configured_origins
        if not allowed_origins:
            logger.warning(
                "CORS_ORIGINS is empty in production mode. "
                "No cross-origin requests will be allowed. "
                "Set CORS_ORIGINS env var to a comma-separated list of allowed origins."
            )
    else:
        if configured_origins and configured_origins != ["*"]:
            allowed_origins = configured_origins
        else:
            allowed_origins = [
                "http://localhost:3000",
                "http://localhost:3002",
                "http://localhost:3005",
                "http://localhost:5173",
                "http://127.0.0.1:3000",
                "http://127.0.0.1:3002",
                "http://127.0.0.1:3005",
                "http://127.0.0.1:5173",
            ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
