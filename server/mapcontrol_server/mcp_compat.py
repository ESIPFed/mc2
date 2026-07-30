"""2026 draft-revision pre-adoption shims (Phase 5, forward-compatible half).

The next MCP spec revision (see ``docs/vision/mcp-2026-protocol-upgrade.md``) is
still a Draft and the installed ``mcp`` SDK (1.28.x) does not speak it yet —
the breaking half (no ``initialize``, ``server/discover``,
``subscriptions/listen``) MUST wait for the SDK. But several of its
requirements are **purely additive on the wire**: old clients ignore unknown
result fields (the MCP schema is ``extra=allow`` / ``.passthrough()`` in both
the Python and TypeScript SDKs, and the spec requires tolerant readers), so we
can emit them TODAY and be conformant the day the revision ships:

* **SEP-2322 — ``resultType``:** every result we return carries
  ``resultType: "complete"``. We never issue server-initiated requests, so
  ``input_required`` never applies; stamping ``complete`` everywhere is exactly
  the new revision's behavior for this server.
* **SEP-2549 — ``ttlMs`` / ``cacheScope``:** required on ``tools/list``,
  ``resources/list``, ``resources/read``, ``resources/templates/list`` (and
  ``prompts/list`` if it ever registers). Policy: static sets (tools,
  templates) → ``public`` / 1 h; the concrete resource list → ``public`` /
  5 min; ``resources/read`` → ``private`` / 5 s (live, mutable map state).
* **SEP-2575 (SHOULD) — serverInfo in ``_meta``:** each result identifies the
  server via ``io.modelcontextprotocol/serverInfo``. ``_meta`` has been a legal
  spec field since 2025-06-18, so this is safe on every negotiated revision.
* **SEP-2164 — resource-not-found = ``-32602``:** the SDK's generic exception
  path converts our corrective ``ValueError`` s into ``ErrorData(code=0)``.
  This shim detects not-found/unknown-resource failures on the
  ``resources/read`` path and re-raises them as ``McpError`` with the
  spec-aligned ``-32602`` (Invalid Params) code, which the SDK passes through
  verbatim.

Mechanism: FastMCP registers plain ``async`` handlers in the low-level
server's ``request_handlers`` dict; we wrap each one post-registration.
``initialize`` is handled inside ``ServerSession`` (not via this dict), so the
legacy handshake is untouched — dual-revision negotiation and the Claude-web
acceptance criterion (roadmap Phase 5) hold by construction.

Everything here is REPLACED by native SDK behavior when the Phase-5 SDK bump
lands; the module then shrinks to nothing and is deleted. ADR-0001: no env
knobs, identical local/cloud behavior.
"""

from __future__ import annotations

import logging

import mcp.types as types
from mcp.shared.exceptions import McpError

from . import __version__
from .mcp_tools import mcp_server

logger = logging.getLogger("mapcontrol")

# ─── SEP-2575 (SHOULD): server identity on every result ─────────────────────
_SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
_SERVER_INFO = {"name": "ESIP Map Control", "version": __version__}

# ─── SEP-2549: cache policy per cacheable request type ───────────────────────
_HOUR_MS = 3_600_000
_FIVE_MIN_MS = 300_000
_FIVE_SEC_MS = 5_000

_CACHE_POLICY: dict[type, tuple[int, str]] = {
    # Static sets — identical for every caller, safe for shared caches.
    types.ListToolsRequest: (_HOUR_MS, "public"),
    types.ListResourceTemplatesRequest: (_HOUR_MS, "public"),
    types.ListPromptsRequest: (_HOUR_MS, "public"),  # if prompts ever register
    # Concrete resource list — currently static (map://maps) but kept short in
    # case dynamic entries appear.
    types.ListResourcesRequest: (_FIVE_MIN_MS, "public"),
    # Live map state (viewport / assets / rasters / screenshots) — mutable and
    # deployment-local; shared intermediaries must not cache it.
    types.ReadResourceRequest: (_FIVE_SEC_MS, "private"),
}

# ─── SEP-2164: resource-not-found → -32602 (Invalid Params) ──────────────────
RESOURCE_NOT_FOUND_CODE = -32602

# Our resource readers raise corrective ValueErrors whose messages all say
# "not found"; FastMCP's template path re-wraps them (string-only), and its
# unknown-URI path raises "Unknown resource: ...". Both are not-found.
_NOT_FOUND_MARKERS = ("not found", "unknown resource")


def _looks_not_found(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _NOT_FOUND_MARKERS)


def _stamp(result: types.Result, req_type: type) -> None:
    """Add the draft-revision additive fields to a result (in place)."""
    # SEP-2322: ordinary, complete result (we have no input_required flows).
    result.resultType = "complete"
    # SEP-2575 SHOULD: identify the server in each result's _meta.
    meta = dict(result.meta or {})
    meta.setdefault(_SERVER_INFO_META_KEY, _SERVER_INFO)
    result.meta = meta
    # SEP-2549: ttlMs / cacheScope on the cacheable result types.
    policy = _CACHE_POLICY.get(req_type)
    if policy is not None:
        ttl_ms, scope = policy
        result.ttlMs = ttl_ms
        result.cacheScope = scope


def _wrap(req_type: type, orig):
    async def handler(req):
        try:
            server_result = await orig(req)
        except McpError:
            raise  # already carries a deliberate code — pass through
        except Exception as exc:
            if req_type is types.ReadResourceRequest and _looks_not_found(exc):
                raise McpError(types.ErrorData(
                    code=RESOURCE_NOT_FOUND_CODE,
                    message=str(exc),
                )) from exc
            raise
        root = getattr(server_result, "root", None)
        if isinstance(root, types.Result):
            _stamp(root, req_type)
        return server_result

    return handler


def install() -> None:
    """Wrap every registered request handler with the compat shims."""
    handlers = mcp_server._mcp_server.request_handlers
    for req_type in list(handlers):
        handlers[req_type] = _wrap(req_type, handlers[req_type])
    logger.info(
        "MCP 2026 draft-revision compat shims installed "
        "(resultType, ttlMs/cacheScope, serverInfo _meta, -32602 not-found) "
        "on %d request handlers",
        len(handlers),
    )


# Import == install (mirrors mcp_resources' import == registration). main.py
# imports this module LAST, after mcp_tools and mcp_resources, so every
# handler — tools, resources, templates — is registered before wrapping.
install()
