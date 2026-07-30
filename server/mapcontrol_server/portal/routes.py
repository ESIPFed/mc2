"""FastAPI routes for the standalone auth portal.

Registered by ``main.py`` ONLY when ``MAPCONTROL_AUTH_MODE=standalone``
(before the catch-all MCP mount, so these match first):

    GET  /            login page ↔ dashboard (session-dependent)
    POST /login       verify credentials → set session cookie
    GET  /logout      clear the session cookie
    GET  /signup      registration form   (human accounts — NOT OAuth DCR,
    POST /signup      which stays at POST /register inside the MCP app)
    GET  /consent     OAuth authorize gate (from PortalAuthServerProvider)
    POST /consent     approve/deny → mint code / drop txn
    GET  /admin       user administration (admin + approvers)
    POST /admin/users approve/disable/role-change actions

Lifecycle: ``portal_startup()`` / ``portal_shutdown()`` are called from the
app lifespan to open the user store and seed the admin account.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import pages
from .provider import get_portal_provider
from .security import (
    SESSION_COOKIE,
    create_session_token,
    hash_password,
    read_session_token,
)
from .store import (
    ROLE_APPROVER,
    ROLE_USER,
    STATUS_APPROVED,
    STATUS_DISABLED,
    STATUS_PENDING,
    CredentialVerifier,
    PortalUser,
    UserStore,
    build_backend,
    seed_admin,
)

logger = logging.getLogger("mapcontrol")

router = APIRouter(tags=["portal"])

_store: UserStore | None = None
_verifier: CredentialVerifier | None = None


# ─── Lifecycle ────────────────────────────────────────────────────────────────

async def portal_startup() -> None:
    """Open the user store + seed the admin account. Fail-loud by design."""
    global _store, _verifier
    _store, _verifier = build_backend()
    await _store.startup()
    await seed_admin(_store)


async def portal_shutdown() -> None:
    global _store, _verifier
    if _store is not None:
        await _store.shutdown()
    _store = None
    _verifier = None


def _require_store() -> UserStore:
    if _store is None:
        raise RuntimeError("Portal not started — portal_startup() was not called")
    return _store


# ─── Session helpers ──────────────────────────────────────────────────────────

async def current_user(request: Request) -> PortalUser | None:
    """The logged-in, still-valid portal user for this request (or None).

    Role/status are re-read from the store on every request so a mid-session
    disable or role change takes effect immediately.
    """
    data = read_session_token(request.cookies.get(SESSION_COOKIE))
    if not data:
        return None
    user = await _require_store().get_user(data.get("u", ""))
    if user is None or not user.is_approved:
        return None
    return user


def _set_session(response, user: PortalUser) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user.username, user.role),
        httponly=True,
        samesite="lax",
        path="/",
    )


def _safe_next(next_url: str) -> str:
    """Only allow same-origin relative redirect targets (open-redirect guard)."""
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


# ─── / (login ↔ dashboard) ────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = await current_user(request)
    if user is None:
        return HTMLResponse(pages.login_page(next_url=str(
            _safe_next(request.query_params.get("next", "/"))
        )))
    base = str(request.base_url).rstrip("/")
    from ..auth_server import _public_base

    return HTMLResponse(pages.dashboard_page(user, _public_base() or base))


@router.post("/login")
async def login(request: Request):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    next_url = _safe_next(str(form.get("next", "/")))

    assert _verifier is not None
    user = await _verifier.verify(username, password)
    if user is None:
        return HTMLResponse(pages.login_page(error="Invalid username or password."),
                            status_code=401)
    if user.status == STATUS_PENDING:
        return HTMLResponse(
            pages.login_page(notice="Your account is pending approval — try again later."),
            status_code=403,
        )
    if user.status == STATUS_DISABLED:
        return HTMLResponse(pages.login_page(error="This account is disabled."),
                            status_code=403)

    response = RedirectResponse(url=next_url, status_code=302)
    _set_session(response, user)
    logger.info("Portal: %s signed in", username)
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


# ─── Signup (human registration; OAuth DCR stays at POST /register) ──────────

@router.get("/signup", response_class=HTMLResponse)
async def signup_form():
    return HTMLResponse(pages.signup_page())


@router.post("/signup")
async def signup(request: Request):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    password2 = str(form.get("password2", ""))

    import re

    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        return HTMLResponse(pages.signup_page(
            error="Username must be 3-32 characters (letters, digits, _ . -)."),
            status_code=400)
    if len(password) < 8:
        return HTMLResponse(pages.signup_page(
            error="Password must be at least 8 characters."), status_code=400)
    if password != password2:
        return HTMLResponse(pages.signup_page(
            error="Passwords do not match."), status_code=400)

    store = _require_store()
    if await store.get_user(username) is not None:
        return HTMLResponse(pages.signup_page(
            error="That username is taken."), status_code=409)

    await store.create_user(PortalUser(
        username=username,
        password_hash=hash_password(password),
        role=ROLE_USER,
        status=STATUS_PENDING,
    ))
    logger.info("Portal: new registration %r (pending approval)", username)
    return HTMLResponse(pages.signup_done_page())


# ─── Consent (the OAuth /authorize gate) ──────────────────────────────────────

@router.get("/consent", response_class=HTMLResponse)
async def consent_form(request: Request):
    txn = request.query_params.get("txn", "")
    user = await current_user(request)
    if user is None:
        # Bounce through login, then return to this consent URL.
        return RedirectResponse(url=f"/?next=/consent%3Ftxn%3D{txn}", status_code=302)
    provider = get_portal_provider()
    entry = provider.peek_txn(txn)
    if entry is None:
        return HTMLResponse(pages.consent_gone_page(user), status_code=410)
    client, params = entry
    client_name = str(getattr(client, "client_name", None) or "An MCP client")
    scope = " ".join(params.scopes) if params.scopes else "mcp:use"
    return HTMLResponse(pages.consent_page(user, txn, client_name, scope))


@router.post("/consent")
async def consent_submit(request: Request):
    form = await request.form()
    txn = str(form.get("txn", ""))
    decision = str(form.get("decision", ""))
    user = await current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=302)
    provider = get_portal_provider()
    if decision != "approve":
        provider.deny(txn)
        return HTMLResponse(pages.consent_gone_page(user), status_code=200)
    redirect_url = provider.approve(txn, user.username)
    if redirect_url is None:
        return HTMLResponse(pages.consent_gone_page(user), status_code=410)
    return RedirectResponse(url=redirect_url, status_code=302)


# ─── Admin ────────────────────────────────────────────────────────────────────

@router.get("/admin", response_class=HTMLResponse)
async def admin(request: Request):
    user = await current_user(request)
    if user is None:
        return RedirectResponse(url="/?next=/admin", status_code=302)
    if not user.can_approve:
        return HTMLResponse(pages.forbidden_page(user), status_code=403)
    users = await _require_store().list_users()
    return HTMLResponse(pages.admin_page(user, users))


@router.post("/admin/users")
async def admin_users(request: Request):
    actor = await current_user(request)
    if actor is None:
        return RedirectResponse(url="/", status_code=302)
    if not actor.can_approve:
        return HTMLResponse(pages.forbidden_page(actor), status_code=403)

    form = await request.form()
    username = str(form.get("username", "")).strip()
    action = str(form.get("action", ""))
    store = _require_store()

    async def render(error: str = "", notice: str = "", status: int = 200):
        return HTMLResponse(
            pages.admin_page(actor, await store.list_users(), error, notice),
            status_code=status,
        )

    target = await store.get_user(username)
    if target is None:
        return await render(error=f"No such user: {username}", status=404)
    if target.username == "admin":
        return await render(error="The admin account cannot be modified here.",
                            status=400)

    if action == "approve":
        await store.update_user(username, status=STATUS_APPROVED)
        return await render(notice=f"{username} approved.")
    if action == "disable":
        await store.update_user(username, status=STATUS_DISABLED)
        return await render(notice=f"{username} disabled.")
    if action in ("make_approver", "revoke_approver"):
        if not actor.is_admin:  # only the admin manages the approver role
            return HTMLResponse(pages.forbidden_page(actor), status_code=403)
        role = ROLE_APPROVER if action == "make_approver" else ROLE_USER
        await store.update_user(username, role=role)
        return await render(notice=f"{username} role set to {role}.")

    return await render(error=f"Unknown action: {action}", status=400)
