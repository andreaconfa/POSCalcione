# app/ws.py
import json
from typing import Set
from fastapi import WebSocket

class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        try:
            self.active_connections.discard(websocket)
        except Exception:
            pass

    async def broadcast_text(self, message: str):
        """Invia testo a tutti i client connessi; rimuove quelli morti."""
        dead = []
        for ws in list(self.active_connections):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_json(self, payload: dict):
        """Invia JSON a tutti i client connessi."""
        await self.broadcast_text(json.dumps(payload))


manager = ConnectionManager()
