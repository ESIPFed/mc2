# ESIP Map Control — MCP Apps Appification (ext-apps / SEP-1865)

**Status:** Plan finalized (spec-verified recon + session model + Phase-5 interaction analysis);
implementation gated on the Option A vs B probe (see §4)
**Created:** 2026-07-16
**Companion to:** `vision/mcp-compliance-roadmap.md` (Phase 4+ §7 "watch MCP Apps"),
`vision/mcp-2026-protocol-upgrade.md` (SEP-1865 listed 🟢 "already on our watch list"),
`mapcontrol_server/mcp_resources.py` (the `map://` taxonomy this app links into).

> **What this is.** The plan for turning ESIP Map Control into an **MCP App**:
> an interactive map UI that renders **inline in the chat** of MCP hosts
> (Claude web/desktop, ChatGPT, Cursor, VS Code Copilot, ...) instead of the
> user opening `/map/{map_id}` in a separate tab. Everything in §2 is
> **primary-source verified 2026-07-16** against the published ext-apps
> specification (`specification/2026-01-26/apps.mdx`), the official MCP Apps
> extension docs (`modelcontextprotocol.io/extensions/apps/*`), and the
> client support matrix.

---

## 1. The session-handling question, answered precisely

"Appification means MCP does the session handling a bit more, right?" —
**true for one kind of session, false for another.** Three sessions are in
play; the traffic lights differ per architecture option (§4):

| Session kind | Today (tab + REST/WS) | Option A: app wraps live `/map/{id}` iframe | Option B: native MapLibre template over host bridge |
|---|---|---|---|
| **Transport/auth session** (connection, tokens, OAuth) | 🔴 ESIP's problem — open `:8000` or the demo-box toy-AS flow | 🟡 **Hybrid.** `tools/call` / `resources/read` ride the host's already-authenticated MCP session (the app iframe is an **MCP client of the HOST** over postMessage, never of ESIP, and never holds a token) — but the *inner* live-map iframe still talks REST/WS directly to ESIP's public origin. | 🟢 **Fully host-owned.** Every byte is proxied over the host's authenticated session; the iframe never touches ESIP directly. The public browser surface could eventually go dark. |
| **ESIP `user_sessions`** (per-viewer viewport, moveend phone-home, WS routing) | 🟢 Works — it *is* the native mechanism | 🟢 **Works unchanged.** The inner page auto-creates its session on load exactly as today; phone-home, WS push, and drawing all stay intact. | 🟡 **Survives, re-plumbed.** The SQLite table + `session_service` logic stay untouched; only the *transport* changes — the moveend phone-home becomes an **app-only tool** (`report_viewport`, `visibility: ["app"]` — invisible to the model, callable by the app via the bridge) carrying `user_session_id` as an explicit argument, so per-viewer state persists. What's lost is server→app *push* (WS) until `subscriptions/listen` lands; poll via the bridge in the interim. |
| **Map identity** (`map_id`) | 🟢 Explicit argument everywhere | 🟢 Unchanged | 🟢 Unchanged — and it is exactly the explicit-handle model both MCP Apps and the 2026 sessionless revision (SEP-2567) assume. |

**Net:** appification moves the *auth/connection lifecycle* (our actual
exposure problem) onto the host, while domain state stays explicit — which the
Phase-0 `stateless_http=True` + explicit-`map_id` design already satisfies.
"App with sessions" is fully possible under BOTH options: Option A keeps
today's sessions literally; Option B reshapes them into tool-argument-carried
handles — which is not a workaround but the SEP-2567-conformant,
forward-compatible design.

---

## 2. Spec ground truth (fetched + verified 2026-07-16)

From `ext-apps/specification/2026-01-26/apps.mdx` + the official extension docs:

1. **Extension identity & negotiation.** MCP Apps is extension
   **`io.modelcontextprotocol/ui`** (SEP-1724 mechanism). The **client**
   advertises it in `initialize` under
   `capabilities.extensions["io.modelcontextprotocol/ui"]` with required
   `mimeTypes: ["text/html;profile=mcp-app"]`. Servers SHOULD check the client
   capability before registering UI-enabled tools; hosts without apps support
   simply fall back to the tool's ordinary content (**graceful degradation is
   mandatory anyway** — tools MUST return meaningful content even when UI is
   available; ours already do: text + `structuredContent`).
2. **UI resource.** Registered like any resource but: URI **MUST** use the
   **`ui://`** scheme; `mimeType` **MUST** be exactly
   **`text/html;profile=mcp-app`** (not plain `text/html`); content is a
   valid HTML5 document delivered via `resources/read` (`text` or base64
   `blob`). Servers **MAY omit `ui://` resources from `resources/list`**
   (discovery is via tool metadata) — keeps the `map://` taxonomy clean.
3. **Tool ↔ UI association.** Tools carry
   **`_meta.ui.resourceUri: "ui://..."`** (the flat `_meta["ui/resourceUri"]`
   is **deprecated**, removal before GA — do not use). Optional
   **`_meta.ui.visibility`**: default `["model", "app"]`;
   **`["app"]` = app-only tool** — hidden from the model's tools/list, callable
   only by the app through the host bridge (host MUST enforce both
   directions). This is the spec-native replacement for our moveend
   phone-home: e.g. a hidden `report_viewport` tool.
4. **CSP is server-declared, host-enforced (default-deny).** The
   `resources/read` result's `_meta.ui.csp` declares exactly what the iframe
   may touch: `connectDomains` (fetch/XHR/**WebSocket** → `connect-src`),
   `resourceDomains` (scripts/img/styles/fonts, wildcard subdomains OK),
   **`frameDomains` (nested iframes → `frame-src`; empty/omitted =
   `frame-src 'none'`)**, `baseUriDomains`; plus `permissions`
   (camera/mic/geolocation/clipboardWrite) and `prefersBorder`. If `csp` is
   omitted the host MUST apply a restrictive default (`connect-src 'none'`,
   inline-only scripts). Hosts MUST NOT allow undeclared domains.
5. **Communication protocol.** JSON-RPC 2.0 over postMessage — the iframe
   "View" acts as an MCP client of the host; the host acts as an MCP server
   that **proxies to the real server**. Lifecycle: `ui/initialize` (with
   `appCapabilities`, e.g. `availableDisplayModes`) → `ui/notifications/initialized`;
   host pushes the triggering tool's result to the app. The app may call:
   `tools/call`, `resources/read`, `ui/message` (post into chat),
   **`ui/update-model-context`** (push structured state into the model's
   context — e.g. "user drew this polygon"), `ui/open-link`, and
   `ui/notifications/size-changed`. Web hosts use a double-iframe sandbox
   (sandbox proxy origin, e.g. Claude's `{hash}.claudemcpcontent.com`).
6. **Host support (per the official client matrix):** Claude web, Claude
   Desktop, VS Code GitHub Copilot, Microsoft 365 Copilot, **ChatGPT**,
   **Cursor**, Goose, Postman, MCPJam, Archestra, PostHog Code. Claude web
   requires a paid plan for custom connectors; `cloudflared tunnel` is the
   documented local-dev path.
7. **SDK reality check.** The helper SDK (`@modelcontextprotocol/ext-apps`,
   `registerAppTool` / `registerAppResource` / `App` class) is
   **TypeScript-only** — but the spec explicitly documents raw-postMessage
   usage, and **server-side there is nothing beyond ordinary MCP**: a
   resource with the right URI/mimeType/`_meta` + `_meta.ui` on tools. Our
   Python 1.x server can ship this with plain resource registration plus the
   **proven `mcp_compat.py` request-handler-wrapping technique** to stamp
   `_meta.ui` onto `tools/list` entries and `_meta.ui.csp` onto the
   `resources/read` result (the 1.x FastMCP decorators don't expose those
   `_meta` fields directly).
8. **Directly relevant reference:** the ext-apps examples include
   **`map-server` (CesiumJS 3D globe)** — an official, working map-as-MCP-App
   precedent, plus a Svelte starter and `basic-host` (a local test host, so we
   can verify without Claude in the loop:
   `SERVERS='["http://localhost:8000/mcp"]' npm start`).

---

## 3. What the spec context changes vs. the pre-recon plan

- **Option A (nested live-map iframe) is spec-legal** — `frameDomains` exists
  precisely for nested iframes, and `connectDomains` covers the inner page's
  WebSocket. BUT it is **host-discretionary**: hosts MAY restrict further, and
  whether **Claude web actually honors `frameDomains`** for arbitrary origins
  is unverified → this is now the single blocking probe for Option A.
- **Option B (native template) is much cheaper than pre-recon estimated.** The
  official `map-server` example is the exact shape; a single-file MapLibre GL
  bundle that renders from `resources/read` on the `map://` taxonomy +
  `tools/call` via the bridge is a bounded build. Missing live-push is
  mitigated by app-driven polling through the bridge (and later by
  `subscriptions/listen` when the 2026 core revision lands).
- **App-only tools solve the `user_sessions` gap elegantly** (Option B): a
  `report_viewport` tool with `visibility: ["app"]` gives us the moveend
  phone-home *through the host's authenticated session* with zero model-facing
  tool-list pollution.
- **`ui/update-model-context` closes the drawing loop**: user draws an AOI in
  the inline map → the app pushes the GeoJSON into the model's context → the
  model can immediately reason about it. This was previously only possible via
  the separate browser tab + re-upload.
- **Graceful degradation is already done**: every ESIP tool returns text +
  `structuredContent`, so non-apps hosts (and the M2M eogpt path) see exactly
  today's behavior. Appification is purely additive on the wire.
- **`_meta` names pinned**: nested `_meta.ui.resourceUri` (NOT the deprecated
  flat key); resource mimeType exactly `text/html;profile=mcp-app`.
- **Advertised == implemented has a new clause**: the `extensions`
  capability field must advertise `io.modelcontextprotocol/ui` only once the
  template + `_meta` stamping are real (grow in lockstep, same as
  tools/resources).

---

## 4. Architecture options (reconsidered)

### Option A — Thin wrapper: nested iframe of the live `/map/{map_id}` page

The `ui://` template is a small shell that (1) does the `ui/initialize`
handshake, (2) receives the triggering tool result (carrying `map_id`/`url`),
(3) renders `<iframe src="https://<esip-origin>/map/{map_id}">`. CSP:
`frameDomains: [ESIP origin]` and the inner page manages its own REST/WS.

- ✅ Full live map (WS push, drawing, terrain) with ~zero frontend rework;
  ESIP `user_sessions` work unchanged (auto-create on load, as today).
- ⚠️ **Gate: does the target host honor `frameDomains`?** (spec-legal,
  host-discretionary). Probe first on `basic-host`, then Claude web.
- ⚠️ ESIP's `/map/*`, `/api/*`, `/ws/*` surface must stay publicly reachable —
  the MCP session only covers `/mcp`. Session handling is *hybrid*.

### Option B — Native app: single-file MapLibre template over the host bridge

Bundled HTML+JS (vite-plugin-singlefile style) that renders purely from
`resources/read` (`map://{map_id}/assets`, `/viewport`, geo+json assets,
raster PNGs) and `tools/call` (zoom, add_geojson, ...) via postMessage.
CSP: only tile-server domains in `resourceDomains`/`connectDomains`.

- ✅ **Pure MCP session handling** — all traffic rides the host's
  authenticated session; the ESIP browser surface could eventually go dark
  publicly. App-only `report_viewport` + `ui/update-model-context` for drawn
  shapes replace the ESIP session mechanics.
- ✅ Canonical, host-blessed pattern (the `map-server` example); no
  `frameDomains` risk.
- ⚠️ Real frontend build (MapLibre bundle, ~bounded but nontrivial); no push
  until `subscriptions/listen` (poll via bridge in the interim); drawing UX
  must be reimplemented on the template.

### Decision posture

Run the **Option-A probe first** (hours, not days): if the target host honors
`frameDomains`, ship A as the demo vehicle and build B behind it as the
strategic destination. If the probe fails on Claude web, go straight to B —
the recon shows it is well-trodden. Either way the server-side plumbing
(§5 items 1–3) is **identical and shared**.

---

## 5. Implementation plan (server = `mcp` 1.x, no beta pins)

1. **`mapcontrol_server/mcp_apps.py`** (import == install, alongside
   `mcp_resources`): registers the `ui://esip/map.html` template resource
   (exact mimeType `text/html;profile=mcp-app`); serves the bundled/inline
   HTML; excluded from `resources/list`.
2. **`_meta` stamping** via the proven `mcp_compat.py` request-handler
   technique: `_meta.ui.resourceUri` on `create_map` + a new
   **`show_map(map_id)`** tool (model-visible; lets the model summon the UI
   for an existing map) in `tools/list`; `_meta.ui.csp` (+`prefersBorder`) on
   the template's `resources/read` result. Option B later adds
   `report_viewport` with `_meta.ui.visibility: ["app"]`.
3. **Capability advertisement:** extend the advertised==implemented wrapper to
   emit `capabilities.extensions["io.modelcontextprotocol/ui"]` once (and only
   once) the above is live.
4. **Template v1 (Option A shell or Option B native, per §4 probe):** raw
   postMessage JSON-RPC per the spec (no SDK dependency needed), or the
   Svelte starter if B.
5. **Tests + CI (rides the existing image gate):** template resource readable
   with correct mimeType + `_meta.ui.csp`; UI-enabled tools carry
   `_meta.ui.resourceUri` (nested form, not the deprecated flat key);
   extension advertised == implemented; `resources/list` unpolluted; all
   legacy suites stay green (Claude-web acceptance criterion from the Phase-5
   doc holds — apps are additive).
6. **Local verification without Claude:** ext-apps `basic-host` pointed at
   `http://localhost:8000/mcp` (ADR-0001: same artifact locally and in cloud).
7. **Demo-box verification:** Claude web custom connector against
   `esip-mcp.earthanalyticslab.com` — the acceptance criterion.

**Invariants carried forward:** no mega-tool; Pydantic as schema source;
advertised == implemented (now including `extensions`); EPSG:4326 across the
boundary; intra-ESIP only; errors via the right channel; ADR-0001
dual-deployability (no new env knobs for the app layer itself; Option A's
public-origin requirement is config, not code).

---

## 5b. Interaction with the 2026 protocol revision (Phase 5) — does it get in the way?

**No — orthogonal, and in two places mutually reinforcing.** Checked against
`vision/mcp-2026-protocol-upgrade.md` and the v2.0.0b2 spike findings:

**Where they don't collide:**

1. **Extensions Track survives the revision.** MCP Apps is negotiated via the
   `extensions` capability field (SEP-2133) — the same mechanism the new
   revision itself uses to deliver Tasks. The deprecation wave
   (Roots/Sampling/Logging, DCR) does not touch extensions.
2. **The app bridge is its own protocol dialect.** `ui/initialize` /
   `ui/message` / etc. over postMessage is NOT the core `initialize` handshake
   that SEP-2575 deletes. When core MCP goes stateless, the app bridge
   lifecycle is untouched by construction.
3. **The session design is forward-compatible.** SEP-2567 mandates cross-call
   state as explicit server-minted handles passed as tool arguments — the
   Option-B shape (`map_id` + `user_session_id` args on app-only tools) is
   exactly that. Conforming to the new spec requires zero rework of the app's
   session model. `Mcp-Session-Id` removal is likewise free
   (`stateless_http=True` since Phase 0).
4. **Graceful degradation covers every revision.** Apps `_meta` is additive on
   the wire; hosts of any vintage that don't speak the extension see today's
   text + `structuredContent` results.

**The two real touchpoints (small; both already on the Phase-5 checklist):**

- **Capability advertisement port:** today the extension is advertised in the
  `initialize` response; under the new revision it moves to `server/discover`.
  The Phase-5 checklist already includes porting the advertised==implemented
  wrapper to `server/discover` — the apps extension is one more field riding
  that same port.
- **`_meta` stamping mechanics:** the `mcp_compat.py` request-handler-wrapping
  technique used to stamp `_meta.ui` on the 1.x SDK re-ports at the v2 bump —
  the v2 spike already found the replacement (constructor `extensions=`;
  result models carry `_meta` natively), and `mcp_compat.py` was always
  scheduled for deletion at the SDK bump.

**Where the new revision actively helps:** `subscriptions/listen` (the one
long-lived stream) is precisely Option B's missing piece — real server→app
push for live map updates, replacing the interim bridge-polling.

**Sequencing rule:** build the app NOW on the 1.x SDK (additive, no beta
pins, all suites stay green); let the already-planned Phase-5 pin-bump PR
(~2026-07-28 triggers) carry the apps `_meta`/extension advertisement over as
part of its existing wrapper-port work. Do not land the apps layer and the
Phase-5 SDK bump in the same change without re-running the full three-suite
CI gate.

---

## 6. Probe findings & verdict (RESOLVED 2026-07-18 — Option B SHIPPED)

**Outcome: the live ESIP map renders inline in Claude web** as a native
MapLibre map inside the app view, following tool calls (zoom, add_geojson,
set_basemap, …) in real time over the WebSocket. User-confirmed working
2026-07-18. Shipped as v1 (`0dc1ec5`) + v1.1 (`8c3c7cd`) on the standalone
repo's `experimental` branch, deployed to the demo box.

### 6a. Answers to the original probe list

1. **`frameDomains` on Claude web: NOT honored.** The sandbox keeps its
   stock `frame-src 'self' blob: data:` regardless of what the resource
   declares (probe 7: meta demonstrably delivered, explicit
   `frame-src` violation blocking the map origin). Option A (nested
   iframe of `/map/{id}`) is therefore not renderable on Claude web today.
   The meta stays declared — it is the correct signal for hosts that do
   honor it (spec §Sandbox proxy step 5) and harmless where ignored.
2. **Display modes / dimensions:** the host sizes the frame from the app's
   `ui/notifications/size-changed`; without it the frame is **0 px tall**
   (probe 5/6 root cause). We report height 480. `availableDisplayModes`
   not yet exercised.
3. **Template prefetch/size:** Claude fetches the template via
   `resources/read` and re-hosts it (`?resource-src=…` on the sandbox
   URL). A **342 KB inline bundle broke document delivery outright**
   (gray broken-page icon, probes 1–3). Keep templates small; load heavy
   libraries as EXTERNAL scripts from your origin via `resourceDomains`.
4. **Auth interplay: works.** The host proxies `/mcp` with its OAuth token
   through the toy AS unchanged; the app view's REST/WS calls hit public
   endpoints keyed by unguessable map_id (no cookies → no third-party-
   cookie swamp).
5. **Tile CSP:** basemap origins are auto-derived from
   `config.map.basemaps` (`mcp_apps._basemap_origins()`) and declared in
   both `connectDomains` (MapLibre fetches tiles via fetch/XHR) and
   `resourceDomains`. Claude plumbs the lists **verbatim** into the
   sandbox CSP.

### 6b. Claude-web Apps sandbox — empirical channel matrix

| Channel | CSP directive | Server declaration | Claude web |
|---|---|---|---|
| fetch/XHR | `connect-src` | `csp.connectDomains` | ✅ honored (verbatim) |
| WebSocket | `connect-src` | `csp.connectDomains` **with explicit `wss://` scheme** | ✅ honored — but `https://host` does NOT match `wss://host` (exact scheme match; only `http/ws` upgrade-match). v1.1 fix. |
| img/script/style | `img-src`/`script-src`/… | `csp.resourceDomains` | ✅ honored (verbatim; also visible as `?resource-src=` on the sandbox URL) |
| nested iframe | `frame-src` | `csp.frameDomains` | ❌ ignored (stock `'self' blob: data:` kept) |
| inline `<script>` (classic) | — | — | ✅ allowed by default |
| `resource_link` content blocks | — | — | ❌ "Resource links are not currently supported" (harmless noise) |

### 6c. Probe history (what each deploy taught us)

| # | Change | Readout | Lesson |
|---|---|---|---|
| 1 | raw-handshake template + `_meta.ui.csp` | gray broken-page icon | (masked — see 4) |
| 2 | official `@modelcontextprotocol/ext-apps` App class, 342 KB vite bundle | still broken icon; box logs prove template WAS read | delivery-side failure |
| 3 | drop `_meta.ui` + min-height | still broken icon | `_meta` not the cause |
| 4 | ~45-line static hello-world | document LOADS (icon gone), nothing visible | **big inline bundles kill template delivery**; visibility issue remains |
| 5 | loud banner + instrumented handshake (host-initiates assumption) | invisible again | wrong handshake direction → no `size-changed` → 0-height frame |
| 6 | SDK-dist–derived dialect: **app initiates** `ui/initialize` (protocol `2026-01-26`) → `initialized` → `size-changed` | full lifecycle works, host=Claude/1.0.0; only nested iframe CSP-blocked | handshake + sizing solved |
| 7 | re-enable `frameDomains` only | stock `frame-src` kept, violation logged | Claude ignores frameDomains |
| 8 | declare all three domain lists + per-channel instrumented tests | connect ✅ resource ✅ frame ❌ | **Option B GO** |
| v1 | native MapLibre template (self-hosted vendor dist) + tile origins in CSP | everything works except `wss://` blocked | CSP scheme exactness |
| v1.1 | `wss://` variant in connectDomains + REST-polling fallback | **WORKING inline live map** 🎉 | done |

### 6d. Shipped architecture (v1.1)

- **Server** (`mcp_apps.py`): extension advertised; `ui://esip/map.html`
  registered (`text/html;profile=mcp-app`); `_meta.ui.resourceUri` stamped
  on create_map/show_map; contents `_meta.ui.csp` declares
  `connectDomains` = {origin, wss-origin, tile origins},
  `resourceDomains` = {origin, tile origins}, `frameDomains` = {origin}.
- **Template** (`static/mcp-app.html`, ~300 lines, no build step):
  hand-rolled handshake → tool-result delivers map url → loads
  self-hosted MapLibre GL 5.23.0 from `/static/vendor/` → REST session →
  WS `session_restore`/`session_snapshot` → native render (raster
  basemap, GeoJSON assets, viewport) → debounced re-render on any WS
  broadcast. Fallback: immediate basemap render + 3 s REST polling when
  the WS is unavailable.
- **Option A** remains the design for frameDomains-honoring hosts (the
  same resource meta already signals it); `app-template/` (vite/SDK
  bundle) is legacy scaffolding, superseded by the hand-rolled template.

**For the transferable, domain-agnostic version of these lessons see
`docs/mcp-apps-field-guide.md`.**

