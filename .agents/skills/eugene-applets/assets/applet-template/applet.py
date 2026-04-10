from __future__ import annotations

from typing import Any

from eugene.core import AppletBase, FieldSpec
from eugene.models import ToolDefinition, TriggerDefinition


class CustomApplet(AppletBase):
    name = "custom_applet"
    description = "Boilerplate applet example"
    load = "lazy"
    inject = "selective"
    can_disable = True

    class Config:
        fields = {
            "example_setting": FieldSpec(default="foo", description="An example setting"),
            "watch_enabled": FieldSpec(default=False, description="Emit proactive trigger sources in the background."),
        }

    async def on_load(self) -> None:
        self.logger.info("Custom applet loaded")

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="custom_tool",
                description="A custom tool that does something.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "param": {"type": "string"},
                    },
                    "required": ["param"],
                },
                applet_name=self.name,
            )
        ]

    def get_trigger_definitions(self) -> list[TriggerDefinition]:
        return [
            TriggerDefinition(
                name="custom_event",
                description="Emitted when the applet notices something worth proactive handling.",
                applet_name=self.name,
                payload_schema={
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "severity": {"type": "string"},
                    },
                },
            )
        ]

    async def handle_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "custom_tool":
            param = arguments.get("param")
            return f"Processed {param} using custom_tool"
        raise ValueError(f"Unknown tool: {name}")

    async def emit_example_source(self, summary: str) -> None:
        await self.emit_trigger(
            "custom_event",
            {
                "summary": summary,
                "severity": "info",
            },
        )
