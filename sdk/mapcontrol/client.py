"""MapControl — top-level entry point for the SDK."""

from __future__ import annotations

import httpx

from .session import MapSession
from .exceptions import ServerError, ConnectionError as MCConnectionError


class MapControl:
    """Client for the Map Control proxy server.

    Usage:
        mc = MapControl(server_url="http://localhost:8080")
        session = mc.create_map()
        print(session.url)
        session.add_polygon(geojson='{"type":"Feature",...}')
    """

    def __init__(self, server_url: str = "http://localhost:8080"):
        self.server_url = server_url.rstrip("/")
        self._client = httpx.Client(base_url=self.server_url, timeout=30.0)

    def create_map(self, theme: str = "auto") -> MapSession:
        """Create a new map and return a MapSession for it.

        Args:
            theme: Map-level UI theme — 'light', 'dark', or 'auto' (default;
                follows each viewer's OS/browser color-scheme preference).
                Changeable later with ``session.set_theme(...)``.
        """
        resp = self._request("POST", "/api/maps", json={"theme": theme})
        map_id = resp["map_id"]
        url = resp["url"]

        # Auto-create a user session
        sess_resp = self._request("POST", f"/api/maps/{map_id}/sessions")

        return MapSession(
            client=self._client,
            server_url=self.server_url,
            map_id=map_id,
            map_url=url,
            user_session_id=sess_resp["user_session_id"],
            session_url=sess_resp["url"],
        )

    def connect_map(self, map_id: str) -> MapSession:
        """Connect to an existing map by ID."""
        # Verify map exists
        info = self._request("GET", f"/api/maps/{map_id}")

        # Create a new user session
        sess_resp = self._request("POST", f"/api/maps/{map_id}/sessions")

        return MapSession(
            client=self._client,
            server_url=self.server_url,
            map_id=map_id,
            map_url=f"{self.server_url}/map/{map_id}",
            user_session_id=sess_resp["user_session_id"],
            session_url=sess_resp["url"],
        )

    def delete_map(self, map_id: str) -> bool:
        """Delete a map and all its data."""
        resp = self._client.delete(f"/api/maps/{map_id}")
        return resp.status_code == 204

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """Make an HTTP request and return parsed JSON."""
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.ConnectError as e:
            raise MCConnectionError(f"Cannot connect to server at {self.server_url}") from e

        if resp.status_code >= 400:
            detail = resp.text
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                pass
            raise ServerError(resp.status_code, detail)

        if resp.status_code == 204:
            return {}
        return resp.json()

    def close(self):
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
