from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import aiosqlite
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile, WebSocket
from pydantic import BaseModel

from eugene.config import DATA_DIR
from eugene.models import Message


class AppletToggleRequest(BaseModel):
    enabled: bool


class AppletConfigUpdateRequest(BaseModel):
    values: dict[str, Any]


def build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    def get_app_state(request: Request) -> Any:
        return request.app.state.app_state

    async def require_api_key(
        request: Request,
        x_api_key: Annotated[str | None, Header(alias="x-api-key")] = None,
        api_key: Annotated[str | None, Query(alias="api_key")] = None,
    ) -> None:
        supplied = x_api_key or api_key
        app_state = get_app_state(request)
        if supplied != app_state.services.config.api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

    @router.get("/health")
    async def health(app_state: Any = Depends(get_app_state), _: None = Depends(require_api_key)) -> dict[str, Any]:
        provider = app_state.services.provider.check_configuration()
        return {
            "ok": provider.ok,
            "provider": provider.model_dump(),
            "channels": {name: status.model_dump() for name, status in app_state.services.channels.statuses().items()},
        }

    @router.get("/config")
    async def get_config(app_state: Any = Depends(get_app_state), _: None = Depends(require_api_key)) -> dict[str, Any]:
        config = app_state.services.config.model_dump()
        config["api_key"] = "***"
        return config

    @router.get("/applets")
    async def list_applets(app_state: Any = Depends(get_app_state), _: None = Depends(require_api_key)) -> list[dict[str, Any]]:
        return [item.model_dump(exclude={"instance"}) for item in app_state.services.applets.registry.values()]

    @router.post("/applets/{name}")
    async def toggle_applet(name: str, payload: AppletToggleRequest, app_state: Any = Depends(get_app_state), _: None = Depends(require_api_key)) -> dict[str, Any]:
        record = app_state.services.applets.registry[name]
        if not payload.enabled and not record.can_disable:
            raise HTTPException(status_code=400, detail="This applet cannot be disabled")
        record.enabled = payload.enabled
        record.status = "disabled" if not payload.enabled else record.status
        if not payload.enabled:
            await app_state.services.applets.unload_applet(name)
        elif record.load == "eager":
            await app_state.services.applets.load_applet(name)
        return {"name": name, "enabled": record.enabled}

    @router.get("/applets/{name}/config")
    async def applet_config(name: str, app_state: Any = Depends(get_app_state), _: None = Depends(require_api_key)) -> dict[str, Any]:
        record = app_state.services.applets.registry[name]
        schema = record.config_schema.copy()
        for key, meta in schema.items():
            if meta["dynamic_source"]:
                meta["options"] = app_state.services.applets.dynamic_options(meta["dynamic_source"])
        return {"schema": schema, "values": record.config}

    @router.post("/applets/{name}/config")
    async def update_applet_config(
        name: str,
        payload: AppletConfigUpdateRequest,
        app_state: Any = Depends(get_app_state),
        _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        record = app_state.services.applets.registry[name]
        record.config.update(payload.values)
        path = DATA_DIR / "applet_configs" / f"{name}.json"
        path.write_text(json.dumps(record.config, indent=2, sort_keys=True), encoding="utf-8")
        if name in app_state.services.applets.instances:
            await app_state.services.applets.unload_applet(name)
            await app_state.services.applets.load_applet(name)
        return {"name": name, "values": record.config}

    @router.get("/channels")
    async def list_channels(app_state: Any = Depends(get_app_state), _: None = Depends(require_api_key)) -> dict[str, Any]:
        return {name: status.model_dump() for name, status in app_state.services.channels.statuses().items()}

    @router.get("/schedules")
    async def list_schedules(app_state: Any = Depends(get_app_state), _: None = Depends(require_api_key)) -> list[dict[str, Any]]:
        return [item.model_dump() for item in app_state.services.scheduler.tasks.values()]

    @router.get("/token-usage")
    async def token_usage(app_state: Any = Depends(get_app_state), _: None = Depends(require_api_key)) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        async with aiosqlite.connect(app_state.services.provider.db_path) as db:
            cursor = await db.execute("select timestamp, model, prompt_tokens, completion_tokens, estimated_cost, origin from token_usage order by id desc limit 100")
            async for row in cursor:
                rows.append(
                    {
                        "timestamp": row[0],
                        "model": row[1],
                        "prompt_tokens": row[2],
                        "completion_tokens": row[3],
                        "estimated_cost": row[4],
                        "origin": row[5],
                    }
                )
        return rows

    @router.get("/history/{session_id}")
    async def history(session_id: str, app_state: Any = Depends(get_app_state), _: None = Depends(require_api_key)) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        async with aiosqlite.connect(app_state.services.memory.db_path) as db:
            cursor = await db.execute(
                "select role, content, created_at from conversation_history where session_id = ? order by id asc",
                (session_id,),
            )
            async for row in cursor:
                rows.append({"role": row[0], "content": row[1], "created_at": row[2]})
        return rows

    @router.delete("/history/{session_id}")
    async def delete_history(session_id: str, app_state: Any = Depends(get_app_state), _: None = Depends(require_api_key)) -> dict[str, Any]:
        async with aiosqlite.connect(app_state.services.memory.db_path) as db:
            await db.execute("delete from conversation_history where session_id = ?", (session_id,))
            await db.commit()
        app_state.services.memory.working.clear_session(session_id)
        return {"deleted": True, "session_id": session_id}

    @router.post("/upload")
    async def upload_attachment(
        file: UploadFile = File(...),
        app_state: Any = Depends(get_app_state),
        _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        upload_dir = DATA_DIR / "uploads"
        upload_dir.mkdir(exist_ok=True)
        target = upload_dir / file.filename
        target.write_bytes(await file.read())
        return {"path": str(target), "filename": file.filename}

    return router


async def authenticate_websocket(websocket: WebSocket, api_key: str) -> None:
    supplied = websocket.query_params.get("api_key") or websocket.headers.get("x-api-key")
    if supplied != api_key:
        await websocket.close(code=4401)
        raise HTTPException(status_code=401, detail="Invalid API key")


def websocket_message(text: str, session_id: str, attachments: list[str] | None = None) -> Message:
    return Message(text=text, source_channel="web", session_id=session_id, attachments=attachments or [])
