"""SSO ticket exchange client.

Supports two modes controlled by SSO_MOCK_ENABLED:
  - Real mode:  GET {SSO_TICKET_EXCHANGE_URL}?ticket={ticket}
  - Mock mode:  Validate ticket against the in-process mock store
"""

from typing import Any, Dict, Optional

import requests

from core.config.settings import settings
from core.infra.logging import get_logger

logger = get_logger(__name__)


class SSOTicketError(Exception):
    """Raised when ticket exchange fails."""

    def __init__(self, message: str, status_code: int = 401):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _mask_ticket(ticket: str) -> str:
    """Mask ticket for safe logging (show first 4 chars only)."""
    if len(ticket) <= 4:
        return "****"
    return ticket[:4] + "****"


def exchange_ticket(ticket: str) -> Dict[str, Any]:
    """Exchange a one-time SSO ticket for user info.

    Returns:
        dict with keys: user_center_id, username, email, avatar_url
        (and any other fields the SSO system returns)

    Raises:
        SSOTicketError on failure
    """
    if not ticket or not ticket.strip():
        raise SSOTicketError("Ticket is empty", status_code=400)

    ticket = ticket.strip()

    # ── Mock mode (built-in mock SSO) ──
    if _is_mock_mode():
        return _exchange_mock(ticket)

    # ── Real mode ──
    return _exchange_remote(ticket)


def _is_mock_mode() -> bool:
    """Check if ticket exchange should use the local mock store.

    Controlled by SSO_EXCHANGE_MODE (preferred) or legacy SSO_MOCK_ENABLED.
      - SSO_EXCHANGE_MODE=mock   → True  (validate against in-memory mock store)
      - SSO_EXCHANGE_MODE=remote → False (call external SSO API)
    """
    exchange_mode = settings.sso.exchange_mode.lower()
    if exchange_mode:
        return exchange_mode == "mock"
    # Legacy fallback
    return settings.sso.mock_enabled


def _exchange_mock(ticket: str) -> Dict[str, Any]:
    """Validate ticket against the in-process mock store."""
    from api.routes.v1.mock_sso import consume_ticket

    user_info = consume_ticket(ticket)
    if user_info is None:
        logger.warning("mock_sso_ticket_invalid", ticket=_mask_ticket(ticket))
        raise SSOTicketError("Invalid or expired ticket")

    logger.info("mock_sso_ticket_exchanged", ticket=_mask_ticket(ticket),
                user_center_id=user_info.get("user_center_id"))
    return user_info


def _exchange_remote(ticket: str) -> Dict[str, Any]:
    """GET the ticket to the real SSO exchange endpoint.

    Calls: GET {SSO_TICKET_EXCHANGE_URL}?ticket={ticket}
    Expected response:
        {
          "code": 0,
          "data": {
            "userInfo": { "userId", "username", "nickname", "avatar", ... },
            "token": "..."
          }
        }
    """
    exchange_url = settings.sso.ticket_exchange_url
    if not exchange_url:
        raise SSOTicketError("SSO_TICKET_EXCHANGE_URL not configured", status_code=502)

    timeout = settings.sso.timeout

    try:
        resp = requests.get(exchange_url, params={"ticket": ticket}, timeout=timeout)
    except requests.RequestException as exc:
        logger.error("sso_exchange_network_error", ticket=_mask_ticket(ticket), error=str(exc))
        raise SSOTicketError("SSO service unavailable", status_code=502) from exc

    if resp.status_code != 200:
        logger.warning("sso_exchange_http_error", ticket=_mask_ticket(ticket),
                       status=resp.status_code, body=resp.text[:200])
        raise SSOTicketError(f"SSO returned HTTP {resp.status_code}", status_code=401)

    try:
        data = resp.json()
    except ValueError:
        raise SSOTicketError("SSO returned invalid JSON", status_code=502)

    # Check business-level error code
    if data.get("code") != 0:
        msg = data.get("message") or data.get("msg") or "SSO ticket exchange failed"
        logger.warning("sso_exchange_biz_error", ticket=_mask_ticket(ticket), code=data.get("code"), msg=msg)
        raise SSOTicketError(str(msg), status_code=401)

    # Try to extract user info from response
    user_info = _normalise_sso_response(data)
    if not user_info:
        raise SSOTicketError("SSO response missing user info", status_code=502)

    logger.info("sso_ticket_exchanged", ticket=_mask_ticket(ticket),
                user_center_id=user_info.get("user_center_id"))
    return user_info


def _normalise_sso_response(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract user info from the SSO response.

    Expected production format:
        {
          "code": 0,
          "data": {
            "userInfo": {
              "userId": "2031613182211670018",
              "username": "jinxingTest",
              "nickname": "iChainAgent测试账号",
              "avatar": null,
              "roles": ["JINGXIN_MENBER"],
              ...
            },
            "token": "eyJ..."
          }
        }

    Also supports flattened formats for backward compatibility:
      - { "data": { "user_center_id": ..., "username": ... } }
      - { "user_center_id": ..., "username": ... }
    """
    # Unwrap nested "data" if present
    inner = data.get("data", data)
    if not isinstance(inner, dict):
        return None

    # ── Production format: data.userInfo ──
    user_info = inner.get("userInfo")
    if isinstance(user_info, dict):
        uid = (
            user_info.get("userId")
            or user_info.get("user_id")
            or user_info.get("subId")
        )
        if not uid:
            return None

        name = user_info.get("username") or user_info.get("nickname") or str(uid)
        nickname = user_info.get("nickname") or name
        sso_token = inner.get("token")

        return {
            "user_center_id": str(uid),
            "username": str(name),
            "nickname": str(nickname),
            "email": user_info.get("email"),
            "avatar_url": user_info.get("avatar_url") or user_info.get("avatar"),
            "roles": user_info.get("roles", []),
            "sso_token": sso_token,
        }

    # ── Fallback: flattened format ──
    uid = (
        inner.get("user_center_id")
        or inner.get("user_id")
        or inner.get("userId")
        or inner.get("uid")
        or inner.get("id")
    )
    name = (
        inner.get("username")
        or inner.get("userName")
        or inner.get("name")
        or inner.get("display_name")
        or inner.get("realname")
        or inner.get("memberNickName")
    )
    nickname = inner.get("memberNickName") or inner.get("nickname") or name

    if not uid:
        return None

    return {
        "user_center_id": str(uid),
        "username": str(name or uid),
        "nickname": str(nickname or uid),
        "email": inner.get("email"),
        "avatar_url": inner.get("avatar_url") or inner.get("avatar"),
    }
