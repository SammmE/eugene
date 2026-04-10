from __future__ import annotations

from eugene.core import ChannelBase
from eugene.models import Message

try:
    import provider_sdk  # type: ignore
except ImportError:  # pragma: no cover
    provider_sdk = None


class ExampleChannel(ChannelBase):
    name = "example"

    def __init__(self, services) -> None:
        super().__init__(services)
        self.client = None

    async def on_start(self) -> None:
        config = self.services.config.channels.get(self.name)
        if not config or not config.enabled or not config.token:
            return None
        if provider_sdk is None:
            raise RuntimeError("provider_sdk is not installed")

        client = provider_sdk.Client(token=config.token)
        self.client = client

        async def handle_inbound(raw_event) -> None:
            message = await self.normalize(raw_event)
            await self.services.event_bus.publish(
                "message.received",
                {"message": message.model_dump(mode="json")},
            )

        client.on_message(handle_inbound)
        await client.connect()

    async def on_stop(self) -> None:
        if self.client is not None:
            await self.client.close()

    async def normalize(self, raw_event) -> Message:
        return Message(
            text=str(raw_event.text),
            source_channel=self.name,
            session_id=str(raw_event.conversation_id),
            attachments=[],
            metadata={},
        )

    async def send(
        self,
        response: str,
        session_id: str,
        metadata: dict | None = None,
    ) -> None:
        if self.client is None:
            return
        await self.client.send_message(
            conversation_id=session_id,
            text=response,
        )
