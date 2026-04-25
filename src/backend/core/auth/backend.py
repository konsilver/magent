"""Authentication and authorization middleware.

Supports three AUTH_MODE values:
  - mock:    Development mode, accepts any Bearer token or generates default user
  - remote:  Legacy production mode, verifies Bearer token with AUTH_API_URL/verify
  - session: SSO ticket mode, validates jx_session Cookie against Redis
"""

from typing import Optional, Dict, Any
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import requests
from pydantic import BaseModel

from core.config.settings import settings
from core.db.engine import get_db
from sqlalchemy.orm import Session
from core.services import UserService
from core.db.repository import AuditLogRepository
from core.infra.logging import get_logger

logger = get_logger(__name__)


class UserContext(BaseModel):
    """User context injected into requests after authentication."""
    user_id: str
    user_center_id: str
    username: str
    email: Optional[str] = None
    avatar_url: Optional[str] = None


def _sso_login_url() -> str:
    """Get the SSO login URL for 401 responses."""
    return settings.sso.login_url


def _auth_mode() -> str:
    return settings.auth.mode


class AuthService:
    """Authentication service for mock / remote Bearer token modes."""

    def __init__(self):
        self.auth_mode = _auth_mode()
        self.user_center_url = settings.auth.api_url
        self.timeout = settings.auth.api_timeout
        self.retry_count = settings.auth.retry_count

    def verify_token_remote(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify token with remote user center."""
        if not self.user_center_url:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": 52002,
                    "message": "AUTH_API_URL not configured",
                    "data": {}
                }
            )

        headers = {"Authorization": f"Bearer {token}"}

        for attempt in range(self.retry_count):
            try:
                response = requests.get(
                    f"{self.user_center_url}/verify",
                    headers=headers,
                    timeout=self.timeout
                )

                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 401:
                    return None
                else:
                    continue

            except requests.RequestException as e:
                if attempt == self.retry_count - 1:
                    raise HTTPException(
                        status_code=502,
                        detail={
                            "code": 52001,
                            "message": "User center unavailable",
                            "data": {"error": str(e)}
                        }
                    )
                continue

        return None

    def verify_token_mock(self, token: str) -> Dict[str, Any]:
        """Mock authentication for development."""
        if token and token != "mock_token":
            return {
                "user_center_id": token,
                "username": token,
                "email": f"{token}@mock.local",
                "avatar_url": None
            }
        return {
            "user_center_id": settings.auth.mock_user_id,
            "username": settings.auth.mock_username,
            "email": "dev@example.com",
            "avatar_url": None
        }

    def verify_token(self, token: str, db: Session = None) -> Dict[str, Any]:
        """Verify token based on configured auth mode (mock or remote)."""
        if self.auth_mode == "mock":
            return self.verify_token_mock(token)
        else:
            user_info = self.verify_token_remote(token)
            if not user_info:
                if db:
                    try:
                        audit_repo = AuditLogRepository(db)
                        audit_repo.create({
                            "user_id": "unknown",
                            "action": "auth.login.failed",
                            "resource_type": "user",
                            "resource_id": "unknown",
                            "status": "failed",
                            "details": {"reason": "invalid_or_expired_token"}
                        })
                    except Exception as e:
                        logger.warning(f"Failed to log auth failure: {e}")

                raise HTTPException(
                    status_code=401,
                    detail={
                        "code": 30002,
                        "message": "Invalid or expired token",
                        "data": {"login_url": _sso_login_url()}
                    }
                )
            return user_info


# Bearer token scheme (do not auto-reject missing header so mock mode can inject dev user)
security = HTTPBearer(auto_error=False)


async def _resolve_session_user(request: Request) -> Optional[UserContext]:
    """Try to resolve user from jx_session Cookie via Redis.

    Returns UserContext if valid, None if no cookie present.
    Raises HTTPException(401) if cookie present but session expired.
    """
    cookie_name = settings.session.cookie_name
    token = request.cookies.get(cookie_name)
    if not token:
        return None

    from core.auth.session import validate_session
    user_data = await validate_session(token)
    if user_data:
        return UserContext(
            user_id=user_data["user_id"],
            user_center_id=user_data.get("user_center_id", ""),
            username=user_data.get("username", ""),
            email=user_data.get("email"),
            avatar_url=user_data.get("avatar_url"),
        )

    # Cookie exists but Redis session expired → clear 401, do NOT fallback
    raise HTTPException(
        status_code=401,
        detail={
            "code": 30003,
            "message": "Session expired",
            "data": {"login_url": _sso_login_url()}
        }
    )


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> UserContext:
    """Dependency to get current authenticated user.

    Authentication priority:
      1. Cookie session (jx_session) → Redis lookup  (session mode main path)
      2. Bearer token → mock / remote verification   (backward compatible)

    In AUTH_MODE=session, Bearer tokens are NOT accepted.
    """
    auth_mode = _auth_mode()

    # ── 1. Try Cookie session first (works in all modes) ──
    session_user = await _resolve_session_user(request)
    if session_user is not None:
        return session_user

    # ── 2. No Cookie session ──
    if auth_mode == "session":
        # Pure session mode: Cookie is mandatory, no Bearer fallback
        raise HTTPException(
            status_code=401,
            detail={
                "code": 30001,
                "message": "Authorization required",
                "data": {"login_url": _sso_login_url()}
            }
        )

    # ── 3. mock / remote: Bearer token path ──
    current_auth = AuthService()

    if credentials is None:
        if auth_mode != "mock":
            raise HTTPException(
                status_code=401,
                detail={
                    "code": 30001,
                    "message": "Authorization header required",
                    "data": {"login_url": _sso_login_url()}
                }
            )
        token = "mock_token"
    else:
        token = credentials.credentials

    user_info = current_auth.verify_token(token, db)

    user_service = UserService(db)
    user_shadow = user_service.get_or_create_user_shadow(
        user_center_id=user_info["user_center_id"],
        username=user_info["username"],
        email=user_info.get("email"),
        avatar_url=user_info.get("avatar_url")
    )

    # Audit log for successful authentication
    audit_repo = AuditLogRepository(db)
    audit_repo.create({
        "user_id": user_shadow.user_id,
        "action": "auth.login.success",
        "resource_type": "user",
        "resource_id": user_shadow.user_id,
        "status": "success"
    })

    return UserContext(
        user_id=user_shadow.user_id,
        user_center_id=user_shadow.user_center_id,
        username=user_shadow.username,
        email=user_shadow.email,
        avatar_url=user_shadow.avatar_url
    )


def require_auth(required: bool = True):
    """Optional authentication dependency.

    Usage:
        @app.get("/public")
        def public_endpoint(user: Optional[UserContext] = Depends(require_auth(False))):
            pass

        @app.get("/private")
        def private_endpoint(user: UserContext = Depends(require_auth(True))):
            pass
    """
    if required:
        return get_current_user
    else:
        async def optional_user(request: Request, db: Session = Depends(get_db)) -> Optional[UserContext]:
            auth_mode = _auth_mode()

            # ── Try Cookie session first (all modes) ──
            try:
                session_user = await _resolve_session_user(request)
                if session_user is not None:
                    return session_user
            except HTTPException:
                # Session cookie expired — for optional auth, return None
                return None

            # ── session mode: no Cookie = no user ──
            if auth_mode == "session":
                return None

            # ── mock / remote: try Bearer ──
            current_auth = AuthService()
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                if auth_mode != "mock":
                    return None
                token = "mock_token"
            else:
                token = auth_header.split(" ", 1)[1]
            try:
                user_info = current_auth.verify_token(token)
                user_service = UserService(db)
                user_shadow = user_service.get_or_create_user_shadow(
                    user_center_id=user_info["user_center_id"],
                    username=user_info["username"],
                    email=user_info.get("email"),
                    avatar_url=user_info.get("avatar_url")
                )

                return UserContext(
                    user_id=user_shadow.user_id,
                    user_center_id=user_shadow.user_center_id,
                    username=user_shadow.username,
                    email=user_shadow.email,
                    avatar_url=user_shadow.avatar_url
                )
            except Exception as e:
                try:
                    db.rollback()
                except Exception:
                    pass
                logger.warning("optional_auth_resolve_failed", error=str(e))
                return None

        return optional_user
