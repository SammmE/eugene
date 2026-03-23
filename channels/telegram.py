from __future__ import annotations

from eugene.core import ChannelBase
from eugene.models import Message

try:
    from telegram import Update  # type: ignore
    from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters  # type: ignore
except ImportError:  # pragma: no cover
    Update = None
    ApplicationBuilder = None
    ContextTypes = None
    MessageHandler = None
    filters = None


class TelegramChannel(ChannelBase):
    name = "telegram"

    def __init__(self, services) -> None:
        super().__init__(services)
        self.application = None

    async def on_start(self) -> None:
        config = self.services.config.channels.get("telegram")
        if not config or not config.token:
            return None
        if ApplicationBuilder is None or MessageHandler is None or filters is None:
            raise RuntimeError("python-telegram-bot is not installed")
        self.application = ApplicationBuilder().token(config.token).build()

        async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if update.effective_message is None or update.effective_chat is None:
                return
            message = await self.normalize(update)
            await self.services.event_bus.publish("message.received", {"message": message.model_dump(mode="json")})

        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()

    async def on_stop(self) -> None:
        if self.application is not None:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()

    async def normalize(self, raw) -> Message:
        return Message(text=str(raw.effective_message.text), source_channel="telegram", session_id=str(raw.effective_chat.id), attachments=[])

    async def send(self, response: str, session_id: str, metadata: dict | None = None) -> None:
        if self.application is not None:
            await self.application.bot.send_message(chat_id=int(session_id), text=response)
