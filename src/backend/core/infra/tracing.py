"""
Distributed tracing with OpenTelemetry for Jingxin-Agent.

This module provides distributed tracing capabilities:
- OpenTelemetry integration with Jaeger
- Traced decorator for automatic span creation
- Context propagation across service boundaries
- Key operation tracking (DB, HTTP, model calls)
"""

import functools
from typing import Any, Callable, Optional, Dict
from contextlib import contextmanager

from core.config.settings import settings

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import Status, StatusCode, Span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


# Global tracer instance
_tracer: Optional[trace.Tracer] = None
_enabled: bool = False


def is_tracing_enabled() -> bool:
    """Check if tracing is enabled."""
    return settings.tracing.enabled


def setup_tracing() -> None:
    """
    Configure OpenTelemetry tracing with Jaeger exporter.

    Environment variables:
        TRACING_ENABLED: Enable/disable tracing (default: false)
        JAEGER_HOST: Jaeger agent host (default: localhost)
        JAEGER_PORT: Jaeger agent port (default: 6831)
        SERVICE_NAME: Service name for tracing (default: jingxin-agent)
        ENV: Environment name (dev/staging/prod)
    """
    global _tracer, _enabled

    if not is_tracing_enabled():
        _enabled = False
        return

    # Service resource
    service_name = settings.tracing.service_name
    environment = settings.server.env

    resource = Resource.create({
        SERVICE_NAME: service_name,
        SERVICE_VERSION: "0.1.0",
        "deployment.environment": environment,
    })

    # OTLP exporter (compatible with Jaeger, Tempo, etc.)
    otlp_endpoint = f"http://{settings.tracing.jaeger_host}:4317"
    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)

    # Tracer provider
    provider = TracerProvider(resource=resource)
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)

    # Set global tracer provider
    trace.set_tracer_provider(provider)

    # Get tracer instance
    _tracer = trace.get_tracer(__name__)
    _enabled = True

    print(f"Tracing enabled: {service_name} -> {otlp_endpoint}")


def get_tracer() -> trace.Tracer:
    """
    Get the global tracer instance.

    Returns:
        Tracer instance or NoOp tracer if tracing is disabled
    """
    global _tracer
    if _tracer is None:
        return trace.get_tracer(__name__)
    return _tracer


def traced(
    operation_name: Optional[str] = None,
    attributes: Optional[Dict[str, Any]] = None
):
    """
    Decorator to automatically trace a function.

    Args:
        operation_name: Name of the operation (defaults to function name)
        attributes: Additional attributes to add to the span

    Example:
        @traced("user_authentication")
        async def authenticate_user(token: str):
            ...

        @traced(attributes={"db.system": "postgresql"})
        def query_database(query: str):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            if not _enabled:
                return await func(*args, **kwargs)

            span_name = operation_name or f"{func.__module__}.{func.__name__}"
            tracer = get_tracer()

            with tracer.start_as_current_span(span_name) as span:
                # Add function info
                span.set_attribute("code.function", func.__name__)
                span.set_attribute("code.namespace", func.__module__)

                # Add custom attributes
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, value)

                try:
                    result = await func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            if not _enabled:
                return func(*args, **kwargs)

            span_name = operation_name or f"{func.__module__}.{func.__name__}"
            tracer = get_tracer()

            with tracer.start_as_current_span(span_name) as span:
                # Add function info
                span.set_attribute("code.function", func.__name__)
                span.set_attribute("code.namespace", func.__module__)

                # Add custom attributes
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, value)

                try:
                    result = func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise

        # Return appropriate wrapper based on function type
        if functools.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


@contextmanager
def trace_span(
    name: str,
    attributes: Optional[Dict[str, Any]] = None,
    kind: trace.SpanKind = trace.SpanKind.INTERNAL
):
    """
    Context manager for creating a traced span.

    Args:
        name: Span name
        attributes: Span attributes
        kind: Span kind (INTERNAL, SERVER, CLIENT, etc.)

    Example:
        with trace_span("database_query", {"db.table": "users"}):
            result = db.query("SELECT * FROM users")
    """
    if not _enabled:
        yield None
        return

    tracer = get_tracer()

    with tracer.start_as_current_span(name, kind=kind) as span:
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, value)

        try:
            yield span
            span.set_status(Status(StatusCode.OK))
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise


def add_span_attribute(key: str, value: Any) -> None:
    """
    Add an attribute to the current span.

    Args:
        key: Attribute key
        value: Attribute value
    """
    if not _enabled:
        return

    span = trace.get_current_span()
    if span:
        span.set_attribute(key, value)


def add_span_event(name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
    """
    Add an event to the current span.

    Args:
        name: Event name
        attributes: Event attributes
    """
    if not _enabled:
        return

    span = trace.get_current_span()
    if span:
        span.add_event(name, attributes or {})


def trace_http_request(
    method: str,
    url: str,
    status_code: Optional[int] = None,
    error: Optional[str] = None
) -> None:
    """
    Add HTTP request attributes to the current span.

    Args:
        method: HTTP method
        url: Request URL
        status_code: Response status code
        error: Error message if request failed
    """
    if not _enabled:
        return

    span = trace.get_current_span()
    if span:
        span.set_attribute("http.method", method)
        span.set_attribute("http.url", url)
        if status_code:
            span.set_attribute("http.status_code", status_code)
        if error:
            span.set_attribute("http.error", error)
            span.set_status(Status(StatusCode.ERROR, error))


def trace_db_query(
    operation: str,
    table: str,
    query: Optional[str] = None,
    error: Optional[str] = None
) -> None:
    """
    Add database query attributes to the current span.

    Args:
        operation: Query operation (SELECT, INSERT, etc.)
        table: Table name
        query: SQL query (sanitized)
        error: Error message if query failed
    """
    if not _enabled:
        return

    span = trace.get_current_span()
    if span:
        span.set_attribute("db.system", "postgresql")
        span.set_attribute("db.operation", operation)
        span.set_attribute("db.table", table)
        if query:
            span.set_attribute("db.statement", query)
        if error:
            span.set_attribute("db.error", error)
            span.set_status(Status(StatusCode.ERROR, error))


def trace_model_call(
    model: str,
    provider: str,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    error: Optional[str] = None
) -> None:
    """
    Add model API call attributes to the current span.

    Args:
        model: Model name
        provider: Provider name
        prompt_tokens: Number of prompt tokens
        completion_tokens: Number of completion tokens
        error: Error message if call failed
    """
    if not _enabled:
        return

    span = trace.get_current_span()
    if span:
        span.set_attribute("ai.model", model)
        span.set_attribute("ai.provider", provider)
        if prompt_tokens:
            span.set_attribute("ai.prompt_tokens", prompt_tokens)
        if completion_tokens:
            span.set_attribute("ai.completion_tokens", completion_tokens)
        if error:
            span.set_attribute("ai.error", error)
            span.set_status(Status(StatusCode.ERROR, error))


def extract_trace_context(headers: Dict[str, str]) -> Any:
    """
    Extract trace context from HTTP headers.

    Args:
        headers: HTTP headers dictionary

    Returns:
        Trace context
    """
    if not _enabled:
        return None

    propagator = TraceContextTextMapPropagator()
    return propagator.extract(headers)


def inject_trace_context(headers: Dict[str, str]) -> Dict[str, str]:
    """
    Inject trace context into HTTP headers.

    Args:
        headers: HTTP headers dictionary

    Returns:
        Headers with trace context injected
    """
    if not _enabled:
        return headers

    propagator = TraceContextTextMapPropagator()
    propagator.inject(headers)
    return headers


# Initialize tracing on module import
setup_tracing()
