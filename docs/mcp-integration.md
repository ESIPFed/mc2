# MCP Integration Guide

This guide explains how to connect the **Map Control MCP server** to Claude (Desktop, Cline/VS Code, Claude.ai web) or OpenAI so that AI assistants can create and control live maps.

---

## Prerequisites

1. **Node.js 18+** installed
2. **Map Control proxy server** running (this is the FastAPI backend that actually serves the maps)

### Start the proxy server

```bash
# From the project root
cd server
pip install -e ".[dev]"
uvicorn mapcontrol_server.main:app --reload --port 8888
```

The proxy server will be available at `http://localhost:8888`.

---

## Step 1: Build the MCP Server

The MCP server lives in a separate directory. Clone/copy it, install dependencies, and build:

```bash
cd /path/to/mapcontrol-mcp
npm install
npm run build
```

This compiles TypeScript to `build/index.js`.

---

## Step 2: Choose Your Transport

The MCP server supports three transports:

| Transport | Use Case | MCP Spec | How Client Connects |
|-----------|----------|----------|-------------------|
| **Stdio** | Claude Desktop, Cline (VS Code) | All versions | Client launches the process directly |
| **Streamable HTTP** ⭐ | Claude.ai web, OpenAI Agents SDK, remote clients | **2025-11-25** (current) | Client POSTs to a single `/mcp` endpoint |
| **SSE (legacy)** | Old clients that haven't upgraded | 2024 (deprecated) | Client connects to `/sse` + `/message` endpoints |

> **Recommended:** Use **Streamable HTTP** for all remote/web connections. It's the current MCP standard (2025-11-25 spec), supported by both Claude.ai and OpenAI's Agents SDK. The old HTTP+SSE transport is deprecated but available via `--sse-legacy` for backwards compatibility.

---

## Option A: Claude Desktop (Stdio)

### Configuration File Location

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

### Add the MCP Server

Edit the config file and add the `mapcontrol` entry under `mcpServers`:

```json
{
  "mcpServers": {
    "mapcontrol": {
      "command": "node",
      "args": [
        "/absolute/path/to/mapcontrol-mcp/build/index.js"
      ],
      "env": {
        "MAPCONTROL_SERVER_URL": "http://localhost:8888"
      }
    }
  }
}
```

> **Important:** Use the absolute path to `build/index.js`. Replace `/absolute/path/to/mapcontrol-mcp/` with the actual location on your machine.

### Restart Claude Desktop

After saving the config, fully quit and reopen Claude Desktop. The map control tools will appear in Claude's tool list.

---

## Option B: Cline / VS Code (Stdio)

### Configuration File Location

Cline stores MCP settings at:
- **macOS:** `~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`
- **Windows:** `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json`

Or configure via: **Cline sidebar → MCP Servers → Edit Config**

### Add the MCP Server

```json
{
  "mcpServers": {
    "mapcontrol": {
      "autoApprove": [],
      "disabled": false,
      "timeout": 60,
      "type": "stdio",
      "command": "node",
      "args": [
        "/absolute/path/to/mapcontrol-mcp/build/index.js"
      ],
      "env": {
        "MAPCONTROL_SERVER_URL": "http://localhost:8888"
      }
    }
  }
}
```

### Restart Cline

After saving, restart the Cline extension (or reload the VS Code window). The map tools will appear in the MCP tools list.

---

## Option C: Claude.ai Web / OpenAI / Remote Clients (Streamable HTTP) ⭐

For Claude.ai web, OpenAI Agents SDK, or any remote client, the MCP server runs as an HTTP server with the **Streamable HTTP** transport (MCP spec 2025-11-25).

### How Streamable HTTP Works

Unlike the old HTTP+SSE transport (which required two endpoints), Streamable HTTP uses a **single `/mcp` endpoint**:

- **POST `/mcp`** — All MCP communication (initialize, tool calls, etc.)
- **GET `/mcp`** — Optional SSE stream for server-initiated messages
- **DELETE `/mcp`** — Session termination
- Sessions are identified by the `Mcp-Session-Id` header (set automatically by clients)

### Start the MCP Server in Streamable HTTP Mode

```bash
# Option 1: Using the --http flag (defaults to port 3100)
node /path/to/mapcontrol-mcp/build/index.js --http

# Option 2: Using the --sse flag (also starts Streamable HTTP — backwards-compatible flag)
node /path/to/mapcontrol-mcp/build/index.js --sse

# Option 3: Using an environment variable for a custom port
HTTP_PORT=3100 node /path/to/mapcontrol-mcp/build/index.js
```

The server will print:
```
Map Control MCP server running on Streamable HTTP at http://localhost:3100
  MCP endpoint: http://localhost:3100/mcp
  Health check: http://localhost:3100/health
  Transport: Streamable HTTP (MCP spec 2025-11-25)
  Map Control server: http://localhost:8888
```

### Expose via Cloudflare Tunnel (for Claude.ai)

Claude.ai needs a public URL. Use Cloudflare Tunnel:

```bash
# Install cloudflared (macOS)
brew install cloudflare/cloudflare/cloudflared

# Create a quick tunnel
cloudflared tunnel --url http://localhost:3100
```

This gives you a URL like `https://some-random-name.trycloudflare.com`.

### Add to Claude.ai

In Claude.ai settings → Integrations → Add MCP Server:
- **URL:** `https://your-tunnel-url.trycloudflare.com/mcp`

### Add to OpenAI Agents SDK

```python
from agents import Agent
from agents.mcp import MCPServerStreamableHTTP

mcp_server = MCPServerStreamableHTTP(url="http://localhost:3100/mcp")

agent = Agent(
    name="map-agent",
    instructions="You can create and control maps.",
    mcp_servers=[mcp_server],
)
```

### Health Check

Verify the server is running:
```bash
curl http://localhost:3100/health
```

Expected response:
```json
{
  "status": "ok",
  "transport": "streamable-http",
  "spec": "2025-11-25",
  "active_sessions": 0,
  "mapcontrol_server": "http://localhost:8888"
}
```

---

## Option D: Legacy SSE Transport (Deprecated)

> ⚠️ **Deprecated:** The old HTTP+SSE transport is kept for backwards compatibility with clients that haven't upgraded to the 2025 MCP spec. Use Streamable HTTP (Option C) for new integrations.

```bash
# Start with the legacy SSE transport
node /path/to/mapcontrol-mcp/build/index.js --sse-legacy
```

This starts the old-style server with separate endpoints:
- `GET /sse` — SSE stream connection
- `POST /message?sessionId=...` — JSON-RPC messages

For Claude.ai with the legacy transport, point to: `https://your-tunnel-url/sse`

---

## Using a Remote / EC2 Deployment

If the proxy server is deployed to AWS EC2 (or any remote host), just change `MAPCONTROL_SERVER_URL` to point to the public IP instead of localhost.

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mapcontrol": {
      "command": "node",
      "args": [
        "/absolute/path/to/mapcontrol-mcp/build/index.js"
      ],
      "env": {
        "MAPCONTROL_SERVER_URL": "http://<EC2_PUBLIC_IP>:8080"
      }
    }
  }
}
```

Replace `<EC2_PUBLIC_IP>` with the actual public IP of your EC2 instance (e.g., `http://100.53.219.245:8080`).

### Cline / VS Code

Same change in the Cline MCP settings — just update the `MAPCONTROL_SERVER_URL` value.

### Finding Your EC2 Public IP

```bash
aws ec2 describe-instances \
    --filters "Name=tag:Name,Values=mapcontrol" "Name=instance-state-name,Values=running" \
    --query "Reservations[0].Instances[0].PublicIpAddress" \
    --output text --region us-east-1
```

> **Note:** EC2 public IPs can change when instances are stopped/started. For a stable URL, assign an Elastic IP or use a domain name.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MAPCONTROL_SERVER_URL` | `http://localhost:8888` | URL of the Map Control proxy server |
| `HTTP_PORT` | — | If set, runs Streamable HTTP transport on this port |
| `SSE_PORT` | — | Alias for HTTP_PORT (backwards-compatible) |

## CLI Flags

| Flag | Description |
|------|-------------|
| `--http` | Start Streamable HTTP transport (port 3100 by default) |
| `--sse` | Same as `--http` (backwards-compatible flag) |
| `--sse-legacy` | Start deprecated HTTP+SSE transport |
| *(none)* | Stdio transport (default — for Claude Desktop, Cline) |

---

## Available Tools (20)

Once connected, the AI assistant will have access to these tools:

| Tool | Description |
|------|-------------|
| `create_map` | Create a new map and get a browser URL |
| `add_polygon` | Add a polygon from GeoJSON |
| `add_path` | Add a line/path from GeoJSON |
| `add_point` | Add a point marker from GeoJSON |
| `zoom_to_point` | Zoom to a lat/lon with animation |
| `zoom_to_assets` | Zoom to fit specific assets in view |
| `zoom_out` | Zoom out by N levels |
| `list_assets` | List all assets on the map |
| `delete_asset` | Remove an asset |
| `set_visibility` | Show/hide one or more assets |
| `update_style` | Change fill color, stroke, etc. |
| `set_basemap` | Switch between OSM and satellite |
| `get_viewport` | Get current map center/zoom/bbox |
| `add_geotiff_rgb` | Add RGB GeoTIFF overlay from URL |
| `add_geotiff_singleband` | Add single-band GeoTIFF with colormap |
| `upload_geotiff` | Upload GeoTIFF from local file/base64 |
| `take_screenshot` | Capture map as PNG |
| `list_drawn_assets` | List user-drawn shapes |
| `enable_drawing` | Enable drawing mode in browser |
| `disable_drawing` | Disable drawing mode |

---

## Quick Test

After setup, try asking Claude:

> "Create a map and add a polygon around downtown Austin, Texas. Then zoom to it and switch to satellite view."

Claude will:
1. Call `create_map` → gives you a URL to open in the browser
2. Call `add_polygon` with GeoJSON for Austin
3. Call `zoom_to_assets` to frame it
4. Call `set_basemap` with "satellite"

---

## Troubleshooting

### "No active map" error
The AI needs to call `create_map` first in each conversation. The MCP server tracks one active map per session.

### Tools not appearing
- **Claude Desktop:** Make sure you fully quit and reopened (not just closed the window)
- **Cline:** Reload the VS Code window or restart the extension
- Check that `build/index.js` exists (run `npm run build` if not)

### Connection refused
Make sure the Map Control proxy server is running on the configured port:
```bash
curl http://localhost:8888/docs
```

### Streamable HTTP session issues
Each client conversation creates a new MCP session identified by `Mcp-Session-Id`. If you see session errors, the client may need to re-initialize.

### Legacy SSE connection drops
The legacy SSE server sends keepalive pings every 25 seconds. If you're behind a proxy with a shorter timeout, use the Streamable HTTP transport instead (it doesn't need keepalives).

---

## Architecture Summary

```
Claude / OpenAI / Any MCP Client
    ↓ MCP protocol (stdio, Streamable HTTP, or legacy SSE)
MCP Server (Node.js, @modelcontextprotocol/sdk v1.29)
    ↓ REST API calls
Map Control Proxy Server (FastAPI, port 8888)
    ↓ WebSocket broadcast
OpenLayers Map Frontend (browser)
```

The MCP server is a thin wrapper — it translates MCP tool calls into REST API requests to the proxy server, which persists state and pushes updates to connected browsers via WebSocket.

---

## Transport Comparison

| Feature | Stdio | Streamable HTTP (2025) | SSE Legacy (2024) |
|---------|-------|----------------------|-------------------|
| **Spec version** | All | 2025-11-25 | 2024 (deprecated) |
| **Endpoints** | N/A (stdin/stdout) | Single `/mcp` | `/sse` + `/message` |
| **Session ID** | N/A | `Mcp-Session-Id` header | Query parameter |
| **SSE streaming** | N/A | Optional (server choice) | Required |
| **Keepalives** | N/A | Not needed | Needed (25s interval) |
| **Claude Desktop** | ✅ | — | — |
| **Cline (VS Code)** | ✅ | — | — |
| **Claude.ai web** | — | ✅ | ⚠️ (compat mode) |
| **OpenAI Agents SDK** | — | ✅ | ❌ |
| **Flag** | *(default)* | `--http` | `--sse-legacy` |
