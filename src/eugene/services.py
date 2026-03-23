from __future__ import annotations

import asyncio
import contextlib
import json
import mimetypes
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite
from fastapi import APIRouter

from eugene.config import APPLETS_DIR, CHANNELS_DIR, DATA_DIR, ROOT_DIR, load_toml
from eugene.core import AppletBase, ChannelBase, ServiceContainer, WorkingMemory, discover_subclass
from eugene.models import AppletRecord, Attachment, ChannelStatus, LLMResult, Message, ProviderCheckResult, ScheduledTask, ToolCall, ToolDefinition, TriggerKind

try:
    import magic  # type: ignore
except ImportError:  # pragma: no cover
    magic = None

try:
    import tomli_w
except ImportError:  # pragma: no cover
    tomli_w = None

try:
    from watchfiles import awatch  # type: ignore
except ImportError:  # pragma: no cover
    awatch = None

try:
    import pdfplumber  # type: ignore
except ImportError:  # pragma: no cover
    pdfplumber = None

try:
    from pypdf import PdfReader  # type: ignore
except ImportError:  # pragma: no cover
    PdfReader = None

try:
    import chromadb  # type: ignore
    from chromadb.config import Settings as ChromaSettings  # type: ignore
except ImportError:  # pragma: no cover
    chromadb = None
    ChromaSettings = None

try:
    from sentence_transformers import SentenceTransformer  # type: ignore
except ImportError:  # pragma: no cover
    SentenceTransformer = None

try:
    from litellm import acompletion, get_max_tokens  # type: ignore
except ImportError:  # pragma: no cover
    acompletion = None
    get_max_tokens = None

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore
    from apscheduler.jobstores.memory import MemoryJobStore  # type: ignore
    from apscheduler.triggers.cron import CronTrigger  # type: ignore
    from apscheduler.triggers.date import DateTrigger  # type: ignore
except ImportError:  # pragma: no cover
    AsyncIOScheduler = None
    MemoryJobStore = None
    CronTrigger = None
    DateTrigger = None

try:
    import discord  # type: ignore
except ImportError:  # pragma: no cover
    discord = None

PROVIDER_ENV_REQUIREMENTS = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "google": ("GOOGLE_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "xai": ("XAI_API_KEY",),
    "ollama": (),
}


class PersonalityService:
    def __init__(self, services: ServiceContainer) -> None:
        self.services = services
        self.path = ROOT_DIR / "personality.toml"
        self.compiled = ""
        self._watch_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self.reload()
        if awatch:
            self._watch_task = asyncio.create_task(self._watch())

    async def stop(self) -> None:
        if self._watch_task:
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch_task

    async def reload(self) -> None:
        data = load_toml(self.path)
        blocks = []
        for section, values in data.items():
            formatted = "\n".join(f"- {key}: {value}" for key, value in values.items())
            blocks.append(f"[{section}]\n{formatted}")
        self.compiled = "\n\n".join(blocks)
        await self.services.event_bus.publish("personality.updated", {"path": str(self.path)})

    async def edit_section(self, section: str, content: dict[str, Any]) -> None:
        if tomli_w is None:
            raise RuntimeError("tomli-w is required to edit personality.toml")
        data = load_toml(self.path)
        data[section] = content
        self.path.write_text(tomli_w.dumps(data), encoding="utf-8")
        await self.reload()

    def read(self) -> str:
        return self.compiled

    async def _watch(self) -> None:
        async for _ in awatch(self.path):
            await self.reload()


class ProviderService:
    def __init__(self, services: ServiceContainer) -> None:
        self.services = services
        self.db_path = DATA_DIR / "eugene.db"

    async def initialize(self) -> None:
        await self._init_db()
        check = self.check_configuration()
        if not check.ok:
            raise RuntimeError(check.message)

    def check_configuration(self) -> ProviderCheckResult:
        if acompletion is None:
            return ProviderCheckResult(ok=False, message="LiteLLM is not installed. Eugene cannot start.")
        models = [self.services.config.default_model, self.services.config.router_model]
        if self.services.config.fallback_model:
            models.append(self.services.config.fallback_model)
        for model in models:
            provider = model.split("/", 1)[0]
            requirements = PROVIDER_ENV_REQUIREMENTS.get(provider)
            if requirements is None:
                continue
            if requirements and not any(os.getenv(name) for name in requirements):
                return ProviderCheckResult(
                    ok=False,
                    message=f"Model '{model}' requires one of {', '.join(requirements)} to be set before startup.",
                )
        return ProviderCheckResult(ok=True, message="Provider configuration looks valid.")

    async def route_applets(self, message: Message, applets: list[AppletRecord]) -> list[str]:
        registry = [{"name": item.name, "description": item.description} for item in applets if item.enabled]
        prompt = json.dumps({"message": message.text, "applets": registry}, separators=(",", ":"))
        result = await self._call_model(
            model=self.services.config.router_model,
            messages=[
                {"role": "system", "content": "Return only a JSON array of relevant applet names."},
                {"role": "user", "content": prompt},
            ],
            origin="router",
            tools=None,
        )
        selected = self._parse_router_response(result.text)
        if selected is not None:
            return selected
        if self.services.config.fallback_model:
            fallback = await self._call_model(
                model=self.services.config.fallback_model,
                messages=[
                    {"role": "system", "content": "Return only a JSON array of relevant applet names."},
                    {"role": "user", "content": prompt},
                ],
                origin="router_fallback",
                tools=None,
            )
            selected = self._parse_router_response(fallback.text)
            if selected is not None:
                return selected
        raise RuntimeError("Routing failed. Router did not return a valid JSON array.")

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        model: str | None = None,
        origin: str = "core",
    ) -> LLMResult:
        active_model = model or self.services.config.default_model
        await self.enforce_context_threshold(active_model, messages)
        return await self._call_model(
            model=active_model,
            messages=messages,
            origin=origin,
            tools=[tool.as_llm_tool() for tool in tools if tool.inject != "never"],
        )

    async def enforce_context_threshold(self, model: str, messages: list[dict[str, Any]]) -> None:
        if get_max_tokens is None:
            return
        max_context = get_max_tokens(model) or 0
        if max_context <= 0:
            return
        approximate = sum(len(item.get("content", "")) for item in messages) // 4
        if approximate >= int(max_context * self.services.config.context_window_threshold):
            session_id = self._extract_session_id(messages)
            if session_id:
                await self.services.memory.summarize_working_memory(session_id)

    async def _call_model(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        origin: str,
        tools: list[dict[str, Any]] | None,
    ) -> LLMResult:
        outbound_messages = [self._sanitize_message(item) for item in messages]
        response = await acompletion(model=model, messages=outbound_messages, tools=tools or None, tool_choice="auto" if tools else None)
        choice = response["choices"][0]["message"]
        usage = response.get("usage", {})
        result = LLMResult(
            text=choice.get("content") or "",
            tool_calls=[
                ToolCall(
                    id=item.get("id"),
                    name=item["function"]["name"],
                    arguments=json.loads(item["function"]["arguments"] or "{}"),
                )
                for item in (choice.get("tool_calls") or [])
            ],
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            estimated_cost=0.0,
            finish_reason=response["choices"][0].get("finish_reason"),
        )
        await self._log(result, origin)
        return result

    def _parse_router_response(self, text: str) -> list[str] | None:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, list) and all(isinstance(item, str) for item in payload):
            return payload
        return None

    def _extract_session_id(self, messages: list[dict[str, Any]]) -> str | None:
        for item in reversed(messages):
            session_id = item.get("session_id")
            if session_id:
                return session_id
        return None

    def _sanitize_message(self, message: dict[str, Any]) -> dict[str, Any]:
        allowed_keys = {"role", "content", "name", "tool_call_id"}
        sanitized = {key: value for key, value in message.items() if key in allowed_keys}
        return sanitized

    async def _init_db(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                create table if not exists token_usage (
                    id integer primary key,
                    timestamp text not null,
                    model text not null,
                    prompt_tokens integer not null,
                    completion_tokens integer not null,
                    estimated_cost real not null,
                    origin text
                )
                """
            )
            await db.commit()

    async def _log(self, result: LLMResult, origin: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "insert into token_usage(timestamp, model, prompt_tokens, completion_tokens, estimated_cost, origin) values (?, ?, ?, ?, ?, ?)",
                (
                    datetime.utcnow().isoformat(),
                    result.model,
                    result.prompt_tokens,
                    result.completion_tokens,
                    result.estimated_cost,
                    origin,
                ),
            )
            await db.commit()


class MemoryService:
    def __init__(self, services: ServiceContainer) -> None:
        self.services = services
        self.working = WorkingMemory(max_turns=services.config.working_memory_turns)
        self.db_path = DATA_DIR / "eugene.db"
        self.chroma_path = DATA_DIR / "chroma"
        self._embedder = None
        self._collection = None

    async def initialize(self) -> None:
        await self._init_db()
        self._initialize_semantic_store()

    async def search_memory(self, query: str, top_k: int = 3) -> list[str]:
        if self._collection is not None and self._embedder is not None:
            embedding = self._embed(query)
            result = self._collection.query(query_embeddings=[embedding], n_results=top_k)
            documents = result.get("documents", [[]])
            return list(documents[0]) if documents else []
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "select content from long_term_memory where content like ? order by id desc limit ?",
                (f"%{query}%", top_k),
            )
            rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def summarize_working_memory(self, session_id: str) -> str:
        window = self.working.get_window(session_id)
        summary = "\n".join(f"{item['role']}: {item['content'][:180]}" for item in window[-8:])
        self.working.set_summary(session_id, summary)
        return summary

    async def store_exchange(self, session_id: str, text: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "insert into long_term_memory(session_id, content, created_at) values (?, ?, ?)",
                (session_id, text, datetime.utcnow().isoformat()),
            )
            await db.commit()
        if self._collection is not None and self._embedder is not None:
            self._collection.add(
                ids=[str(uuid4())],
                documents=[text],
                embeddings=[self._embed(text)],
                metadatas=[{"session_id": session_id, "created_at": datetime.utcnow().isoformat()}],
            )
        await self.services.event_bus.publish("memory.stored", {"session_id": session_id})

    async def set_fact(self, key: str, value: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("insert or replace into structured_facts(key, value) values (?, ?)", (key, value))
            await db.commit()

    async def get_fact(self, key: str) -> str | None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("select value from structured_facts where key = ?", (key,))
            row = await cursor.fetchone()
        return row[0] if row else None

    async def consolidate_exchange(self, session_id: str, user_text: str, assistant_text: str) -> None:
        combined = f"User: {user_text}\nAssistant: {assistant_text}"
        await self.store_exchange(session_id, combined)

    async def _init_db(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                create table if not exists structured_facts (
                    key text primary key,
                    value text not null
                )
                """
            )
            await db.execute(
                """
                create table if not exists long_term_memory (
                    id integer primary key,
                    session_id text not null,
                    content text not null,
                    created_at text not null
                )
                """
            )
            await db.execute(
                """
                create table if not exists conversation_history (
                    id integer primary key,
                    session_id text not null,
                    role text not null,
                    content text not null,
                    created_at text not null
                )
                """
            )
            await db.commit()

    def _initialize_semantic_store(self) -> None:
        if chromadb is None or ChromaSettings is None or SentenceTransformer is None:
            return
        self.chroma_path.mkdir(exist_ok=True)
        client = chromadb.PersistentClient(path=str(self.chroma_path), settings=ChromaSettings(allow_reset=False))
        self._collection = client.get_or_create_collection("memories")
        self._embedder = SentenceTransformer("all-MiniLM-L6-v2")

    def _embed(self, text: str) -> list[float]:
        assert self._embedder is not None
        return self._embedder.encode(text).tolist()


class MCPManager:
    def __init__(self, services: ServiceContainer) -> None:
        self.services = services
        self.started: set[str] = set()
        self.degraded: dict[str, str] = {}

    async def start_eager(self) -> None:
        for record in self.services.applets.registry.values():
            if record.enabled and record.mcp_start == "eager" and record.load == "eager":
                await self.ensure_started(record.name)

    async def ensure_started(self, applet_name: str) -> None:
        self.started.add(applet_name)

    async def stop(self) -> None:
        self.started.clear()

    async def get_tools(self, applet: AppletBase) -> list[ToolDefinition]:
        if applet.requires_mcp:
            await self.ensure_started(applet.name)
        return applet.get_tools()


class FileHandler:
    def __init__(self, services: ServiceContainer) -> None:
        self.services = services

    async def resolve_message(self, message: Message) -> Message:
        resolved: list[Attachment] = []
        for item in message.attachments:
            if isinstance(item, Attachment):
                resolved.append(item)
                continue
            attachment = await self._resolve_attachment(item)
            if attachment:
                resolved.append(attachment)
                await self.services.event_bus.publish("file.attached", {"session_id": message.session_id, "attachment": attachment.model_dump(mode="json")})
        return message.model_copy(update={"attachments": resolved})

    async def _resolve_attachment(self, ref: str) -> Attachment | None:
        suffix = Path(ref).suffix.lower()
        for applet in self.services.applets.instances.values():
            if suffix in getattr(applet, "supported_extensions", []):
                handled = await applet.handle_file(ref)
                if handled:
                    return handled
        path = Path(ref)
        if not path.exists():
            return None
        raw = path.read_bytes()
        file_type = self._detect_type(path, raw)
        if file_type == "application/pdf":
            content = await self._extract_pdf(path)
            return Attachment(original_filename=path.name, file_type=file_type, content=content, chunked=len(content) > 8_000)
        if file_type.startswith("text/") or path.suffix.lower() in {".md", ".py", ".toml", ".json", ".yaml", ".yml"}:
            text = raw.decode("utf-8", errors="replace")
            return Attachment(original_filename=path.name, file_type=file_type, content=text[:8_000], chunked=len(text) > 8_000)
        if file_type.startswith("image/"):
            return Attachment(
                original_filename=path.name,
                file_type=file_type,
                content=f"Image attachment available at {path}. Use a vision-capable model or summarize externally if needed.",
                metadata={"path": str(path)},
            )
        return Attachment(original_filename=path.name, file_type=file_type, content=f"Attachment available at {path}", metadata={"path": str(path)})

    def _detect_type(self, path: Path, raw: bytes) -> str:
        if magic:
            return magic.from_buffer(raw, mime=True)
        return mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    async def _extract_pdf(self, path: Path) -> str:
        if pdfplumber:
            with pdfplumber.open(path) as pdf:
                return "\n".join(page.extract_text() or "" for page in pdf.pages)
        if PdfReader:
            reader = PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        raise RuntimeError("PDF extraction requires pdfplumber or pypdf.")


class AppletManager:
    def __init__(self, services: ServiceContainer) -> None:
        self.services = services
        self.registry: dict[str, AppletRecord] = {}
        self.instances: dict[str, AppletBase] = {}

    async def scan(self) -> None:
        for folder in sorted(path for path in APPLETS_DIR.iterdir() if path.is_dir()):
            meta_path = folder / "applet.toml"
            applet_file = folder / "applet.py"
            if not meta_path.exists() or not applet_file.exists():
                continue
            try:
                raw = load_toml(meta_path)
                name, values = self._parse_applet_toml(raw)
                cls = discover_subclass(applet_file, AppletBase)
                if cls is None:
                    continue
                config_schema = self._config_schema(cls)
                record = AppletRecord(
                    name=name,
                    description=values.get("description", getattr(cls, "description", "")),
                    module_path=str(applet_file),
                    folder_path=str(folder),
                    enabled=values.get("enabled", True),
                    load=getattr(cls, "load", "lazy"),
                    inject=getattr(cls, "inject", "selective"),
                    mcp_start="lazy" if getattr(cls, "load", "lazy") == "lazy" else getattr(cls, "mcp_start", "lazy"),
                    can_disable=getattr(cls, "can_disable", True),
                    config=self._merge_config(name, values, cls),
                    config_schema=config_schema,
                    status="disabled" if not values.get("enabled", True) else "discovered",
                )
                self.registry[name] = record
            except Exception as exc:
                broken_name = folder.name
                self.registry[broken_name] = AppletRecord(
                    name=broken_name,
                    description=f"Failed to load applet from {folder.name}",
                    module_path=str(applet_file),
                    folder_path=str(folder),
                    enabled=False,
                    errors=[str(exc)],
                    status="degraded",
                )
        for name, record in self.registry.items():
            if record.enabled and record.load == "eager":
                await self.load_applet(name)

    async def load_applet(self, name: str) -> AppletBase:
        if name in self.instances:
            return self.instances[name]
        record = self.registry[name]
        cls = discover_subclass(Path(record.module_path), AppletBase)
        if cls is None:
            raise RuntimeError(f"Applet class not found for {name}")
        self._check_requirements(record)
        instance = cls(record, self.services)
        await instance.on_load()
        self.instances[name] = instance
        record.instance = instance
        record.status = "loaded"
        if type(instance).on_event is not AppletBase.on_event:
            for event_name in ("message.received", "file.attached", "task.fired", "memory.stored", "personality.updated"):
                self.services.event_bus.subscribe(event_name, instance.on_event)
        await self.services.event_bus.publish("applet.loaded", {"name": name})
        return instance

    async def unload_applet(self, name: str) -> None:
        instance = self.instances.pop(name, None)
        if instance is not None:
            await instance.on_unload()
            self.registry[name].instance = None
            self.registry[name].status = "discovered"
            await self.services.event_bus.publish("applet.unloaded", {"name": name})

    async def get_candidate_applets(self, names: list[str]) -> list[AppletBase]:
        selected: list[AppletBase] = []
        for name in names:
            record = self.registry.get(name)
            if record and record.enabled:
                selected.append(await self.load_applet(name))
        return selected

    async def always_on_tools(self) -> list[ToolDefinition]:
        tools: list[ToolDefinition] = []
        for record in self.registry.values():
            if record.enabled and record.inject == "always":
                instance = await self.load_applet(record.name)
                tools.extend(instance.get_tools())
        return tools

    async def context_blocks(self) -> list[str]:
        blocks = []
        for record in self.registry.values():
            if record.enabled and record.inject == "always":
                instance = await self.load_applet(record.name)
                text = instance.get_context_injection()
                if text:
                    blocks.append(text)
        return blocks

    async def selective_tools(self, names: list[str]) -> list[ToolDefinition]:
        tools: list[ToolDefinition] = []
        for applet in await self.get_candidate_applets(names):
            tools.extend(await self.services.mcp.get_tools(applet))
        return tools

    def awareness_block(self) -> str:
        applet_lines = [f"- {item.name}: {item.description}" for item in self.registry.values() if item.enabled]
        channel_lines = [f"- {item.name}: {'connected' if item.connected else 'idle'}" for item in self.services.channels.statuses().values()]
        task_count = len(self.services.scheduler.tasks)
        return "\n".join(
            [
                "Self-awareness:",
                "Available applets:",
                *applet_lines,
                "Connected channels:",
                *(channel_lines or ["- none"]),
                f"Scheduled tasks: {task_count}",
            ]
        )

    def routes(self) -> list[tuple[str, APIRouter]]:
        routes: list[tuple[str, APIRouter]] = []
        for name, instance in self.instances.items():
            for prefix, router in instance.get_routes():
                routes.append((f"/applets/{name}{prefix}", router))
        return routes

    async def load_route_applets(self) -> None:
        for name, record in self.registry.items():
            if not record.enabled:
                continue
            cls = discover_subclass(Path(record.module_path), AppletBase)
            if cls is not None and cls.get_routes is not AppletBase.get_routes:
                await self.load_applet(name)

    def dynamic_options(self, source: str) -> list[str]:
        if source == "dynamic:active_channels":
            return sorted(self.services.channels.channels.keys())
        if source == "dynamic:enabled_applets":
            return sorted(name for name, record in self.registry.items() if record.enabled)
        if source == "dynamic:active_providers":
            return sorted({self.services.config.default_model.split("/", 1)[0], self.services.config.router_model.split("/", 1)[0]})
        return []

    def _merge_config(self, name: str, toml_values: dict[str, Any], cls: type[AppletBase]) -> dict[str, Any]:
        schema = getattr(getattr(cls, "Config", object), "fields", {})
        merged = {field: spec.default for field, spec in schema.items()}
        merged.update({key: value for key, value in toml_values.items() if key != "description"})
        user_path = DATA_DIR / "applet_configs" / f"{name}.json"
        if user_path.exists():
            merged.update(json.loads(user_path.read_text(encoding="utf-8")))
        if name == "filesystem":
            merged.setdefault("root_path", self.services.config.filesystem_root)
        return merged

    def _config_schema(self, cls: type[AppletBase]) -> dict[str, Any]:
        schema = {}
        for key, spec in getattr(getattr(cls, "Config", object), "fields", {}).items():
            schema[key] = {
                "default": spec.default,
                "description": spec.description,
                "options": spec.options or [],
                "dynamic_source": spec.dynamic_source,
            }
        return schema

    def _parse_applet_toml(self, raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        applet_block = raw.get("applet")
        if not isinstance(applet_block, dict) or not applet_block:
            raise RuntimeError("applet.toml must declare a [applet.name] table.")
        if len(applet_block) != 1:
            raise RuntimeError("applet.toml must declare exactly one [applet.name] table.")
        name, values = next(iter(applet_block.items()))
        if not isinstance(values, dict):
            raise RuntimeError("applet.toml applet table must contain key-value settings.")
        return name, dict(values)

    def _check_requirements(self, record: AppletRecord) -> None:
        requirements_path = Path(record.folder_path) / "requirements.txt"
        if not requirements_path.exists():
            return
        missing = []
        for requirement in requirements_path.read_text(encoding="utf-8").splitlines():
            requirement = requirement.strip()
            if not requirement or requirement.startswith("#"):
                continue
            module_name = requirement.split("[", 1)[0].split("=", 1)[0].split("<", 1)[0].split(">", 1)[0].replace("-", "_")
            try:
                __import__(module_name)
            except ImportError:
                missing.append(requirement)
        if missing:
            raise RuntimeError(f"Applet '{record.name}' is missing requirements: {', '.join(missing)}")


class ChannelManager:
    def __init__(self, services: ServiceContainer) -> None:
        self.services = services
        self.channels: dict[str, ChannelBase] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self.web_sessions: dict[str, Any] = {}
        self.pending_web_tasks: list[str] = []
        self._status: dict[str, ChannelStatus] = {}

    async def scan(self) -> None:
        for path in sorted(CHANNELS_DIR.glob("*.py")):
            cls = discover_subclass(path, ChannelBase)
            if cls is None:
                continue
            channel = cls(self.services)
            self.channels[channel.name] = channel
            enabled = self.services.config.channels.get(channel.name, None)
            self._status[channel.name] = ChannelStatus(name=channel.name, enabled=enabled.enabled if enabled else True)

    async def start(self) -> None:
        for name, channel in self.channels.items():
            if not self._status[name].enabled:
                continue
            self._tasks.append(asyncio.create_task(self._start_channel(name, channel)))

    async def stop(self) -> None:
        for channel in self.channels.values():
            with contextlib.suppress(Exception):
                await channel.on_stop()
        for task in self._tasks:
            task.cancel()

    async def deliver(self, response: str, channel_name: str, session_id: str, metadata: dict[str, Any] | None = None) -> None:
        channel = self.channels[channel_name]
        await channel.send(response, session_id, metadata=metadata)

    def register_websocket(self, session_id: str, websocket: Any) -> None:
        self.web_sessions[session_id] = websocket
        self._status["web"].connected = True
        if self.pending_web_tasks:
            self.services.scheduler.reassign_pending_web_tasks(session_id)

    def unregister_websocket(self, session_id: str) -> None:
        self.web_sessions.pop(session_id, None)
        self._status["web"].connected = bool(self.web_sessions)
        self.services.scheduler.handle_web_disconnect(session_id)

    def statuses(self) -> dict[str, ChannelStatus]:
        return self._status

    async def _start_channel(self, name: str, channel: ChannelBase) -> None:
        try:
            self._status[name].connected = True
            self._status[name].details = "starting"
            await channel.on_start()
            self._status[name].details = "started"
        except Exception as exc:  # pragma: no cover
            self._status[name].connected = False
            self._status[name].details = str(exc)


class SchedulerService:
    def __init__(self, services: ServiceContainer) -> None:
        self.services = services
        self.db_path = DATA_DIR / "eugene.db"
        self.scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()}) if AsyncIOScheduler and MemoryJobStore else None
        self.tasks: dict[str, ScheduledTask] = {}

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                create table if not exists scheduled_tasks (
                    id text primary key,
                    payload text not null
                )
                """
            )
            await db.commit()

    async def start(self) -> None:
        await self._load_persisted_tasks()
        if self.scheduler and not self.scheduler.running:
            self.scheduler.start()
        for instance in self.services.applets.instances.values():
            for task in instance.get_scheduled_tasks():
                if task.id not in self.tasks:
                    await self.register(task)

    async def stop(self) -> None:
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def register(self, task: ScheduledTask) -> ScheduledTask:
        self.tasks[task.id] = task
        await self._persist(task)
        if self.scheduler:
            self._schedule(task)
        await self.services.event_bus.publish("task.scheduled", {"task_id": task.id})
        return task

    async def delete(self, task_id: str) -> None:
        self.tasks.pop(task_id, None)
        if self.scheduler:
            with contextlib.suppress(Exception):
                self.scheduler.remove_job(task_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("delete from scheduled_tasks where id = ?", (task_id,))
            await db.commit()

    def handle_web_disconnect(self, session_id: str) -> None:
        for task in self.tasks.values():
            if task.origin_channel == "web" and task.session_id == session_id:
                if self.services.config.primary_channel:
                    task.origin_channel = self.services.config.primary_channel
                else:
                    self.services.channels.pending_web_tasks.append(task.id)

    def reassign_pending_web_tasks(self, session_id: str) -> None:
        for task_id in self.services.channels.pending_web_tasks:
            task = self.tasks.get(task_id)
            if task:
                task.origin_channel = "web"
                task.session_id = session_id
        self.services.channels.pending_web_tasks.clear()

    def _schedule(self, task: ScheduledTask) -> None:
        if self.scheduler is None:
            return
        if task.trigger_type == "cron":
            timezone = self.services.applets.instances["clock"].config["timezone"]
            trigger = CronTrigger.from_crontab(task.trigger_value, timezone=timezone)
        else:
            trigger = DateTrigger(run_date=datetime.fromisoformat(task.trigger_value))
        self.scheduler.add_job(self._fire_task, trigger=trigger, id=task.id, replace_existing=True, kwargs={"task_id": task.id})

    async def _fire_task(self, task_id: str) -> None:
        task = self.tasks[task_id]
        message = Message(
            text=task.prompt,
            source_channel=task.origin_channel or self.services.config.primary_channel or "web",
            session_id=task.session_id or str(uuid4()),
            trigger=TriggerKind.SCHEDULED,
            metadata={"task_id": task.id, "applet_name": task.applet_name},
        )
        await self.services.event_bus.publish("task.fired", {"task_id": task.id})
        await self.services.event_bus.publish("message.received", {"message": message.model_dump(mode="json")})

    async def _persist(self, task: ScheduledTask) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("insert or replace into scheduled_tasks(id, payload) values (?, ?)", (task.id, task.model_dump_json()))
            await db.commit()

    async def _load_persisted_tasks(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("select payload from scheduled_tasks")
            rows = await cursor.fetchall()
        for row in rows:
            task = ScheduledTask.model_validate_json(row[0])
            self.tasks[task.id] = task
            if self.scheduler:
                self._schedule(task)


@dataclass
class PromptBundle:
    messages: list[dict[str, Any]]
    session_id: str


class EugeneCore:
    def __init__(self, services: ServiceContainer) -> None:
        self.services = services
        services.event_bus.subscribe("message.received", self._handle_message_event)
        services.event_bus.subscribe("message.response", self._handle_response_event)

    async def _handle_message_event(self, event) -> None:
        raw = event.payload["message"]
        message = Message.model_validate(raw)
        normalized = await self.services.files.resolve_message(message)
        content = self._message_content(normalized)
        self.services.memory.working.add_turn(normalized.session_id, "user", content)
        await self._log_history(normalized.session_id, "user", content)

        try:
            selected_names = await self.services.provider.route_applets(normalized, list(self.services.applets.registry.values()))
            selected = await self.services.applets.get_candidate_applets(selected_names)
            for applet in selected:
                await applet.on_message(normalized)

            always_tools = await self.services.applets.always_on_tools()
            selective_tools = []
            for applet in selected:
                selective_tools.extend(await self.services.mcp.get_tools(applet))
            prompt = await self._build_prompt(normalized, selected_names)

            result = await self.services.provider.complete(messages=prompt.messages, tools=always_tools + selective_tools)
            depth = 0
            while result.tool_calls and depth < self.services.config.max_tool_depth:
                prompt.messages.append({"role": "assistant", "content": result.text, "session_id": prompt.session_id})
                for call in result.tool_calls:
                    output = await self._dispatch_tool(call, selected)
                    prompt.messages.append(
                        {
                            "role": "tool",
                            "content": json.dumps(output),
                            "tool_call_id": call.id or call.name,
                            "name": call.name,
                            "session_id": prompt.session_id,
                        }
                    )
                depth += 1
                result = await self.services.provider.complete(messages=prompt.messages, tools=always_tools + selective_tools)

            response_text = result.text
            asyncio.create_task(self.services.memory.consolidate_exchange(normalized.session_id, normalized.text, response_text))
        except Exception as exc:
            response_text = f"Eugene hit an internal error while handling this message: {exc}"

        self.services.memory.working.add_turn(normalized.session_id, "assistant", response_text)
        await self._log_history(normalized.session_id, "assistant", response_text)
        await self.services.event_bus.publish(
            "message.response",
            {
                "response": response_text,
                "channel": normalized.source_channel,
                "session_id": normalized.session_id,
                "metadata": normalized.metadata,
            },
        )

    async def _handle_response_event(self, event) -> None:
        await self.services.channels.deliver(
            event.payload["response"],
            event.payload["channel"],
            event.payload["session_id"],
            metadata=event.payload.get("metadata"),
        )

    async def _build_prompt(self, message: Message, selected_names: list[str]) -> PromptBundle:
        system_parts = [self.services.personality.read()]
        system_parts.extend(await self.services.applets.context_blocks())
        system_parts.append(self.services.applets.awareness_block())
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "\n\n".join(part for part in system_parts if part), "session_id": message.session_id}
        ]
        for turn in self.services.memory.working.get_window(message.session_id):
            turn["session_id"] = message.session_id
            messages.append(turn)
        messages.append({"role": "user", "content": self._message_content(message), "session_id": message.session_id})
        return PromptBundle(messages=messages, session_id=message.session_id)

    async def _dispatch_tool(self, call: ToolCall, selected_applets: list[AppletBase]) -> Any:
        for applet in self.services.applets.instances.values():
            for tool in applet.get_tools():
                if tool.name == call.name:
                    return await applet.handle_tool(call.name, call.arguments)
        for applet in selected_applets:
            for tool in applet.get_tools():
                if tool.name == call.name:
                    return await applet.handle_tool(call.name, call.arguments)
        return {"error": f"Unknown tool {call.name}"}

    def _message_content(self, message: Message) -> str:
        attachment_text = "\n\n".join(
            f"Attachment: {item.original_filename}\nType: {item.file_type}\nContent:\n{item.content}"
            for item in message.attachments
            if isinstance(item, Attachment)
        )
        return f"{message.text}\n\n{attachment_text}".strip()

    async def _log_history(self, session_id: str, role: str, content: str) -> None:
        async with aiosqlite.connect(self.services.memory.db_path) as db:
            await db.execute(
                "insert into conversation_history(session_id, role, content, created_at) values (?, ?, ?, ?)",
                (session_id, role, content, datetime.utcnow().isoformat()),
            )
            await db.commit()
