"""Server-rendered HTML pages for the standalone auth portal.

Plain Python string templates (no Jinja2 — zero new template deps; every
interpolated value goes through ``html.escape``). The visual system follows
**shadcn/ui conventions built with Tailwind**: hsl design tokens, hairline
borders, rounded-xl cards, restrained shadows — mapped onto the ESIP
Federation palette (deep ocean-navy surfaces, ESIP blue primary, leaf-green
accents). Dark by default, light via ``prefers-color-scheme``.

The stylesheet is COMPILED Tailwind, committed at ``static/portal.css``.
Source of truth: ``server/portal.tw.css`` + ``server/tailwind.config.js`` —
rebuild with the command documented in either file after editing class names
here. No Node in the runtime image or the dev loop (ADR-0001).

Interactivity is a few lines of dependency-free vanilla JS (tabs, copy
buttons, the connection verifier) inlined at the end of the page.
"""

from __future__ import annotations

from html import escape

# ─── Brand mark (inline SVG, ESIP blue→green gradient globe) ─────────────────

_LOGO = """<svg class="logo" width="26" height="26" viewBox="0 0 26 26" fill="none" aria-hidden="true">
  <defs><linearGradient id="eg" x1="0" y1="0" x2="26" y2="26">
    <stop offset="0" stop-color="#4f8fd0"/><stop offset="1" stop-color="#6ebe4a"/>
  </linearGradient></defs>
  <circle cx="13" cy="13" r="11" stroke="url(#eg)" stroke-width="2"/>
  <ellipse cx="13" cy="13" rx="5" ry="11" stroke="url(#eg)" stroke-width="1.5"/>
  <path d="M2.5 9.5h21M2.5 16.5h21" stroke="url(#eg)" stroke-width="1.5"/>
</svg>"""

_LOGO_LG = _LOGO.replace('width="26" height="26"', 'width="44" height="44"')

# Small monochrome glyphs for the client tabs (16px, currentColor).
_ICON_CHAT = """<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M14 8A6 6 0 1 1 4.7 3.1L2 2.5l.9 2.4A6 6 0 0 1 14 8Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" transform="rotate(180 8 8)"/></svg>"""
_ICON_TERM = """<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="1.5" y="2.5" width="13" height="11" rx="2" stroke="currentColor" stroke-width="1.4"/><path d="m4.5 6 2.5 2-2.5 2M8.5 10.5h3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>"""
_ICON_SPARK = """<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 1.5 9.6 6 14 8l-4.4 2L8 14.5 6.4 10 2 8l4.4-2L8 1.5Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>"""
_ICON_PLUG = """<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M5 2v3.5M11 2v3.5M4 5.5h8v2a4 4 0 0 1-3 3.9V14H7v-2.6a4 4 0 0 1-3-3.9v-2Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>"""
_ICON_GRID = """<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="2" y="2" width="5" height="5" rx="1.2" stroke="currentColor" stroke-width="1.4"/><rect x="9" y="2" width="5" height="5" rx="1.2" stroke="currentColor" stroke-width="1.4"/><rect x="2" y="9" width="5" height="5" rx="1.2" stroke="currentColor" stroke-width="1.4"/><rect x="9" y="9" width="5" height="5" rx="1.2" stroke="currentColor" stroke-width="1.4"/></svg>"""


def _nav(user=None) -> str:
    """Top navbar: brand at left; account controls at right."""
    right = ""
    if user is not None:
        admin_link = (
            '<a class="nav-link" href="/admin">Admin</a>'
            if user.can_approve else ""
        )
        right = f"""
      <div class="nav-right">
        {admin_link}
        <span class="nav-user">{escape(user.username)}
          <span class="role-chip role-{escape(user.role)}">{escape(user.role)}</span></span>
        <a class="nav-link" href="/logout">Log out</a>
      </div>"""
    return f"""
  <nav class="topbar">
    <a class="brand" href="/">
      {_LOGO}
      <span class="brand-name">ESIP Map Control</span>
      <span class="brand-sub">MCP server</span>
    </a>
    {right}
  </nav>"""


def _page(title: str, body: str, user=None, scripts: str = "") -> str:
    return f"""<!doctype html>
<html lang="en"><head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} — ESIP Map Control</title>
  <link rel="stylesheet" href="/static/portal.css">
</head><body>
{_nav(user)}
  <main class="wrap">
{body}
  </main>
  <footer class="footer">
    <p>ESIP Map Control — a remotely controllable map exposed over the Model
       Context Protocol.</p>
    <p class="cite">In collaboration with the
       <a href="https://www.esipfed.org" rel="noopener">Earth Science Information
       Partners (ESIP) Federation</a>.</p>
  </footer>
{scripts}
</body></html>"""


def _flash(error: str = "", notice: str = "") -> str:
    parts = []
    if error:
        parts.append(f'<div class="flash flash-error">{escape(error)}</div>')
    if notice:
        parts.append(f'<div class="flash flash-notice">{escape(notice)}</div>')
    return "\n".join(parts)


# ─── Login / signup ───────────────────────────────────────────────────────────

def login_page(error: str = "", notice: str = "", next_url: str = "/") -> str:
    body = f"""
    <div class="card auth-card">
      <div class="text-center mb-5">{_LOGO_LG}</div>
      <h1 class="text-center">Sign in</h1>
      <p class="muted small text-center mt-1">Access the ESIP Map Control MCP server.</p>
      {_flash(error, notice)}
      <form method="post" action="/login">
        <input type="hidden" name="next" value="{escape(next_url)}">
        <label>Username
          <input name="username" autocomplete="username" required autofocus>
        </label>
        <label>Password
          <input type="password" name="password" autocomplete="current-password" required>
        </label>
        <button type="submit" class="btn btn-primary btn-block">Sign in</button>
      </form>
      <p class="muted small text-center mt-5">No account? <a href="/signup">Register</a> —
         an administrator approves new accounts.</p>
    </div>"""
    return _page("Sign in", body)


def signup_page(error: str = "") -> str:
    body = f"""
    <div class="card auth-card">
      <div class="text-center mb-5">{_LOGO_LG}</div>
      <h1 class="text-center">Create account</h1>
      <p class="muted small text-center mt-1">An administrator must approve it
         before you can sign in and connect MCP clients.</p>
      {_flash(error)}
      <form method="post" action="/signup">
        <label>Username
          <input name="username" autocomplete="username" required autofocus
                 pattern="[A-Za-z0-9_.-]{{3,32}}"
                 title="3-32 characters: letters, digits, _ . -">
        </label>
        <label>Password
          <input type="password" name="password" autocomplete="new-password"
                 required minlength="8">
        </label>
        <label>Confirm password
          <input type="password" name="password2" autocomplete="new-password"
                 required minlength="8">
        </label>
        <button type="submit" class="btn btn-primary btn-block">Create account</button>
      </form>
      <p class="muted small text-center mt-5"><a href="/">Back to sign in</a></p>
    </div>"""
    return _page("Register", body)


def signup_done_page() -> str:
    body = f"""
    <div class="card auth-card text-center">
      <div class="mb-5">{_LOGO_LG}</div>
      <h1>Request received</h1>
      <p class="mt-3">Your account is <span class="status-chip status-pending">pending</span>
         approval. An administrator will review it — try signing in later.</p>
      <p class="muted small mt-5"><a href="/">Back to sign in</a></p>
    </div>"""
    return _page("Registered", body)


# ─── Dashboard (post-login: how to connect MCP clients) ──────────────────────

_TOOLS: list[tuple[str, str]] = [
    ("create_map", "New live map; returns a browser URL"),
    ("add_geojson", "Points, paths & polygons with styling"),
    ("add_geotiff", "RGB or single-band rasters from a URL"),
    ("zoom", "Fly to a point, bbox, or asset"),
    ("set_basemap", "Streets, satellite, terrain, …"),
    ("set_theme", "Light / dark / auto chrome"),
    ("update_style", "Restyle an existing layer"),
    ("set_visibility", "Show / hide layers"),
    ("list_assets", "Inventory of everything on the map"),
    ("delete_asset", "Remove a layer"),
    ("get_viewport", "Current center / zoom / bearing"),
    ("take_screenshot", "Server-side PNG of the live map"),
]


def _copy_pre(code: str, pid: str) -> str:
    """A <pre> block with its own copy button."""
    return f"""<pre><code id="{pid}">{code}</code><button type="button"
      class="copy-btn" data-copy="{pid}">Copy</button></pre>"""


def _guide(steps: list[tuple[str, str]]) -> str:
    """Numbered guide steps: (headline_html, detail_html)."""
    out = ['<div class="guide">']
    for i, (head, detail) in enumerate(steps, 1):
        detail_html = f"<p>{detail}</p>" if detail else ""
        out.append(f"""<div class="guide-step">
          <span class="guide-num">{i}</span>
          <div class="guide-body">{head}{detail_html}</div>
        </div>""")
    out.append("</div>")
    return "\n".join(out)


# Tabs / copy / verify — dependency-free vanilla JS. NOT an f-string:
# literal braces stay literal.
_DASH_JS = """
<script>
(function () {
  // Tabs
  var tabs = document.querySelectorAll('.tab');
  tabs.forEach(function (t) {
    t.addEventListener('click', function () {
      tabs.forEach(function (o) { o.setAttribute('aria-selected', 'false'); });
      document.querySelectorAll('.tabpanel').forEach(function (p) {
        p.classList.remove('active');
      });
      t.setAttribute('aria-selected', 'true');
      document.getElementById(t.dataset.panel).classList.add('active');
    });
  });

  // Copy buttons ("Copy" -> "Copied ✓")
  document.querySelectorAll('[data-copy]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var el = document.getElementById(btn.dataset.copy);
      navigator.clipboard.writeText(el.textContent.trim()).then(function () {
        btn.classList.add('copied');
        var old = btn.textContent;
        btn.textContent = 'Copied ✓';
        setTimeout(function () {
          btn.classList.remove('copied');
          btn.textContent = old;
        }, 1600);
      });
    });
  });

  // Connection verifier
  var vbtn = document.getElementById('verify-btn');
  if (!vbtn) return;
  function mark(id, ok, hint) {
    var dot = document.getElementById('vd-' + id);
    var h = document.getElementById('vh-' + id);
    dot.classList.remove('ok', 'fail');
    dot.classList.add(ok ? 'ok' : 'fail');
    dot.textContent = ok ? '✓' : '✕';
    h.textContent = hint || '';
  }
  vbtn.addEventListener('click', function () {
    ['prm', 'auth', 'as'].forEach(function (id) {
      var dot = document.getElementById('vd-' + id);
      dot.classList.remove('ok', 'fail');
      dot.textContent = '…';
      document.getElementById('vh-' + id).textContent = '';
    });
    fetch('/.well-known/oauth-protected-resource')
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function () { mark('prm', true, 'RFC 9728 OK'); })
      .catch(function (e) { mark('prm', false, 'HTTP ' + e); });
    fetch('/mcp', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      .then(function (r) {
        var ok = r.status === 401 && r.headers.get('WWW-Authenticate');
        mark('auth', !!ok, ok ? '401 + challenge' :
          (r.status === 401 ? 'No WWW-Authenticate header' : 'Expected 401, got ' + r.status));
      })
      .catch(function () { mark('auth', false, 'Network error'); });
    fetch('/.well-known/oauth-authorization-server')
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (j) {
        var ok = j.authorization_endpoint && j.token_endpoint;
        mark('as', !!ok, ok ? 'RFC 8414 OK' : 'Missing endpoints');
      })
      .catch(function (e) { mark('as', false, 'HTTP ' + e); });
  });
})();
</script>"""


def dashboard_page(user, base_url: str) -> str:
    b = escape(base_url.rstrip("/"))
    mcp_url = f"{b}/mcp"

    first_prompt = ("Create a map of the Gulf of Mexico on a satellite basemap, "
                    "zoom to New Orleans, then take a screenshot.")

    # ── Per-client tab panels ──
    claude_web = _guide([
        (f"Copy the endpoint URL above — <code>{mcp_url}</code>.", ""),
        ("In Claude, open <b>Settings&nbsp;→ Connectors&nbsp;→ Add custom connector</b>.",
         "On claude.ai this lives under your profile menu. Connectors may need "
         "to be enabled for your account or organization plan."),
        ("Name it <i>ESIP Map Control</i>, paste the URL, and click <b>Add</b>.",
         "Claude fetches the discovery documents automatically — there is no "
         "client ID or secret to fill in (it registers itself via RFC 7591)."),
        ("Complete the sign-in when Claude opens this portal.",
         "You'll land on the consent page — review the request and click "
         "<b>Approve</b>. The window closes and the connector turns green."),
        ("Enable the tools in a chat.",
         "In a new conversation, open the connectors menu, toggle "
         "<i>ESIP Map Control</i> on, and ask Claude to make a map."),
    ]) + f"""
      <h2 class="mt-8">Try it first</h2>
      {_copy_pre(escape(first_prompt), "fp-web")}
      <div class="note"><b>Troubleshooting:</b> if tool calls start failing with
        401 after some time, your token expired — disable and re-enable the
        connector to re-run the OAuth flow. If the connector won't add at all,
        run the connection check above.</div>"""

    claude_code = f"""
      <p class="muted text-[14px] mt-2">Register the server over streamable HTTP:</p>
      {_copy_pre(escape(f"claude mcp add --transport http esip-map {mcp_url}"), "cc-add")}
    """ + _guide([
        ("Run the command above in your terminal.",
         "This writes the server into your Claude Code MCP config under the "
         "name <code>esip-map</code>."),
        ("Authenticate on first use.",
         "The first tool call triggers the OAuth flow: your browser opens this "
         "portal — sign in and click <b>Approve</b>. Tokens are cached locally."),
        ("Check the connection.",
         "Inside Claude Code, run <code>/mcp</code> to list servers and their "
         "auth state; <code>/mcp auth esip-map</code> re-authenticates."),
    ]) + f"""
      <h2 class="mt-8">Try it first</h2>
      {_copy_pre(escape(first_prompt), "fp-cc")}
      <div class="note"><b>Troubleshooting:</b> <code>claude mcp list</code>
        shows configured servers. Remove and re-add with
        <code>claude mcp remove esip-map</code> if the endpoint changes.</div>"""

    chatgpt = _guide([
        ("Enable Developer mode.",
         "In ChatGPT: <b>Settings → Connectors → Advanced → Developer mode</b>. "
         "This is required for custom MCP connectors and may need a Plus/Pro "
         "or workspace-admin-enabled plan."),
        (f"Create a connector with MCP server URL <code>{mcp_url}</code>.",
         "Choose <b>OAuth</b> as the authentication method. Leave client "
         "credentials empty — the server supports Dynamic Client Registration."),
        ("Approve the connection.",
         "ChatGPT redirects to this portal; sign in and click <b>Approve</b>. "
         "You'll be sent back and the connector shows as connected."),
        ("Use it in a conversation.",
         "Attach the connector via the tools menu in the composer, then ask "
         "for a map."),
    ]) + f"""
      <h2 class="mt-8">Try it first</h2>
      {_copy_pre(escape(first_prompt), "fp-gpt")}
      <div class="note"><b>Troubleshooting:</b> if the connector reports an
        auth error, remove it and re-add — stale tokens aren't refreshed
        automatically in developer mode.</div>"""

    cline = f"""
      <p class="muted text-[14px] mt-2">Add to <code>cline_mcp_settings.json</code>
        (MCP Servers → Configure):</p>
      {_copy_pre(escape('{'
          f'\n  "mcpServers": {{'
          f'\n    "esip-map": {{'
          f'\n      "type": "streamableHttp",'
          f'\n      "url": "{mcp_url}"'
          '\n    }\n  }\n}'), "cl-cfg")}
    """ + _guide([
        ("Paste the config and save.",
         "Cline picks up the file change and starts the connection."),
        ("Complete the OAuth flow.",
         "Cline detects the 401 challenge, opens this portal in your browser — "
         "sign in and <b>Approve</b>. The server row turns green."),
        ("Verify the tools appeared.",
         "Expand the server in the MCP panel: you should see all 12 tools "
         "listed and ready."),
    ]) + f"""
      <h2 class="mt-8">Try it first</h2>
      {_copy_pre(escape(first_prompt), "fp-cl")}"""

    generic = f"""
      <p class="muted text-[14px] mt-2">Any client speaking <b>MCP Streamable
        HTTP</b> with OAuth 2.1 discovery works. The server implements:</p>
      <div class="guide mt-4">
        <div class="guide-step"><span class="guide-num">·</span>
          <div class="guide-body">RFC 9728 protected-resource metadata —
            <a href="{b}/.well-known/oauth-protected-resource">/.well-known/oauth-protected-resource</a></div></div>
        <div class="guide-step"><span class="guide-num">·</span>
          <div class="guide-body">RFC 8414 AS metadata —
            <a href="{b}/.well-known/oauth-authorization-server">/.well-known/oauth-authorization-server</a></div></div>
        <div class="guide-step"><span class="guide-num">·</span>
          <div class="guide-body">RFC 7591 Dynamic Client Registration, PKCE
            (S256), authorization-code flow with consent at
            <code>/consent</code></div></div>
      </div>
      <p class="muted text-[14px] mt-5">Flow: <code>POST /mcp</code> → 401 with
        <code>WWW-Authenticate</code> → discovery → register → authorize (user
        signs in here) → token → authenticated MCP session. Scope:
        <code>mcp:use</code>.</p>"""

    tools_html = "\n".join(
        f'<div class="tool"><code>{escape(name)}</code><p>{escape(desc)}</p></div>'
        for name, desc in _TOOLS
    )

    body = f"""
    <div class="hero">
      <h1>Connected. Point your MCP client here.</h1>
      <p class="lede">This server exposes live-map tools — create maps, add
         GeoJSON &amp; GeoTIFF layers, zoom, restyle, screenshot — over the
         Model Context Protocol, secured with OAuth&nbsp;2.1.</p>
      <div class="endpoint-row">
        <code id="mcp-url">{mcp_url}</code>
        <button type="button" class="btn btn-ghost" data-copy="mcp-url">Copy</button>
      </div>
    </div>

    <div class="steps">
      <div class="step"><span class="step-num">1</span>
        <h3>Sign in</h3><p>You're here — this portal is the identity layer for
        the map server.</p></div>
      <div class="step"><span class="step-num">2</span>
        <h3>Approve your client</h3><p>Add the endpoint to your AI client; it
        sends you back here once to authorize.</p></div>
      <div class="step"><span class="step-num">3</span>
        <h3>Control live maps</h3><p>Your AI drives a real map in the browser —
        layers, styling, screenshots.</p></div>
    </div>

    <div class="tabs">
      <h2 class="mb-3">Set up your client</h2>
      <div class="tablist" role="tablist">
        <button class="tab" role="tab" aria-selected="true"  data-panel="p-web">{_ICON_CHAT} Claude.ai</button>
        <button class="tab" role="tab" aria-selected="false" data-panel="p-cc">{_ICON_TERM} Claude Code</button>
        <button class="tab" role="tab" aria-selected="false" data-panel="p-gpt">{_ICON_SPARK} ChatGPT</button>
        <button class="tab" role="tab" aria-selected="false" data-panel="p-cl">{_ICON_PLUG} Cline</button>
        <button class="tab" role="tab" aria-selected="false" data-panel="p-gen">{_ICON_GRID} Other clients</button>
      </div>
      <div class="tabpanel active card" id="p-web" role="tabpanel">{claude_web}</div>
      <div class="tabpanel card" id="p-cc"  role="tabpanel">{claude_code}</div>
      <div class="tabpanel card" id="p-gpt" role="tabpanel">{chatgpt}</div>
      <div class="tabpanel card" id="p-cl"  role="tabpanel">{cline}</div>
      <div class="tabpanel card" id="p-gen" role="tabpanel">{generic}</div>
    </div>

    <div class="card mt-8">
      <div class="flex items-center justify-between gap-4">
        <div>
          <h2>Connection check</h2>
          <p class="muted small mt-0.5">Probes this server's MCP endpoints from
             your browser.</p>
        </div>
        <button type="button" id="verify-btn" class="btn btn-ghost">Verify</button>
      </div>
      <div class="mt-4">
        <div class="verify-row"><span class="verify-dot" id="vd-prm">·</span>
          OAuth discovery <span class="muted small">(protected-resource metadata)</span>
          <span class="verify-hint" id="vh-prm"></span></div>
        <div class="verify-row"><span class="verify-dot" id="vd-auth">·</span>
          Auth challenge on <code>/mcp</code> <span class="muted small">(401 expected)</span>
          <span class="verify-hint" id="vh-auth"></span></div>
        <div class="verify-row"><span class="verify-dot" id="vd-as">·</span>
          Authorization server metadata
          <span class="verify-hint" id="vh-as"></span></div>
      </div>
    </div>

    <div class="mt-10">
      <h2>The 12 tools</h2>
      <p class="muted small mt-0.5">All coordinates are EPSG:4326 (lon, lat).</p>
      <div class="toolgrid">{tools_html}</div>
    </div>"""
    return _page("Dashboard", body, user=user, scripts=_DASH_JS)


# ─── Consent (the OAuth /authorize gate) ──────────────────────────────────────

def consent_page(user, txn: str, client_name: str, scope: str) -> str:
    body = f"""
    <div class="card auth-card">
      <div class="text-center mb-5">{_LOGO_LG}</div>
      <h1 class="text-center">Authorize access</h1>
      <p class="mt-4"><b>{escape(client_name)}</b> is requesting access to
         control live maps on your behalf.</p>
      <p class="muted small mt-2">Scope: <code>{escape(scope)}</code> ·
         Signed in as <b>{escape(user.username)}</b></p>
      <form method="post" action="/consent" class="consent-actions">
        <input type="hidden" name="txn" value="{escape(txn)}">
        <button type="submit" name="decision" value="approve"
                class="btn btn-primary flex-1">Approve</button>
        <button type="submit" name="decision" value="deny"
                class="btn btn-danger flex-1">Deny</button>
      </form>
    </div>"""
    return _page("Authorize", body, user=user)


def consent_gone_page(user=None) -> str:
    body = """
    <div class="card auth-card text-center">
      <h1>Authorization request expired</h1>
      <p class="muted mt-3">Please restart the connection from your MCP client.</p>
    </div>"""
    return _page("Expired", body, user=user)


# ─── Admin ────────────────────────────────────────────────────────────────────

def admin_page(user, users: list, error: str = "", notice: str = "") -> str:
    rows = []
    for u in users:
        actions = []
        if u.username != "admin":
            if u.status == "pending":
                actions.append(_admin_btn(u.username, "approve", "Approve", "btn-primary"))
            if u.status == "approved":
                actions.append(_admin_btn(u.username, "disable", "Disable", "btn-danger"))
            if u.status == "disabled":
                actions.append(_admin_btn(u.username, "approve", "Re-enable", "btn-primary"))
            # Only the admin account manages the approver role.
            if user.is_admin:
                if u.role == "user":
                    actions.append(_admin_btn(u.username, "make_approver", "Make approver", "btn-ghost"))
                elif u.role == "approver":
                    actions.append(_admin_btn(u.username, "revoke_approver", "Revoke approver", "btn-ghost"))
        rows.append(f"""
        <tr>
          <td class="font-medium">{escape(u.username)}</td>
          <td><span class="role-chip role-{escape(u.role)}">{escape(u.role)}</span></td>
          <td><span class="status-chip status-{escape(u.status)}">{escape(u.status)}</span></td>
          <td class="muted small">{escape((u.created_at or "")[:10])}</td>
          <td class="actions">{' '.join(actions)}</td>
        </tr>""")
    body = f"""
    <h1>User administration</h1>
    <p class="muted mt-1">Approve pending registrations, disable accounts{
        ", and grant the approver role" if user.is_admin else ""}.</p>
    {_flash(error, notice)}
    <div class="card mt-6">
      <table class="users">
        <thead><tr>
          <th>User</th><th>Role</th><th>Status</th><th>Created</th><th></th>
        </tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>"""
    return _page("Admin", body, user=user)


def _admin_btn(username: str, action: str, label: str, cls: str) -> str:
    return f"""<form method="post" action="/admin/users" class="inline">
      <input type="hidden" name="username" value="{escape(username)}">
      <input type="hidden" name="action" value="{escape(action)}">
      <button type="submit" class="btn btn-sm {cls}">{escape(label)}</button>
    </form>"""


def forbidden_page(user=None) -> str:
    body = """
    <div class="card auth-card text-center">
      <h1>Not allowed</h1>
      <p class="muted mt-3">You don't have permission to view that page.</p>
      <p class="mt-4"><a href="/">Back to the dashboard</a></p>
    </div>"""
    return _page("Forbidden", body, user=user)
