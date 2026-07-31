# ESIP Map Control — MCP Compliance & Auth Grand Plan

**Status:** Active implementation reference (for coding agents and maintainers)
**Created:** 2026-06-22
**Companion to:** `vision/specification.md`, `vision/architecture.md`, `vision/mcp-tool-consolidation.md`,
and the external "ESIP Map Control — MCP Compliance Strategy" report (§-references below cite that report).

> This document is the durable, multi-phase plan for making the ESIP Map Control
> server a real, deployed, spec-compliant MCP server and then authenticating and
> hardening it. It is written so any future task can pick up at the current phase
> without re-deriving the context.

---

## 0. Ground truth (empirically verified 2026-06-22)

These facts were confirmed against the live `[Experimental] Stable` EC2 deployment
(`i-092f6ec2f227d1d07`, `18.116.107.200`, region `us-east-2`) and the codebase:

- The deployed `esip-mapcontrol:ex-latest` container (port 8000, Watchtower-managed)
  is the **FastAPI REST proxy**, *not* an MCP server. Verified: `POST /mcp` → **404**;
  container `CMD` = `uvicorn mapcontrol_server.main:app`; zero `mcp`/`fastmcp` imports
  in the server code prior to this work.
- The map works via **REST (`/api/...`) + WebSocket (`/ws/...`) + served frontend
  (`/map/{id}`) + SQLite**. MCP is **not** in that path. Removing MCP entirely would
  not affect the running map.
- The only pre-existing MCP was a **laptop-only Node wrapper**
  (`~/Documents/Cline/MCP/mapcontrol-mcp/`) used by Claude Desktop / Cline over stdio.
  It was never built into an image, never pushed to GHCR, never deployed.
- Production consumers are **pure machine-to-machine**: `eogpt-server` and
  `eogpt-client` (via the Python `mapcontrol` SDK → REST) plus the browser iframe.
  **No Claude.ai / interactive-OAuth client exists in this deployment.**
- **Security risk today:** security group `sg-09b05996a3efdb463` exposes ports
  22/80/443/5173/8000/8888/8900/8901/8903 to `0.0.0.0/0`; `:8000` is unauthenticated
  with `CORS allow_origins=["*"]`. Anyone with the IP can control any map.
- Internals are clean for reuse: `POST /api/maps/{id}/events` is a thin wrapper over
  `event_service.process_event(map_id, MapEvent)`; all I/O is already Pydantic-typed
  (`MapEvent`, `AssetStyle`, `AssetResponse`, `ViewportResponse`, ...).

### Architecture decision (settles report §3)

Add MCP **in-process** as a `/mcp` router on the existing FastAPI app (official MCP
Python SDK / FastMCP, Streamable HTTP), wrapping the same service functions and the
same Pydantic models. **No new container, no new pipeline, no new state.** This keeps
ESIP one stable, independent unit on the existing GitHub Actions → GHCR → Watchtower
CI/CD. The laptop Node MCP becomes redundant (retire, or keep for local stdio dev).

Rationale: state locality (Resources/subscriptions later need in-process access to the
proxy's state) and a single schema source (kills the documented 13/17/20 tool-count
drift, which exists *because* the Node server hand-maintains a parallel TypeScript
schema). Operational consolidation reinforces it.

---

## Phase 0 — Make MCP real & deployed (in-app `/mcp` router)  ← CURRENT

**Goal:** the deployed container natively speaks MCP; `POST /mcp` stops being 404.
Purely additive — the map, REST, and WebSocket are untouched.

- Add the `mcp` SDK to `server/pyproject.toml` dependencies.
- New module `mapcontrol_server/mcp_tools.py` (NOT named `mcp` — would shadow the SDK):
  a `FastMCP` instance plus the consolidated **11-tool** set from
  `mcp-tool-consolidation.md`, each a thin wrapper over `event_service` /
  `session_service` / `asset_service`:
  `create_map`, `add_geojson`, `add_geotiff`, `zoom`, `list_assets`, `delete_asset`,
  `set_visibility`, `update_style`, `set_basemap`, `get_viewport`, `take_screenshot`.
- Mount Streamable HTTP at `/mcp` in `main.py`; run `mcp.session_manager.run()` inside
  the existing FastAPI `lifespan`.
- Advertise **only** `tools` for now (advertised == implemented invariant).
- Carry `map_id` as an explicit tool argument (stateless-core / §7-ready), not only via
  session binding. Construct `stateless_http=True`.
- **Acceptance:** `initialize` + `tools/list` + a representative `tools/call` round-trip
  succeeds against the running container; the map still renders identically via REST/WS.

**Risk:** low. **Touches:** ESIP repo only.

---

## Phase 1 — Edge hardening (stop the bleeding; no app-auth yet)

**Goal:** remove the wide-open exposure with zero disruption to the working stack.

- Lock the security group: stop exposing `:8000` (and ideally `:8900/8903/5173`) to
  `0.0.0.0/0`; keep service-to-service traffic on the instance/Docker network; restrict
  SSH (22) to known IPs.
- Move EOGPT→ESIP and browser→ESIP access to the minimal public surface (one front
  door). This is the one coordinated step, since EOGPT currently calls the public IP.
- Tighten `CORS` off `*` to known origins.
- Transport hardening on `/mcp` (report §5a) via FastMCP `TransportSecuritySettings`:
  `Origin` allowlist → **HTTP 403** on mismatch (DNS-rebinding protection);
  validate `MCP-Protocol-Version` header with a sane default; confirm `Mcp-Session-Id`
  is CSPRNG + 404-on-unknown; loopback-bind for local mode.
- **Decision (default):** tighten SG + internal networking now; layer Cloudflare Access
  or a reverse proxy later if a public interactive surface appears.

**Risk:** medium (coordinated networking change). **Touches:** AWS SG + EOGPT env URLs + ESIP CORS/transport.

---

## Phase 2 — §5b Authorization on `/mcp` (scoped to the real trust boundary)

**Goal:** ESIP becomes an OAuth 2.1 **Resource Server**; callers present scoped tokens.

- Since prod is M2M, implement **scoped tokens for eogpt-server/-client** first. Defer
  interactive OAuth (dynamic client registration, incremental-scope consent, URL-mode
  elicitation) until an interactive client actually exists.
- RS essentials: `.well-known` Protected Resource Metadata (RFC 9728);
  `WWW-Authenticate` on 401 with `.well-known` fallback; OIDC discovery; a bearer
  validation `TokenVerifier` on `/mcp` (FastMCP supports `token_verifier` / `auth`).
- **Decision (default):** ESIP **self-issued signed scoped tokens** (max independence,
  no external IdP), with the RS metadata shaped so an external AS (Cognito in-account /
  Auth0 / Keycloak) can slot in later.
- Errors via the correct channel: validation/input failures → Tool Execution Errors
  (`isError`), not protocol errors.

**Risk:** medium. **Touches:** ESIP repo + EOGPT (must send the token — allowed; the
EOGPT→ESIP one-way dependency holds).

---

## Phase 3 — Conformance harness in CI (report §8; ESIP's first pipeline test)  ✅ DONE

**Goal:** lock the gains so they don't regress across spec revisions.

> **Implemented 2026-06-25** in `.github/workflows/deploy.yml`: the deploy job now
> **builds the image, loads it, runs the conformance + auth suites INSIDE that exact
> artifact, and only pushes to GHCR if they pass** (a real gate). See the progress
> log entry below for the full design + rationale.

- GitHub Actions job: boot server → assert advertised caps == implemented → enumerate
  `tools/list` + a representative call → malformed GeoJSON returns `isError` (not a
  crash) → version negotiation for `2025-06-18` and `2025-11-25` →
  `401 → PRM → token → 200` on `/mcp`.
- Folds into the existing ESIP deploy workflow.

**Risk:** low. **Touches:** ESIP repo CI.

---

## Phase 4+ — Idiomatic breadth (post-auth; report §5c–e, §6, §7)

Captured for completeness; sequenced after auth.

- **§5c Tools upgrade:** annotations (`readOnlyHint` / `destructiveHint` /
  `idempotentHint` / `openWorldHint`); structured `outputSchema` for read tools;
  **resource links** for screenshots/GeoTIFFs instead of base64 inlining; server-side
  GeoJSON validation surfaced as Tool Execution Errors.
- **§6 Resources reframe (highest leverage):** expose canonical state as Resources with
  subscriptions — `map://{map_id}/viewport|assets|drawn|screenshots/{id}|rasters/{id}`;
  `notifications/resources/updated` on mutation, `list_changed` on add/remove; cursor
  pagination. **CRS invariant: EPSG:4326 only across the MCP boundary** (6933 compute /
  3857 display never leak).
- **§5d Prompts / Completion / Logging:** workflow prompts (frame/compare AOIs,
  before/after swipe); `completion/complete` (optionally backed by Pelias/geocoder);
  bridge existing logs to MCP logging.
- **§5e Utilities:** pagination everywhere; progress for slow ops (GeoTIFF, screenshot);
  cancellation; Tasks (experimental) for long ingest.
- **§7 Forward:** stateless-core-ready identity (already satisfied via explicit
  `map_id`); watch MCP Apps as a possible future bet; adopt new things as opt-in
  extensions.

---

## Phase 5 — 2026 protocol revision readiness (draft spec; execution gated)

**Goal:** stay compliant with the next MCP spec revision (the "July 2026 or
later" protocol upgrade) — the largest breaking change in MCP's history:
stateless MCP (no `initialize`; SEP-2575), sessionless Streamable HTTP
(SEP-2567), mandatory `server/discover`, `resultType` on all results
(SEP-2322), required `Mcp-Method`/`Mcp-Name` headers (SEP-2243), required
`ttlMs`/`cacheScope` on lists/reads (SEP-2549), `subscriptions/listen`
replacing `resources/subscribe`, and the Roots/Sampling/Logging + DCR
deprecations.

**The full SEP-by-SEP inventory, readiness scorecard, trigger conditions, and
execution checklist live in `vision/mcp-2026-protocol-upgrade.md`.** Do not
execute until BOTH triggers fire: (1) the draft revision is published as
Current, and (2) a released `mcp` Python SDK supports it. MCP's feature
lifecycle policy (SEP-2596) guarantees a ≥ 12-month deprecation window, so
there is no day-one breakage.

**Why we're well-positioned:** `stateless_http=True` + explicit `map_id`
(Phase 0) is exactly the sessionless/stateless model the new revision
mandates; advertised == implemented migrates cleanly to `server/discover`;
subscriptions were deferred, so we never shipped the API the draft deletes.

**Risk:** low until triggers fire; then medium (test/CI rework). **Touches:**
ESIP repo only (demo-box toy AS additionally needs DCR→CIMD later).

---

## Invariants (every phase)

- **No mega-tool.** Keep typed, bounded schemas; never collapse to
  `map_command(action, params)`.
- **Schema source of truth = Pydantic models.** Derive MCP tool/resource/prompt schemas
  from them; do not hand-maintain a parallel definition.
- **Advertised capabilities == implemented capabilities.**
- **EPSG:4326 only across the MCP boundary** (6933 compute / 3857 display, neither leaks).
- **Intra-ESIP only.** Never introduce an ESIP→EOGPT dependency.
- **Errors via the right channel.** Input/validation → Tool Execution Errors so the
  model self-corrects; genuine protocol violations → JSON-RPC errors.
- **Dual deployability (local + cloud).** ESIP — both the standalone server
  container *and* the `mapcontrol` SDK — must run locally (offline,
  `docker-compose.local.yml`, port `7777→8000`) and in cloud from ONE codebase;
  behavior differs only by config, with a **safe local default** and the cloud
  env-file opting into stricter values (e.g. `MAPCONTROL_ALLOWED_ORIGINS`,
  `MAPCONTROL_MCP_DNS_REBINDING_PROTECTION`). No cloud-only/local-only forks.
  Binding platform constraint: **EOGPT-Roadmap `decisions/ADR-0001-dual-deployability.md`**.


---

## Decisions taken (with rationale)

1. **Phase 0 host:** in-app FastMCP `/mcp` router on the existing FastAPI proxy
   (matches the deployed reality + the FastMCP intent).
2. **Phase 1 edge:** tighten SG + internal networking first; Cloudflare/reverse-proxy later.
3. **Phase 2 issuer:** ESIP self-issued signed scoped tokens (max independence);
   external AS slot-in later.

---

## Progress log

- **2026-06-22:** Ground truth established; grand plan written.
- **2026-06-22 — Phase 0 IMPLEMENTED (in-app `/mcp` router):**
  - Added `mcp>=1.28.0` to `server/pyproject.toml`.
  - New `mapcontrol_server/mcp_tools.py`: a `FastMCP` instance (`stateless_http=True`)
    with the 11 consolidated tools wrapping `event_service` / `session_service` /
    `asset_service`. Style input uses the `AssetStyle` Pydantic model; tools carry
    explicit `map_id`; GeoJSON is validated and failures raise → Tool Execution
    Errors. Tool annotations (`readOnlyHint`/`destructiveHint`/`idempotentHint`/
    `openWorldHint`) set.
  - `main.py`: import `mcp_server`, run `mcp_server.session_manager.run()` inside the
    existing lifespan, and `app.mount("/", mcp_server.streamable_http_app())` as the
    LAST mount (catch-all; `/mcp` resolves with no redirect, all REST/WS/`/map`
    routes matched first).
  - **Verified end-to-end** (TestClient against the real app, throwaway DB):
    `initialize` (200, protocol 2025-06-18) → `tools/list` returns exactly the 11
    tools → `tools/call create_map` → `tools/call add_geojson` creates an asset →
    REST `GET …/assets` sees it → malformed GeoJSON returns `isError` (not a
    JSON-RPC error) → `/map/{id}` still serves HTML, REST `POST /api/maps` still 201.
    All 8 checks PASS. The committed e2e harness lives at `server/tests/test_mcp.py`.
  - Env knobs added (defaults preserve Phase-0 permissiveness):
    `MAPCONTROL_PUBLIC_URL` (returned map/screenshot URLs),
    `MAPCONTROL_MCP_DNS_REBINDING_PROTECTION` (default off; Phase 1 turns on),
    `MAPCONTROL_MCP_ALLOWED_ORIGINS`, `MAPCONTROL_MCP_ALLOWED_HOSTS`.

- **2026-06-22 — Phase 1 Tier A SHIPPED (dual-safe, in-repo edits):** the
  low-risk, config-gated half of edge hardening, done without touching infra so
  both local and cloud stay green.
  - **Config-driven CORS** in `main.py`: `MAPCONTROL_ALLOWED_ORIGINS`
    (comma-separated) replaces the hardcoded `allow_origins=["*"]`. Default
    (unset / `*`) stays permissive so local dev is untouched; the cloud env-file
    sets the real browser origins to lock the public surface. Also fixed the
    invalid wildcard-origin + `allow_credentials=True` combo (credentials only
    enabled when origins are explicitly enumerated).
  - **Capability over-advertise fixed** (advertised == implemented): wrapped
    `mcp_server._mcp_server.create_initialization_options` to strip the
    unimplemented `prompts` / `resources` / `experimental` capabilities; only
    `tools` is now advertised. Removed/narrowed when those land for real.
  - **`\s` SyntaxWarning fixed** in `main.py` (`/^MapTiler\\s+/`).
  - **e2e test extended** (`tests/test_mcp.py`): asserts `initialize` advertises
    `tools` and NOT prompts/resources/experimental. Full suite passes; import is
    clean under `-W error::SyntaxWarning`.
  - **Dual-deployability** recorded as a platform standard (EOGPT-Roadmap repo).
  - **Still pending (Phase 1 Tier B, coordinated infra, NOT in this change):**
    lock security group `sg-09b05996a3efdb463` off `0.0.0.0/0` + re-point
    EOGPT→ESIP traffic to the internal surface (the three-legs split:
    M2M internalized, browser behind one hardened front door, postMessage
    origin-allowlisted); flip on the MCP transport env knobs on the EC2
    env-file (`MAPCONTROL_MCP_DNS_REBINDING_PROTECTION=1` + allowed
    origins/hosts).

- **2026-06-23 — Phase 1 Tier B Step 2 SHIPPED (ESIP prefix-aware, dual-safe):**
  the one real code change needed before the single-origin reverse proxy can
  front ESIP under a sub-path. Added **`MAPCONTROL_ROOT_PATH`** (config.py +
  env override; normalized to leading-slash/no-trailing-slash). Default `""` →
  served at root, so local dev / docker-compose.local.yml / direct-port and
  internal M2M are byte-for-byte unchanged (ADR-0001). When set (e.g.
  `/service/map`) it is handed to FastAPI as the ASGI `root_path`.
  - **Key mechanism (empirically verified):** Starlette auto-prefixes
    `request.base_url` with `root_path`, so EVERY `BASE_URL`-derived URL in the
    served map (REST `/api/...` fetches, `/api/files/...` GeoTIFF/screenshot
    overlays, screenshot `full_url`, `/map/{id}` links) resolves through the
    proxy with no per-URL edits. Routing still matches on the UN-prefixed path,
    so prefixed (browser-via-proxy) and un-prefixed (internal M2M + in-container
    Playwright) requests BOTH route — the dual-deployability requirement.
  - **Manual prefixing (the only URLs that bypass base_url):** the WebSocket
    URLs (built client-side from `window.location.host`, via a new `ROOT_PATH`
    JS const) and the server-rendered root-absolute `/static/...` asset tags.
  - **Tests:** `server/tests/test_mcp.py` adds `test_served_map_no_root_path`
    (root → un-prefixed WS/static, `BASE_URL` bare) and
    `test_served_map_with_root_path` (`/service/map` → prefixed WS/static +
    auto-prefixed `BASE_URL`). Both drive `serve_map` directly with a synthetic
    scope (the MCP Streamable-HTTP session manager's `run()` is a once-per-
    process singleton, so only the original `test_mcp_phase0` may enter lifespan).
    Full suite: 3 passed.
  - **Still pending (Steps 3–5, coordinated infra):** nginx+certbot as code,
    re-point EC2 env-files (set `MAPCONTROL_ROOT_PATH=/service/map` +
    `MAPCONTROL_PUBLIC_URL` https + CORS/MCP allowlists +
    `MAPCONTROL_MCP_DNS_REBINDING_PROTECTION=1`), verify full UI over HTTPS,
    then lock the SG. See `EOGPT-Roadmap/runbooks/phase1-edge-cutover.md`.

- **2026-06-25 — Phase 3 DONE (CI conformance + auth gate):** ESIP's first
  pipeline test. Turned the build-and-push workflow (`.github/workflows/deploy.yml`)
  into a real **gate**: build the image → `load` it into the runner's Docker
  daemon (single-platform `linux/amd64`) → run the conformance + auth suites
  **inside that exact image** → push to GHCR **only if both pass** (`push: false`
  on the build step; a separate `docker push` step gated on
  `github.event_name == 'push'` AND the prior steps succeeding).
  - **Why inside the image (not a separate pip job):** the gate then validates
    the precise deployable artifact — same layers/digest, real runtime env (GDAL,
    the `mcp` SDK, `pyjwt`). The standalone test runners
    (`python tests/test_mcp.py`, `python tests/test_mcp_auth.py`) use only runtime
    deps (the httpx-backed Starlette `TestClient`), so **no `dev` extras need to
    ship in the image**. Each suite runs in its **own `docker run`** so the MCP
    Streamable-HTTP session manager's once-per-process `run()` never collides.
  - **What it asserts** (the two committed suites): MCP `initialize` over
    Streamable HTTP (protocol `2025-06-18`); **advertised caps == implemented**
    (tools only — no phantom prompts/resources/experimental); the **11** tools;
    malformed GeoJSON → **Tool Execution Error (`isError`)**, not a protocol
    crash; reverse-proxy **root-path** prefixing both ways; and **§5b auth** —
    `401 → PRM doc → token → 200`, `403` on missing scope, expired/forged/garbage
    → 401, audience + issuer pinned, and enabled-without-secret fails loudly.
  - **PR-safe / ADR-0001:** PRs build + run the gate but never push (a red gate is
    a failed required check that blocks the merge). Auth tests set their own env,
    so the image default stays auth-OFF; nothing about local/compose behavior
    changes.
  - **Verified locally before commit:** `docker build` then both runners inside
    the image → `RESULT: ALL PASSED`, `EXIT=0` for each.
  - **Hang-on-exit fix (caught on the first CI run):** the standalone runners
    (`python tests/test_mcp.py` / `_auth.py`) printed `RESULT: ALL PASSED` but
    then **blocked interpreter shutdown** — the MCP session manager / TestClient
    anyio portal / aiosqlite leave non-daemon threads alive, so the process never
    exited and the CI `docker run` hung indefinitely (first PR run sat ~10 min on
    the gate before being cancelled). Fix: each runner's `__main__` block now
    `os._exit(0)` immediately after success (assertions already validated;
    pytest does NOT hit this path). Belt-and-suspenders: both gate steps are
    wrapped in `timeout 120` so any future regression fails fast instead of
    burning the runner budget. Re-validated inside the image: both exit `0`.
  - **Known gap vs. the Phase-3 checklist:** ~~version negotiation is exercised for
    `2025-06-18` only; the `2025-11-25` revision is **not yet** asserted~~ —
    RESOLVED 2026-07-16 (see the Phase 5 progress entry): `test_mcp.py` now
    asserts `2025-11-25` negotiation and unknown-version fallback.

- **2026-06-25 — Demo OAuth Authorization Server (toy AS) for a standalone
  Claude-web demo box (side quest, additive, default-OFF):** §5b makes ESIP an
  OAuth **Resource Server** (it *validates* a bearer token someone else minted).
  That is enough for the pure-M2M production topology but NOT for a generic MCP
  client like **Claude.ai (web) Custom Connectors**, which runs the full OAuth 2.1
  discovery + Dynamic Client Registration + authorization-code-with-PKCE dance and
  has nowhere to paste a static token. To demo ESIP's real map tools from Claude
  web, we added the missing **Authorization-Server** half as an in-process,
  in-memory, single-tenant **toy**.
  - **New `server/mapcontrol_server/auth_server.py`:** `DemoAuthServerProvider`
    implements the FastMCP `OAuthAuthorizationServerProvider` Protocol (mcp
    `1.28.0`) — in-memory client/code/token stores, opaque random tokens (no JWT),
    PKCE handled by the SDK token handler. `build_auth_server()` returns
    `(provider, AuthSettings)` with `ClientRegistrationOptions(enabled=True)` so
    DCR is live. Gated by **`MAPCONTROL_MCP_AS=1`** (default OFF). Optional static
    consent screen via `MAPCONTROL_MCP_AS_CONSENT=1` (+ `..._PASSWORD`); default is
    auto-approve.
  - **`mcp_tools.py` wiring:** the toy AS and §5b are **mutually exclusive**
    (FastMCP forbids passing both `auth_server_provider` and `token_verifier`).
    `build_auth_server()` is tried first; if ON it supplies the provider and §5b's
    `token_verifier` is forced to `None`; if OFF we fall back to §5b's
    `build_auth()` exactly as before. Neither on → both `None` → open `/mcp`
    (Phase 0/1). All three states are byte-for-byte unchanged for local/compose.
  - **Routes:** because `main.py` mounts the MCP app at `/` (catch-all, last),
    FastMCP's `streamable_http_app()` lands `/authorize`, `/token`, `/register`,
    and `/.well-known/oauth-authorization-server` at the **origin root** — exactly
    where Claude discovers them. The §5b PRM (`/.well-known/oauth-protected-
    resource`) is served by the same app. A small gated `/as/consent` GET/POST pair
    in `main.py` drives the same provider instance for the optional consent screen.
  - **Test + gate:** new `server/tests/test_mcp_as.py` walks the whole flow
    against the built image (AS metadata → DCR → `/authorize` PKCE → `/token` →
    authenticated `/mcp` 200, plus 401-without, one-time-code-reuse rejected, PKCE
    mismatch rejected, AS-OFF-by-default). Added as a third CI gate step
    (`MCP authorization-server gate (demo OAuth AS)`) in `deploy.yml`. All three
    suites (`test_mcp`, `test_mcp_auth`, `test_mcp_as`) pass locally.
  - **⚠️ SCOPE — production ESIP must NOT carry its own AS:** this toy AS is *only*
    for the standalone `esip-mcp.earthanalyticslab.com` demo box (own EC2,
    `MAPCONTROL_MCP_AS=1`). The **EOGPT-coupled** ESIP service stays a Resource
    Server (§5b) and will defer to **EOGPT's future platform authentication** (a
    real AS / IdP — the EOGPT login issuing scoped tokens, or Cognito/Auth0). Do
    not enable `MAPCONTROL_MCP_AS` on the experimental/production ESIP. When EOGPT
    auth lands, the standalone demo can switch to it by swapping the verifier +
    `issuer_url` (the §5b metadata is already shaped for an external AS).

- **2026-07-13 — §6 Resources + §5c resource links SHIPPED (representation-only):**
  the highest-leverage Phase 4+ item. The MCP boundary now exposes the server's
  canonical state as **Resources** with a stable `map://` URI scheme, and every
  mutating tool returns **ResourceLinks** to what it created. Purely additive on
  the representation layer — zero changes to services/models/REST/WS/SDK
  behavior (one additive read-only helper: `session_service.list_maps()`).
  - **New `server/mapcontrol_server/mcp_resources.py`:** resource templates on
    the SAME `mcp_server` instance (thin readers over `session_service` /
    `asset_service` / the file store), each in its most natural MIME type:
    `map://maps`, `map://{map_id}`, `.../viewport` (JSON); `.../assets` (JSON
    index carrying each asset's canonical `resource_uri`);
    `.../assets/{asset_id}` (**`application/geo+json`** geometry),
    `.../assets/{asset_id}/style` (JSON); `.../drawn` (geo+json
    FeatureCollection of user-drawn shapes, properties tagged with asset
    identity); `.../rasters/{asset_id}` and `.../screenshots/{screenshot_id}`
    (**binary `image/png`** blobs read from `file_dir` with path-escape
    guards). Unknown map/asset → corrective `ValueError` → resource read error
    (protocol survives). EPSG:4326-only and explicit-`map_id` invariants hold.
  - **`mcp_tools.py` upgrades:** the `map://` URI helpers live here (single
    source; `mcp_resources` imports them one-way). `create_map`, `add_geojson`,
    `add_geotiff` now return `CallToolResult` with ResourceLink content blocks
    + the SAME legacy JSON as both text and `structuredContent` (nothing that
    parses today's output breaks). **`take_screenshot` returns the PNG inline**
    as base64 `ImageContent` — Claude web renders it in-chat instead of a dead
    link — plus the ResourceLink and the legacy `url`/`full_url` fields, with a
    1.5 MB guardrail (larger captures: link-only + note, protecting context).
    `add_geotiff` is deliberately link-only (rasters can be large; read
    `map://.../rasters/{id}` on demand). `list_assets` rows carry
    `resource_uri` (+ `raster_uri` for geotiffs).
  - **Advertised == implemented, updated:** the init-options wrapper (renamed
    `_create_initialization_options_implemented_only`) now KEEPS `resources`
    (subscribe/listChanged remain false — subscriptions/notifications still
    deferred) and still strips `prompts`/`experimental`.
  - **Wiring:** `main.py` imports `mcp_resources` after `mcp_tools` (import ==
    registration; no new mounts, no new env knobs — ADR-0001 holds: local and
    cloud behavior identical).
  - **Tests (`tests/test_mcp.py`, rides the existing CI gate unchanged):**
    initialize advertises `tools` + `resources` (no subscribe claim, no
    prompts/experimental); `resources/templates/list` == the 8 templates;
    `resources/list` contains `map://maps`; `resources/read` round-trips JSON
    (maps + assets index), `application/geo+json` (asset geometry), and binary
    `image/png` (seeded screenshot → base64 blob); mutating tools emit
    `resource_link` blocks; unknown map/asset reads error without crashing.
    All three suites (`test_mcp`, `test_mcp_auth`, `test_mcp_as`) pass via the
    standalone runners. (Reminder: run them as `python tests/test_mcp.py` —
    pytest hangs at teardown per the known 2026-06-25 thread-shutdown quirk.)
  - **Still deferred (next sessions):** resource **subscriptions** +
    `notifications/resources/updated` / `list_changed` (grow the capability
    flags in lockstep when they land); cursor pagination; **MCP Apps** (`ui://`
    HTML template embedding the live `/map/{map_id}` iframe inline in Claude —
    the resource taxonomy shipped here is exactly what that template will link
    into).

### Phase 0 known follow-ups (carry into Phase 1)



- **Capability over-advertise (advertised != implemented):** ~~FastMCP advertises
  `resources`/`prompts`/`experimental` with only `tools` implemented~~ — RESOLVED in
  stages: 2026-06-22 suppressed all three; 2026-07-13 `resources` became real
  (§6 Resources layer) and is advertised again; `prompts`/`experimental` remain
  suppressed until they land (§5d).
- **Pre-existing `SyntaxWarning: invalid escape sequence '\s'`** in `main.py` (the
  `/^MapTiler\s+/` regex inside the inline HTML f-string) — unrelated to MCP, harmless,
  worth a one-line fix (`\\s`) opportunistically.
- **Transport hardening** (Origin→403, `MCP-Protocol-Version`, session-id entropy) is
  deliberately deferred to Phase 1; the env knobs above are the on-switch.


- **2026-07-18 — MCP APPS SHIPPED & WORKING IN CLAUDE WEB (Option B; v1.1):**
  the live ESIP map renders **inline in the Claude web chat** as a native
  MapLibre map in the app view, following tool calls (zoom, add_geojson,
  set_basemap, …) in real time over the WebSocket. User-confirmed 2026-07-18.
  - **Root causes found across 8 instrumented probes** (full history +
    Claude-web CSP channel matrix: `vision/mcp-apps-appification.md` §6):
    (1) large inline template bundles (342 KB vite/SDK) break sandbox
    document delivery outright; (2) the **app initiates** `ui/initialize`
    (protocol `2026-01-26`) — waiting for the host = 0-px invisible frame
    (no `size-changed`); (3) Claude web **ignores `frameDomains`** (stock
    `frame-src`), killing Option A there; (4) `connectDomains`/
    `resourceDomains` ARE honored verbatim — but CSP scheme matching is
    exact: `https://host` does not authorize `wss://host`.
  - **Shipped (v1 `0dc1ec5` + v1.1 `8c3c7cd` on `experimental`, deployed to
    the demo box):** `mcp_apps._ui_resource_meta()` declares connect =
    {origin, wss-origin, config-derived tile origins}, resource = {origin,
    tile origins}, frame = {origin} (Option-A signal for honoring hosts);
    template rewritten hand-rolled (~300 lines, no build step): handshake →
    tool-result → self-hosted MapLibre GL 5.23.0 from `/static/vendor/` →
    REST session → WS `session_restore`/`session_snapshot` → native render
    + debounced live re-render; fallback to immediate basemap paint + 3 s
    REST polling when WS is blocked.
  - **Docs:** appification vision doc §6 resolved (answers/matrix/history);
    NEW **`docs/mcp-apps-field-guide.md`** — domain-agnostic checklist,
    landmine list, debugging playbook + minimal diagnostic template for any
    team building an MCP App.
  - All suites green throughout (test_mcp / auth / AS / portal); CI image
    gate green on every push.

- **2026-07-16 (night) — MCP APPS OPTION A IMPLEMENTED (branch
  `feature/esip/mcp-apps`, all three suites green):** the server half of the
  ext-apps extension is live per `vision/mcp-apps-appification.md` §5, on the
  stable 1.x SDK.
  - **New `mapcontrol_server/mcp_apps.py`** (import == install; main.py order:
    after mcp_tools/mcp_resources, BEFORE mcp_compat so every result still
    gets the 2026 stamps): registers the **`ui://esip/map.html`** template
    resource (mimeType exactly `text/html;profile=mcp-app`) + a new
    **`show_map(map_id)`** tool (12 tools total); wraps `tools/list` to stamp
    **`_meta.ui.resourceUri`** (nested form) onto `create_map`/`show_map`;
    wraps `resources/read` to stamp **`_meta.ui`** (csp.frameDomains =
    `MAPCONTROL_PUBLIC_URL` origin + prefersBorder) onto the template via
    `model_copy(update={"_meta": ...})` (empirically the reliable 1.x wire
    path); wraps `resources/list` to hide `ui://` (spec MAY; keeps map://
    clean); extends the init-options wrapper to advertise
    **`capabilities.extensions["io.modelcontextprotocol/ui"]`**
    (ServerCapabilities is extra=allow — probe-verified serialization).
  - **New `static/mcp-app.html`** (Option A thin wrapper): raw postMessage
    ext-apps dialect (`ui/initialize` → `initialized` →
    `ui/notifications/tool-result` → renders the tool result's `url` in a
    nested iframe; `size-changed` for host sizing). No SDK dependency.
  - **Tests (`tests/test_mcp.py`, rides the CI gate unchanged):** extension
    advertised == implemented; 12-tool set; `_meta.ui.resourceUri` on exactly
    the UI tools (deprecated flat key asserted ABSENT; non-UI tools clean);
    template read-back (mimeType + frameDomains + prefersBorder + HTML);
    `ui://` absent from resources/list; `show_map` round-trip + unknown-map
    `isError`. All three standalone suites PASS (`test_mcp`, `test_mcp_auth`,
    `test_mcp_as`). ADR-0001 holds — no new env knobs.
  - **Remaining (from the vision doc):** the Claude-web `frameDomains` probe
    (Option A gate) on the demo box; if it fails, swap the template for the
    Option-B native MapLibre build (server plumbing above is shared).

- **2026-07-16 (evening) — MCP APPS APPIFICATION RECON DONE (spec-verified):**
  the "MCP Apps as a possible future bet" watch item (§7 / Phase 4+) is now a
  concrete, spec-grounded plan: **`vision/mcp-apps-appification.md`**. Key
  facts verified against the published ext-apps spec
  (`specification/2026-01-26/apps.mdx`), the official extension docs, and the
  client support matrix: extension id `io.modelcontextprotocol/ui` (client
  advertises in `capabilities.extensions`; graceful text-fallback mandatory —
  our tools already comply); `ui://` template resource with mimeType exactly
  `text/html;profile=mcp-app` (MAY be omitted from `resources/list`); tools
  reference it via nested **`_meta.ui.resourceUri`** (flat form deprecated);
  server-declared host-enforced CSP incl. **`frameDomains`** (nested iframes —
  makes the "embed the live `/map/{id}` page" thin option spec-legal but
  host-discretionary) and `visibility: ["app"]` app-only tools (spec-native
  replacement for the moveend phone-home); iframe⇄host is JSON-RPC over
  postMessage **proxied over the host's already-authenticated MCP session**
  (the session-handling win: auth/connection lifecycle becomes the host's
  job; ESIP `user_sessions` remain domain-internal; explicit `map_id` is
  exactly the state model Apps assume). Hosts shipping Apps today: Claude
  web/desktop, ChatGPT, Cursor, VS Code Copilot, M365 Copilot, Goose,
  Postman + more. Official `map-server` (CesiumJS) example exists; local
  verification possible without Claude via ext-apps `basic-host`. Decision
  pending: Option A (nested live-map iframe, gated on a Claude-web
  `frameDomains` probe) vs Option B (native single-file MapLibre template
  over the bridge — the strategic destination). Server-side plumbing is
  shared either way and 1.x-SDK-compatible (mcp_compat-style `_meta`
  stamping; no beta pins). **Plan finalized same evening:** §1 now carries the
  per-option traffic-light session table (ESIP `user_sessions` work unchanged
  under A; re-plumb as explicit `user_session_id` tool args on app-only tools
  under B — the SEP-2567-conformant shape) and new §5b analyzes the Phase-5 /
  2026-revision interaction: **no conflict** (extensions track survives;
  the app bridge is its own dialect; sessions-as-handles is the new spec's
  own model), two small touchpoints both already on the Phase-5 checklist
  (advertisement moves to `server/discover`; `_meta` stamping re-ports to
  v2's native `extensions=`), and `subscriptions/listen` actively upgrades
  Option B. Sequencing: build on 1.x now; the Phase-5 pin-bump PR carries
  the apps advertisement over.

- **2026-07-16 (evening) — v2.0.0b2 MIGRATION SPIKE RUN (findings recorded):**
  built a throwaway `MCPServer` on `mcp==2.0.0b2` in a scratch venv and drove
  it over Streamable HTTP. Key findings (full detail:
  `vision/mcp-2026-protocol-upgrade.md` §7; committed recon script:
  `server/tests/spike_mcp_v2.py`, spike_-prefixed so pytest/CI never collect
  it): **stateless `tools/call` without `initialize` WORKS**; **legacy
  `initialize` still round-trips** (dual-revision confirmed — but v2
  re-advertises prompts/resources/experimental, so the advertised==implemented
  wrapper must be re-ported); **`server/discover` is NOT in b2** (-32601 —
  server-side stateless half still landing; re-probe each beta);
  **DNS-rebinding protection is ON by default in v2** (421) — opposite of 1.x,
  our env-knob semantics need re-mapping (ADR-0001); `FastMCP`→`MCPServer`,
  `stateless_http`/`transport_security` moved to `streamable_http_app()`,
  `mcp.types`/`mcp.shared.version` gone, result models natively carry
  `result_type`/`ttl_ms`/`cache_scope` + constructor `cache_hints=` replaces
  our `mcp_compat.py` stamping on migration. Conclusion: full port PR is
  premature until `server/discover` ships (b3/rc, expected by 2026-07-28);
  the spike script is the tripwire to re-run per release.

- **2026-07-16 (evening) — TRIGGER STATUS CORRECTION (primary-source verified):**
  the earlier "no release date / no SDK speaks it" ground truth is now stale.
  **`mcp==2.0.0b2` is on PyPI** (2026-07-14, opt-in pre-release; stable
  `pip install mcp` still resolves 1.x) and implements the draft revision incl.
  `subscriptions/listen`; its release notes state **"Stable v2 is still
  targeted for 2026-07-28 alongside the spec release."** TypeScript SDK is at
  `2.0.0-beta.4`. So both §5 triggers fire ~2026-07-28. Consequences: watch
  cadence weekly; next action is a **v2-beta migration spike branch** (pin
  `mcp==2.0.0b2`, port init-options wrapper → `server/discover`, migrate the
  test harness `httpx`→`httpx2`, keep legacy 1.x dual-revision path green) so
  the day-one PR is a pin bump. Do NOT merge/deploy the beta. Details:
  `vision/mcp-2026-protocol-upgrade.md` §1 corrections + §5 trigger status.

- **2026-07-16 (later) — Phase 5 forward-compatible half SHIPPED (draft-revision
  pre-adoption):** the additive-on-the-wire subset of the 2026 draft revision is
  implemented NOW (old clients ignore unknown result fields — the MCP schema is
  tolerant by design), leaving only the SDK-gated breaking half for the §5
  triggers. New **`mapcontrol_server/mcp_compat.py`** (import == install, LAST
  in `main.py`; wraps the low-level `request_handlers` — `initialize` lives in
  `ServerSession`, so the legacy handshake / Claude-web criterion is untouched
  by construction): every result stamps **`resultType: "complete"`**
  (SEP-2322), **`ttlMs`/`cacheScope`** on cacheable results (SEP-2549 —
  tools/templates public/1h, resources/list public/5min, resources/read
  private/5s), **serverInfo in `_meta`** (SEP-2575 SHOULD), and
  resource-not-found reads → **`-32602`** (SEP-2164; was the SDK's generic
  code 0). Toy AS adds **`iss`** to authorization responses (SEP-2468 /
  RFC 9207). Test harness now sends **`Mcp-Method`/`Mcp-Name`** headers
  (SEP-2243) and asserts all of the above + deterministic tools/list order;
  `test_mcp_as.py` asserts `iss`. All three standalone suites PASS; rides the
  existing CI gate; ADR-0001 holds (no env knobs). `mcp_compat.py` is deleted
  when the Phase-5 SDK bump provides these natively. Details:
  `vision/mcp-2026-protocol-upgrade.md` §6b.

- **2026-07-16 — Phase 5 OPENED (2026 protocol revision readiness; SEP inventory):**
  fetched and inventoried all 41 Final SEPs from modelcontextprotocol.io/seps,
  the **draft changelog for the next spec revision**, the versioning policy, and
  the MCP roadmap. Findings: the "July 2026" protocol upgrade = the draft
  revision built from the March–April 2026 Final SEP batch (2567 sessionless,
  2575 stateless / no-`initialize` / `server/discover`, 2322 MRTR/`resultType`,
  2243 required headers, 2549 required `ttlMs`/`cacheScope`, 2577 + DCR
  deprecations). No release date announced; current spec stays `2025-11-25`;
  no released Python SDK speaks the draft → **execution gated on upstream
  triggers**, not started. Wrote the durable inventory + execution checklist:
  **`vision/mcp-2026-protocol-upgrade.md`** (impact table for every SEP against
  our surface, readiness scorecard, trigger conditions). Also **closed the
  flagged Phase-3 known gap** in `tests/test_mcp.py`: version negotiation now
  asserts `2025-11-25` is echoed verbatim AND an unknown/future version
  (`2099-01-01`) falls back to a server-supported version — rides the existing
  CI gate unchanged. Re-anchored deferred work: resource subscriptions must
  target the new `subscriptions/listen` (not `resources/subscribe`), long
  GeoTIFF ingest targets the `io.modelcontextprotocol/tasks` extension.

- **2026-07-15 — Screenshot globe-bug FIXED (camera persistence, layered):**
  `take_screenshot` used to render the default world view ("the globe") or a
  blurry mid-load frame whenever the view had been set via the `zoom` tool.
  Root causes: (1) `zoom_*` events were broadcast-only — live viewers moved but
  nothing was persisted; (2) viewports are stored per-session
  (`user_sessions.viewport`, phoned home by browsers on `moveend`) and the
  headless screenshot page always starts a *fresh* session, so it could never
  see anyone else's camera; (3) the served page always booted at the config
  default and only moved after the WS `session_restore` arrived — after
  Playwright's `networkidle`, so the capture raced a second round of tile
  loads. Fix (all four layers, behavior additive):
  - **`event_service`:** `zoom_to_point` / `zoom_to_bbox` / `zoom_to_assets`
    now materialize the requested view into `maps.config` (same pattern as
    theme/basemap) via new `session_service.set_map_viewport()`. `zoom_out`
    stays ephemeral (relative).
  - **`session_service`:** snapshot/effective-viewport fallback chain — own
    session viewport → shared map view (map-level zoom persistence vs the
    latest-active session's phone-home, **newest wins** by timestamp) → assets
    bbox → config default. New read-only `get_effective_viewport()`.
  - **`serve_map`:** resolves the effective viewport SERVER-SIDE and renders
    it into `DEFAULTS` (`center`/`zoom` or `bounds` + `fitBoundsOptions`), so
    the very first tiles a fresh/headless viewer requests are the right ones.
  - **`screenshot_service`:** deterministic capture wait — polls
    `window.__esipInternals.map` for `loaded() && areTilesLoaded()` (15s
    budget + 300ms paint settle) instead of a blind 2s sleep; non-map pages
    (CI smoke test's `data:` URL) fall back to the fixed wait, so the
    screenshot smoke gate is unchanged.
  - **Tests:** `test_mcp.py` now asserts zoom → `get_viewport` (no session)
    returns the persisted camera, and the served page carries the injected
    initial center/zoom/bounds. All standalone suites pass.
