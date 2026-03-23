from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from eugene.api import authenticate_websocket, build_api_router, websocket_message
from eugene.config import STATIC_DIR, ensure_runtime_dirs, load_config
from eugene.core import EventBus, ServiceContainer
from eugene.services import AppletManager, ChannelManager, EugeneCore, FileHandler, MCPManager, MemoryService, PersonalityService, ProviderService, SchedulerService


@dataclass
class AppState:
    services: ServiceContainer


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_runtime_dirs()
    config = load_config()
    event_bus = EventBus()
    services = ServiceContainer(config=config, event_bus=event_bus)
    services.personality = PersonalityService(services)
    services.provider = ProviderService(services)
    services.memory = MemoryService(services)
    services.mcp = MCPManager(services)
    services.applets = AppletManager(services)
    services.channels = ChannelManager(services)
    services.scheduler = SchedulerService(services)
    services.files = FileHandler(services)
    services.core = EugeneCore(services)
    app.state.app_state = AppState(services=services)

    await event_bus.start()
    await services.provider.initialize()
    await services.personality.start()
    await services.memory.initialize()
    await services.scheduler.initialize()
    await services.applets.scan()
    await services.channels.scan()
    await services.applets.load_route_applets()
    await services.mcp.start_eager()
    for prefix, router in services.applets.routes():
        app.include_router(router, prefix=prefix)
    await services.channels.start()
    await services.scheduler.start()
    yield
    await services.scheduler.stop()
    await services.channels.stop()
    await services.mcp.stop()
    await services.personality.stop()
    await event_bus.stop()


app = FastAPI(title="Eugene", lifespan=lifespan)
app.include_router(build_api_router())


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    app_state: AppState = websocket.app.state.app_state
    await authenticate_websocket(websocket, app_state.services.config.api_key)
    await websocket.accept()
    app_state.services.channels.register_websocket(session_id, websocket)
    try:
        while True:
            payload = await websocket.receive_json()
            message = websocket_message(payload["text"], session_id, attachments=payload.get("attachments"))
            await app_state.services.event_bus.publish("message.received", {"message": message.model_dump(mode="json")})
    except WebSocketDisconnect:
        app_state.services.channels.unregister_websocket(session_id)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
