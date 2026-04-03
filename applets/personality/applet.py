from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from eugene.core import AppletBase, FieldSpec
from eugene.models import ToolDefinition

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

try:
    import tomli_w  # type: ignore
except ImportError:  # pragma: no cover
    tomli_w = None

try:
    from watchfiles import awatch  # type: ignore
except ImportError:  # pragma: no cover
    awatch = None

# Default personality content written on first run if no file is found.
_DEFAULT_PERSONALITY = """\
[identity]
name = "Eugene"
role = "Personal AI assistant"

[behavior]
tone = "helpful and concise"
clarification = "ask only when it materially changes the outcome"
"""


class PersonalityApplet(AppletBase):
    name = "personality"
    description = "Reads and updates Eugene's personality configuration."
    load = "eager"
    inject = "always"
    can_disable = False

    class Config:
        fields = {
            "personality_file": FieldSpec(
                default="",
                description=(
                    "Path to the personality TOML file. "
                    "Defaults to personality.toml in this applet's folder. "
                    "Supports absolute paths or paths relative to the applet folder."
                ),
            ),
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_load(self) -> None:
        self._compiled: str = ""
        self._watch_task: asyncio.Task[None] | None = None
        self._toml_path: Path = self._resolve_path()
        self._ensure_default()
        await self._reload()
        if awatch is not None:
            self._watch_task = asyncio.create_task(self._watch())
        self.logger.info("Personality applet loaded path={path}", path=str(self._toml_path))

    async def on_unload(self) -> None:
        if self._watch_task:
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch_task

    # ------------------------------------------------------------------
    # Context injection
    # ------------------------------------------------------------------

    def get_context_injection(self) -> str:
        return self._compiled

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="read_personality",
                description="Read the compiled personality prompt.",
                applet_name=self.name,
                inject="always",
            ),
            ToolDefinition(
                name="edit_personality",
                description="Add or update a named section in personality.toml.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "section": {"type": "string"},
                        "content": {"type": "object"},
                    },
                    "required": ["section", "content"],
                },
                applet_name=self.name,
                inject="always",
            ),
        ]

    async def handle_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "read_personality":
            return self._compiled
        if name == "edit_personality":
            await self._edit_section(arguments["section"], arguments["content"])
            return "Personality updated."
        raise ValueError(name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_path(self) -> Path:
        """Return the effective personality TOML path."""
        configured: str = str(self.config.get("personality_file") or "")
        if configured:
            p = Path(configured)
            if not p.is_absolute():
                p = Path(self.record.folder_path) / p
            return p.resolve()
        # Default: personality.toml inside this applet's folder
        return Path(self.record.folder_path) / "personality.toml"

    def _ensure_default(self) -> None:
        """Create a default personality.toml if it doesn't exist yet."""
        if not self._toml_path.exists():
            self._toml_path.parent.mkdir(parents=True, exist_ok=True)
            self._toml_path.write_text(_DEFAULT_PERSONALITY, encoding="utf-8")
            self.logger.info(
                "Created default personality.toml at path={path}", path=str(self._toml_path)
            )

    async def _reload(self) -> None:
        try:
            raw = self._toml_path.read_bytes()
            data: dict[str, Any] = tomllib.loads(raw.decode("utf-8"))
        except Exception as exc:
            self.logger.warning(
                "Failed to load personality TOML path={path} error={error}",
                path=str(self._toml_path),
                error=exc,
            )
            return
        blocks: list[str] = []
        for section, values in data.items():
            if isinstance(values, dict):
                formatted = "\n".join(f"- {key}: {value}" for key, value in values.items())
                blocks.append(f"[{section}]\n{formatted}")
        self._compiled = "\n\n".join(blocks)
        self.logger.debug(
            "Personality reloaded sections={count}", count=len(data)
        )
        await self.services.event_bus.publish(
            "personality.updated", {"path": str(self._toml_path)}
        )

    async def _edit_section(self, section: str, content: dict[str, Any]) -> None:
        if tomli_w is None:
            raise RuntimeError(
                "tomli-w is required to edit personality.toml. Install it with: uv add tomli-w"
            )
        try:
            raw = self._toml_path.read_bytes()
            data: dict[str, Any] = tomllib.loads(raw.decode("utf-8"))
        except Exception:
            data = {}
        data[section] = content
        self._toml_path.write_text(tomli_w.dumps(data), encoding="utf-8")
        await self._reload()

    async def _watch(self) -> None:
        assert awatch is not None
        async for _ in awatch(str(self._toml_path)):
            await self._reload()
