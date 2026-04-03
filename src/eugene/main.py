from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from loguru import logger

from eugene.api import authenticate_websocket, build_api_router, websocket_message
from eugene.config import STATIC_DIR, ensure_runtime_dirs, load_config
from eugene.core import EventBus, ServiceContainer
from eugene.logging_utils import setup_logging
from eugene.services import AppletManager, ChannelManager, EugeneCore, FileHandler, FrontendReloadService, MCPManager, MemoryService, PromptCompressionService, ProviderService, SchedulerService


@dataclass
class AppState:
    services: ServiceContainer


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_runtime_dirs()
    config = load_config()
    setup_logging(
        level=config.log_level,
        log_file=config.log_file,
        rotation=config.log_rotation,
        retention=config.log_retention,
        serialize=config.log_json,
    )
    logger.bind(component="startup").info("Starting Eugene host={host} port={port}", host=config.host, port=config.port)
    event_bus = EventBus()
    services = ServiceContainer(config=config, event_bus=event_bus)
    services.compressor = PromptCompressionService(services)
    services.frontend_reload = FrontendReloadService(services)
    services.provider = ProviderService(services)
    services.memory = MemoryService(services)
    services.mcp = MCPManager(services)
    services.mcp.load_registry()
    services.applets = AppletManager(services)
    services.channels = ChannelManager(services)
    services.scheduler = SchedulerService(services)
    services.files = FileHandler(services)
    services.core = EugeneCore(services)
    app.state.app_state = AppState(services=services)

    await event_bus.start()
    await services.provider.initialize()
    await services.memory.initialize()
    await services.compressor.initialize()
    await services.scheduler.initialize()
    await services.applets.scan()
    await services.channels.scan()
    await services.applets.load_route_applets()
    await services.mcp.start_eager()
    for prefix, router in services.applets.routes():
        app.include_router(router, prefix=prefix)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    await services.channels.start()
    await services.scheduler.start()
    await services.frontend_reload.start()
    logger.bind(component="startup").info("Eugene startup complete")
    yield
    logger.bind(component="shutdown").info("Shutting down Eugene")
    await services.frontend_reload.stop()
    await services.scheduler.stop()
    await services.channels.stop()
    await services.mcp.stop()
    await event_bus.stop()
    logger.bind(component="shutdown").info("Eugene shutdown complete")


app = FastAPI(title="Eugene", lifespan=lifespan)
app.include_router(build_api_router())


@app.websocket("/ws/frontend-reload")
async def frontend_reload_websocket(websocket: WebSocket) -> None:
    app_state: AppState = websocket.app.state.app_state
    await websocket.accept()
    app_state.services.frontend_reload.register_client(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        app_state.services.frontend_reload.unregister_client(websocket)


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    app_state: AppState = websocket.app.state.app_state
    ws_logger = logger.bind(component="websocket", session_id=session_id)
    ws_logger.info("WebSocket authentication start")
    is_authenticated = await authenticate_websocket(websocket, app_state.services.config.api_key)
    if not is_authenticated:
        return
    await websocket.accept()
    ws_logger.info("WebSocket connected")
    app_state.services.channels.register_websocket(session_id, websocket)
    try:
        while True:
            payload = await websocket.receive_json()
            ws_logger.debug("WebSocket payload received keys={keys}", keys=sorted(payload.keys()))
            message = websocket_message(payload["text"], session_id, attachments=payload.get("attachments"))
            await app_state.services.event_bus.publish("message.received", {"message": message.model_dump(mode="json")})
    except WebSocketDisconnect:
        ws_logger.info("WebSocket disconnected")
        app_state.services.channels.unregister_websocket(session_id)

