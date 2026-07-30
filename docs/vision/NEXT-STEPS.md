# ESIP MCP — Handoff & Next Step

**Updated:** 2026-06-22 · **Full plan:** `vision/mcp-compliance-roadmap.md`

## Where we are (Phase 0 = DONE, shipped)
- In-process MCP server mounted at `/mcp` (Streamable HTTP / FastMCP) on the existing
  FastAPI proxy — additive, wraps the same event/session/asset services. 11 consolidated tools.
- Merged: **PR #6 → `experimental`** (squash, commit `5949db7`). Branch `feature/mcp-router` deleted.
- CI run `27990034478` = **success** → new `ghcr.io/ama-labs/esip-mapcontrol:ex-latest` pushed →
  Watchtower (30s poll) auto-deploys to EC2 `18.116.107.200:8000`.
- Verified locally via `server/tests/test_mcp.py` (8/8 checks).

### Verify the deploy actually landed (run when resuming)
```bash
# Should flip from 404 (old image) to 400/406/200 (MCP live) within a few min of merge:
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://18.116.107.200:8000/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"x","version":"0"}}}'
```
(404 = Watchtower hasn't pulled yet; wait and re-poll. Anything non-404 = MCP is deployed.)

## GitHub / account gotcha (important for next session)
- The repo is `AMA-Labs/ESIP-MapControlMCP`. **Only the `isConic` gh account can access it** (ADMIN).
  `otto-AMA` is in org `AMA-Intelligence` (different org) and **cannot** see it; `owagner-atayo` can't either.
- So for THIS repo: `gh auth switch --user isConic` before any push/PR/merge.
  Git author email stays `otto.wagner@ama-inc.com` (already set on the repo).
- Shell tip: never inline multi-line `--body`; use `--body-file /tmp/...md`. Avoid `gh run watch` (interactive); poll `gh run view <id> --json status,conclusion` in a loop.

## NEXT STEP — Phase 1: Edge hardening (low effort, closes the real hole)
Goal: stop `:8000` being world-open + unauthenticated, with zero disruption. No app-auth yet.

1. **Lock the security group `sg-09b05996a3efdb463`** (region `us-east-2`): today ports
   22/80/443/5173/8000/8888/8900/8901/8903 are all `0.0.0.0/0`. Decide the minimal public
   surface; keep service-to-service on the instance/Docker network; restrict SSH(22) to known IPs.
   ⚠️ Coordinated step: EOGPT currently reaches ESIP at the public IP, so closing `:8000`
   means pointing eogpt-server/-client + browser at the right surface first.
2. **CORS** in `server/mapcontrol_server/main.py`: change `allow_origins=["*"]` to a known list.
3. **MCP transport hardening** (already wired behind env knobs in `mcp_tools.py`, just flip on):
   - `MAPCONTROL_MCP_DNS_REBINDING_PROTECTION=1` + `MAPCONTROL_MCP_ALLOWED_ORIGINS` / `MAPCONTROL_MCP_ALLOWED_HOSTS`
     → Origin/Host mismatch returns 4xx (report §5a). Set these in the EC2 `--env-file`.
   - Verify `MCP-Protocol-Version` handling + `Mcp-Session-Id` semantics.
4. **Fix capability over-advertise** (advertised==implemented invariant): FastMCP advertises
   `resources`/`prompts`/`experimental` though only `tools` is implemented. Suppress the
   unimplemented ones (or implement them in Phase 4+).
5. **Tiny cleanup:** `SyntaxWarning: invalid escape sequence '\s'` in `main.py` (`/^MapTiler\s+/`
   inside the inline HTML f-string) → make it `\\s`.

**Decision still open (Phase 1 edge strategy):** (a) tighten SG + internal networking only
[default, no new infra], (b) Cloudflare Access in front, or (c) self-hosted reverse proxy.

## Later phases (see roadmap)
- **Phase 2** §5b auth on `/mcp`: OAuth 2.1 Resource Server, RFC 9728 PRM, scoped M2M tokens
  (default: ESIP self-issued signed tokens). Prod is pure M2M — no interactive OAuth needed yet.
- **Phase 3** CI conformance harness (build on `server/tests/test_mcp.py`).
- **Phase 4+** Resources reframe (§6), Prompts/Completion/Logging, utilities.

## Portal UI — deferred ideas (from the 2026-07 UI overhaul)
The portal (login/dashboard/admin, `server/mapcontrol_server/portal/`) shipped
a Tailwind/shadcn-style redesign. Deliberately deferred to a later iteration:
- **Live map hero:** embed a real map from this server (iframe to `/map/{id}`,
  auto-created demo map, slow drift over satellite, dark theme) on the
  dashboard — the product demoing itself. Cheap; needs a public demo map with
  sensible GC.
- **Per-user client/token management:** list a user's authorized OAuth
  clients ("Claude.ai — connected 2 days ago") with revoke buttons. Requires
  token enumeration/revocation plumbing in `portal/provider.py` (tokens are
  currently in-memory in the toy AS; persistence first).
- **Per-tool mini-docs:** expand each tool chip (details/summary) with args +
  a one-line example, so the dashboard doubles as lightweight API docs. Keep
  in sync with `mcp_tools.py` docstrings.
- CSS build note: `static/portal.css` is COMPILED Tailwind (committed).
  Source: `server/portal.tw.css` + `server/tailwind.config.js`; rebuild
  command documented in both files. Don't hand-edit the compiled file.

## Housekeeping
- ⚠️ **Rotate the AWS SSO credentials** pasted earlier in chat — treat as compromised.
- `/tmp/*.py`, `/tmp/*.sh`, `/tmp/*.md` probe/test scaffolding can be discarded.
