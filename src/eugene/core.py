from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import inspect
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

from eugene.models import AppletRecord, Attachment, ConversationTurn, Event, Message, ScheduledTask, ToolDefinition, TriggerDefinition


EventHandler = Callable[[Event], Awaitable[None]]


class FieldSpec(BaseModel):
    default: Any = None
    description: str = ""
    options: list[str] | None = None
    dynamic_source: str | None = None


class AppletBase:
    name = "base"
    description = ""
    load = "lazy"
    inject = "selective"
    mcp_start = "lazy"
    supported_extensions: list[str] = []
    requires_mcp = False
    can_disable = True
    model: str | None = None

    class Config:
        fields: dict[str, FieldSpec] = {}

    def __init__(self, record: AppletRecord, services: "ServiceContainer") -> None:
        self.record = record
        self.services = services
        self.config = record.config
        self.logger = logger.bind(component="applet", applet=self.name)

    async def on_load(self) -> None:
        return None

    async def on_unload(self) -> None:
        return None

    async def on_message(self, message: Message) -> None:
        return None

    async def on_event(self, event: Event) -> None:
        return None

    async def handle_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        raise NotImplementedError(f"{self.name} does not implement tool {name}")

    def get_tools(self) -> list[ToolDefinition]:
        return []

    def get_trigger_definitions(self) -> list[TriggerDefinition]:
        return []

    def get_context_injection(self) -> str:
        return ""

    def get_scheduled_tasks(self) -> list[ScheduledTask]:
        return []

    def get_routes(self) -> list[tuple[str, APIRouter]]:
        return []

    async def handle_file(self, attachment_ref: str) -> Attachment | None:
        return None

    async def emit_trigger(self, signal_name: str, payload: dict[str, Any] | None = None) -> None:
        proactive = getattr(self.services, "proactive", None)
        if proactive is None:
            raise RuntimeError("Proactive trigger service is not available")
        await proactive.emit(applet_name=self.name, signal_name=signal_name, payload=payload or {})


class ChannelBase:
    name = "base"

    def __init__(self, services: "ServiceContainer") -> None:
        self.services = services
        self.logger = logger.bind(component="channel", channel=self.name)

    async def on_start(self) -> None:
        raise NotImplementedError

    async def on_stop(self) -> None:
        raise NotImplementedError

    async def normalize(self, raw: Any) -> Message:
        raise NotImplementedError

    async def send(self, response: str, session_id: str, metadata: dict[str, Any] | None = None) -> None:
        raise NotImplementedError


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._running = False

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._subscribers[event_type].append(handler)
        logger.bind(component="event_bus", event_type=event_type).debug("Handler subscribed")

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        logger.bind(component="event_bus", event_type=event_type).debug("Event queued")
        await self._queue.put(Event(event_type=event_type, payload=payload))

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        logger.bind(component="event_bus").info("Event bus started")
        self._worker = asyncio.create_task(self._drain())

    async def stop(self) -> None:
        self._running = False
        if self._worker:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
        logger.bind(component="event_bus").info("Event bus stopped")

    async def _drain(self) -> None:
        while self._running:
            event = await self._queue.get()
            logger.bind(component="event_bus", event_type=event.event_type).debug("Event dequeued")
            handlers = list(self._subscribers.get(event.event_type, []))
            if not handlers:
                logger.bind(component="event_bus", event_type=event.event_type).debug("No handlers for event")
                continue
            results = await asyncio.gather(*(handler(event) for handler in handlers), return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.bind(component="event_bus", event_type=event.event_type).exception("Event handler failed")


class WorkingMemory:
    def __init__(self, max_turns: int) -> None:
        self.max_turns = max_turns
        self._turns: dict[str, deque[ConversationTurn]] = defaultdict(lambda: deque(maxlen=max_turns))
        self._summaries: dict[str, str] = {}

    def add_turn(self, session_id: str, role: str, content: str) -> None:
        self._turns[session_id].append(ConversationTurn(role=role, content=content))

    def get_window(self, session_id: str) -> list[dict[str, str]]:
        turns = [turn.model_dump(mode="json") for turn in self._turns[session_id]]
        summary = self._summaries.get(session_id)
        if summary:
            return [{"role": "system", "content": f"Working memory summary:\n{summary}"}] + turns
        return turns

    def set_summary(self, session_id: str, summary: str) -> None:
        self._summaries[session_id] = summary

    def get_summary(self, session_id: str) -> str | None:
        return self._summaries.get(session_id)

    def clear_session(self, session_id: str) -> None:
        self._turns.pop(session_id, None)
        self._summaries.pop(session_id, None)


@dataclass
class ServiceContainer:
    config: Any
    event_bus: EventBus
    compressor: Any = None
    frontend_reload: Any = None
    applets: Any = None
    channels: Any = None
    provider: Any = None
    personality: Any = None
    memory: Any = None
    scheduler: Any = None
    proactive: Any = None
    mcp: Any = None
    files: Any = None
    core: Any = None


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def discover_subclass(path: Path, base_type: type[Any]) -> type[Any] | None:
    module = load_module(path)
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, base_type) and obj is not base_type:
            return obj
    return None
