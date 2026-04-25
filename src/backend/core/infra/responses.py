"""Unified API response utilities."""

from typing import Any, Optional, Dict
import uuid
from datetime import datetime
from fastapi.responses import JSONResponse


def generate_trace_id() -> str:
    """Generate a unique trace ID for request tracking."""
    return f"req_{uuid.uuid4().hex[:16]}"


def success_response(
    data: Any = None,
    message: str = "Success",
    code: int = 10000,
    trace_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a success response.

    Args:
        data: Response data
        message: Success message
        code: Business status code (default 10000 for success)
        trace_id: Optional trace ID

    Returns:
        Standard API response dict
    """
    return {
        "code": code,
        "message": message,
        "data": data,
        "trace_id": trace_id or generate_trace_id(),
        "timestamp": int(datetime.utcnow().timestamp() * 1000)
    }


def created_response(
    data: Any = None,
    message: str = "Resource created successfully",
    trace_id: Optional[str] = None
) -> Dict[str, Any]:
    """Create a 201 Created response."""
    return success_response(data, message, code=10001, trace_id=trace_id)


def error_response(
    code: int,
    message: str,
    data: Optional[Dict[str, Any]] = None,
    status_code: int = 500,
    trace_id: Optional[str] = None
) -> JSONResponse:
    """
    Create an error response.

    Args:
        code: Business error code
        message: Error message
        data: Additional error data
        status_code: HTTP status code
        trace_id: Optional trace ID

    Returns:
        JSONResponse with error details
    """
    response_data = {
        "code": code,
        "message": message,
        "data": data or {},
        "trace_id": trace_id or generate_trace_id(),
        "timestamp": int(datetime.utcnow().timestamp() * 1000)
    }

    return JSONResponse(
        status_code=status_code,
        content=response_data
    )


# Alias used by some route modules
ok = success_response


def paginated_response(
    items: list,
    page: int,
    page_size: int,
    total_items: int,
    message: str = "Success",
    trace_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a paginated response.

    Args:
        items: List of items for current page
        page: Current page number (1-indexed)
        page_size: Items per page
        total_items: Total number of items
        message: Success message
        trace_id: Optional trace ID

    Returns:
        Standard API response with pagination metadata
    """
    total_pages = (total_items + page_size - 1) // page_size

    data = {
        "items": items,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
            "has_previous": page > 1,
            "has_next": page < total_pages
        }
    }

    return success_response(data, message, trace_id=trace_id)
