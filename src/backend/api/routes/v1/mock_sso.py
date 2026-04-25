"""Mock SSO server for local development and testing.

Provides two endpoints that simulate the external unified login system:
  - GET  /mock-sso/login          → Generates a one-time ticket and redirects to the app
  - POST /mock-sso/ticket/exchange → Validates the ticket and returns user info

Enable with:  SSO_MOCK_ENABLED=true  in .env

To test the full flow manually:
  1. Open http://localhost:3001/mock-sso/login?redirect=/ in your browser
  2. The mock SSO will generate a ticket and redirect to the app with ?ticket=...
"""

import os
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from core.infra.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/mock-sso", tags=["Mock SSO"])

# ── In-memory ticket store ───────────────────────────────────────────────
# ticket → { user_info, created_at }
# Tickets expire after 300 seconds and are one-time use.
_TICKET_STORE: Dict[str, Dict[str, Any]] = {}
_TICKET_TTL = 300  # seconds

# Predefined mock users with passwords
# password 字段仅用于 mock 登录验证，不会随 user_info 下发给业务系统
MOCK_USERS = [
    {
        "user_center_id": "sso_zhangsan_001",
        "username": "张三",
        "email": "zhangsan@example.com",
        "avatar_url": None,
        "password": "zhangsan123",
    },
    {
        "user_center_id": "sso_lisi_002",
        "username": "李四",
        "email": "lisi@example.com",
        "avatar_url": None,
        "password": "lisi@456",
    },
    {
        "user_center_id": "sso_wangwu_003",
        "username": "王五",
        "email": "wangwu@example.com",
        "avatar_url": None,
        "password": "wangwu#789",
    },
    {
        "user_center_id": "sso_gongxinyuan_004",
        "username": "gongxinyuan",
        "email": "gongxinyuan@example.com",
        "avatar_url": None,
        "password": "gongxinyuan123",
    },
]

# username → user dict，方便快速查找
_USER_BY_NAME: Dict[str, Dict[str, Any]] = {u["username"]: u for u in MOCK_USERS}


def _cleanup_expired() -> None:
    """Remove expired tickets from the store."""
    now = time.time()
    expired = [t for t, v in _TICKET_STORE.items() if now - v["created_at"] > _TICKET_TTL]
    for t in expired:
        _TICKET_STORE.pop(t, None)


def _generate_ticket(user_info: Dict[str, Any]) -> str:
    """Generate a one-time ticket for the given user."""
    _cleanup_expired()
    ticket = f"mock_ticket_{secrets.token_urlsafe(16)}"
    _TICKET_STORE[ticket] = {
        "user_info": user_info,
        "created_at": time.time(),
    }
    return ticket


def consume_ticket(ticket: str) -> Optional[Dict[str, Any]]:
    """Consume (validate + delete) a ticket. Returns user info or None."""
    _cleanup_expired()
    entry = _TICKET_STORE.pop(ticket, None)
    if entry is None:
        return None
    if time.time() - entry["created_at"] > _TICKET_TTL:
        return None
    return entry["user_info"]


def _user_info_without_password(user: Dict[str, Any]) -> Dict[str, Any]:
    """返回不含 password 字段的用户信息，供下游业务使用。"""
    return {k: v for k, v in user.items() if k != "password"}


def _resolve_frontend_origin(request: Request, redirect: str) -> str:
    """Resolve the frontend origin dynamically instead of hard-coding one port."""
    configured = os.getenv("MOCK_SSO_APP_BASE", "").strip()
    if configured:
        return configured.rstrip("/")

    parsed_redirect = urlparse(redirect or "")
    if parsed_redirect.scheme and parsed_redirect.netloc:
        return f"{parsed_redirect.scheme}://{parsed_redirect.netloc}"

    for header in ("origin", "referer"):
        raw = (request.headers.get(header) or "").strip()
        if not raw:
            continue
        parsed = urlparse(raw)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"

    forwarded_host = (request.headers.get("x-forwarded-host") or "").strip()
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").strip() or request.url.scheme
    if forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}"

    frontend_port = os.getenv("FRONTEND_PORT", "3000")
    return f"http://localhost:{frontend_port}"


def _build_redirect_target(request: Request, redirect: str, ticket: str) -> str:
    """Redirect back to the frontend page that initiated the login flow."""
    frontend_origin = _resolve_frontend_origin(request, redirect)
    parsed_redirect = urlparse(redirect or "/")

    redirect_path = parsed_redirect.path or "/"
    redirect_query = parsed_redirect.query
    redirect_fragment = parsed_redirect.fragment

    existing_query = dict(parse_qsl(redirect_query, keep_blank_values=True))
    existing_query["ticket"] = ticket
    existing_query["redirect"] = redirect_path

    return urlunparse((
        urlparse(frontend_origin).scheme,
        urlparse(frontend_origin).netloc,
        redirect_path,
        "",
        urlencode(existing_query, doseq=True),
        redirect_fragment,
    ))


# ── Endpoints ────────────────────────────────────────────────────────────

@router.get("/login", summary="模拟 SSO 登录页面")
async def mock_login_page(
    request: Request,
    redirect: str = Query("/", description="登录成功后前端跳转路径"),
    auto: Optional[str] = Query(None, description="自动登录的用户序号 (0/1/2)，跳过密码验证"),
    error: Optional[str] = Query(None, description="登录失败时的错误提示"),
):
    """Render a login page with username selector and password input.

    If ``auto`` is provided, skip password verification and immediately redirect.
    """
    # Auto-login shortcut（用于脚本/测试，跳过密码）
    if auto is not None:
        try:
            idx = int(auto)
            user = MOCK_USERS[idx]
        except (ValueError, IndexError):
            user = MOCK_USERS[0]
        ticket = _generate_ticket(_user_info_without_password(user))
        target = _build_redirect_target(request, redirect, ticket)
        return RedirectResponse(url=target, status_code=302)

    # Build user option list for <select>
    user_options = ""
    for u in MOCK_USERS:
        user_options += f'<option value="{u["username"]}">{u["username"]} ({u["email"]})</option>\n'

    error_html = f'<div class="error-row" id="formError" role="alert"{" hidden" if not error else ""}>{error or ""}</div>'

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>统一身份认证登录</title>
  <link rel="preload" as="image" href="/home/mock-sso-bg-original.png"/>
  <style>
    :root {{
      --primary:#126dff;
      --primary-hover:#3c87ff;
      --text:#262626;
      --muted:#808080;
      --border:#d8dbe2;
      --danger:#fc5d5d;
    }}
    * {{ box-sizing:border-box; }}
    html, body {{ height:100%; }}
    body {{
      margin:0;
      font-family:"PingFang SC","Microsoft YaHei","微软雅黑",sans-serif;
      color:var(--text);
      background:
        linear-gradient(128deg, rgba(219, 233, 255, 0.18) 0%, rgba(237, 244, 255, 0.10) 36%, rgba(249, 251, 255, 0.06) 74%, rgba(244, 248, 255, 0.16) 100%),
        url('/home/mock-sso-bg-original.png') center center / cover no-repeat,
        linear-gradient(180deg, #f7fbff 0%, #edf5ff 100%);
      background-attachment: fixed;
    }}
    .page {{
      position:relative;
      min-height:100vh;
      overflow:hidden;
    }}
    .page::before {{
      content:"";
      position:absolute;
      inset:0;
      background:
        radial-gradient(circle at 18% 18%, rgba(18,109,255,0.08) 0, rgba(18,109,255,0) 24%),
        radial-gradient(circle at 88% 12%, rgba(164,197,255,0.14) 0, rgba(164,197,255,0) 22%);
      pointer-events:none;
    }}
    .brand {{
      position:relative;
      z-index:1;
      display:flex;
      align-items:center;
      gap:12px;
      padding:46px 56px 0;
    }}
    .brand-link {{
      display:inline-flex;
      align-items:center;
      gap:12px;
      text-decoration:none;
      color:inherit;
      border-radius:12px;
      transition:opacity .18s ease, transform .18s ease;
    }}
    .brand-link:hover {{
      opacity:.9;
      transform:translateY(-1px);
    }}
    .brand img {{
      width:38px;
      height:38px;
      display:block;
    }}
    .brand span {{
      font-size:24px;
      font-weight:600;
      line-height:1;
      color:#101828;
    }}
    .main {{
      position:relative;
      z-index:1;
      display:grid;
      grid-template-columns:minmax(0, 1.08fr) minmax(380px, 497px);
      gap:64px;
      align-items:center;
      min-height:calc(100vh - 140px);
      padding:24px 88px 96px;
    }}
    .visual {{
      position:relative;
      min-height:640px;
    }}
    .scene-tag {{
      position:absolute;
      display:inline-flex;
      align-items:center;
      justify-content:center;
      min-width:62px;
      height:26px;
      padding:0 12px;
      background:rgba(255,255,255,0.92);
      border:1px solid rgba(18,109,255,0.34);
      border-radius:999px;
      box-shadow:0 8px 18px rgba(37,99,235,0.08);
      color:#4c4c4c;
      font-size:12px;
      line-height:1;
    }}
    .scene-tag::after {{
      content:"";
      position:absolute;
      left:50%;
      top:100%;
      width:1px;
      height:18px;
      background:linear-gradient(180deg, rgba(18,109,255,0.4) 0%, rgba(18,109,255,0) 100%);
      transform:translateX(-50%);
    }}
    .tag-ai {{ top:8.8%; left:17%; }}
    .tag-agent {{ top:26.8%; left:56%; }}
    .tag-kb {{ top:64%; left:32%; }}
    .panel {{
      display:flex;
      align-items:center;
      justify-content:center;
    }}
    .card {{
      width:100%;
      min-height:516px;
      padding:78px 40px 40px;
      background:linear-gradient(180deg, rgba(255,255,255,0.93) 0%, rgba(255,255,255,0.88) 100%);
      border:1px solid rgba(255,255,255,0.72);
      border-radius:14px;
      box-shadow:0 18px 50px rgba(149,171,209,0.18), inset 0 1px 0 rgba(255,255,255,0.65);
      backdrop-filter:blur(10px);
    }}
    .title {{
      margin:0 0 44px;
      font-size:28px;
      line-height:1.2;
      font-weight:600;
      text-align:center;
      color:var(--text);
    }}
    .hint {{
      margin:0 0 22px;
      color:var(--muted);
      font-size:14px;
      text-align:center;
    }}
    .mock-badge {{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      margin:0 auto 10px;
      padding:3px 10px;
      border-radius:999px;
      background:rgba(255,243,224,0.86);
      color:#e67626;
      font-size:11px;
      letter-spacing:.04em;
    }}
    form {{ display:flex; flex-direction:column; gap:18px; }}
    .sr-only {{
      position:absolute;
      width:1px;
      height:1px;
      padding:0;
      margin:-1px;
      overflow:hidden;
      clip:rect(0,0,0,0);
      white-space:nowrap;
      border:0;
    }}
    .field-shell {{
      position:relative;
      display:flex;
      align-items:center;
      height:46px;
      border:1px solid rgba(18,109,255,0.18);
      border-radius:8px;
      background:rgba(255,255,255,0.96);
      transition:border-color .2s ease, box-shadow .2s ease;
      overflow:hidden;
    }}
    .field-shell.username-shell {{
      overflow:visible;
    }}
    .field-shell:focus-within {{
      border-color:var(--primary);
      box-shadow:0 0 0 2px rgba(18,109,255,0.08);
    }}
    .field-icon {{
      flex:0 0 36px;
      width:36px;
      height:100%;
      display:flex;
      align-items:center;
      justify-content:center;
      color:#c3c7cf;
      opacity:.96;
    }}
    .field-icon svg {{
      width:18px;
      height:18px;
      display:block;
      stroke:currentColor;
      fill:none;
      stroke-width:1.75;
      stroke-linecap:round;
      stroke-linejoin:round;
    }}
    .field-user svg {{
      width:19px;
      height:19px;
    }}
    .field-pass svg {{
      width:18px;
      height:18px;
    }}
    .username-field {{
      position:relative;
      flex:1;
      height:100%;
    }}
    .username-input {{
      width:100%;
      height:100%;
      display:flex;
      align-items:center;
      justify-content:flex-start;
      padding:0 38px 0 4px;
      font-size:14px;
      color:var(--text);
      border:none;
      background:transparent;
      cursor:pointer;
      user-select:none;
      position:relative;
      text-align:left;
    }}
    .username-caret {{
      position:absolute;
      right:16px;
      top:50%;
      width:12px;
      height:12px;
      background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12' fill='none'%3E%3Cpath d='M2.5 4.5 6 8l3.5-3.5' stroke='%23333' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") center / 12px 12px no-repeat;
      transform:translateY(-50%);
      transition:transform .24s cubic-bezier(.22, 1, .36, 1), opacity .2s ease;
      opacity:.82;
      pointer-events:none;
      transform-origin:center;
    }}
    .username-field.open .username-caret {{
      transform:translateY(-50%) rotate(180deg);
    }}
    .username-value {{
      display:block;
      width:100%;
      overflow:hidden;
      white-space:nowrap;
      text-overflow:ellipsis;
    }}
    .username-menu {{
      position:absolute;
      left:-1px;
      right:-1px;
      top:calc(100% + 8px);
      margin:0;
      padding:8px;
      list-style:none;
      border:1px solid rgba(18,109,255,0.14);
      border-radius:10px;
      background:rgba(255,255,255,0.98);
      box-shadow:0 16px 34px rgba(86, 113, 164, 0.16), 0 2px 10px rgba(110, 135, 179, 0.08);
      backdrop-filter:blur(12px);
      display:none;
      z-index:10;
    }}
    .username-field.open .username-menu {{
      display:block;
    }}
    .username-option {{
      display:flex;
      align-items:center;
      width:100%;
      min-height:40px;
      padding:0 14px;
      border:none;
      border-radius:8px;
      background:transparent;
      color:#243248;
      font-size:14px;
      line-height:1.45;
      text-align:left;
      cursor:pointer;
      transition:background .18s ease, color .18s ease;
    }}
    .username-option:hover {{
      background:rgba(18,109,255,0.08);
      color:#1f4fd9;
    }}
    .username-option.active {{
      background:linear-gradient(180deg, rgba(18,109,255,0.12) 0%, rgba(18,109,255,0.08) 100%);
      color:#1650e6;
      font-weight:600;
      margin:2px 0;
    }}
    .username-option:focus-visible,
    .username-input:focus-visible {{
      outline:none;
      box-shadow:0 0 0 2px rgba(18,109,255,0.12);
    }}
    input[type=password] {{
      display:block;
      width:100%;
      height:100%;
      padding:0 16px 0 4px;
      font-size:14px;
      color:var(--text);
      border:none;
      border-radius:0;
      background:transparent;
      outline:none;
      box-shadow:none;
    }}
    .error-row {{
      margin-top:-4px;
      margin-bottom:4px;
      color:var(--danger);
      font-size:13px;
      line-height:1.5;
    }}
    .error-row[hidden] {{
      display:none;
    }}
    button[type=submit] {{
      display:block;
      width:100%;
      height:52px;
      margin-top:6px;
      font-size:18px;
      font-weight:600;
      letter-spacing:.08em;
      color:#fff;
      background:var(--primary);
      border:none;
      border-radius:8px;
      box-shadow:0 14px 24px rgba(18,109,255,0.18);
      cursor:pointer;
      transition:background .2s ease, transform .2s ease;
    }}
    button[type=submit]:hover {{
      background:var(--primary-hover);
      transform:translateY(-1px);
    }}
    .footer {{
      position:absolute;
      left:50%;
      bottom:22px;
      z-index:1;
      width:min(90vw, 720px);
      color:rgba(92, 108, 132, 0.72);
      font-size:12px;
      font-weight:400;
      line-height:1.8;
      letter-spacing:.02em;
      text-align:center;
      transform:translateX(-50%);
      transition:color .24s ease, opacity .24s ease, transform .24s ease;
      cursor:default;
    }}
    .footer::after {{
      content:"";
      position:absolute;
      left:50%;
      bottom:-4px;
      width:140px;
      height:1px;
      background:linear-gradient(90deg, rgba(18,109,255,0) 0%, rgba(18,109,255,0.18) 50%, rgba(18,109,255,0) 100%);
      opacity:0;
      transform:translateX(-50%);
      transition:opacity .24s ease, width .28s ease;
      pointer-events:none;
    }}
    .footer:hover {{
      color:rgba(63, 84, 119, 0.88);
      transform:translateX(-50%) translateY(-1px);
    }}
    .footer:hover::after {{
      opacity:1;
      width:184px;
    }}
    @media (max-width: 960px) {{
      .brand {{ padding:28px 24px 0; }}
      .main {{
        grid-template-columns:1fr;
        gap:24px;
        min-height:auto;
        padding:12px 20px 108px;
      }}
      .visual {{ display:none; }}
      .card {{ min-height:unset; padding:40px 22px 28px; }}
      .title {{ margin-bottom:28px; font-size:24px; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <header class="brand">
      <a class="brand-link" href="/mock-sso/login?redirect={redirect}">
        <img src="/home/logo.svg" alt="logo" />
        <span>经信智能体</span>
      </a>
    </header>
    <main class="main">
      <section class="visual" aria-hidden="true">
        <div class="scene-tag tag-ai">AI 问答</div>
        <div class="scene-tag tag-agent">智能体</div>
        <div class="scene-tag tag-kb">知识库</div>
      </section>
      <section class="panel">
        <div class="card">
          <div style="text-align:center"><span class="mock-badge">Mock SSO</span></div>
          <h1 class="title">统一身份认证登录</h1>
          <p class="hint">开发测试环境，请选择账号并输入密码</p>
          <form method="POST" action="/mock-sso/login" id="loginForm" novalidate>
            <input type="hidden" name="redirect" value="{redirect}"/>
            <div>
              <label class="sr-only" for="username">账号</label>
              <div class="field-shell username-shell">
                <span class="field-icon field-user" aria-hidden="true">
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M12 12a4.2 4.2 0 1 0 0-8.4a4.2 4.2 0 0 0 0 8.4Z"></path>
                    <path d="M4.8 19.2a7.2 7.2 0 0 1 14.4 0"></path>
                  </svg>
                </span>
                <div class="username-field" id="usernameField">
                  <input type="hidden" name="username" id="username" value="{MOCK_USERS[0]["username"]}"/>
                  <button type="button" class="username-input" id="usernameTrigger" aria-haspopup="listbox" aria-expanded="false" aria-controls="usernameMenu">
                    <span class="username-value" id="usernameValue">{MOCK_USERS[0]["username"]} ({MOCK_USERS[0]["email"]})</span>
                    <span class="username-caret" aria-hidden="true"></span>
                  </button>
                  <ul class="username-menu" id="usernameMenu" role="listbox" aria-labelledby="usernameTrigger">
                    {''.join(f'<li><button type="button" class="username-option{" active" if i == 0 else ""}" data-username="{u["username"]}" data-label="{u["username"]} ({u["email"]})" role="option" aria-selected="{"true" if i == 0 else "false"}">{u["username"]} ({u["email"]})</button></li>' for i, u in enumerate(MOCK_USERS))}
                  </ul>
                </div>
              </div>
            </div>
            <div>
              <label class="sr-only" for="password">密码</label>
              <div class="field-shell">
                <span class="field-icon field-pass" aria-hidden="true">
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <rect x="5.5" y="10.5" width="13" height="9" rx="2"></rect>
                    <path d="M8.5 10.5V8.4a3.5 3.5 0 1 1 7 0v2.1"></path>
                  </svg>
                </span>
                <input type="password" name="password" id="password" placeholder="请输入密码" autocomplete="current-password" required/>
              </div>
            </div>
            {error_html}
            <button type="submit">登录</button>
          </form>
        </div>
      </section>
    </main>
    <footer class="footer">
      致力于构建面向未来的组织级AI生产力平台
    </footer>
  </div>
  <script>
    (function() {{
      const field = document.getElementById('usernameField');
      const trigger = document.getElementById('usernameTrigger');
      const menu = document.getElementById('usernameMenu');
      const value = document.getElementById('usernameValue');
      const input = document.getElementById('username');
      const form = document.getElementById('loginForm');
      const password = document.getElementById('password');
      const formError = document.getElementById('formError');
      if (!field || !trigger || !menu || !value || !input || !form || !password || !formError) return;

      const options = Array.from(menu.querySelectorAll('.username-option'));

      function showError(message) {{
        formError.textContent = message;
        formError.hidden = false;
      }}

      function clearError() {{
        formError.textContent = '';
        formError.hidden = true;
      }}

      function setOpen(open) {{
        field.classList.toggle('open', open);
        trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
      }}

      function selectOption(option) {{
        const username = option.getAttribute('data-username') || '';
        const label = option.getAttribute('data-label') || '';
        input.value = username;
        value.textContent = label;
        options.forEach((item) => {{
          const active = item === option;
          item.classList.toggle('active', active);
          item.setAttribute('aria-selected', active ? 'true' : 'false');
        }});
        setOpen(false);
      }}

      trigger.addEventListener('click', () => {{
        setOpen(!field.classList.contains('open'));
      }});

      options.forEach((option) => {{
        option.addEventListener('click', () => selectOption(option));
      }});

      form.addEventListener('submit', (event) => {{
        if (!password.value.trim()) {{
          event.preventDefault();
          showError('请输入密码');
          password.focus();
          return;
        }}
        clearError();
      }});

      password.addEventListener('input', () => {{
        if (password.value.trim()) {{
          clearError();
        }}
      }});

      document.addEventListener('click', (event) => {{
        if (!field.contains(event.target)) {{
          setOpen(false);
        }}
      }});

      document.addEventListener('keydown', (event) => {{
        if (event.key === 'Escape') {{
          setOpen(false);
        }}
      }});
    }})();
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.post("/login", summary="模拟 SSO 密码验证")
async def mock_login_submit(
    request: Request,
    username: str = Form(""),
    password: Optional[str] = Form(None),
    redirect: str = Form("/"),
):
    """Handle login form submission: validate password and issue a ticket."""
    if not password or not password.strip():
        return RedirectResponse(
            url=f"/mock-sso/login?redirect={redirect}&error=请输入密码",
            status_code=303,
        )

    user = _USER_BY_NAME.get(username)
    if user is None or user["password"] != password:
        # 密码错误，重定向回登录页并显示错误信息
        return RedirectResponse(
            url=f"/mock-sso/login?redirect={redirect}&error=用户名或密码错误，请重试",
            status_code=303,
        )

    ticket = _generate_ticket(_user_info_without_password(user))
    target = _build_redirect_target(request, redirect, ticket)
    return RedirectResponse(url=target, status_code=303)


@router.post("/ticket/exchange", summary="模拟 ticket 换取用户信息")
async def mock_ticket_exchange(body: dict):
    """Simulate the SSO ticket exchange endpoint.

    This is what the backend's ``sso_client.py`` calls when ``SSO_MOCK_ENABLED=true``.
    It can also be called externally if ``SSO_TICKET_EXCHANGE_URL`` points here.
    """
    ticket = body.get("ticket", "")
    user_info = consume_ticket(ticket)

    if user_info is None:
        return {"code": 401, "message": "Invalid or expired ticket", "data": None}

    return {
        "code": 0,
        "message": "ok",
        "data": user_info,
    }
