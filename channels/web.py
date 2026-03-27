from __future__ import annotations

import asyncio

from eugene.core import ChannelBase
from eugene.models import Message


class WebChannel(ChannelBase):
    name = "web"
    stream_chunk_chars = 40

    async def on_start(self) -> None:
        return None

    async def on_stop(self) -> None:
        return None

    async def normalize(self, raw: dict) -> Message:
        return Message(text=raw["text"], source_channel="web", session_id=raw["session_id"], attachments=raw.get("attachments", []))

    async def send(self, response: str, session_id: str, metadata: dict | None = None) -> None:
        websocket = self.services.channels.web_sessions.get(session_id)
        if websocket is not None:
            if response:
                for i in range(0, len(response), self.stream_chunk_chars):
                    chunk = response[i : i + self.stream_chunk_chars]
                    await websocket.send_json({"type": "message.delta", "delta": chunk, "metadata": metadata or {}})
                    # Yield to the event loop so the client can paint incrementally.
                    await asyncio.sleep(0)
            await websocket.send_json({"type": "message.response", "text": response, "metadata": metadata or {}})
