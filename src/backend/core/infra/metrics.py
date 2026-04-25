"""
Prometheus metrics for Jingxin-Agent.

This module defines and exports all Prometheus metrics for monitoring:
- HTTP request metrics (count, latency, status)
- SSE connection metrics
- Model API call metrics
- Database connection pool metrics
- Business metrics (chats, messages, etc.)
"""

import os

try:
    from prometheus_client import Counter, Histogram, Gauge, Info
except ModuleNotFoundError:
    class _NoopMetric:
        def labels(self, **kwargs):
            _ = kwargs
            return self

        def inc(self, value=1):
            _ = value

        def dec(self, value=1):
            _ = value

        def observe(self, value):
            _ = value

        def set(self, value):
            _ = value

        def info(self, value):
            _ = value

    def Counter(*args, **kwargs):  # type: ignore
        _ = args, kwargs
        return _NoopMetric()

    def Histogram(*args, **kwargs):  # type: ignore
        _ = args, kwargs
        return _NoopMetric()

    def Gauge(*args, **kwargs):  # type: ignore
        _ = args, kwargs
        return _NoopMetric()

    def Info(*args, **kwargs):  # type: ignore
        _ = args, kwargs
        return _NoopMetric()


# Service information
service_info = Info('jingxin_agent_service', 'Service information')
service_info.info({
    'version': '0.1.0',
    'environment': os.getenv('ENV', 'dev')
})


# HTTP Request Metrics
http_requests_total = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status']
)

http_request_duration_seconds = Histogram(
    'http_request_duration_seconds',
    'HTTP request latency in seconds',
    ['method', 'endpoint'],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

http_request_size_bytes = Histogram(
    'http_request_size_bytes',
    'HTTP request size in bytes',
    ['method', 'endpoint'],
    buckets=[100, 1000, 10000, 100000, 1000000, 10000000]
)

http_response_size_bytes = Histogram(
    'http_response_size_bytes',
    'HTTP response size in bytes',
    ['method', 'endpoint'],
    buckets=[100, 1000, 10000, 100000, 1000000, 10000000]
)


# SSE Metrics
sse_active_connections = Gauge(
    'sse_active_connections',
    'Number of active SSE connections',
    ['endpoint']
)

sse_messages_sent_total = Counter(
    'sse_messages_sent_total',
    'Total SSE messages sent',
    ['endpoint', 'event_type']
)

sse_connection_duration_seconds = Histogram(
    'sse_connection_duration_seconds',
    'SSE connection duration in seconds',
    ['endpoint'],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600]
)

sse_errors_total = Counter(
    'sse_errors_total',
    'Total SSE errors',
    ['endpoint', 'error_type']
)


# Model API Metrics
model_api_requests_total = Counter(
    'model_api_requests_total',
    'Total model API requests',
    ['model', 'provider', 'status']
)

model_api_duration_seconds = Histogram(
    'model_api_duration_seconds',
    'Model API call duration in seconds',
    ['model', 'provider'],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0]
)

model_api_tokens_total = Counter(
    'model_api_tokens_total',
    'Total tokens used',
    ['model', 'provider', 'token_type']  # token_type: prompt, completion
)

model_api_errors_total = Counter(
    'model_api_errors_total',
    'Total model API errors',
    ['model', 'provider', 'error_type']
)


# Database Metrics
db_connections = Gauge(
    'db_connections',
    'Database connection pool status',
    ['state']  # state: active, idle, total
)

db_query_duration_seconds = Histogram(
    'db_query_duration_seconds',
    'Database query duration in seconds',
    ['operation', 'table'],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0]
)

db_queries_total = Counter(
    'db_queries_total',
    'Total database queries',
    ['operation', 'table', 'status']
)

db_errors_total = Counter(
    'db_errors_total',
    'Total database errors',
    ['operation', 'table', 'error_type']
)


# Storage Metrics (S3)
storage_operations_total = Counter(
    'storage_operations_total',
    'Total storage operations',
    ['operation', 'status']  # operation: upload, download, delete
)

storage_operation_duration_seconds = Histogram(
    'storage_operation_duration_seconds',
    'Storage operation duration in seconds',
    ['operation'],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
)

storage_bytes_transferred = Counter(
    'storage_bytes_transferred',
    'Total bytes transferred to/from storage',
    ['operation']  # operation: upload, download
)

storage_errors_total = Counter(
    'storage_errors_total',
    'Total storage errors',
    ['operation', 'error_type']
)


# User Center Integration Metrics
user_center_requests_total = Counter(
    'user_center_requests_total',
    'Total user center API requests',
    ['endpoint', 'status']
)

user_center_duration_seconds = Histogram(
    'user_center_duration_seconds',
    'User center API call duration in seconds',
    ['endpoint'],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0]
)

user_center_errors_total = Counter(
    'user_center_errors_total',
    'Total user center errors',
    ['endpoint', 'error_type']
)


# Business Metrics
chats_created_total = Counter(
    'chats_created_total',
    'Total chat sessions created',
    ['user_type']  # user_type: regular, premium, etc.
)

messages_sent_total = Counter(
    'messages_sent_total',
    'Total messages sent',
    ['role', 'chat_type']  # role: user, assistant
)

artifacts_created_total = Counter(
    'artifacts_created_total',
    'Total artifacts created',
    ['artifact_type']
)

kb_documents_uploaded_total = Counter(
    'kb_documents_uploaded_total',
    'Total knowledge base documents uploaded',
    ['document_type']
)


# Rate Limiting Metrics
rate_limit_exceeded_total = Counter(
    'rate_limit_exceeded_total',
    'Total rate limit exceeded events',
    ['endpoint', 'limit_type']
)


# Circuit Breaker Metrics
circuit_breaker_state = Gauge(
    'circuit_breaker_state',
    'Circuit breaker state (0=closed, 1=open, 2=half_open)',
    ['service']
)

circuit_breaker_opened_total = Counter(
    'circuit_breaker_opened_total',
    'Total circuit breaker opens',
    ['service']
)


# Cache Metrics (if caching is implemented)
cache_hits_total = Counter(
    'cache_hits_total',
    'Total cache hits',
    ['cache_name']
)

cache_misses_total = Counter(
    'cache_misses_total',
    'Total cache misses',
    ['cache_name']
)


def record_http_request(method: str, endpoint: str, status_code: int, duration: float):
    """
    Record HTTP request metrics.

    Args:
        method: HTTP method (GET, POST, etc.)
        endpoint: Request endpoint path
        status_code: HTTP status code
        duration: Request duration in seconds
    """
    status = f"{status_code // 100}xx"
    http_requests_total.labels(method=method, endpoint=endpoint, status=status).inc()
    http_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(duration)


def record_sse_connection(endpoint: str, duration: float, messages_sent: int, error: str = None):
    """
    Record SSE connection metrics.

    Args:
        endpoint: SSE endpoint path
        duration: Connection duration in seconds
        messages_sent: Number of messages sent during connection
        error: Error type if connection failed
    """
    sse_connection_duration_seconds.labels(endpoint=endpoint).observe(duration)

    if error:
        sse_errors_total.labels(endpoint=endpoint, error_type=error).inc()


def record_model_request(model: str, provider: str, status: str, duration: float,
                         prompt_tokens: int = 0, completion_tokens: int = 0,
                         error_type: str = None):
    """
    Record model API request metrics.

    Args:
        model: Model name
        provider: Provider name (openai, anthropic, etc.)
        status: Request status (success, error)
        duration: Request duration in seconds
        prompt_tokens: Number of prompt tokens
        completion_tokens: Number of completion tokens
        error_type: Error type if request failed
    """
    model_api_requests_total.labels(model=model, provider=provider, status=status).inc()
    model_api_duration_seconds.labels(model=model, provider=provider).observe(duration)

    if prompt_tokens > 0:
        model_api_tokens_total.labels(model=model, provider=provider, token_type='prompt').inc(prompt_tokens)
    if completion_tokens > 0:
        model_api_tokens_total.labels(model=model, provider=provider, token_type='completion').inc(completion_tokens)

    if error_type:
        model_api_errors_total.labels(model=model, provider=provider, error_type=error_type).inc()


def record_db_query(operation: str, table: str, status: str, duration: float, error_type: str = None):
    """
    Record database query metrics.

    Args:
        operation: Query operation (select, insert, update, delete)
        table: Table name
        status: Query status (success, error)
        duration: Query duration in seconds
        error_type: Error type if query failed
    """
    db_queries_total.labels(operation=operation, table=table, status=status).inc()
    db_query_duration_seconds.labels(operation=operation, table=table).observe(duration)

    if error_type:
        db_errors_total.labels(operation=operation, table=table, error_type=error_type).inc()


def update_db_connection_pool(active: int, idle: int, total: int):
    """
    Update database connection pool metrics.

    Args:
        active: Number of active connections
        idle: Number of idle connections
        total: Total number of connections
    """
    db_connections.labels(state='active').set(active)
    db_connections.labels(state='idle').set(idle)
    db_connections.labels(state='total').set(total)
