"""Audit log API endpoints."""

from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
import csv
import io
import json

from core.db.engine import get_db
from core.auth.backend import get_current_user, UserContext
from core.db.repository import AuditLogRepository
from core.infra.responses import success_response, error_response

router = APIRouter(prefix="/v1/audit")


class AuditLogResponse(BaseModel):
    """Audit log response model."""
    log_id: int
    user_id: str
    action: str
    resource_type: str
    resource_id: str
    status: str
    details: Optional[dict] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    trace_id: Optional[str] = None
    created_at: str


class AuditLogListResponse(BaseModel):
    """Audit log list response model."""
    logs: List[AuditLogResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


@router.get("/logs", response_model=dict)
async def list_audit_logs(
    user: UserContext = Depends(get_current_user),
    action: Optional[str] = Query(None, description="Filter by action"),
    resource_type: Optional[str] = Query(None, description="Filter by resource type"),
    start_date: Optional[datetime] = Query(None, description="Start date filter (ISO 8601)"),
    end_date: Optional[datetime] = Query(None, description="End date filter (ISO 8601)"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    db: Session = Depends(get_db)
):
    """
    Query audit logs.

    Users can only query their own audit logs unless they are admin.

    Query parameters:
    - action: Filter by action type (e.g., "chat.session.created")
    - resource_type: Filter by resource type (e.g., "chat_session")
    - start_date: Start date in ISO 8601 format
    - end_date: End date in ISO 8601 format
    - page: Page number (default: 1)
    - page_size: Items per page (default: 50, max: 100)
    """
    audit_repo = AuditLogRepository(db)

    # Get logs with filters
    logs, total = audit_repo.list_with_filters(
        user_id=user.user_id,
        action=action,
        resource_type=resource_type,
        start_date=start_date,
        end_date=end_date,
        page=page,
        page_size=page_size
    )

    # Convert to response format
    log_responses = []
    for log in logs:
        log_responses.append(AuditLogResponse(
            log_id=log.log_id,
            user_id=log.user_id,
            action=log.action,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            status=log.status,
            details=log.details,
            ip_address=log.ip_address,
            user_agent=log.user_agent,
            trace_id=log.trace_id,
            created_at=log.created_at.isoformat()
        ))

    total_pages = (total + page_size - 1) // page_size

    response_data = AuditLogListResponse(
        logs=log_responses,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages
    )

    return success_response(data=response_data.dict())


@router.get("/logs/{log_id}", response_model=dict)
async def get_audit_log(
    log_id: int,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get a specific audit log by ID.

    Users can only access their own audit logs.
    """
    audit_repo = AuditLogRepository(db)
    log = audit_repo.get_by_id(log_id)

    if not log:
        return error_response(
            code=40401,
            message="Audit log not found"
        )

    # Check ownership
    if log.user_id != user.user_id:
        return error_response(
            code=40301,
            message="Access denied"
        )

    log_response = AuditLogResponse(
        log_id=log.log_id,
        user_id=log.user_id,
        action=log.action,
        resource_type=log.resource_type,
        resource_id=log.resource_id,
        status=log.status,
        details=log.details,
        ip_address=log.ip_address,
        user_agent=log.user_agent,
        trace_id=log.trace_id,
        created_at=log.created_at.isoformat()
    )

    return success_response(data=log_response.dict())


@router.get("/logs/export/csv")
async def export_audit_logs_csv(
    user: UserContext = Depends(get_current_user),
    action: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Export audit logs as CSV.

    Users can only export their own audit logs.
    """
    from fastapi.responses import StreamingResponse

    audit_repo = AuditLogRepository(db)

    # Get all matching logs (no pagination for export)
    logs, _ = audit_repo.list_with_filters(
        user_id=user.user_id,
        action=action,
        resource_type=resource_type,
        start_date=start_date,
        end_date=end_date,
        page=1,
        page_size=10000  # Max export limit
    )

    # Generate CSV
    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow([
        'log_id', 'user_id', 'action', 'resource_type', 'resource_id',
        'status', 'details', 'ip_address', 'user_agent', 'trace_id', 'created_at'
    ])

    # Write data
    for log in logs:
        writer.writerow([
            log.log_id,
            log.user_id,
            log.action,
            log.resource_type,
            log.resource_id,
            log.status,
            json.dumps(log.details) if log.details else '',
            log.ip_address or '',
            log.user_agent or '',
            log.trace_id or '',
            log.created_at.isoformat()
        ])

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=audit_logs_{user.user_id}_{datetime.utcnow().strftime('%Y%m%d')}.csv"
        }
    )


@router.get("/logs/export/json")
async def export_audit_logs_json(
    user: UserContext = Depends(get_current_user),
    action: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Export audit logs as JSON.

    Users can only export their own audit logs.
    """
    from fastapi.responses import JSONResponse

    audit_repo = AuditLogRepository(db)

    # Get all matching logs (no pagination for export)
    logs, total = audit_repo.list_with_filters(
        user_id=user.user_id,
        action=action,
        resource_type=resource_type,
        start_date=start_date,
        end_date=end_date,
        page=1,
        page_size=10000  # Max export limit
    )

    # Convert to dict
    log_data = []
    for log in logs:
        log_data.append({
            'log_id': log.log_id,
            'user_id': log.user_id,
            'action': log.action,
            'resource_type': log.resource_type,
            'resource_id': log.resource_id,
            'status': log.status,
            'details': log.details,
            'ip_address': log.ip_address,
            'user_agent': log.user_agent,
            'trace_id': log.trace_id,
            'created_at': log.created_at.isoformat()
        })

    response_data = {
        'logs': log_data,
        'total': total,
        'exported_at': datetime.utcnow().isoformat()
    }

    return JSONResponse(
        content=response_data,
        headers={
            "Content-Disposition": f"attachment; filename=audit_logs_{user.user_id}_{datetime.utcnow().strftime('%Y%m%d')}.json"
        }
    )


@router.get("/stats", response_model=dict)
async def get_audit_stats(
    user: UserContext = Depends(get_current_user),
    days: int = Query(7, ge=1, le=90, description="Number of days to analyze"),
    db: Session = Depends(get_db)
):
    """
    Get audit statistics for the current user.

    Returns statistics like:
    - Total actions in period
    - Actions by type
    - Failed actions count
    - Most active days
    """
    audit_repo = AuditLogRepository(db)
    stats = audit_repo.get_user_stats(user.user_id, days)

    return success_response(data=stats)
