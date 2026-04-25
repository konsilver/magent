#!/usr/bin/env python3
"""
Self-test for observability features.

Tests:
- Logging configuration
- Metrics collection
- Rate limiting
- Circuit breaker
- Health check endpoints
"""

import os
import sys
import time
import asyncio
from unittest.mock import Mock, patch, MagicMock

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set test environment
os.environ["ENV"] = "test"
os.environ["LOG_LEVEL"] = "INFO"
os.environ["TRACING_ENABLED"] = "false"
os.environ["RATE_LIMIT_ENABLED"] = "true"


def test_logging_config():
    """Test logging configuration."""
    print("Testing logging configuration...")

    from core.infra.logging import (
        get_logger, generate_trace_id, LogContext,
        trace_id_var, user_id_var, chat_id_var
    )

    # Test logger creation
    logger = get_logger(__name__)
    assert logger is not None, "Logger should be created"

    # Test trace ID generation
    trace_id = generate_trace_id()
    assert trace_id, "Trace ID should be generated"
    assert len(trace_id) > 0, "Trace ID should not be empty"

    # Test log context
    with LogContext(trace_id="test-trace-123", user_id="user-456", chat_id="chat-789"):
        assert trace_id_var.get() == "test-trace-123", "Trace ID should be set"
        assert user_id_var.get() == "user-456", "User ID should be set"
        assert chat_id_var.get() == "chat-789", "Chat ID should be set"

        # Log a message
        logger.info("test_message", key="value")

    # Context should be cleared
    assert trace_id_var.get() == "", "Trace ID should be cleared"
    assert user_id_var.get() == "", "User ID should be cleared"
    assert chat_id_var.get() == "", "Chat ID should be cleared"

    print("✓ Logging configuration test passed")


def test_metrics():
    """Test metrics collection."""
    print("Testing metrics collection...")

    from core.infra.metrics import (
        record_http_request, record_model_request, record_db_query,
        update_db_connection_pool, http_requests_total, model_api_requests_total
    )

    # Test HTTP request metrics
    initial_count = http_requests_total.labels(
        method="GET", endpoint="/test", status="2xx"
    )._value.get()

    record_http_request("GET", "/test", 200, 0.1)

    new_count = http_requests_total.labels(
        method="GET", endpoint="/test", status="2xx"
    )._value.get()

    assert new_count > initial_count, "HTTP request count should increase"

    # Test model request metrics
    initial_model_count = model_api_requests_total.labels(
        model="gpt-4", provider="openai", status="success"
    )._value.get()

    record_model_request(
        model="gpt-4",
        provider="openai",
        status="success",
        duration=1.5,
        prompt_tokens=100,
        completion_tokens=50
    )

    new_model_count = model_api_requests_total.labels(
        model="gpt-4", provider="openai", status="success"
    )._value.get()

    assert new_model_count > initial_model_count, "Model request count should increase"

    # Test DB query metrics
    record_db_query("select", "users", "success", 0.05)

    # Test DB connection pool metrics
    update_db_connection_pool(active=10, idle=5, total=15)

    print("✓ Metrics collection test passed")


def test_tracing():
    """Test tracing functionality."""
    print("Testing tracing...")

    # Tracing is disabled in test environment
    from core.infra.tracing import (
        is_tracing_enabled, traced, trace_span,
        add_span_attribute, get_tracer
    )

    assert not is_tracing_enabled(), "Tracing should be disabled in test"

    # Test traced decorator (should be no-op when disabled)
    @traced("test_operation")
    async def async_test_func():
        return "success"

    @traced("test_sync_operation")
    def sync_test_func():
        return "success"

    # Run async function
    result = asyncio.run(async_test_func())
    assert result == "success", "Async traced function should work"

    # Run sync function
    result = sync_test_func()
    assert result == "success", "Sync traced function should work"

    # Test trace span (should be no-op when disabled)
    with trace_span("test_span", {"key": "value"}):
        add_span_attribute("test_attr", "test_value")

    # Get tracer (should not fail even when disabled)
    tracer = get_tracer()
    assert tracer is not None, "Tracer should always be available"

    print("✓ Tracing test passed")


def test_rate_limiting():
    """Test rate limiting."""
    print("Testing rate limiting...")

    from core.infra.rate_limit import limiter, get_rate_limit_enabled

    assert get_rate_limit_enabled(), "Rate limiting should be enabled in test"
    assert limiter is not None, "Limiter should be created"
    assert limiter.enabled, "Limiter should be enabled"

    print("✓ Rate limiting test passed")


def test_circuit_breaker():
    """Test circuit breaker."""
    print("Testing circuit breaker...")

    from core.infra.rate_limit import CircuitBreaker, CircuitBreakerOpenError, CircuitBreakerState

    # Create a circuit breaker with low thresholds for testing
    breaker = CircuitBreaker(
        name="test_service",
        failure_threshold=3,
        success_threshold=2,
        timeout=1
    )

    # Initial state should be CLOSED
    assert breaker.state == CircuitBreakerState.CLOSED, "Initial state should be CLOSED"

    # Test successful calls
    def success_func():
        return "success"

    result = breaker.call(success_func)
    assert result == "success", "Successful call should return result"
    assert breaker.state == CircuitBreakerState.CLOSED, "State should remain CLOSED"

    # Test failing calls
    def failing_func():
        raise ValueError("Test error")

    # Trigger failures to open circuit
    for i in range(3):
        try:
            breaker.call(failing_func)
            assert False, "Should have raised ValueError"
        except ValueError:
            pass

    # Circuit should be OPEN now
    assert breaker.state == CircuitBreakerState.OPEN, "Circuit should be OPEN after failures"

    # Calls should fail fast
    try:
        breaker.call(success_func)
        assert False, "Should have raised CircuitBreakerOpenError"
    except CircuitBreakerOpenError:
        pass

    # Wait for timeout
    time.sleep(1.1)

    # Next call should transition to HALF_OPEN
    result = breaker.call(success_func)
    assert result == "success", "Call should succeed in HALF_OPEN"
    assert breaker.state == CircuitBreakerState.HALF_OPEN, "State should be HALF_OPEN"

    # Another success should close the circuit
    result = breaker.call(success_func)
    assert result == "success", "Call should succeed"
    assert breaker.state == CircuitBreakerState.CLOSED, "Circuit should be CLOSED after recovery"

    print("✓ Circuit breaker test passed")


def test_async_circuit_breaker():
    """Test async circuit breaker."""
    print("Testing async circuit breaker...")

    from core.infra.rate_limit import CircuitBreaker, CircuitBreakerState

    breaker = CircuitBreaker(
        name="test_async_service",
        failure_threshold=2,
        success_threshold=1,
        timeout=1
    )

    async def async_success_func():
        return "async_success"

    async def async_failing_func():
        raise ValueError("Async test error")

    async def run_test():
        # Test successful call
        result = await breaker.call_async(async_success_func)
        assert result == "async_success", "Async call should succeed"

        # Trigger failures
        for _ in range(2):
            try:
                await breaker.call_async(async_failing_func)
                assert False, "Should have raised ValueError"
            except ValueError:
                pass

        # Circuit should be open
        assert breaker.state == CircuitBreakerState.OPEN, "Circuit should be OPEN"

        # Wait and test recovery
        await asyncio.sleep(1.1)

        # Should succeed and close
        result = await breaker.call_async(async_success_func)
        assert result == "async_success", "Should succeed after timeout"
        assert breaker.state == CircuitBreakerState.CLOSED, "Circuit should close after recovery"

    asyncio.run(run_test())

    print("✓ Async circuit breaker test passed")


def test_health_endpoints():
    """Test health check endpoints."""
    print("Testing health check endpoints...")

    # We'll test this by importing the app and checking the routes exist
    from api.app import app

    # Check that health endpoints are registered
    routes = [route.path for route in app.routes]

    assert "/health" in routes, "/health endpoint should exist"
    assert "/ready" in routes, "/ready endpoint should exist"
    assert "/live" in routes, "/live endpoint should exist"
    assert "/metrics" in routes, "/metrics endpoint should exist"

    print("✓ Health endpoints test passed")


def test_is_internal_ip():
    """Test internal IP detection."""
    print("Testing internal IP detection...")

    from api.app import is_internal_ip

    # Test localhost
    assert is_internal_ip("127.0.0.1"), "127.0.0.1 should be internal"
    assert is_internal_ip("::1"), "::1 should be internal"
    assert is_internal_ip("localhost"), "localhost should be internal"

    # Test private networks
    assert is_internal_ip("10.0.0.1"), "10.0.0.1 should be internal"
    assert is_internal_ip("10.255.255.255"), "10.255.255.255 should be internal"
    assert is_internal_ip("172.16.0.1"), "172.16.0.1 should be internal"
    assert is_internal_ip("172.31.255.255"), "172.31.255.255 should be internal"
    assert is_internal_ip("192.168.0.1"), "192.168.0.1 should be internal"
    assert is_internal_ip("192.168.255.255"), "192.168.255.255 should be internal"

    # Test public IPs
    assert not is_internal_ip("8.8.8.8"), "8.8.8.8 should not be internal"
    assert not is_internal_ip("1.2.3.4"), "1.2.3.4 should not be internal"

    print("✓ Internal IP detection test passed")


def run_all_tests():
    """Run all observability tests."""
    print("=" * 60)
    print("Running Observability Self-Tests")
    print("=" * 60)

    try:
        test_logging_config()
        test_metrics()
        test_tracing()
        test_rate_limiting()
        test_circuit_breaker()
        test_async_circuit_breaker()
        test_health_endpoints()
        test_is_internal_ip()

        print("=" * 60)
        print("All observability tests passed! ✓")
        print("=" * 60)
        return True

    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
