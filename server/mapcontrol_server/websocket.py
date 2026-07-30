"""WebSocket connection manager for real-time event broadcasting."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from fastapi import WebSocket

logger = logging.getLogger(__name__)


@dataclass
class ConnectionInfo:
    websocket: WebSocket
    map_id: str
    user_session_id: str


class ConnectionManager:
    """Manages WebSocket connections grouped by map_id."""

    def __init__(self):
        # map_id -> list of ConnectionInfo
        self._connections: dict[str, list[ConnectionInfo]] = {}

    async def connect(self, websocket: WebSocket, map_id: str, user_session_id: str):
        """Accept a WebSocket connection and register it."""
        await websocket.accept()
        info = ConnectionInfo(
            websocket=websocket,
            map_id=map_id,
            user_session_id=user_session_id,
        )
        if map_id not in self._connections:
            self._connections[map_id] = []
        self._connections[map_id].append(info)
        logger.info(f"WebSocket connected: map={map_id} session={user_session_id}")

    def disconnect(self, websocket: WebSocket, map_id: str):
        """Remove a WebSocket connection."""
        if map_id in self._connections:
            self._connections[map_id] = [
                c for c in self._connections[map_id] if c.websocket != websocket
            ]
            if not self._connections[map_id]:
                del self._connections[map_id]
        logger.info(f"WebSocket disconnected: map={map_id}")

    async def broadcast_to_map(self, map_id: str, message: dict):
        """Send a message to all connections on a given map."""
        if map_id not in self._connections:
            return

        dead = []
        data = json.dumps(message)
        for conn in self._connections[map_id]:
            try:
                await conn.websocket.send_text(data)
            except Exception:
                dead.append(conn)

        # Clean up dead connections
        for conn in dead:
            self._connections[map_id].remove(conn)

    async def send_to_session(self, map_id: str, user_session_id: str, message: dict):
        """Send a message to a specific user session on a map."""
        if map_id not in self._connections:
            return

        data = json.dumps(message)
        for conn in self._connections[map_id]:
            if conn.user_session_id == user_session_id:
                try:
                    await conn.websocket.send_text(data)
                except Exception:
                    pass

    def get_connection_count(self, map_id: str) -> int:
        """Get the number of active connections for a map."""
        return len(self._connections.get(map_id, []))

    def has_session_connection(self, map_id: str, user_session_id: str) -> bool:
        """Check if a specific session has an active WebSocket connection."""
        if map_id not in self._connections:
            return False
        return any(
            c.user_session_id == user_session_id
            for c in self._connections[map_id]
        )


# Singleton instance
manager = ConnectionManager()
