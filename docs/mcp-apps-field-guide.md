# MCP Apps Field Guide

**A practical handbook for building interactive widgets that render inline in
AI chat clients (Claude web/desktop, ChatGPT, Cursor, …) via the MCP Apps
extension.**

**Audience:** anyone building an MCP server — in any domain (charts,
dashboards, maps, forms, data tables, 3D viewers, document previews) — who
wants an interactive UI to appear **inside the conversation** when their tool
runs, and especially anyone whose widget currently shows a gray broken-page
icon, an invisible strip, silent data failures, or nothing at all.

**Provenance:** everything here was learned empirically through many
deploy-and-probe cycles against a real Apps host, cross-checked against the
published spec. Every "landmine" in §9 cost a full debugging cycle in
practice. Spec referenced: MCP Apps extension (`io.modelcontextprotocol/ui`,
SEP-1865), spec revision `2026-01-26`.

---

## Table of contents

1. [What MCP Apps are (and when to use them)](#1-what-mcp-apps-are-and-when-to-use-them)
2. [Architecture deep-dive: the three layers](#2-architecture-deep-dive-the-three-layers)
3. [Protocol reference: every message you will send or receive](#3-protocol-reference)
4. [Server implementation guide](#4-server-implementation-guide)
5. [Template implementation guide](#5-template-implementation-guide)
6. [CSP and sandboxing](#6-csp-and-sandboxing)
7. [Host compatibility notes](#7-host-compatibility-notes)
8. [Debugging playbook](#8-debugging-playbook)
9. [Landmines and gotchas](#9-landmines-and-gotchas)
10. [Production checklist](#10-production-checklist)

---

## 1. What MCP Apps are (and when to use them)

An **MCP App** is an HTML document your MCP server publishes as a special
resource. When one of your tools runs in a supporting host, the host fetches
that document, renders it in a sandboxed iframe inside the chat, and connects
it to the conversation over a message bridge. The result: instead of the
model describing your tool's output in prose, the user sees (and interacts
with) an actual UI — a live chart, a map, a form, a table.

**The extension is negotiated, not assumed.** MCP Apps is an *extension* to
core MCP, identified as `io.modelcontextprotocol/ui`. Hosts that support it
advertise it; hosts that don't simply ignore your UI metadata. This drives
the single most important design rule:

> **Design rule 0 — graceful degradation is mandatory.** Every UI-enabled
> tool must still return meaningful, self-sufficient output (`content` text
> blocks + `structuredContent`) so that hosts without Apps support — and
> machine-to-machine callers — get full value with no UI at all. The widget
> is an enhancement layer, never the only representation.

**When Apps are the right choice:**

- The output is inherently visual or interactive (plots, maps, tables with
  sorting, pickers, forms, media).
- The user benefits from manipulating the result (zooming, filtering,
  selecting) without another round-trip through the model.
- You want state the user creates in the UI (a selection, a drawn shape, a
  form entry) to flow back into the conversation.

**When they're the wrong choice:**

- Output that is naturally textual/structured — the model already renders
  markdown, tables, and code well.
- One-shot images — returning an inline image content block is far simpler
  than an app.
- Anything that must work in every client today — Apps support is still
  uneven across hosts (see §7).

---

## 2. Architecture deep-dive: the three layers

```
 LAYER 1: DISCOVERY              LAYER 2: DELIVERY                LAYER 3: RUNTIME
┌──────────────────────┐       ┌───────────────────────┐        ┌───────────────────────────┐
│ initialize:           │       │ host calls             │        │ your JS runs inside a      │
│  capabilities.        │       │ resources/read on the  │        │ DOUBLE-SANDBOXED iframe    │
│  extensions[…/ui]     │       │ ui:// URI              │        │ on a host-owned origin     │
│                       │       │                        │        │                            │
│ tools/list:           │──────▶│ host RE-HOSTS the HTML │───────▶│ JSON-RPC 2.0 over          │
│  tool._meta.ui.       │       │ itself (your server    │        │ window.parent.postMessage; │
│  resourceUri =        │       │ never serves the page  │        │ host proxies tools/call &  │
│  "ui://…"             │       │ to the browser)        │        │ resources/read to your     │
│                       │       │ + applies the CSP you  │        │ MCP server over ITS        │
│                       │       │ declared in _meta      │        │ authenticated session      │
└──────────────────────┘       └───────────────────────┘        └───────────────────────────┘
```

### Layer 1 — Discovery

Two pieces of metadata connect a tool to a UI:

1. Your server advertises the extension in its `initialize` response under
   `capabilities.extensions`.
2. Each UI-enabled tool carries `_meta.ui.resourceUri` pointing at a
   `ui://` resource.

When the model calls a tool that carries that metadata in a supporting host,
the host knows to allocate a widget frame in the transcript and fetch the
template.

### Layer 2 — Delivery

The host reads your template with an ordinary `resources/read` request on
your MCP connection. **It then serves the HTML itself** from a dedicated
sandbox origin it controls (a per-conversation or per-resource subdomain).
Consequences that surprise people:

- Your web server's headers (CSP, CORS, X-Frame-Options) on the template are
  **irrelevant** — the template travels inside an MCP response body, not
  over HTTP to the browser.
- The document's effective CSP is assembled by the **host** from the
  `_meta.ui.csp` you attach to the `resources/read` result (§6), layered on
  the host's own restrictions.
- Web hosts typically use a **double-iframe sandbox**: an outer host-page
  iframe containing a sandbox-proxy page on a separate origin, which loads
  your HTML into an inner frame and relays messages both ways. You never see
  this structure from inside; you just talk to `window.parent`.
- Templates may be cached by the host. Treat the template like a versioned
  artifact; when debugging, always confirm which version actually loaded
  (put a version string in your on-page log).

### Layer 3 — Runtime

Inside the sandbox, your script speaks **JSON-RPC 2.0 over
`window.parent.postMessage`**. Conceptually the app is an *MCP client of the
host*, and the host is an MCP server that proxies selected methods to your
real MCP server over its own already-authenticated session. Implications:

- The app never holds credentials to your backend. Calls made via the bridge
  (`tools/call`, `resources/read`) are authenticated by the host, not you.
- Direct network calls (fetch/XHR/WebSocket) from the iframe to your own
  backend ARE possible, but only to origins you declared in `connectDomains`
  — and those calls arrive unauthenticated (no cookies), so design your
  endpoints accordingly (capability URLs / unguessable IDs / tokens passed
  through the tool result).
- After your tool call finishes, the host injects a note into the model's
  context along the lines of *"this tool call rendered an interactive
  widget"* — a useful debugging signal (§8) and a reminder that the model
  knows a widget exists but not what's inside it unless you push context
  back (`ui/update-model-context`).

---

## 3. Protocol reference

All messages are JSON-RPC 2.0 objects posted between your document and
`window.parent`. Requests carry `id`; notifications don't. **Validate every
incoming message** (`data.jsonrpc === "2.0"`) — the sandbox window receives
unrelated traffic (React devtools chatter, host telemetry).

### 3.1 Lifecycle — who speaks first (critical)

> **THE APP INITIATES THE HANDSHAKE.** Your script sends `ui/initialize` as
> a *request* to `window.parent`. The host replies with a result. If you
> instead wait for the host to contact you, you will wait forever, no error
> will surface anywhere, and your frame will render 0 pixels tall.

Sequence:

```
APP  → HOST   ui/initialize            (request, carries appInfo/capabilities/protocolVersion)
HOST → APP    (result)                 (hostInfo, hostCapabilities, hostContext)
APP  → HOST   ui/notifications/initialized
APP  → HOST   ui/notifications/size-changed        ← REQUIRED for visibility
HOST → APP    ui/notifications/tool-input          (the triggering tool's arguments)
HOST → APP    ui/notifications/tool-result         (the triggering tool's result)  ← render here
HOST → APP    ui/notifications/host-context-changed (theme etc., any time)
HOST → APP    ping                                  (request; reply with {} result)
```

### 3.2 `ui/initialize` (app → host, request)

```json
{ "jsonrpc": "2.0", "id": "init-1", "method": "ui/initialize",
  "params": {
    "appInfo": { "name": "My Widget", "version": "1.0.0" },
    "appCapabilities": {},
    "protocolVersion": "2026-01-26"
  } }
```

Practical notes:

- **Retry it.** The sandbox relay can attach after your script runs. Resend
  every ~700 ms (cap at ~10 attempts) until you receive the result; make the
  handler idempotent (ignore late replies to superseded ids).
- `appCapabilities` may declare things like
  `availableDisplayModes: ["inline", "fullscreen", "pip"]` and app-exposed
  tools; `{}` is fine to start.

Host result (shape, abridged):

```json
{ "jsonrpc": "2.0", "id": "init-1",
  "result": {
    "protocolVersion": "2026-01-26",
    "hostInfo": { "name": "…", "version": "…" },
    "hostCapabilities": { "serverTools": {}, "serverResources": {}, "…": {} },
    "hostContext": {
      "theme": "dark",
      "displayMode": "inline",
      "locale": "en-US",
      "timeZone": "America/Chicago",
      "platform": "web",
      "styles": { "variables": { "--color-background-primary": "…" } },
      "safeAreaInsets": { "top": 0, "right": 0, "bottom": 0, "left": 0 }
    } } }
```

Use `hostContext.theme` to match light/dark; hosts may also supply CSS
variables and fonts for visual integration.

### 3.3 `ui/notifications/initialized` (app → host)

Empty-params notification sent immediately after you accept the initialize
result. The host will not push tool notifications before it.

### 3.4 `ui/notifications/size-changed` (app → host) — **not optional**

```json
{ "jsonrpc": "2.0", "method": "ui/notifications/size-changed",
  "params": { "width": 800, "height": 480 } }
```

Hosts size your iframe from this. **If you never send it, your frame is
0 px tall**: the document loads, scripts run, network calls succeed — and
the user sees nothing. Send it right after `initialized` and again whenever
content height changes. A robust pattern:

```js
const ro = new ResizeObserver(() => {
  const h = Math.ceil(document.documentElement.getBoundingClientRect().height);
  parent.postMessage({ jsonrpc:"2.0", method:"ui/notifications/size-changed",
                       params:{ width: Math.ceil(innerWidth), height: h }}, "*");
});
ro.observe(document.documentElement);
ro.observe(document.body);
```

(For fixed-height widgets, one hardcoded send is fine.)

### 3.5 Tool notifications (host → app)

- **`ui/notifications/tool-input`** — the arguments the model passed to the
  triggering tool: `params: { "arguments": { … } }`.
- **`ui/notifications/tool-input-partial`** — streamed argument fragments
  while the model is still typing the call. Arrives many times; noisy.
  Ignore unless you're building live-preview UX; definitely don't log each
  one in production.
- **`ui/notifications/tool-result`** — the full `CallToolResult` of the
  triggering call: `params: { "content": [ … ], "structuredContent": { … },
  "isError": false }`. **This is your render trigger.** Prefer
  `structuredContent`; fall back to parsing the first JSON-looking `text`
  block. Handle `isError: true` with a visible message.
- **`ui/notifications/tool-cancelled`** — the call was cancelled; show a
  neutral state.

### 3.6 `ui/notifications/host-context-changed` (host → app)

Partial updates to hostContext (commonly theme flips). Merge, don't replace.

### 3.7 Host requests you must answer

The host may send requests (notably `ping`). Anything with both `id` and
`method`: reply `{ "jsonrpc":"2.0", "id": <same>, "result": {} }`. Unanswered
pings can cause the host to consider the app dead.

### 3.8 App → host calls (beyond the handshake)

All of these are requests you send to `window.parent`; the host may proxy,
fulfill, deny, or gate them behind user approval:

| Method | Purpose |
|---|---|
| `tools/call` | Invoke a tool on YOUR MCP server through the host's authenticated session. This is how in-widget actions (button clicks, form submits) reach your backend without direct network access. |
| `resources/read` / `resources/list` | Read your server's resources through the bridge — a fully host-mediated data path that needs no `connectDomains` at all. |
| `ui/message` | Post a message into the chat on the user's behalf. |
| `ui/update-model-context` | Push structured state into the model's context (e.g. "user selected rows 3–7", "user drew this polygon") so the model can reason about UI state on its next turn. This is the mechanism that closes the loop from UI back to conversation. |
| `ui/open-link` | Ask the host to open a URL in a new tab (user-gesture-gated). |
| `ui/request-display-mode` | Request e.g. fullscreen. Host may decline. |

**App-only tools.** A tool published with `_meta.ui.visibility: ["app"]` is
hidden from the model but callable by your app via `tools/call` — ideal for
high-frequency UI telemetry (viewport reports, autosave) that would pollute
the model's tool list. Default visibility is `["model", "app"]`.

---

## 4. Server implementation guide

Server-side, MCP Apps requires **no special SDK** — it is ordinary MCP with
specific values in specific fields. Four obligations:

### 4.1 Advertise the extension

In your `initialize` response:

```json
"capabilities": {
  "tools": { }, "resources": { },
  "extensions": {
    "io.modelcontextprotocol/ui": {
      "mimeTypes": ["text/html;profile=mcp-app"]
    }
  }
}
```

Only advertise it once the template and metadata are actually served
(advertised == implemented).

### 4.2 Register the template resource

- URI **must** use the `ui://` scheme (e.g. `ui://myapp/widget.html`).
- mimeType **must** be exactly **`text/html;profile=mcp-app`** — plain
  `text/html` is not recognized as an app template.
- Content is a complete HTML5 document, delivered via `resources/read` as
  `text` (or base64 `blob`).
- You **may omit** `ui://` resources from `resources/list` — discovery is
  via tool metadata, and hiding them keeps your resource taxonomy clean.

### 4.3 Stamp tool metadata

Every UI-enabled tool in `tools/list` carries:

```json
"_meta": { "ui": { "resourceUri": "ui://myapp/widget.html" } }
```

Use the **nested** object form. (An older flat `_meta["ui/resourceUri"]`
string key is deprecated.) Optionally add
`"visibility": ["app"]` for app-only tools (§3.8).

### 4.4 Declare the CSP on the read result

Attach `_meta.ui.csp` to the resource **contents** returned by
`resources/read` (see §6 for semantics):

```json
"contents": [{
  "uri": "ui://myapp/widget.html",
  "mimeType": "text/html;profile=mcp-app",
  "text": "<!DOCTYPE html>…",
  "_meta": { "ui": {
    "csp": {
      "connectDomains":  ["https://api.example.com", "wss://api.example.com"],
      "resourceDomains": ["https://api.example.com", "https://cdn.example.com"],
      "frameDomains":    []
    },
    "prefersBorder": true
  } }
}]
```

### 4.5 TypeScript example (official helper SDK)

```ts
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { registerAppTool, registerAppResource,
         RESOURCE_MIME_TYPE } from "@modelcontextprotocol/ext-apps/server";

const server = new McpServer({ name: "my-server", version: "1.0.0" });

registerAppResource(server, "widget", "ui://myapp/widget.html",
  { mimeType: RESOURCE_MIME_TYPE,
    _meta: { ui: { csp: {
      connectDomains: ["https://api.example.com", "wss://api.example.com"],
      resourceDomains: ["https://api.example.com"] } } } },
  async () => ({ contents: [{ uri: "ui://myapp/widget.html",
                              mimeType: RESOURCE_MIME_TYPE,
                              text: TEMPLATE_HTML }] }));

registerAppTool(server, "show_thing",
  { description: "Render the thing inline",
    inputSchema: { id: z.string() },
    _meta: { ui: { resourceUri: "ui://myapp/widget.html" } } },
  async ({ id }) => ({
    content: [{ type: "text", text: JSON.stringify({ id }) }],
    structuredContent: { id, url: `https://api.example.com/things/${id}` },
  }));
```

### 4.6 Python example (FastMCP-style, 1.x SDK)

The 1.x Python SDK's decorators don't expose `_meta` on tools or resource
contents directly. Two workable techniques:

```python
from mcp.server.fastmcp import FastMCP
import mcp.types as types

mcp_server = FastMCP("my-server", stateless_http=True)

UI_URI = "ui://myapp/widget.html"
UI_MIME = "text/html;profile=mcp-app"
UI_TOOLS = {"show_thing"}

@mcp_server.resource(UI_URI, mime_type=UI_MIME)
async def widget_template() -> str:
    return TEMPLATE_HTML  # read from disk in practice

# Technique: wrap the low-level request handlers post-registration
def _wrap_list_tools(orig):
    async def handler(req):
        res = await orig(req)
        for tool in res.root.tools:
            if tool.name in UI_TOOLS:
                meta = dict(tool.meta or {})
                meta.setdefault("ui", {})["resourceUri"] = UI_URI
                tool.meta = meta
        return res
    return handler

def _wrap_read_resource(orig):
    async def handler(req):
        res = await orig(req)
        if str(req.params.uri) == UI_URI:
            ui_meta = {"ui": {"csp": {
                "connectDomains":  ["https://api.example.com",
                                     "wss://api.example.com"],
                "resourceDomains": ["https://api.example.com"]}}}
            # model_copy(update={"_meta": ...}) is the reliable wire path
            res.root.contents = [c.model_copy(update={"_meta": ui_meta})
                                 for c in res.root.contents]
        return res
    return handler

handlers = mcp_server._mcp_server.request_handlers
handlers[types.ListToolsRequest] = _wrap_list_tools(
    handlers[types.ListToolsRequest])
handlers[types.ReadResourceRequest] = _wrap_read_resource(
    handlers[types.ReadResourceRequest])

# Extension advertisement: wrap create_initialization_options and add
# capabilities.extensions["io.modelcontextprotocol/ui"] = {"mimeTypes": [UI_MIME]}
# (ServerCapabilities is extra-tolerant, so the field serializes.)
```

### 4.7 Tool result design

- Put the data the widget needs in **`structuredContent`** (machine-shaped,
  no prose). Duplicate a human-readable summary in a `text` content block.
- If the widget needs to reach your backend directly (streaming, tiles,
  large blobs), include the **URL** in structuredContent and make the
  endpoint capability-addressed (unguessable ID in the path) since sandbox
  requests carry no cookies.
- Be wary of exotic content block types; some hosts reject blocks they don't
  support and inject a visible "not supported" note into the transcript.
  Text + structuredContent is universally safe.

---

## 5. Template implementation guide

### 5.1 Size budget: keep it SMALL

The single most destructive failure mode we found: **a large inline JS
bundle (hundreds of KB, typical single-file build with a bundled SDK +
dependencies) can make the host's sandbox fail to load the document at
all** — the widget area renders a gray broken-page icon and no error appears
anywhere you can see. The template that finally worked everywhere was a few
hundred lines of hand-written HTML+JS.

Rules of thumb:

- Hand-roll the handshake (§3) — it's ~60 lines; you don't need an SDK in
  the template.
- Load heavy libraries (chart/map/3D engines) as **external
  `<script src="https://your-origin/…">`** authorized via
  `resourceDomains`. Self-host them on your own origin so one domain entry
  covers everything and you're immune to CDN/CSP mismatches.
- No sourcemaps, no dev builds, no bundled node_modules in the template.

### 5.2 Annotated skeleton

A complete, production-shaped template (rendering left as a stub):

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>My Widget</title>
<style>
  html, body { margin: 0; height: 100%; background: #111826; }
  #root { position: relative; width: 100%; height: 480px; }
  #status {                                   /* visible status/error line */
    position: absolute; top: 0; left: 0; right: 0; z-index: 10;
    background: rgba(17,24,38,.92); color: #9fb3d1;
    font: 12px/1.6 ui-monospace, monospace; padding: 6px 10px;
  }
  #status.hidden { display: none; }
</style>
</head>
<body>
<div id="root"><div id="status">connecting…</div></div>
<script>
"use strict";
var statusEl = document.getElementById("status");
function setStatus(t){ statusEl.textContent = t; statusEl.classList.remove("hidden"); }
function hideStatus(){ statusEl.classList.add("hidden"); }
function post(m){ window.parent.postMessage(m, "*"); }

/* ---- 1. handshake: APP INITIATES, with retry ---- */
var initDone = false, attempts = 0, pendingId = null;
function tryInit(){
  if (initDone || attempts >= 10) return;
  attempts++; pendingId = "init-" + attempts;
  post({ jsonrpc:"2.0", id: pendingId, method:"ui/initialize",
         params:{ appInfo:{ name:"My Widget", version:"1.0.0" },
                  appCapabilities:{}, protocolVersion:"2026-01-26" }});
  setTimeout(tryInit, 700);
}

function sendSize(h){
  post({ jsonrpc:"2.0", method:"ui/notifications/size-changed",
         params:{ width: Math.ceil(innerWidth) || 800, height: h || 480 }});
}

/* ---- 2. message router ---- */
var toolResultSeen = false;
addEventListener("message", function (ev) {
  var d = ev.data;
  if (!d || typeof d !== "object" || d.jsonrpc !== "2.0") return; // filter noise

  // replies to our requests
  if (d.id !== undefined && (d.result !== undefined || d.error !== undefined)) {
    if (!initDone && d.id === pendingId && !d.error) {
      initDone = true;
      applyHostContext((d.result || {}).hostContext || {});
      post({ jsonrpc:"2.0", method:"ui/notifications/initialized" });
      sendSize();
      setStatus("connected — waiting for data…");
    }
    return;
  }

  // host requests / notifications
  if (d.method) {
    if (d.id !== undefined)            // e.g. ping → must answer
      post({ jsonrpc:"2.0", id: d.id, result: {} });

    if (d.method === "ui/notifications/tool-result" && !toolResultSeen) {
      toolResultSeen = true;
      render(extract(d.params));
    } else if (d.method === "ui/notifications/host-context-changed") {
      applyHostContext(d.params || {});
    }
    // ui/notifications/tool-input-partial arrives repeatedly — ignore it
  }
});

/* ---- 3. data extraction: prefer structuredContent ---- */
function extract(result){
  if (result && result.structuredContent) return result.structuredContent;
  var blocks = (result && result.content) || [];
  for (var i = 0; i < blocks.length; i++)
    if (blocks[i].type === "text")
      try { return JSON.parse(blocks[i].text); } catch (e) {}
  return {};
}

/* ---- 4. your rendering ---- */
function applyHostContext(ctx){
  if (ctx.theme) document.documentElement.dataset.theme = ctx.theme;
}
function render(data){
  if (!data || data.isError) { setStatus("tool reported an error"); return; }
  // Option 1: render purely from `data`
  // Option 2: load your engine + fetch more from your backend:
  //   var s = document.createElement("script");
  //   s.src = "https://your-origin/static/vendor/engine.js";  // resourceDomains
  //   s.onload = function(){ boot(data); };
  //   s.onerror = function(){ setStatus("engine failed to load"); };
  //   document.head.appendChild(s);
  hideStatus();
}

setStatus("starting…");
tryInit();
</script>
</body>
</html>
```

### 5.3 Live data: bridge vs direct network

Two channels for anything beyond the initial tool result:

| Channel | Auth | CSP needed | Push support | Use for |
|---|---|---|---|---|
| **Host bridge** (`tools/call`, `resources/read` via postMessage) | Host's session (secure by construction) | none | request/response only | Actions, secure reads, anything sensitive |
| **Direct network** (fetch/XHR/WS to your origin) | none (no cookies) — design for capability URLs | `connectDomains` (+ `wss://` for sockets) | WebSocket/SSE possible | High-frequency data, streams, tiles, media |

Robust widgets use both: bridge for actions, direct fetch/WS for volume —
**with graceful fallback** (if the WS is blocked by a host's CSP, fall back
to short-interval REST polling; render your empty/base state immediately
rather than gating first paint on data).

### 5.4 UI-to-conversation feedback

When the user does something meaningful in the widget, push it back:

```js
post({ jsonrpc:"2.0", id:"ctx-1", method:"ui/update-model-context",
       params:{ structuredContent:{ userSelection:{ ids:[3,4,5] } } } });
```

Now the model can reason about the selection on its next turn. Without this,
the model only knows "a widget rendered" — nothing about its state.

---

## 6. CSP and sandboxing

The host builds the sandbox CSP from your declaration. Default is
**deny-almost-everything**: no network, no nested frames; inline classic
scripts generally allowed.

| Field | Maps to | Governs | Notes |
|---|---|---|---|
| `connectDomains` | `connect-src` | fetch, XHR, **WebSocket**, EventSource | **Scheme matching is exact for secure schemes: `https://host` does NOT authorize `wss://host`.** List both if you use sockets. |
| `resourceDomains` | `img-src`, `script-src`, `style-src`, `font-src`, `media-src` | external scripts, images, css, fonts | Wildcard subdomains (`https://*.example.com`) supported per spec. |
| `frameDomains` | `frame-src` | nested `<iframe>`s | Omitted/empty ⇒ `frame-src 'none'`. **Host-discretionary — at least one major web host currently ignores it entirely** (§7). Do not architect around nested iframes. |
| `baseUriDomains` | `base-uri` | `<base href>` | Rarely needed. |
| `permissions` | Permission-Policy / iframe `allow` | camera, microphone, geolocation, clipboard-write | Request only what you use; hosts may prompt or deny. |
| `prefersBorder` | — | visual boundary | Hint that the host should draw a border/background around your widget. |
| `domain` | — | dedicated sandbox origin | Host-specific; for OAuth callbacks / stable origins. Consult host docs. |

Additional realities:

- Your **backend** still needs permissive CORS for direct calls from the
  sandbox origin (which you don't control and which may vary):
  `Access-Control-Allow-Origin: *` is appropriate for uncredentialed
  capability-URL endpoints.
- **No cookies.** The sandbox is a foreign origin with storage partitioning;
  never depend on session cookies for widget-originated requests.
- Everything you fetch directly must be served over **https/wss** — mixed
  content is blocked before CSP even applies.

---

## 7. Host compatibility notes

Support is real but uneven; behaviors below were observed mid-2026 and WILL
change. Design defensively.

- **Advertised support:** Claude web/desktop, ChatGPT, Cursor, VS Code
  Copilot, Microsoft 365 Copilot, Goose, Postman, and others per the
  official clients matrix.
- **Observed on a major web host:**
  - `connectDomains` and `resourceDomains` honored **verbatim** (the values
    are even visible as query parameters on the sandbox frame URL —
    a handy debugging signal).
  - `frameDomains` **ignored** — the stock `frame-src` is kept regardless of
    declaration. "Wrap my existing web app in an iframe" does not work
    there; render natively in the template instead.
  - Large inline bundles → document fails to load (gray broken-page icon).
  - Exotic tool-result content blocks (e.g. resource links) → rejected with
    a visible "not currently supported" note.
  - Custom connectors may require a paid plan; local development against
    such hosts typically goes through a public tunnel (e.g.
    `cloudflared tunnel`).
- **Test locally first.** The official `ext-apps` repository ships
  `examples/basic-host` — a minimal local Apps host:
  `SERVERS='["http://localhost:PORT/mcp"]' npm start`. Verifying your server
  + template there separates "my implementation is wrong" from "this host
  has a quirk" before you burn cycles on a production host.

---

## 8. Debugging playbook

### 8.1 Symptom table

| Symptom | Layer | Likely cause | Fix |
|---|---|---|---|
| No widget area at all in the chat | Discovery | extension not advertised, tool `_meta` missing/malformed (flat key?), wrong mimeType | Verify §4.1–4.3 with a raw `initialize` + `tools/list` + `resources/read` against your server |
| Gray broken-page icon in the widget area | Delivery | sandbox failed to LOAD your document — usually template too large or malformed HTML | Strip to a hello-world; bisect content back in |
| Widget area exists but is completely empty/blank | Runtime | frame is 0 px (no `size-changed`) — usually wrong handshake direction — or content below the fold / theme-colored invisible | Instrumented diagnostic template (§8.3); banner pinned top:0 |
| Widget shows but data never arrives | Runtime | CSP blocked fetch/WS (`wss://` missing?), backend CORS, wrong URL extraction | In-page `securitypolicyviolation` listener + parent-console check |
| Widget worked, then breaks after edits | Delivery | host cached an old template, or your new bundle crossed the size threshold | Version string in the on-page log; fresh conversation per test |
| "[This tool call rendered an interactive widget…]" visible in transcript | — | pipeline IS engaged through delivery | failure is inside your template/runtime |

### 8.2 Techniques

1. **Log to the PAGE, not the console.** You often can't conveniently open
   devtools on the inner sandbox frame, and browser find-in-page cannot
   search inside a cross-origin iframe. Print every lifecycle step as
   visible text.
2. **In-page CSP ground truth:**
   ```js
   document.addEventListener("securitypolicyviolation", e =>
     log("VIOLATION dir=" + e.violatedDirective + " blocked=" + e.blockedURI));
   ```
   This tells you the exact directive and blocked origin — no guessing.
3. **Parent-page devtools still help:** CSP violations bubble to the console,
   and the sandbox frame's URL query string often reveals which of your
   declared domains the host actually accepted.
4. **Server-side confirmation:** log `resources/read` hits on the `ui://`
   URI. Proves discovery+delivery even when the screen shows nothing.
5. **One variable per deploy.** Sandbox behavior is a black box; change one
   thing, redeploy, observe, record. Changing template + meta + handshake at
   once teaches you nothing.
6. **Make diagnostics unmissable:** bright banner at `top:0` (short frames
   clip the bottom), high-contrast colors (dark-on-dark vanishes in dark
   themes), version string in the banner.
7. **Fresh conversation per probe** to dodge template/tool caching.

### 8.3 Minimal diagnostic template

Deploy this BEFORE your real UI; it validates all three layers and prints
the tool-result payload shape you'll be parsing:

```html
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>
  html,body{margin:0;background:#0b1220}
  #b{position:fixed;top:0;left:0;right:0;background:#ea580c;color:#fff;
     font:700 18px sans-serif;padding:8px}
  #l{margin-top:48px;color:#a5f3fc;font:13px/1.7 monospace;white-space:pre-wrap;
     word-break:break-all;padding:0 10px}
</style></head><body>
<div id="b">MCP-App diagnostic v1 — template visible</div><div id="l"></div>
<script>
var L=document.getElementById("l"),s=[];function log(x){s.push(x);L.textContent=s.join("\n")}
document.addEventListener("securitypolicyviolation",function(e){
  log("VIOLATION dir="+e.violatedDirective+" blocked="+(e.blockedURI||"?"))});
var done=false,n=0,id=null;
function init(){if(done||n>=10)return;n++;id="init-"+n;
  parent.postMessage({jsonrpc:"2.0",id:id,method:"ui/initialize",
    params:{appInfo:{name:"diag",version:"1"},appCapabilities:{},
            protocolVersion:"2026-01-26"}},"*");
  log("sent:ui/initialize ("+n+")");setTimeout(init,700)}
addEventListener("message",function(ev){var d=ev.data;
  if(!d||d.jsonrpc!=="2.0")return;
  if(d.id!==undefined&&(d.result!==undefined||d.error!==undefined)){
    if(!done&&d.id===id&&!d.error){done=true;
      log("recv:init-result host="+(((d.result||{}).hostInfo||{}).name||"?"));
      parent.postMessage({jsonrpc:"2.0",method:"ui/notifications/initialized"},"*");
      parent.postMessage({jsonrpc:"2.0",method:"ui/notifications/size-changed",
                          params:{width:800,height:480}},"*");
      log("sent:initialized + size-changed")}
    return}
  if(d.method){
    if(d.method!=="ui/notifications/tool-input-partial")log("recv:"+d.method);
    if(d.id!==undefined)parent.postMessage({jsonrpc:"2.0",id:d.id,result:{}},"*");
    if(d.method==="ui/notifications/tool-result")
      log("  params="+JSON.stringify(d.params).slice(0,400))}});
log("js:start");init();
</script></body></html>
```

Expected happy path in the widget:

```
js:start
sent:ui/initialize (1)
recv:init-result host=<hostname>
sent:initialized + size-changed
recv:ui/notifications/tool-input
recv:ui/notifications/tool-result
  params={"content":[…],"structuredContent":{…}}
```

Once you see that, your pipeline works end-to-end. Swap in the real UI and
add CSP domains one at a time, watching for VIOLATION lines.

---

## 9. Landmines and gotchas

Ranked roughly by hours they cost in practice:

1. **Handshake direction.** The APP sends `ui/initialize`; the host answers.
   Backwards = an invisible 0-px ghost with zero errors anywhere.
2. **`size-changed` is not optional.** No notification → no height →
   invisible. And it's YOUR job to resend on content growth.
3. **Inline bundle size.** Big single-file builds break document delivery
   silently. Hand-roll the handshake; externalize libraries.
4. **CSP scheme exactness.** `https://x` ≠ `wss://x` in `connect-src`.
   Declare both, or your WebSocket dies with a violation while all your
   fetches work.
5. **`frameDomains` may be ignored.** Never make "embed my existing page in
   a nested iframe" your only plan. Native rendering in the template is the
   portable architecture.
6. **The template is re-hosted, not fetched from you.** Your HTTP headers on
   it don't matter; the host's caching does. Version-stamp your template.
7. **Exotic content blocks get rejected visibly.** Some hosts inject "not
   currently supported" text for block types they don't handle. Stick to
   text + structuredContent for UI tools.
8. **No cookies in the sandbox.** Direct calls are unauthenticated; use
   capability URLs / tokens carried in the tool result. Never rely on an
   existing login session.
9. **`tool-input-partial` spam.** Streamed argument fragments arrive many
   times per call. Logging or re-rendering per message will wreck
   performance and readability.
10. **Dark-theme invisibility & below-the-fold status text.** During
    debugging, dark-on-dark content and vertically-centered messages in a
    short frame both read as "nothing rendered". Pin diagnostics to top:0 in
    loud colors.
11. **Find-in-page can't see you.** Ctrl-F doesn't search cross-origin
    iframes; absence of your text in a page search proves nothing.
12. **Unanswered host pings.** Requests (with `id`) from the host need
    replies or the app may be considered dead.
13. **Unvalidated postMessage traffic.** The window receives unrelated
    messages (devtools, host internals). Filter on `jsonrpc === "2.0"` and,
    when responding, correlate ids strictly.

---

## 10. Production checklist

**Server**

- [ ] `capabilities.extensions["io.modelcontextprotocol/ui"]` advertised with
      `mimeTypes: ["text/html;profile=mcp-app"]`
- [ ] Template resource on a `ui://` URI, mimeType exactly
      `text/html;profile=mcp-app`
- [ ] `_meta.ui.resourceUri` (nested form) on every UI-enabled tool
- [ ] `_meta.ui.csp` on the read result: `connectDomains` (incl. `wss://` if
      sockets), `resourceDomains`; `frameDomains` only if genuinely needed
- [ ] UI tools also return complete text + `structuredContent`
      (graceful degradation everywhere)
- [ ] Backend endpoints hit directly from the widget: CORS open,
      capability-addressed, cookie-free, https/wss

**Template**

- [ ] Small (tens of KB, not hundreds); libraries external via
      `resourceDomains`
- [ ] App-initiated `ui/initialize` with retry; protocol `2026-01-26`
- [ ] `initialized` + `size-changed` sent after handshake; resize handling
      for dynamic content
- [ ] Renders from `ui/notifications/tool-result` (structuredContent first);
      handles `isError`
- [ ] Answers host requests (ping); ignores `tool-input-partial`
- [ ] Theme handling from `hostContext` / `host-context-changed`
- [ ] Fallbacks: base state paints immediately; WS falls back to polling;
      every failure prints a visible status line
- [ ] `ui/update-model-context` for meaningful user interactions

**Process**

- [ ] Verified against a local Apps host (`ext-apps` `basic-host`) before a
      production host
- [ ] Diagnostic template (§8.3) kept in the repo for future regressions
- [ ] One-variable-per-deploy discipline; version string visible in-widget
- [ ] Fresh conversation per test run

---

## 11. References

- **Spec:** modelcontextprotocol.io → extensions → apps
  (`specification/2026-01-26/apps.mdx`) — authoritative for the resource
  format, `_meta` fields, CSP semantics, and the sandbox-proxy contract.
- **Official examples & SDK:** `github.com/modelcontextprotocol/ext-apps` —
  the `App` class (template side), `registerAppTool`/`registerAppResource`
  (TS server side), several example servers, and `examples/basic-host` for
  local verification.
- **Extension mechanism:** MCP capability extensions (SEP-1724) — how
  `capabilities.extensions` negotiation works in general.
