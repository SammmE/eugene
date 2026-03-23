from __future__ import annotations

from eugene.core import AppletBase
from eugene.models import ToolDefinition


class MemoryApplet(AppletBase):
    name = "memory"
    description = "Search long-term memory and summarize working memory."
    load = "eager"
    inject = "always"
    can_disable = False

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="search_memory",
                description="Search long-term memory for relevant prior information.",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
                applet_name=self.name,
                inject="always",
            ),
            ToolDefinition(
                name="summarize_working_memory",
                description="Compress the rolling conversation window for the active session.",
                input_schema={"type": "object", "properties": {"session_id": {"type": "string"}}},
                applet_name=self.name,
                inject="always",
            ),
        ]

    async def handle_tool(self, name: str, arguments: dict) -> list[str] | str:
        if name == "search_memory":
            return await self.services.memory.search_memory(arguments["query"])
        if name == "summarize_working_memory":
            return await self.services.memory.summarize_working_memory(arguments["session_id"])
        raise ValueError(name)
