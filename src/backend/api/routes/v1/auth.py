"""Authentication routes for SSO ticket exchange and session management.

Endpoints:
  POST /v1/auth/ticket/exchange   - Exchange a one-time SSO ticket for a session
  GET  /v1/auth/session/check     - Check current session validity (cookie-based)
  POST /v1/auth/logout            - Revoke current session and clear cookie
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Depends, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth.session import (
    create_session,
    validate_session,
    revoke_session,
    session_cookie_params,
    expires_at_iso,
)
from core.db.engine import get_db
from core.infra.logging import get_logger
from core.db.repository import AuditLogRepository
from core.infra.responses import success_response
from core.services import UserService
from core.auth.sso import SSOTicketError, exchange_ticket
from core.config.settings import settings

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/auth", tags=["Auth"])


# ── Request / Response models ────────────────────────────────────────────

class TicketExchangeRequest(BaseModel):
    ticket: str


# ── Helpers ──────────────────────────────────────────────────────────────

def _login_url() -> str:
    """Get the SSO login URL for 401 responses."""
    return settings.sso.login_url


def _set_session_cookie(response: Response, token: str) -> None:
    """Set the session cookie on the response."""
    params = session_cookie_params()
    response.set_cookie(value=token, **params)


def _clear_session_cookie(response: Response) -> None:
    """Delete the session cookie."""
    name = settings.session.cookie_name
    response.delete_cookie(key=name, path="/")


def _mask_ticket(ticket: str) -> str:
    if len(ticket) <= 4:
        return "****"
    return ticket[:4] + "****"


# ── POST /v1/auth/ticket/exchange ────────────────────────────────────────

@router.post("/ticket/exchange", summary="Ticket 换取登录会话")
async def ticket_exchange(
    body: TicketExchangeRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
):
    """Exchange a one-time SSO ticket for a session cookie + user info.

    The response sets an HttpOnly cookie and also returns user data in the body
    so the frontend can use it directly without a subsequent /v1/me call.
    """
    audit = AuditLogRepository(db)

    try:
        # 1. Exchange ticket with SSO (real or mock)
        sso_user = exchange_ticket(body.ticket)
    except SSOTicketError as exc:
        try:
            audit.create({
                "user_id": "unknown",
                "action": "auth.ticket.exchange.failed",
                "resource_type": "auth",
                "resource_id": _mask_ticket(body.ticket),
                "status": "failure",
                "details": {"reason": exc.message},
                "ip_address": request.client.host if request.client else None,
                "user_agent": request.headers.get("user-agent"),
            })
        except Exception:
            # audit_logs has FK on user_id — "unknown" may not exist; don't let
            # the audit failure mask the real auth error
            try:
                db.rollback()
            except Exception:
                pass
            logger.warning("audit_log_failed_for_ticket_exchange",
                           reason=exc.message, ticket=_mask_ticket(body.ticket))
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": 30002, "message": exc.message, "data": {"login_url": _login_url()}},
        )

    # 2. Upsert user_shadow in PostgreSQL
    user_service = UserService(db)
    user_shadow = user_service.get_or_create_user_shadow(
        user_center_id=sso_user["user_center_id"],
        username=sso_user["username"],
        email=sso_user.get("email"),
        avatar_url=sso_user.get("avatar_url"),
    )

    # 3. Create session in Redis
    user_data = {
        "user_id": user_shadow.user_id,
        "user_center_id": user_shadow.user_center_id,
        "username": user_shadow.username,
        "email": user_shadow.email,
        "avatar_url": user_shadow.avatar_url,
    }
    token = await create_session(user_data)

    # 4. Set cookie
    _set_session_cookie(response, token)

    # 5. Audit log
    audit.create({
        "user_id": user_shadow.user_id,
        "action": "auth.ticket.exchange.success",
        "resource_type": "auth",
        "resource_id": user_shadow.user_id,
        "status": "success",
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    })

    logger.info("ticket_exchange_success", user_id=user_shadow.user_id,
                user_center_id=user_shadow.user_center_id)

    return success_response(
        data={
            "user_id": user_shadow.user_id,
            "username": user_shadow.username,
            "email": user_shadow.email,
            "avatar_url": user_shadow.avatar_url,
            "expires_at": expires_at_iso(),
        },
        message="Login successful",
    )


# ── GET /v1/auth/session/check ───────────────────────────────────────────

@router.get("/session/check", summary="检查当前会话状态")
async def session_check(request: Request):
    """Check if the current cookie session is still valid.

    Used by the frontend on page refresh (when sessionStorage is lost but
    the cookie survives).  Returns user info if valid, 401 with login_url
    if expired.
    """
    cookie_name = settings.session.cookie_name
    token = request.cookies.get(cookie_name)

    if not token:
        raise HTTPException(
            status_code=401,
            detail={"code": 30003, "message": "No session", "data": {"login_url": _login_url()}},
        )

    user_data = await validate_session(token)
    if not user_data:
        raise HTTPException(
            status_code=401,
            detail={"code": 30003, "message": "Session expired", "data": {"login_url": _login_url()}},
        )

    return success_response(
        data={
            "user_id": user_data["user_id"],
            "username": user_data.get("username", ""),
            "email": user_data.get("email"),
            "avatar_url": user_data.get("avatar_url"),
            "expires_at": expires_at_iso(),
        },
        message="Session valid",
    )


# ── POST /v1/auth/logout ─────────────────────────────────────────────────

@router.post("/logout", summary="登出（注销会话）")
async def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Revoke the current session in Redis and clear the cookie."""
    cookie_name = settings.session.cookie_name
    token = request.cookies.get(cookie_name)

    user_id = "unknown"
    if token:
        # Try to read user_id before revoking
        user_data = await validate_session(token)
        if user_data:
            user_id = user_data.get("user_id", "unknown")
        await revoke_session(token)

    _clear_session_cookie(response)

    # Audit
    try:
        audit = AuditLogRepository(db)
        audit.create({
            "user_id": user_id,
            "action": "auth.logout",
            "resource_type": "auth",
            "resource_id": user_id,
            "status": "success",
            "ip_address": request.client.host if request.client else None,
        })
    except Exception as exc:
        logger.warning("logout_audit_failed", user_id=user_id, error=str(exc))

    logger.info("user_logged_out", user_id=user_id)

    return success_response(
        message="Logged out successfully",
        data={"login_url": _login_url()},
    )
