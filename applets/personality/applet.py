from __future__ import annotations

from eugene.core import AppletBase
from eugene.models import ToolDefinition


class PersonalityApplet(AppletBase):
    name = "personality"
    description = "Always-on personality tools."
    load = "eager"
    inject = "always"
    can_disable = False

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(name="read_personality", description="Read compiled personality prompt.", applet_name=self.name, inject="always"),
            ToolDefinition(
                name="edit_personality",
                description="Add or update a named section in personality.toml.",
                input_schema={"type": "object", "properties": {"section": {"type": "string"}, "content": {"type": "object"}}},
                applet_name=self.name,
                inject="always",
            ),
        ]

    async def handle_tool(self, name: str, arguments: dict) -> str:
        if name == "read_personality":
            return self.services.personality.read()
        if name == "edit_personality":
            await self.services.personality.edit_section(arguments["section"], arguments["content"])
            return "Personality updated."
        raise ValueError(name)
