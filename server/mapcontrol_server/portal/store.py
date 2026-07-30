"""User persistence + credential verification for the standalone portal.

Extensibility contract (batteries included, swappable)
------------------------------------------------------
Two small Protocols decouple the portal's pages/flows from *where* users live
and *how* credentials are checked:

* :class:`UserStore` — persistence (list/get/create/update users).
* :class:`CredentialVerifier` — "is this username+password valid?".

Defaults (zero config):

* ``SqlUserStore`` — SQLAlchemy Core (async) on SQLite at
  ``./data/portal.db``. Set ``MAPCONTROL_USERS_DB_URL`` to any SQLAlchemy
  async URL (``postgresql+asyncpg://…``, ``mysql+aiomysql://…``) to swap the
  database with no code changes (install the driver in your image).
* ``LocalCredentialVerifier`` — scrypt password hashes checked against the
  store (see ``security.py``).

Custom backend (``MAPCONTROL_AUTH_BACKEND=pkg.module:factory``): the dotted
path must resolve to a zero-arg callable returning a
``(UserStore, CredentialVerifier)`` tuple. This lets deployments plug in
LDAP / OIDC-password / anything without forking ESIP. Misconfiguration fails
loudly at startup — an auth boundary must never silently degrade.

Bootstrap: on startup the ``admin`` account is seeded from
``MAPCONTROL_ADMIN_PASSWORD`` (required in standalone mode — fail-loud,
mirroring ``MAPCONTROL_MCP_AUTH_SECRET``'s behavior in §5b).
"""

from __future__ import annotations

import importlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from .security import hash_password, verify_password

logger = logging.getLogger("mapcontrol")

# ─── Domain model ─────────────────────────────────────────────────────────────

ROLE_ADMIN = "admin"
ROLE_APPROVER = "approver"
ROLE_USER = "user"

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DISABLED = "disabled"


@dataclass
class PortalUser:
    username: str
    password_hash: str
    role: str = ROLE_USER            # admin | approver | user
    status: str = STATUS_PENDING     # pending | approved | disabled
    created_at: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def can_approve(self) -> bool:
        return self.role in (ROLE_ADMIN, ROLE_APPROVER)

    @property
    def is_approved(self) -> bool:
        return self.status == STATUS_APPROVED


# ─── Protocols (the extensibility seam) ───────────────────────────────────────

@runtime_checkable
class UserStore(Protocol):
    async def startup(self) -> None: ...
    async def shutdown(self) -> None: ...
    async def get_user(self, username: str) -> PortalUser | None: ...
    async def list_users(self) -> list[PortalUser]: ...
    async def create_user(self, user: PortalUser) -> PortalUser: ...
    async def update_user(
        self, username: str, *,
        status: str | None = None,
        role: str | None = None,
        password_hash: str | None = None,
    ) -> PortalUser | None: ...


@runtime_checkable
class CredentialVerifier(Protocol):
    async def verify(self, username: str, password: str) -> PortalUser | None:
        """Return the user when the credentials are valid, else ``None``."""
        ...


# ─── Default store: SQLAlchemy Core (async), SQLite by default ───────────────

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS portal_users (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    status        TEXT NOT NULL DEFAULT 'pending',
    created_at    TEXT NOT NULL
)
"""


def _default_db_url() -> str:
    explicit = os.environ.get("MAPCONTROL_USERS_DB_URL", "").strip()
    if explicit:
        return explicit
    base = os.environ.get("MAPCONTROL_PORTAL_DATA_DIR", "./data")
    return f"sqlite+aiosqlite:///{base}/portal.db"


class SqlUserStore:
    """SQLAlchemy-Core async store. SQLite (aiosqlite) by default; any async
    SQLAlchemy URL via ``MAPCONTROL_USERS_DB_URL``."""

    def __init__(self, db_url: str | None = None) -> None:
        self._db_url = db_url or _default_db_url()
        self._engine = None

    async def startup(self) -> None:
        from pathlib import Path

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        if self._db_url.startswith("sqlite"):
            # Make sure the parent directory exists for file-backed SQLite.
            path_part = self._db_url.split("///", 1)[-1]
            if path_part and path_part != ":memory:":
                Path(path_part).parent.mkdir(parents=True, exist_ok=True)

        self._engine = create_async_engine(self._db_url)
        async with self._engine.begin() as conn:
            await conn.execute(text(_TABLE_DDL))
        logger.info("Portal user store ready (%s)", self._db_url.split("://")[0])

    async def shutdown(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    def _require_engine(self):
        if self._engine is None:
            raise RuntimeError("SqlUserStore not started — call startup() first")
        return self._engine

    @staticmethod
    def _row_to_user(row) -> PortalUser:
        return PortalUser(
            username=row.username,
            password_hash=row.password_hash,
            role=row.role,
            status=row.status,
            created_at=row.created_at,
        )

    async def get_user(self, username: str) -> PortalUser | None:
        from sqlalchemy import text

        async with self._require_engine().connect() as conn:
            result = await conn.execute(
                text("SELECT * FROM portal_users WHERE username = :u"),
                {"u": username},
            )
            row = result.first()
            return self._row_to_user(row) if row else None

    async def list_users(self) -> list[PortalUser]:
        from sqlalchemy import text

        async with self._require_engine().connect() as conn:
            result = await conn.execute(
                text("SELECT * FROM portal_users ORDER BY created_at ASC")
            )
            return [self._row_to_user(row) for row in result.all()]

    async def create_user(self, user: PortalUser) -> PortalUser:
        from sqlalchemy import text

        if not user.created_at:
            user.created_at = datetime.now(timezone.utc).isoformat()
        async with self._require_engine().begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO portal_users "
                    "(username, password_hash, role, status, created_at) "
                    "VALUES (:username, :password_hash, :role, :status, :created_at)"
                ),
                {
                    "username": user.username,
                    "password_hash": user.password_hash,
                    "role": user.role,
                    "status": user.status,
                    "created_at": user.created_at,
                },
            )
        return user

    async def update_user(
        self, username: str, *,
        status: str | None = None,
        role: str | None = None,
        password_hash: str | None = None,
    ) -> PortalUser | None:
        from sqlalchemy import text

        sets, params = [], {"u": username}
        if status is not None:
            sets.append("status = :status")
            params["status"] = status
        if role is not None:
            sets.append("role = :role")
            params["role"] = role
        if password_hash is not None:
            sets.append("password_hash = :ph")
            params["ph"] = password_hash
        if sets:
            async with self._require_engine().begin() as conn:
                await conn.execute(
                    text(f"UPDATE portal_users SET {', '.join(sets)} WHERE username = :u"),
                    params,
                )
        return await self.get_user(username)


# ─── Default verifier: scrypt against the store ───────────────────────────────

class LocalCredentialVerifier:
    def __init__(self, store: UserStore) -> None:
        self._store = store

    async def verify(self, username: str, password: str) -> PortalUser | None:
        user = await self._store.get_user(username)
        if user is None:
            # Burn comparable time so missing-user vs wrong-password is not a
            # trivially observable timing difference.
            verify_password(password, hash_password("timing-equalizer"))
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user


# ─── Backend factory (default vs dotted-path plugin) ─────────────────────────

def build_backend() -> tuple[UserStore, CredentialVerifier]:
    """Instantiate the configured (store, verifier) pair. Fail loud on error."""
    spec = os.environ.get("MAPCONTROL_AUTH_BACKEND", "").strip()
    if not spec or spec == "local":
        store = SqlUserStore()
        return store, LocalCredentialVerifier(store)

    if ":" not in spec:
        raise RuntimeError(
            f"MAPCONTROL_AUTH_BACKEND={spec!r} is invalid — expected "
            "'local' or a dotted path 'pkg.module:factory'."
        )
    module_path, attr = spec.split(":", 1)
    try:
        module = importlib.import_module(module_path)
        factory = getattr(module, attr)
        store, verifier = factory()
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load MAPCONTROL_AUTH_BACKEND={spec!r}: {exc}. "
            "The factory must be a zero-arg callable returning "
            "(UserStore, CredentialVerifier)."
        ) from exc
    if not isinstance(store, UserStore) or not isinstance(verifier, CredentialVerifier):
        raise RuntimeError(
            f"MAPCONTROL_AUTH_BACKEND={spec!r} returned objects that do not "
            "satisfy the UserStore / CredentialVerifier protocols."
        )
    logger.info("Portal auth backend: custom (%s)", spec)
    return store, verifier


# ─── Admin bootstrap ──────────────────────────────────────────────────────────

async def seed_admin(store: UserStore) -> None:
    """Ensure the ``admin`` account exists (standalone mode requires it).

    Fail-loud contract: MAPCONTROL_ADMIN_PASSWORD is REQUIRED the first time
    (no admin row yet). Once the admin exists the env var becomes optional —
    it does NOT overwrite the stored password on later boots (rotate via the
    portal instead).
    """
    existing = await store.get_user("admin")
    if existing is not None:
        return
    password = os.environ.get("MAPCONTROL_ADMIN_PASSWORD", "")
    if not password:
        raise RuntimeError(
            "MAPCONTROL_AUTH_MODE=standalone but MAPCONTROL_ADMIN_PASSWORD is "
            "unset and no admin account exists yet. Set a strong admin "
            "password (this seeds the 'admin' portal account on first run)."
        )
    await store.create_user(PortalUser(
        username="admin",
        password_hash=hash_password(password),
        role=ROLE_ADMIN,
        status=STATUS_APPROVED,
    ))
    logger.info("Portal: seeded the 'admin' account")
