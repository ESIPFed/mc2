# ESIP Map Control — MCP 2026 Protocol Revision Readiness (SEP Inventory)

**Status:** Inventory + readiness plan (execution gated on upstream triggers, see §5)
**Created:** 2026-07-16
**Companion to:** `vision/mcp-compliance-roadmap.md` (the grand plan; this doc is
referenced from its Phase 5 section and progress log).

> **What this is.** The word going around is that "MCP is upgrading their
> protocols in July 2026 or later." This document decodes that: it inventories
> every MCP Specification Enhancement Proposal (SEP) from
> https://modelcontextprotocol.io/seps (41 Final SEPs as of 2026-07-16, all
> reviewed), maps the published **draft changelog for the next spec revision**
> against ESIP Map Control's `/mcp` implementation, and defines exactly what we
> change — and *when* — to stay compliant.

---

## 1. Ground truth (fetched + verified 2026-07-16)

- **Current protocol version: `2025-11-25`** (per
  https://modelcontextprotocol.io/docs/learn/versioning). Our deployed server
  negotiates `2025-06-18` in tests and runs `mcp` SDK ≥ 1.28.0, whose
  `LATEST_PROTOCOL_VERSION = "2025-11-25"` and
  `SUPPORTED_PROTOCOL_VERSIONS = ["2024-11-05", "2025-03-26", "2025-06-18",
  "2025-11-25"]` (verified in the installed SDK source).
- **There is a published DRAFT changelog for the next revision**
  (https://modelcontextprotocol.io/specification/draft/changelog). The recently
  finalized SEP batch (SEP-2567, 2575, 2577, 2596, 2663 — all Final,
  March–April 2026) **is** that revision. It is the largest breaking change in
  MCP's history.
- ~~No official release date is announced~~ — **CORRECTED 2026-07-16 (evening),
  primary-source verified:** the python-sdk **v2.0.0b2 release notes state
  "Stable v2 is still targeted for 2026-07-28 alongside the spec release."**
  The "July 2026" protocol upgrade is real and dated. Versioning policy:
  the version string bumps **only** on backwards-incompatible changes.
- **Deprecation safety net:** SEP-2596 (feature lifecycle) guarantees
  deprecated features remain in the spec **≥ 12 months** (≥ 90 days under the
  expedited exception) before removal. Nothing breaks on day one.
- ~~We cannot ship the new revision yet — no released Python SDK speaks it~~ —
  **CORRECTED 2026-07-16 (evening):** `mcp==2.0.0b2` **is on PyPI**
  (published 2026-07-14; opt-in pre-release — `pip install mcp` still resolves
  to stable 1.x). It implements the draft revision including client-side
  `subscriptions/listen` (SEP-2575). The TypeScript SDK (Claude web's stack)
  is at `2.0.0-beta.4` (2026-07-13). **Migration wrinkle:** v2 replaces
  `httpx`/`httpx-sse` with `httpx2` (>=2.5.0) — our Starlette TestClient
  harness already prints the matching deprecation warning. Production still
  should not pin a beta of a Draft spec (both may shift before 2026-07-28),
  but a **migration spike branch is now the right move** — see §5 status.

Sources snapshotted during this inventory: SEP index (`/seps`), all 41 Final
SEP markdown files (from the `modelcontextprotocol/modelcontextprotocol` repo's
`seps/` directory), the draft changelog, the versioning policy, and the MCP
roadmap (`/development/roadmap`, last updated 2026-03-05).

---

## 2. The draft revision, decoded (what actually changes)

From the draft changelog, in impact order for ESIP:

1. **Stateless MCP (SEP-2575).** The `initialize` / `notifications/initialized`
   handshake is **removed**. Every request carries protocol version + client
   capabilities in `_meta` (`io.modelcontextprotocol/protocolVersion`,
   `io.modelcontextprotocol/clientCapabilities`). Servers identify themselves in
   each result's `_meta` (`io.modelcontextprotocol/serverInfo`). Version
   mismatch → `UnsupportedProtocolVersionError` (renumbered `-32022`).
2. **Mandatory `server/discover` RPC (SEP-2575).** Servers **MUST** implement
   it: advertises supported protocol versions, capabilities, identity. This is
   the new home of "advertised == implemented".
3. **Sessionless MCP (SEP-2567).** `Mcp-Session-Id` header removed from
   Streamable HTTP. List endpoints must not vary per-connection. Cross-call
   state = explicit server-minted handles passed as ordinary tool arguments.
4. **`subscriptions/listen` (SEP-2575).** Replaces the HTTP GET endpoint and
   `resources/subscribe`/`unsubscribe` with ONE long-lived POST-response stream;
   clients opt into `toolsListChanged` / `resourcesListChanged` /
   `resourceSubscriptions` etc.; notifications tagged with
   `io.modelcontextprotocol/subscriptionId`.
5. **`resultType` required on all results (SEP-2322 / MRTR).** `"complete"` for
   ordinary results; `"input_required"` for multi-round-trip interim results.
   MRTR replaces server-initiated requests (`sampling/createMessage`,
   `elicitation/create`, `roots/list`).
6. **Removed:** `ping`, `logging/setLevel`, `notifications/roots/list_changed`,
   SSE resumability (`Last-Event-ID` / event IDs). Log level per-request via
   `_meta` `io.modelcontextprotocol/logLevel`.
7. **Required HTTP headers (SEP-2243):** `Mcp-Method`, `Mcp-Name` on Streamable
   HTTP POSTs; custom headers via `x-mcp-header` tool parameters.
8. **Cacheability (SEP-2549):** `ttlMs` + `cacheScope` (`"public"`/`"private"`)
   **required** on `tools/list`, `prompts/list`, `resources/list`,
   `resources/read`, `resources/templates/list` (new `CacheableResult`).
9. **Tasks → extension (SEP-2663):** `io.modelcontextprotocol/tasks`; polling
   `tasks/get` (+ `tasks/update`), no more blocking `tasks/result`.
10. **Deprecated (12-month clock starts at release):** Roots, Sampling, Logging
    (SEP-2577); HTTP+SSE transport reclassified Deprecated (SEP-2596); OAuth
    2.0 **Dynamic Client Registration (RFC 7591) deprecated** in favor of
    **Client ID Metadata Documents** (still available for back-compat).
11. **Error codes:** resource-not-found `-32002` → `-32602` (SEP-2164); MCP
    reserves `-32020..-32099`; `-32000..-32019` stays implementation-defined.
12. **Schema:** `inputSchema`/`outputSchema` allow full JSON Schema 2020-12
    keywords; `structuredContent` any JSON value (SEP-2106); deterministic
    `tools/list` ordering recommended (prompt-cache friendliness).

---

## 3. SEP-by-SEP impact inventory for ESIP Map Control

Legend: 🔴 breaking / must act · 🟡 moderate / should act · 🟢 no action or
already compliant. "ESIP surface" = `mcp_tools.py`, `mcp_resources.py`,
`main.py` mounting, `auth.py` (§5b RS), `auth_server.py` (demo-box toy AS),
`tests/test_mcp*.py`, CI gate in `.github/workflows/deploy.yml`.

### 🔴 High impact (the core of the upgrade)

| SEP | What changes | ESIP impact & remediation |
|---|---|---|
| **SEP-2575** Make MCP Stateless | `initialize` handshake removed; `_meta`-carried version/caps; mandatory `server/discover`; `ping`/`logging/setLevel` removed; SSE resumability removed | Everything that touches `initialize` must be reworked **when the SDK ships support**: the e2e suites drive `initialize` directly; the "advertised == implemented" wrapper wraps `create_initialization_options` (its logic migrates to `server/discover`); CI gate asserts the handshake. We use neither `ping` nor `logging/setLevel` nor resumability → those removals are free. |
| **SEP-2567** Sessionless MCP | `Mcp-Session-Id` gone; state via explicit handles as tool args | **Already positioned:** `stateless_http=True` + explicit `map_id` on every tool was the deliberate Phase-0 design ("stateless-core / §7-ready"). `map_id` *is* our server-minted handle. Remaining: tests pass `Mcp-Session-Id` opportunistically — drop when upgrading; confirm list results don't vary per-connection (they don't — single global tool/template set). |
| **SEP-2322** MRTR + `resultType` | All results carry `resultType`; server-initiated requests replaced by input_required round-trips | SDK-level serialization change; our `CallToolResult` returns inherit it on SDK bump. We never issue server-initiated sampling/elicitation/roots requests → no MRTR flows to build. Tests must tolerate/assert the new field. |
| **SEP-2243** HTTP header standardization | `Mcp-Method` + `Mcp-Name` REQUIRED on Streamable HTTP POSTs | Server side arrives via SDK bump (transport validates). Our raw-HTTP test harnesses (`_mcp()` helper) must send the headers or requests will be rejected under the new revision. Any future EOGPT MCP client must send them too. |
| **SEP-2549** TTL for list results | `ttlMs` + `cacheScope` required on `tools/list`, `resources/list`, `resources/read`, `resources/templates/list` | Our §6 Resources layer must declare cache policy. Map state is mutable and per-map → `cacheScope: "private"`, short `ttlMs` for `resources/read` of live state (viewport/assets); `tools/list` and `resources/templates/list` are static → long TTL. Wire through whatever knobs the SDK exposes. |
| **`subscriptions/listen`** (part of SEP-2575) | Replaces `resources/subscribe`/`unsubscribe` + HTTP GET stream | Our **deferred** resource-subscriptions work (roadmap "Still deferred") must target THIS mechanism, not the old one. Deferring subscriptions was the right call — we never shipped the soon-to-be-removed API. Design note: per-map `resourceSubscriptions` filtering by `map://{map_id}/...` URI. |

### 🟡 Moderate impact

| SEP | What changes | ESIP impact & remediation |
|---|---|---|
| **SEP-2164** Resource-not-found error code | `-32002` → `-32602` (Invalid Params) | Our unknown-map/unknown-asset resource reads raise `ValueError` → SDK maps to an error code. Verify the SDK emits `-32602` post-bump; our tests assert `"error" in response` (code-agnostic) so they survive, but should pin the new code when upgrading. |
| **SEP-2577** Deprecate Roots/Sampling/Logging | 12-month removal clock | We implement none of them and (Phase 1 invariant) advertise none. Confirm the SDK bump doesn't re-advertise; nothing else to do. |
| **DCR deprecation** (draft changelog #10; supersedes the SEP-991 direction) | RFC 7591 DCR deprecated in favor of Client ID Metadata Documents | **Demo-box toy AS only** (`auth_server.py` has `ClientRegistrationOptions(enabled=True)` — that *is* DCR). Prod ESIP is a pure Resource Server (§5b) and is unaffected. When Claude-web supports CIMD, migrate the demo box; DCR remains functional for ≥ 12 months. |
| **SEP-2468** `iss` in authorization responses | AS SHOULD include `iss` (RFC 9207); clients MUST validate | Toy AS (demo box) should add `iss` to its authorization responses. RS side (§5b) unaffected. |
| **SEP-2207** OIDC refresh-token guidance | Rotation/expiry guidance for AS | Toy AS: opaque tokens, no refresh tokens today — note for if/when we add them. Prod defers to EOGPT's future platform auth. |
| **SEP-2106 / SEP-1613** JSON Schema 2020-12 | Full 2020-12 keyword set; `$ref` resolution requirements | Free via Pydantic → SDK schema generation. Watch item: if we ever hand-tune schemas, stay within composition-keyword resource bounds. |
| **SEP-2663 / SEP-1686** Tasks (now an extension) | `io.modelcontextprotocol/tasks`, polling model | Our deferred "§5e Tasks for long GeoTIFF ingest" idea must target the **extension**, not the removed experimental core API. |
| **SEP-2596** Feature lifecycle policy | Deprecated/Removed states, ≥ 12-month window, deprecated-features registry | Process to internalize: check the registry (`/specification/draft/deprecated`) at every SDK/spec bump. |
| **SEP-2549 caching + deterministic ordering** (minor #3) | `tools/list` SHOULD be deterministic | FastMCP registration order is deterministic (import order) — verify post-bump, cheap test assertion. |

### 🟢 Low / no impact (verified against our surface)

- **SEP-1865 MCP Apps** (Extensions Track) — already on our watch list; the §6
  `map://` taxonomy is exactly what a `ui://` template would link into. Opt-in.
- **SEP-2133 Extensions** — mechanism by which Tasks/Apps arrive; `extensions`
  field lands in capabilities via SDK. No action.
- **SEP-986 tool name format** — our 11 snake_case names comply.
- **SEP-1303 validation errors as Tool Execution Errors** — implemented since
  Phase 0 (it's a roadmap invariant); the draft keeps it.
- **SEP-985 RFC 9728 PRM** — §5b already aligned (PRM doc + `WWW-Authenticate`).
- **SEP-1046 client-credentials flow** — relevant *option* for the future
  EOGPT→ESIP M2M token flow (Phase 2 evolution), not a requirement.
- **SEP-991 CIMD** — superseded-in-spirit by the DCR deprecation above (CIMD is
  now the *preferred* registration mechanism); demo-box concern only.
- **SEP-990 enterprise IdP controls, SEP-1024 local-install security,
  SEP-1034/1330/1036 elicitation, SEP-1577 sampling-with-tools** — features we
  don't implement (and elicitation/sampling are being reshaped/deprecated
  anyway). No action.
- **SEP-1699 SSE polling via server-side disconnect** — superseded by the
  draft's removal of resumability; no action.
- **SEP-414 OTel trace context in `_meta`** — optional observability
  convention; nice-to-have when EOGPT propagates traces.
- **SEP-1730 SDK tiers, SEP-2484 conformance tests required, SEP-1850 PR-based
  SEPs, SEP-932/2085/2148/2149/1302/994 governance/process** — ecosystem
  process; indirectly good for us (Tier-1 Python SDK, official conformance
  suites we can adopt in the CI gate).
- **SEP-1319 decouple request payload from RPC methods, SEP-973 `_meta`/icons
  metadata, SEP-2260 server-request association** — SDK internals / features we
  don't use.

---

## 4. Readiness scorecard (why we're in good shape)

| Invariant we already hold | Draft-revision payoff |
|---|---|
| `stateless_http=True` + explicit `map_id` on every tool (Phase 0) | SEP-2567/2575 sessionless+stateless: our tool surface needs **zero redesign** |
| Advertised == implemented (init-options wrapper) | Migrates cleanly to `server/discover`; no phantom capabilities to unwind |
| Validation errors → `isError` (SEP-1303) | Unchanged in draft; already conformant |
| No Roots / Sampling / Logging / elicitation implemented | Entire deprecation wave (SEP-2577) is a no-op for us |
| Resource subscriptions deferred | Never shipped the API the draft deletes; build once, on `subscriptions/listen` |
| Pydantic as single schema source | JSON Schema 2020-12 loosening arrives free via SDK |
| CI conformance gate inside the deployable image (Phase 3) | The rework lands as *test edits*, and the gate blocks regressions during the transition |

**The real work, concentrated:** the `initialize`-shaped surface (tests + CI +
init-options wrapper + raw-HTTP harness headers), `ttlMs`/`cacheScope` on
lists/reads, `server/discover`, and — demo box only — the DCR→CIMD migration.

---

## 5. Trigger conditions & execution checklist

**Do NOT execute the upgrade until BOTH:**

1. **Spec trigger:** the draft revision is published as **Current** (a dated
   version string replaces `2025-11-25` at
   https://modelcontextprotocol.io/docs/learn/versioning).
2. **SDK trigger:** a released `mcp` Python SDK version lists that revision in
   `SUPPORTED_PROTOCOL_VERSIONS` (check
   `mcp/types.py::LATEST_PROTOCOL_VERSION` in the release).

> **TRIGGER STATUS (updated 2026-07-16 evening): both are about to fire.**
> Spec release + stable SDK v2 are jointly targeted for **2026-07-28** (per
> the python-sdk v2.0.0b2 release notes); `mcp==2.0.0b2` is already on PyPI
> as an opt-in pre-release. Watch cadence is now **weekly, not quarterly**.
> Recommended: run the §5 checklist as a **migration spike on a branch
> pinned to the current v2 beta** (do NOT merge/deploy until stable v2 +
> published spec land), so the day-one PR is a version-pin bump. Budget for
> the `httpx`→`httpx2` harness migration in that spike.

**Acceptance criterion (non-negotiable): Claude web Custom Connector
compatibility.** The Phase 5 PR MUST NOT break mounting ESIP on Claude.ai
(web). Concretely: (a) **dual-revision negotiation stays ON** — the legacy
`initialize` path (`2025-11-25` and earlier) must keep round-tripping for as
long as any real client (Claude web included) still speaks it; never enable a
hypothetical "new-revision-only" SDK mode ahead of Claude's own migration;
(b) the demo box's **DCR-based OAuth flow keeps working** (`test_mcp_as.py`
stays green in the CI gate) until Claude web supports Client ID Metadata
Documents — only then execute the DCR→CIMD migration, following Claude's
timing, not the spec's. Rationale: Claude.ai is Anthropic's own client and
will track the new revision quickly, but the deprecation window (≥ 12 months)
plus dual-revision support means there is no point at which a compliant ESIP
and any vintage of Claude web fail to negotiate.

**When both triggers fire, execute (one PR riding the existing CI gate):**

- [ ] Bump `mcp` SDK in `server/pyproject.toml`; read its migration notes.
- [ ] Old-revision compatibility decision: the SDK will likely keep
      negotiating old versions — keep dual-revision support ON (EOGPT clients
      may lag); rely on the 12-month deprecation window.
- [ ] Port the "advertised == implemented" wrapper to `server/discover`
      (tools + resources true; prompts/experimental absent until real).
- [ ] Add `ttlMs`/`cacheScope` on `tools/list`, `resources/list`,
      `resources/read`, `resources/templates/list` (private/short for live map
      state; long for static tool/template sets).
- [ ] Verify resource-not-found reads emit `-32602`; pin in tests.
- [ ] Update `tests/test_mcp*.py` + CI gate: new-revision path (no
      `initialize`; `_meta` version; `Mcp-Method`/`Mcp-Name` headers;
      `resultType: "complete"`; `server/discover` assertions) PLUS a
      legacy-path check while old revisions stay supported (the Claude-web
      acceptance criterion above: legacy `initialize` round-trip +
      `test_mcp_as.py` DCR flow must stay green).
- [ ] Confirm deterministic `tools/list` ordering (assert in tests).
- [ ] Demo box only: toy AS adds `iss` (SEP-2468); plan DCR→CIMD when
      Claude-web supports CIMD.
- [ ] Re-run the full three-suite gate (`test_mcp`, `test_mcp_auth`,
      `test_mcp_as`) inside the built image; update
      `vision/mcp-compliance-roadmap.md` progress log.

**Future work re-anchoring (unchanged priorities, new targets):**
- Resource subscriptions → build on `subscriptions/listen`.
- Long GeoTIFF ingest → `io.modelcontextprotocol/tasks` extension.
- MCP Apps (`ui://` live-map template) → `ext-apps` extension track.

---

## 6. Done in this pass (2026-07-16)

- Full SEP inventory (this document).
- Closed the roadmap's flagged Phase-3 gap: `tests/test_mcp.py` now asserts
  version negotiation for **`2025-11-25`** (the current spec revision) in
  addition to `2025-06-18`, and asserts a future/unknown requested version
  falls back to the server's latest supported version — both ride the existing
  CI gate. No behavior change to the server itself (the installed SDK already
  supports `2025-11-25`; it was simply never asserted).

### 6b. Pre-adoption SHIPPED (2026-07-16, same day) — the forward-compatible half

The draft revision splits into a **breaking half** (no `initialize`,
`server/discover`, `subscriptions/listen` — MUST wait for the SDK; still
gated on §5's triggers) and a **purely additive half** old clients ignore
(the MCP schema is `extra=allow`/`.passthrough()` in the Python and TS SDKs;
the spec requires tolerant readers). The additive half is now **implemented
and asserted in CI**:

- **New `server/mapcontrol_server/mcp_compat.py`** (imported LAST in
  `main.py`; wraps the low-level server's `request_handlers` dict —
  `initialize` lives in `ServerSession`, so the legacy handshake and the
  Claude-web acceptance criterion are untouched by construction):
  - **SEP-2322:** every result carries `resultType: "complete"` (including
    `isError` tool results — those are ordinary complete results).
  - **SEP-2549:** `ttlMs`/`cacheScope` on `tools/list` + `resources/
    templates/list` (+ `prompts/list` if ever registered) → `public`/1 h;
    `resources/list` → `public`/5 min; `resources/read` → `private`/5 s
    (live mutable map state; shared intermediaries must not cache).
  - **SEP-2575 (SHOULD):** `io.modelcontextprotocol/serverInfo` in every
    result's `_meta`.
  - **SEP-2164:** resource-not-found reads now return **`-32602`** (was the
    SDK's generic `code: 0` from our corrective `ValueError`s).
- **SEP-2468 (toy AS, demo box):** authorization responses now carry the
  RFC 9207 `iss` parameter (`auth_server.py::_issue_code` →
  `construct_redirect_uri(..., iss=...)`); asserted in `test_mcp_as.py`.
- **SEP-2243 (harness side):** the `test_mcp.py` `_mcp()` helper now sends
  `Mcp-Method` (+ `Mcp-Name` for `tools/call`) on every POST — harmless
  today, required under the new revision, so the CI gate already exercises
  the future-conformant client shape.
- **Deterministic `tools/list` ordering** (draft minor #3) asserted.
- All three standalone suites pass (`test_mcp`, `test_mcp_auth`,
  `test_mcp_as`); everything rides the existing CI gate unchanged. ADR-0001
  holds: no env knobs, identical local/cloud behavior.

**Remaining for §5 execution (unchanged, still trigger-gated):** SDK bump,
`initialize` → `server/discover` port of the advertised==implemented wrapper,
`_meta`-carried version/caps, `subscriptions/listen`, SSE-resumability
removal follow-through, demo-box DCR→CIMD. `mcp_compat.py` is deleted when
the SDK provides these natively.

---

## 7. v2.0.0b2 migration spike — empirical findings (2026-07-16 evening)

Ran a live spike against `mcp==2.0.0b2` in a scratch venv (throwaway
`MCPServer` + Streamable HTTP + Starlette TestClient). **What the ESIP port
will actually look like:**

**API surface changes (mechanical, must-do):**
1. **`FastMCP` is gone** → `from mcp.server import MCPServer`. Decorators
   survive (`.tool()`, `.resource()`, `.prompt()`, `.custom_route()`), as do
   `session_manager` and `streamable_http_app()`. Constructor keeps
   `auth_server_provider` / `token_verifier` / `auth` (toy AS + §5b port
   cleanly) and gains `cache_hints=`, `subscriptions=`, `extensions=`,
   `resource_security=`.
2. **`stateless_http` and `transport_security` moved** from the constructor to
   `streamable_http_app(stateless_http=True, transport_security=..., host=...)`.
3. **`mcp.types` is gone** — types are top-level (`from mcp import
   ListToolsResult, ...`); `mcp.shared.version` is gone too (our test import
   `from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS` must change).
4. **Result models natively carry `result_type`, `ttl_ms`, `cache_scope`**
   (snake_case fields → camelCase wire) — `mcp_compat.py`'s stamping becomes
   redundant → delete on migration, replaced by
   `cache_hints={"tools/list": CacheHint(ttl_ms=3_600_000, scope="public"), ...}`.

**Behavioral findings (verified end-to-end):**
5. ✅ **Stateless works:** `tools/call` with NO `initialize` succeeds
   (`stateless_http=True`) — the handshake requirement is gone.
6. ✅ **Legacy `initialize` still round-trips** (`2025-11-25` echoed) —
   dual-revision support confirmed; Claude-web criterion satisfiable. **BUT**
   v2 re-advertises `prompts`/`resources`/`experimental` in the legacy init
   response → the advertised==implemented wrapper must be RE-PORTED (the 1.x
   `create_initialization_options` hook may not exist; find v2's equivalent).
7. ⚠️ **`server/discover` NOT implemented in b2** (`-32601 Method not found`)
   — the server-side stateless half is still landing (b2 shipped the CLIENT
   half of `subscriptions/listen`; a `subscriptions=` server param + module
   exist). Expect b3/stable. Re-probe on each beta.
8. ⚠️ **DNS-rebinding protection is ON BY DEFAULT in v2** (421 Misdirected
   Request on unknown `Host`) — the OPPOSITE of the 1.x default our Phase-0/1
   env knobs assume. On migration: default flips to secure; our
   `MAPCONTROL_MCP_DNS_REBINDING_PROTECTION` knob semantics must be re-mapped
   (local/compose needs `allowed_hosts` or explicit opt-out — ADR-0001).
9. ℹ️ New-revision result fields did NOT appear on the wire for our
   un-versioned/legacy requests — v2 appears to serialize them
   **per negotiated revision** (old clients see old shapes). Correct behavior;
   our unconditional `mcp_compat.py` stamping is the interim stand-in.
10. Client-side (`eogpt` future + our harness): `httpx` → `httpx2`.

**Spike code:** `/tmp/probe_v2e.py` (scratch venv `/tmp/mcp2-spike-venv`,
`pip install mcp==2.0.0b2 starlette`). Not committed — b2 lacks
`server/discover`, so a full port PR is premature; re-run the spike against
b3/rc, then execute §5.
