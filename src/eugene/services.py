from __future__ import annotations

import asyncio
import contextlib
import json
import mimetypes
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite
from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field

from eugene.config import APPLETS_DIR, CHANNELS_DIR, DATA_DIR, ROOT_DIR, load_toml
from eugene.core import AppletBase, ChannelBase, ServiceContainer, WorkingMemory, discover_subclass
from eugene.logging_utils import preview
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
    from llmlingua import PromptCompressor  # type: ignore
except ImportError:  # pragma: no cover
    PromptCompressor = None

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
    "nvidia_nim": ("NVIDIA_NIM_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "xai": ("XAI_API_KEY",),
    "ollama": (),
}

NVIDIA_NIM_DEFAULT_API_BASE = "https://integrate.api.nvidia.com/v1/"


class PromptCompressionService:
    """Built-in prompt compression tool using LLMLingua-2 (not applet-based)."""

    def __init__(self, services: ServiceContainer) -> None:
        self.services = services
        self.enabled = bool(services.config.compress_prompt)
        self.available = False
        self._compressor = None

    async def initialize(self) -> None:
        if not self.enabled:
            logger.bind(component="compression").info("Prompt compression disabled")
            return
        if PromptCompressor is None:
            logger.bind(component="compression").warning("Prompt compression requested but llmlingua is not installed")
            return
        try:
            self._compressor = PromptCompressor(
                model_name=self.services.config.compress_prompt_model,
                use_llmlingua2=True,
            )
            self.available = True
            logger.bind(component="compression").info(
                "Prompt compression enabled model={model} rate={rate} min_chars={min_chars}",
                model=self.services.config.compress_prompt_model,
                rate=self.services.config.compress_prompt_rate,
                min_chars=self.services.config.compress_prompt_min_chars,
            )
        except Exception:
            logger.bind(component="compression").exception("Failed to initialize LLMLingua-2 compressor")
            self.available = False

    def compress_messages(self, messages: list[dict[str, Any]], *, origin: str, model: str) -> list[dict[str, Any]]:
        if not self.enabled or not self.available or self._compressor is None:
            return messages

        compressed_messages: list[dict[str, Any]] = []
        original_chars = 0
        compressed_chars = 0

        for message in messages:
            role = str(message.get("role", ""))
            content = message.get("content")
            if not isinstance(content, str):
                compressed_messages.append(message)
                continue

            original_chars += len(content)

            # Keep system instructions untouched for stability.
            if role == "system" or len(content) < self.services.config.compress_prompt_min_chars:
                compressed_messages.append(message)
                compressed_chars += len(content)
                continue

            try:
                result = self._compressor.compress_prompt(
                    content,
                    rate=self.services.config.compress_prompt_rate,
                    force_tokens=["\n", "?"],
                )
                candidate = result.get("compressed_prompt") if isinstance(result, dict) else None
                if isinstance(candidate, str) and candidate.strip():
                    updated = dict(message)
                    updated["content"] = candidate
                    compressed_messages.append(updated)
                    compressed_chars += len(candidate)
                else:
                    compressed_messages.append(message)
                    compressed_chars += len(content)
            except Exception as exc:
                logger.bind(component="compression", origin=origin, model=model).warning(
                    "Compression failed for one message; using original content error={error}",
                    error=preview(exc),
                )
                compressed_messages.append(message)
                compressed_chars += len(content)

        if original_chars > 0 and compressed_chars < original_chars:
            ratio = round(original_chars / max(compressed_chars, 1), 2)
            logger.bind(component="compression", origin=origin, model=model).info(
                "Prompt compression applied original_chars={original} compressed_chars={compressed} ratio={ratio}x",
                original=original_chars,
                compressed=compressed_chars,
                ratio=ratio,
            )
        return compressed_messages


class FrontendReloadService:
    """Watches frontend sources, rebuilds static assets, and asks browsers to reload."""

    def __init__(self, services: ServiceContainer) -> None:
        self.services = services
        self.enabled = bool(services.config.frontend_auto_reload)
        self.frontend_dir = ROOT_DIR / "frontend"
        self._watch_task: asyncio.Task[None] | None = None
        self._build_lock = asyncio.Lock()
        self._clients: set[Any] = set()

    async def start(self) -> None:
        if not self.enabled:
            logger.bind(component="frontend_reload").info("Frontend auto-reload disabled")
            return
        if awatch is None:
            logger.bind(component="frontend_reload").warning("watchfiles unavailable; frontend auto-reload disabled")
            return
        if not self.frontend_dir.exists() or not (self.frontend_dir / "package.json").exists():
            logger.bind(component="frontend_reload").warning("frontend directory/package.json not found; auto-reload disabled")
            return
        self._watch_task = asyncio.create_task(self._watch())
        logger.bind(component="frontend_reload").info("Frontend auto-reload watcher started")

    async def stop(self) -> None:
        if self._watch_task:
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch_task
            self._watch_task = None
        self._clients.clear()
        logger.bind(component="frontend_reload").info("Frontend auto-reload watcher stopped")

    def register_client(self, websocket: Any) -> None:
        self._clients.add(websocket)

    def unregister_client(self, websocket: Any) -> None:
        self._clients.discard(websocket)

    async def _watch(self) -> None:
        watch_paths = [
            self.frontend_dir / "src",
            self.frontend_dir / "index.html",
            self.frontend_dir / "vite.config.ts",
        ]
        existing = [path for path in watch_paths if path.exists()]
        if not existing:
            return

        async for changes in awatch(*existing):
            if not self._has_relevant_change(changes):
                continue
            await asyncio.sleep(self.services.config.frontend_reload_debounce_ms / 1000)
            await self._rebuild_and_notify()

    def _has_relevant_change(self, changes: set[tuple[Any, str]]) -> bool:
        for _, changed in changes:
            path = changed.lower()
            if any(path.endswith(ext) for ext in (".tsx", ".ts", ".jsx", ".js", ".css", ".html", ".json")):
                return True
        return False

    async def _rebuild_and_notify(self) -> None:
        async with self._build_lock:
            logger.bind(component="frontend_reload").info("Frontend change detected; rebuilding static assets")
            process = await asyncio.create_subprocess_shell(
                "npm run build:static",
                cwd=str(self.frontend_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                logger.bind(component="frontend_reload").error(
                    "Frontend static rebuild failed code={code} stderr={stderr}",
                    code=process.returncode,
                    stderr=preview(stderr.decode("utf-8", errors="replace"), max_len=2000),
                )
                return
            logger.bind(component="frontend_reload").info("Frontend static rebuild complete")
            await self._notify_reload_clients()

    async def _notify_reload_clients(self) -> None:
        if not self._clients:
            return
        stale: list[Any] = []
        payload = {"type": "frontend.reload"}
        for websocket in self._clients:
            try:
                await websocket.send_json(payload)
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            self._clients.discard(websocket)
        logger.bind(component="frontend_reload").info("Reload notifications sent client_count={count}", count=len(self._clients))


class PersonalityService:
    def __init__(self, services: ServiceContainer) -> None:
        self.services = services
        self.path = ROOT_DIR / "personality.toml"
        self.compiled = ""
        self._watch_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        logger.bind(component="personality").info("Starting personality service path={path}", path=str(self.path))
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
        logger.bind(component="personality").debug("Personality reloaded sections={count}", count=len(data))
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
        logger.bind(component="provider").info("Provider configuration check ok={ok} message={message}", ok=check.ok, message=check.message)
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

    async def route_applets(self, message: Message, applets: list[AppletRecord], mcp_servers: list[dict[str, str]] | None = None) -> list[str]:
        registry = [{"name": item.name, "description": item.description} for item in applets if item.enabled]
        payload: dict[str, Any] = {"message": message.text, "applets": registry}
        if mcp_servers:
            payload["mcp_servers"] = mcp_servers
        prompt = json.dumps(payload, separators=(",", ":"))
        routing_messages = [
            {"role": "system", "content": "Return only a JSON array of relevant applet and/or MCP server names."},
            {"role": "user", "content": prompt},
        ]
        max_retries = self.services.config.router_retry_attempts
        last_error_text = ""
        last_router_text = ""
        last_fallback_text = ""

        for attempt in range(max_retries + 1):
            try:
                result = await self._call_model(
                    model=self.services.config.router_model,
                    messages=routing_messages,
                    origin="router",
                    tools=None,
                )
                last_router_text = result.text
                selected = self._parse_router_response(result.text)
                if selected is not None:
                    logger.bind(component="routing", session_id=message.session_id).info("Router selected applets={selected}", selected=selected)
                    return selected

                last_error_text = f"Router output was not a JSON array: {preview(result.text)}"
                if self.services.config.fallback_model:
                    fallback = await self._call_model(
                        model=self.services.config.fallback_model,
                        messages=routing_messages,
                        origin="router_fallback",
                        tools=None,
                    )
                    last_fallback_text = fallback.text
                    selected = self._parse_router_response(fallback.text)
                    if selected is not None:
                        logger.bind(component="routing", session_id=message.session_id).info("Fallback router selected applets={selected}", selected=selected)
                        return selected
                    last_error_text = (
                        f"Router output was not a JSON array: {preview(result.text)}; "
                        f"Fallback output was not a JSON array: {preview(fallback.text)}"
                    )
            except Exception as exc:
                last_error_text = str(exc)
                logger.bind(component="routing", session_id=message.session_id).warning(
                    "Routing attempt failed attempt={attempt}/{total} error={error}",
                    attempt=attempt + 1,
                    total=max_retries + 1,
                    error=preview(exc),
                )

            if attempt >= max_retries:
                break

            routing_messages.append(
                {
                    "role": "system",
                    "content": (
                        "Retrying routing after invalid output. "
                        "Return only a JSON array of applet names from this allowed list.\n\n"
                        f"Allowed applet names: {[item['name'] for item in registry]}\n\n"
                        f"Previous routing failure:\n{preview(last_error_text, max_len=4000)}"
                    ),
                }
            )
            await asyncio.sleep(min(0.25 * (attempt + 1), 1.0))

        debug_tail = self._format_routing_debug(
            attempts=max_retries + 1,
            session_id=message.session_id,
            registry_names=[item["name"] for item in registry],
            last_error=last_error_text,
            last_router_text=last_router_text,
            last_fallback_text=last_fallback_text,
        )
        logger.bind(component="routing", session_id=message.session_id).error("Routing failed after retries")
        raise RuntimeError(f"Routing failed. Router did not return a valid JSON array.{debug_tail}")


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
        deduped_tools = self._dedupe_tools(tools)
        logger.bind(component="provider", origin=origin).debug(
            "Completing model={model} message_count={message_count} tool_count={tool_count}",
            model=active_model,
            message_count=len(messages),
            tool_count=len([tool for tool in deduped_tools if tool.inject != "never"]),
        )
        return await self._call_model(
            model=active_model,
            messages=messages,
            origin=origin,
            tools=[tool.as_llm_tool() for tool in deduped_tools if tool.inject != "never"],
        )

    async def enforce_context_threshold(self, model: str, messages: list[dict[str, Any]]) -> None:
        if get_max_tokens is None:
            return
        resolved_model, model_kwargs = self._prepare_litellm_request(model)
        try:
            max_context = get_max_tokens(
                resolved_model,
                custom_llm_provider=model_kwargs.get("custom_llm_provider"),
            ) or 0
        except Exception:
            logger.bind(component="provider", model=model).debug(
                "Skipping context-window lookup because model metadata is unavailable"
            )
            return
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
        if self.services.compressor is not None:
            outbound_messages = self.services.compressor.compress_messages(outbound_messages, origin=origin, model=model)
        resolved_model, request_kwargs = self._prepare_litellm_request(model)
        message_chars = sum(len(str(item.get("content", ""))) for item in outbound_messages)
        tool_chars = len(json.dumps(tools or [], ensure_ascii=False))
        logger.bind(component="provider", origin=origin).debug(
            "Calling LLM model={model} resolved_model={resolved_model} messages={messages} tools={tools} message_chars={message_chars} tool_chars={tool_chars}",
            model=model,
            resolved_model=resolved_model,
            messages=len(outbound_messages),
            tools=len(tools or []),
            message_chars=message_chars,
            tool_chars=tool_chars,
        )
        response = await acompletion(
            model=resolved_model,
            messages=outbound_messages,
            tools=tools or None,
            tool_choice="auto" if tools else None,
            **request_kwargs,
        )
        choice = response["choices"][0]["message"]
        usage = response.get("usage", {})
        raw_tool_calls = choice.get("tool_calls") or []
        normalized_tool_calls = [self._normalize_tool_call_payload(item) for item in raw_tool_calls]
        result = LLMResult(
            text=choice.get("content") or "",
            tool_calls=[
                ToolCall(
                    id=item.get("id"),
                    name=item["function"]["name"],
                    arguments=json.loads(item["function"]["arguments"] or "{}"),
                )
                for item in normalized_tool_calls
            ],
            tool_calls_payload=normalized_tool_calls,
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            estimated_cost=0.0,
            finish_reason=response["choices"][0].get("finish_reason"),
        )
        logger.bind(component="provider", origin=origin).debug(
            "LLM response finish_reason={finish_reason} tool_calls={tool_calls} prompt_tokens={prompt_tokens} completion_tokens={completion_tokens}",
            finish_reason=result.finish_reason,
            tool_calls=[call.name for call in result.tool_calls],
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )
        await self._log(result, origin)
        return result

    def _prepare_litellm_request(self, model: str) -> tuple[str, dict[str, Any]]:
        if model.startswith("nvidia_nim/"):
            stripped_model = model.split("/", 1)[1]
            api_base = os.getenv("NVIDIA_NIM_API_BASE", NVIDIA_NIM_DEFAULT_API_BASE).strip() or NVIDIA_NIM_DEFAULT_API_BASE
            return stripped_model, {
                "custom_llm_provider": "nvidia_nim",
                "api_base": api_base,
            }
        return model, {}

    def _dedupe_tools(self, tools: list[ToolDefinition]) -> list[ToolDefinition]:
        deduped: list[ToolDefinition] = []
        seen: set[str] = set()
        for tool in tools:
            if tool.name in seen:
                continue
            seen.add(tool.name)
            deduped.append(tool)
        return deduped

    def _normalize_tool_call_payload(self, item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            return item
        if hasattr(item, "model_dump"):
            dumped = item.model_dump(mode="json")
            if isinstance(dumped, dict):
                return dumped
        if hasattr(item, "dict"):
            dumped = item.dict()
            if isinstance(dumped, dict):
                return dumped
        raise TypeError(f"Unsupported tool call payload type: {type(item).__name__}")

    def _parse_router_response(self, text: str) -> list[str] | None:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, list) and all(isinstance(item, str) for item in payload):
            return payload
        return None

    def _format_routing_debug(
        self,
        *,
        attempts: int,
        session_id: str,
        registry_names: list[str],
        last_error: str,
        last_router_text: str,
        last_fallback_text: str,
    ) -> str:
        if not self.services.config.router_error_debug:
            return ""
        return "\n\nRouting debug:" + "\n".join(
            [
                f"\n- attempts: {attempts}",
                f"- session_id: {session_id}",
                f"- available_applets: {registry_names}",
                f"- router_model: {self.services.config.router_model}",
                f"- fallback_model: {self.services.config.fallback_model or '<none>'}",
                f"- last_error: {preview(last_error, max_len=1000)}",
                f"- last_router_text: {preview(last_router_text, max_len=1000)}",
                f"- last_fallback_text: {preview(last_fallback_text, max_len=1000)}",
            ]
        )

    def _extract_session_id(self, messages: list[dict[str, Any]]) -> str | None:
        for item in reversed(messages):
            session_id = item.get("session_id")
            if session_id:
                return session_id
        return None

    def _sanitize_message(self, message: dict[str, Any]) -> dict[str, Any]:
        allowed_keys = {"role", "content", "name", "tool_call_id", "tool_calls"}
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
        logger.bind(component="memory").info("Memory initialized semantic_store={enabled}", enabled=self._collection is not None and self._embedder is not None)

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


class MCPServerRecord(BaseModel):
    """A single entry from mcp_registry.json."""
    name: str
    package: str = ""
    transport: str = "npm"   # "npm" or "command"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    description: str = ""
    lazy: bool = True
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class _RunningMCP:
    """Tracks a single running MCP server."""

    __slots__ = ("record", "session", "_cm_stack", "tools")

    def __init__(self, record: MCPServerRecord) -> None:
        self.record = record
        self.session: Any = None
        self._cm_stack: contextlib.AsyncExitStack | None = None
        self.tools: list[ToolDefinition] = []


class MCPManager:
    def __init__(self, services: ServiceContainer) -> None:
        self.services = services
        # ── applet-level tracking (retained for backward compat) ──
        self.started: set[str] = set()
        self.degraded: dict[str, str] = {}
        # ── external MCP servers ──
        self.registry: dict[str, MCPServerRecord] = {}
        self._running: dict[str, _RunningMCP] = {}

    # ── registry ─────────────────────────────────────────────────────

    def load_registry(self, path: Path | None = None) -> None:
        registry_path = path or (ROOT_DIR / "mcp_registry.json")
        if not registry_path.exists():
            logger.bind(component="mcp").info("No mcp_registry.json found; external MCP servers disabled")
            return
        try:
            raw = json.loads(registry_path.read_text(encoding="utf-8"))
            for entry in raw:
                record = MCPServerRecord.model_validate(entry)
                self.registry[record.name] = record
                logger.bind(component="mcp", server=record.name).info(
                    "MCP server registered lazy={lazy} transport={transport}",
                    lazy=record.lazy,
                    transport=record.transport,
                )
        except Exception:
            logger.bind(component="mcp").exception("Failed to load mcp_registry.json")

    # ── lifecycle ────────────────────────────────────────────────────

    async def start_eager(self) -> None:
        # Applet-level eager starts (backward compat)
        if self.services.applets:
            for record in self.services.applets.registry.values():
                if record.enabled and record.mcp_start == "eager" and record.load == "eager":
                    self.started.add(record.name)
        # External MCP servers with lazy=false
        for name, record in self.registry.items():
            if record.enabled and not record.lazy:
                await self.ensure_server_started(name)

    async def ensure_started(self, applet_name: str) -> None:
        """Mark an applet's internal MCP as started (backward compat)."""
        self.started.add(applet_name)
        logger.bind(component="mcp", applet=applet_name).debug("MCP marked started")

    async def ensure_server_started(self, name: str) -> None:
        """Spawn an external MCP server if not already running."""
        if name in self._running:
            return
        record = self.registry.get(name)
        if record is None:
            logger.bind(component="mcp", server=name).warning("MCP server not found in registry")
            return

        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            logger.bind(component="mcp", server=name).error("mcp package not installed; cannot start external servers")
            self.degraded[name] = "mcp package not installed"
            return

        cmd, cmd_args = self._build_command(record)
        env = {**os.environ, **record.env}

        logger.bind(component="mcp", server=name).info(
            "Starting external MCP server command={cmd} args={args}",
            cmd=cmd,
            args=cmd_args,
        )

        running = _RunningMCP(record)
        stack = contextlib.AsyncExitStack()
        try:
            server_params = StdioServerParameters(command=cmd, args=cmd_args, env=env)
            transport = await stack.enter_async_context(stdio_client(server_params))
            read_stream, write_stream = transport
            session: ClientSession = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()

            tools_result = await session.list_tools()
            running.session = session
            running._cm_stack = stack
            running.tools = [
                ToolDefinition(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=tool.inputSchema if hasattr(tool, "inputSchema") else (tool.input_schema if hasattr(tool, "input_schema") else {}),
                    applet_name=f"mcp:{name}",
                )
                for tool in tools_result.tools
            ]
            self._running[name] = running
            logger.bind(component="mcp", server=name).info(
                "External MCP server started tools={tools}",
                tools=[t.name for t in running.tools],
            )
        except Exception:
            await stack.aclose()
            logger.bind(component="mcp", server=name).exception("Failed to start external MCP server")
            self.degraded[name] = "startup failed"

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Dispatch a tool call to an external MCP server."""
        running = self._running.get(server_name)
        if running is None or running.session is None:
            return {"error": f"MCP server '{server_name}' is not running"}
        try:
            result = await running.session.call_tool(tool_name, arguments)
            # MCP returns content as a list of content blocks; flatten to text
            if hasattr(result, "content") and isinstance(result.content, list):
                texts = []
                for block in result.content:
                    if hasattr(block, "text"):
                        texts.append(block.text)
                    else:
                        texts.append(str(block))
                return "\n".join(texts)
            return str(result)
        except Exception as exc:
            logger.bind(component="mcp", server=server_name, tool=tool_name).exception("MCP tool call failed")
            return {"error": f"MCP tool call failed: {exc}"}

    async def stop(self) -> None:
        self.started.clear()
        for name, running in list(self._running.items()):
            try:
                if running._cm_stack:
                    await running._cm_stack.aclose()
                logger.bind(component="mcp", server=name).info("External MCP server stopped")
            except Exception:
                logger.bind(component="mcp", server=name).exception("Error stopping MCP server")
        self._running.clear()

    # ── tool queries ─────────────────────────────────────────────────

    async def get_tools(self, applet: AppletBase) -> list[ToolDefinition]:
        """Get tools from an applet (backward compat for internal applets)."""
        if applet.requires_mcp:
            await self.ensure_started(applet.name)
        return applet.get_tools()

    def get_server_tools(self, server_name: str) -> list[ToolDefinition]:
        """Return cached tools for a running external MCP server."""
        running = self._running.get(server_name)
        return list(running.tools) if running else []

    def get_all_server_tools(self) -> list[ToolDefinition]:
        """Return tools from all running external MCP servers."""
        tools: list[ToolDefinition] = []
        for running in self._running.values():
            tools.extend(running.tools)
        return tools

    def get_registry_for_router(self) -> list[dict[str, str]]:
        """Return registry entries formatted for the routing model."""
        return [
            {"name": record.name, "description": record.description}
            for record in self.registry.values()
            if record.enabled
        ]

    def find_server_for_tool(self, tool_name: str) -> str | None:
        """Find which MCP server owns a given tool name."""
        for name, running in self._running.items():
            for tool in running.tools:
                if tool.name == tool_name:
                    return name
        return None

    # ── internal helpers ─────────────────────────────────────────────

    @staticmethod
    def _build_command(record: MCPServerRecord) -> tuple[str, list[str]]:
        if record.transport == "npm":
            return "npx", ["-y", record.package, *record.args]
        if record.transport == "command":
            return record.command, list(record.args)
        raise ValueError(f"Unknown MCP transport: {record.transport}")


class FileHandler:
    def __init__(self, services: ServiceContainer) -> None:
        self.services = services

    async def resolve_message(self, message: Message) -> Message:
        resolved: list[Attachment] = []
        logger.bind(component="files", session_id=message.session_id).debug("Resolving attachments count={count}", count=len(message.attachments))
        for item in message.attachments:
            if isinstance(item, Attachment):
                resolved.append(item)
                continue
            attachment = await self._resolve_attachment(item)
            if attachment:
                resolved.append(attachment)
                logger.bind(component="files", session_id=message.session_id).info(
                    "Attachment resolved filename={filename} type={file_type} chunked={chunked}",
                    filename=attachment.original_filename,
                    file_type=attachment.file_type,
                    chunked=attachment.chunked,
                )
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
        logger.bind(component="applets").info("Scanning applets path={path}", path=str(APPLETS_DIR))
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
                logger.bind(component="applets", applet=name).debug("Applet discovered enabled={enabled} load={load}", enabled=record.enabled, load=record.load)
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
                logger.bind(component="applets", applet=broken_name).exception("Applet discovery failed")
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
        logger.bind(component="applets", applet=name).info("Applet loaded")
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
        mcp_registry = self.services.mcp.get_registry_for_router()
        mcp_lines = [f"- {s['name']}: {s['description']}" for s in mcp_registry] if mcp_registry else ["- none"]
        mcp_running = list(self.services.mcp._running.keys())
        parts = [
            "Self-awareness:",
            "Available applets:",
            *applet_lines,
            "Available MCP servers:",
            *mcp_lines,
        ]
        if mcp_running:
            parts.append(f"Running MCP servers: {', '.join(mcp_running)}")
        parts.extend([
            "Connected channels:",
            *(channel_lines or ["- none"]),
            f"Scheduled tasks: {task_count}",
        ])
        return "\n".join(parts)

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
        logger.bind(component="channels").info("Scanning channels path={path}", path=str(CHANNELS_DIR))
        for path in sorted(CHANNELS_DIR.glob("*.py")):
            cls = discover_subclass(path, ChannelBase)
            if cls is None:
                continue
            channel = cls(self.services)
            self.channels[channel.name] = channel
            enabled = self.services.config.channels.get(channel.name, None)
            self._status[channel.name] = ChannelStatus(name=channel.name, enabled=enabled.enabled if enabled else True)
            logger.bind(component="channels", channel=channel.name).debug("Channel discovered enabled={enabled}", enabled=self._status[channel.name].enabled)

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
            logger.bind(component="channels", channel=name).info("Channel started")
        except Exception as exc:  # pragma: no cover
            self._status[name].connected = False
            self._status[name].details = str(exc)
            logger.bind(component="channels", channel=name).exception("Channel failed to start")


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
        logger.bind(component="scheduler", task_id=task.id).info(
            "Scheduled task registered name={name} trigger_type={trigger_type} trigger_value={trigger_value}",
            name=task.name,
            trigger_type=task.trigger_type,
            trigger_value=task.trigger_value,
        )
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
        source_channel = task.origin_channel or self.services.config.primary_channel or "web"
        channel_status = self.services.channels.statuses().get(source_channel)

        if task.trigger_type == "cron" and (source_channel not in self.services.channels.channels or not channel_status or not channel_status.enabled):
            logger.bind(component="scheduler", task_id=task_id, channel=source_channel).warning(
                "Skipping cron task because channel is unavailable"
            )
            return

        logger.bind(component="scheduler", task_id=task_id, channel=source_channel).info("Firing scheduled task")
        message = Message(
            text=task.prompt,
            source_channel=source_channel,
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
    max_tool_result_chars = 16_000

    def __init__(self, services: ServiceContainer) -> None:
        self.services = services
        self.logger = logger.bind(component="core")
        services.event_bus.subscribe("message.received", self._handle_message_event)
        services.event_bus.subscribe("message.response", self._handle_response_event)

    async def _handle_message_event(self, event) -> None:
        raw = event.payload["message"]
        message = Message.model_validate(raw)
        normalized = await self.services.files.resolve_message(message)
        self.logger.info(
            "Message received session_id={session_id} channel={channel} text_len={text_len} attachments={attachments}",
            session_id=normalized.session_id,
            channel=normalized.source_channel,
            text_len=len(normalized.text),
            attachments=len(normalized.attachments),
        )
        content = self._message_content(normalized)
        self.services.memory.working.add_turn(normalized.session_id, "user", content)
        await self._log_history(normalized.session_id, "user", content)

        try:
            mcp_servers = self.services.mcp.get_registry_for_router()
            selected_names = await self.services.provider.route_applets(
                normalized, list(self.services.applets.registry.values()), mcp_servers=mcp_servers or None,
            )
            # Separate applet names from MCP server names
            mcp_server_names = {r["name"] for r in mcp_servers} if mcp_servers else set()
            applet_names = [n for n in selected_names if n not in mcp_server_names]
            selected_mcp_names = [n for n in selected_names if n in mcp_server_names]

            selected = await self.services.applets.get_candidate_applets(applet_names)
            self.logger.debug(
                "Selected applets session_id={session_id} applets={applets} mcp_servers={mcp}",
                session_id=normalized.session_id,
                applets=applet_names,
                mcp=selected_mcp_names,
            )
            for applet in selected:
                await applet.on_message(normalized)

            # Start selected MCP servers (lazy activation)
            for mcp_name in selected_mcp_names:
                await self.services.mcp.ensure_server_started(mcp_name)

            always_tools = await self.services.applets.always_on_tools()
            selective_tools = []
            for applet in selected:
                selective_tools.extend(await self.services.mcp.get_tools(applet))
            # Add tools from selected MCP servers
            mcp_tools: list[ToolDefinition] = []
            for mcp_name in selected_mcp_names:
                mcp_tools.extend(self.services.mcp.get_server_tools(mcp_name))
            prompt = await self._build_prompt(normalized, selected_names)
            all_tools = always_tools + selective_tools + mcp_tools
            self.logger.debug(
                "Tool inventory session_id={session_id} tool_names={tool_names}",
                session_id=normalized.session_id,
                tool_names=[tool.name for tool in all_tools],
            )

            result = await self._complete_with_tool_call_retries(
                messages=prompt.messages,
                tools=all_tools,
                selected_names=selected_names,
                stage="initial_completion",
            )
            depth = 0
            while result.tool_calls and depth < self.services.config.max_tool_depth:
                prompt.messages.append(
                    {
                        "role": "assistant",
                        "content": result.text or "",
                        "tool_calls": result.tool_calls_payload,
                        "session_id": prompt.session_id,
                    }
                )
                for call in result.tool_calls:
                    output = await self._dispatch_tool(
                        call,
                        selected,
                        session_id=prompt.session_id,
                        source_channel=normalized.source_channel,
                    )
                    prompt.messages.append(
                        {
                            "role": "tool",
                            "content": self._serialize_tool_output(call.name, output),
                            "tool_call_id": call.id or call.name,
                            "name": call.name,
                            "session_id": prompt.session_id,
                        }
                    )
                depth += 1
                result = await self._complete_with_tool_call_retries(
                    messages=prompt.messages,
                    tools=all_tools,
                    selected_names=selected_names,
                    stage=f"tool_loop_depth_{depth}",
                )

            response_text = result.text
            self.logger.info("Message handled session_id={session_id} response_len={response_len}", session_id=normalized.session_id, response_len=len(response_text))
            asyncio.create_task(self.services.memory.consolidate_exchange(normalized.session_id, normalized.text, response_text))
        except Exception as exc:
            self.logger.exception("Message handling failed session_id={session_id}", session_id=normalized.session_id)
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
        self.logger.debug(
            "Delivering response session_id={session_id} channel={channel}",
            session_id=event.payload["session_id"],
            channel=event.payload["channel"],
        )
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

    async def _dispatch_tool(
        self,
        call: ToolCall,
        selected_applets: list[AppletBase],
        *,
        session_id: str,
        source_channel: str,
    ) -> Any:
        tool_logger = logger.bind(component="tool_call", tool_name=call.name, tool_call_id=call.id or "")
        runtime_arguments = dict(call.arguments)
        runtime_arguments["_runtime_session_id"] = session_id
        runtime_arguments["_runtime_source_channel"] = source_channel
        tool_logger.info("Dispatch start arguments={arguments}", arguments=preview(runtime_arguments))
        for applet in self.services.applets.instances.values():
            for tool in applet.get_tools():
                if tool.name == call.name:
                    tool_logger.debug("Dispatch target applet={applet}", applet=applet.name)
                    output = await applet.handle_tool(call.name, runtime_arguments)
                    tool_logger.info("Dispatch success output={output}", output=preview(output))
                    return output
        for applet in selected_applets:
            for tool in applet.get_tools():
                if tool.name == call.name:
                    tool_logger.debug("Dispatch target applet={applet}", applet=applet.name)
                    output = await applet.handle_tool(call.name, runtime_arguments)
                    tool_logger.info("Dispatch success output={output}", output=preview(output))
                    return output
        # Fallback: check running MCP servers
        mcp_server = self.services.mcp.find_server_for_tool(call.name)
        if mcp_server:
            tool_logger.debug("Dispatch target mcp_server={server}", server=mcp_server)
            output = await self.services.mcp.call_tool(mcp_server, call.name, runtime_arguments)
            tool_logger.info("Dispatch success (MCP) output={output}", output=preview(output))
            return output
        tool_logger.error("Dispatch failed unknown tool")
        return {"error": f"Unknown tool {call.name}"}

    async def _complete_with_tool_call_retries(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        selected_names: list[str],
        stage: str,
    ) -> LLMResult:
        max_retries = self.services.config.tool_call_retry_attempts
        session_id = self._extract_session_id(messages)
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                self.logger.debug(
                    "Model completion attempt stage={stage} attempt={attempt}/{total}",
                    stage=stage,
                    attempt=attempt + 1,
                    total=max_retries + 1,
                )
                return await self.services.provider.complete(messages=messages, tools=tools)
            except Exception as exc:
                if not self._is_tool_call_validation_error(exc):
                    raise
                last_error = exc
                missing_tool = self._extract_missing_tool_name(exc)
                if missing_tool:
                    injected = await self._ensure_missing_tool_available(missing_tool, tools, selected_names)
                    self.logger.warning(
                        "Missing tool detected tool={tool} injected={injected}",
                        tool=missing_tool,
                        injected=injected,
                    )
                self.logger.warning(
                    "Tool call validation failure stage={stage} attempt={attempt}/{total} error={error}",
                    stage=stage,
                    attempt=attempt + 1,
                    total=max_retries + 1,
                    error=preview(exc),
                )
                if attempt >= max_retries:
                    debug_tail = self._format_tool_call_debug(
                        stage=stage,
                        attempts=attempt + 1,
                        selected_names=selected_names,
                        tools=tools,
                        session_id=session_id,
                        error=exc,
                    )
                    raise RuntimeError(f"Tool call retries exhausted after {attempt + 1} attempts.{debug_tail}") from exc

                # Provide the exact validation error so the model can fix its tool arguments.
                validation_error = preview(str(exc), max_len=4000)
                available_tools = sorted({tool.name for tool in tools})
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Retrying after a tool schema validation failure. "
                            "Use the error details below to correct the next tool call. "
                            "Only call tools from the available tool list below. "
                            "Do not send null values; omit optional fields unless you provide a valid value that matches the schema exactly. "
                            "For scheduling, channel and session are resolved by backend runtime context, not by model arguments.\n\n"
                            f"Available tools: {available_tools}\n\n"
                            f"Validation error:\n{validation_error}"
                        ),
                        "session_id": session_id or "",
                    }
                )
                await asyncio.sleep(min(0.25 * (attempt + 1), 1.0))

        assert last_error is not None
        raise last_error

    def _is_tool_call_validation_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return "tool call validation failed" in text or "tool_use_failed" in text

    def _extract_missing_tool_name(self, exc: Exception) -> str | None:
        match = re.search(r"attempted to call tool '([^']+)' which was not in request\.tools", str(exc))
        if match:
            return match.group(1)
        return None

    async def _ensure_missing_tool_available(self, missing_tool: str, tools: list[ToolDefinition], selected_names: list[str]) -> bool:
        record = self.services.applets.registry.get(missing_tool)
        if record is None or not record.enabled:
            return False
        applet = await self.services.applets.load_applet(missing_tool)
        applet_tools = await self.services.mcp.get_tools(applet)
        existing = {tool.name for tool in tools}
        additions = [tool for tool in applet_tools if tool.name not in existing]
        if additions:
            tools.extend(additions)
        if missing_tool not in selected_names:
            selected_names.append(missing_tool)
        return bool(additions)

    def _extract_session_id(self, messages: list[dict[str, Any]]) -> str | None:
        for item in reversed(messages):
            session_id = item.get("session_id")
            if isinstance(session_id, str) and session_id:
                return session_id
        return None

    def _format_tool_call_debug(
        self,
        *,
        stage: str,
        attempts: int,
        selected_names: list[str],
        tools: list[ToolDefinition],
        session_id: str | None,
        error: Exception,
    ) -> str:
        if not self.services.config.tool_call_error_debug:
            return ""
        tool_names = sorted({tool.name for tool in tools})
        return "\nDebug info:" + "\n".join(
            [
                f"\n- stage: {stage}",
                f"- attempts: {attempts}",
                f"- session_id: {session_id or '<none>'}",
                f"- selected_applets: {selected_names}",
                f"- available_tools: {tool_names}",
                f"- model: {self.services.config.default_model}",
                f"- last_error: {error}",
            ]
        )

    def _message_content(self, message: Message) -> str:
        attachment_text = "\n\n".join(
            f"Attachment: {item.original_filename}\nType: {item.file_type}\nContent:\n{item.content}"
            for item in message.attachments
            if isinstance(item, Attachment)
        )
        return f"{message.text}\n\n{attachment_text}".strip()

    def _serialize_tool_output(self, tool_name: str, output: Any) -> str:
        try:
            serialized = json.dumps(output, ensure_ascii=False)
        except TypeError:
            serialized = json.dumps(str(output), ensure_ascii=False)

        if len(serialized) <= self.max_tool_result_chars:
            return serialized

        if isinstance(output, (dict, list)):
            summary = {
                "notice": (
                    f"Tool output truncated for prompt safety. Original serialized length was {len(serialized)} characters. "
                    "Use a narrower follow-up tool call if more detail is needed."
                ),
                "tool_name": tool_name,
                "original_type": type(output).__name__,
                "preview": preview(output, max_len=6000),
            }
            return json.dumps(summary, ensure_ascii=False)

        return json.dumps(
            {
                "notice": (
                    f"Tool output truncated for prompt safety. Original serialized length was {len(serialized)} characters. "
                    "Use a narrower follow-up tool call if more detail is needed."
                ),
                "tool_name": tool_name,
                "preview": serialized[:6000],
            },
            ensure_ascii=False,
        )

    async def _log_history(self, session_id: str, role: str, content: str) -> None:
        async with aiosqlite.connect(self.services.memory.db_path) as db:
            await db.execute(
                "insert into conversation_history(session_id, role, content, created_at) values (?, ?, ?, ?)",
                (session_id, role, content, datetime.utcnow().isoformat()),
            )
            await db.commit()
