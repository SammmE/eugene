from __future__ import annotations

from eugene.core import ChannelBase
from eugene.models import Message

try:
    import discord  # type: ignore
except ImportError:  # pragma: no cover
    discord = None


class DiscordChannel(ChannelBase):
    name = "discord"

    def __init__(self, services) -> None:
        super().__init__(services)
        self.client = None

    async def on_start(self) -> None:
        config = self.services.config.channels.get("discord")
        if not config or not config.token:
            return None
        if discord is None:
            raise RuntimeError("discord.py is not installed")
        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self.client = client

        @client.event
        async def on_message(raw_message):
            if raw_message.author.bot:
                return
            message = await self.normalize(raw_message)
            await self.services.event_bus.publish("message.received", {"message": message.model_dump(mode="json")})

        await client.login(config.token)
        await client.connect(reconnect=True)

    async def on_stop(self) -> None:
        if self.client is not None:
            await self.client.close()

    async def normalize(self, raw) -> Message:
        return Message(text=str(raw.content), source_channel="discord", session_id=str(raw.channel.id), attachments=[], metadata={"author_id": str(raw.author.id)})

    async def send(self, response: str, session_id: str, metadata: dict | None = None) -> None:
        if self.client is None:
            return
        channel = self.client.get_channel(int(session_id))
        if channel is not None:
            await channel.send(response)
