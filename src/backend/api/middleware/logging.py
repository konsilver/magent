"""HTTP logging and request-size-limit middleware.

Uses pure ASGI middleware instead of Starlette's BaseHTTPMiddleware
to avoid breaking SSE streaming.  BaseHTTPMiddleware wraps response
bodies in a background task that can silently drop yields from async
generators when there is a gap between consecutive chunks.
"""

import re
import time
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from core.config.settings import settings
from core.infra.logging import get_logger, generate_trace_id, LogContext
from core.infra.metrics import (
    record_http_request,
    http_request_size_bytes,
    http_response_size_bytes,
)

logger = get_logger(__name__)

# Sensitive field patterns for log sanitization
SENSITIVE_PATTERNS = [
    (
        re.compile(
            r'"(password|token|secret|api_key|authorization)"\s*:\s*"[^"]*"',
            re.IGNORECASE,
        ),
        r'"\1": "***"',
    ),
    (re.compile(r"Bearer\s+[\w\-\.]+", re.IGNORECASE), "Bearer ***"),
]


def sanitize_log(text: str) -> str:
    """Sanitize sensitive information from log text."""
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class LoggingMiddleware:
    """Pure ASGI logging middleware — does NOT wrap the response body.

    Unlike ``@app.middleware("http")`` (BaseHTTPMiddleware), this class
    intercepts only the response *start* message to capture the status
    code and attach headers, then passes all body chunks straight through.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    # Paths that should never be logged (healthcheck noise)
    _SILENT_PATHS = frozenset({"/health", "/healthz", "/ready", "/metrics"})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)

        # Skip logging for health/metrics endpoints to avoid log spam from polling
        if request.url.path in self._SILENT_PATHS:
            await self.app(scope, receive, send)
            return

        trace_id = request.headers.get("X-Trace-ID") or generate_trace_id()
        user_id = getattr(request.state, "user_id", None) if hasattr(request, "state") else None
        chat_id = request.query_params.get("chat_id") or request.path_params.get("chat_id")

        start_time = time.time()
        status_code = 0

        with LogContext(trace_id=trace_id, user_id=user_id, chat_id=chat_id):
            logger.info(
                "request_started",
                method=request.method,
                path=request.url.path,
                client_ip=request.client.host if request.client else None,
            )

            async def send_wrapper(message: dict) -> None:
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = message.get("status", 0)
                    # Inject trace_id header
                    headers = list(message.get("headers", []))
                    headers.append((b"x-trace-id", trace_id.encode()))
                    message = {**message, "headers": headers}
                await send(message)

            try:
                await self.app(scope, receive, send_wrapper)
            except Exception as e:
                duration = time.time() - start_time
                logger.error(
                    "request_failed",
                    method=request.method,
                    path=request.url.path,
                    error=str(e),
                    latency=duration,
                    exc_info=True,
                )
                raise
            finally:
                duration = time.time() - start_time
                if status_code:
                    endpoint = request.url.path
                    record_http_request(
                        method=request.method,
                        endpoint=endpoint,
                        status_code=status_code,
                        duration=duration,
                    )
                    content_length = request.headers.get("content-length")
                    if content_length:
                        try:
                            http_request_size_bytes.labels(
                                method=request.method, endpoint=endpoint
                            ).observe(int(content_length))
                        except (ValueError, TypeError):
                            pass
                    logger.info(
                        "request_completed",
                        method=request.method,
                        path=request.url.path,
                        status_code=status_code,
                        latency=duration,
                    )


class RequestSizeLimitMiddleware:
    """Pure ASGI middleware to reject oversized request bodies."""

    def __init__(self, app: ASGIApp, max_size: int) -> None:
        self.app = app
        self.max_size = max_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > self.max_size:
                        response = JSONResponse(
                            status_code=413,
                            content={
                                "code": 41301,
                                "message": (
                                    f"Request body too large. "
                                    f"Maximum size: {self.max_size} bytes"
                                ),
                                "data": {},
                            },
                        )
                        await response(scope, receive, send)
                        return
                except ValueError:
                    pass

        await self.app(scope, receive, send)


def setup_logging_middleware(app: FastAPI) -> None:
    """Register request-size-limit and logging middleware on *app*.

    Uses pure ASGI middleware classes (not @app.middleware) to avoid
    breaking SSE streaming responses.

    Middleware is added via ``app.add_middleware`` in the order they
    should wrap: first = outermost.
    """
    max_size = settings.server.max_request_size
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware, max_size=max_size)
