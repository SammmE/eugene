from __future__ import annotations
from typing import Any
from eugene.core import AppletBase, FieldSpec
from eugene.models import ToolDefinition

class CustomApplet(AppletBase):
    name = "custom_applet"
    description = "Boilerplate applet example"
    load = "lazy"
    inject = "selective"
    can_disable = True

    class Config:
        fields = {
            "example_setting": FieldSpec(default="foo", description="An example setting"),
        }

    async def on_load(self) -> None:
        self.logger.info("Custom applet loaded!")

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
                    "required": ["param"]
                },
                applet_name=self.name,
            )
        ]

    async def handle_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "custom_tool":
            param = arguments.get("param")
            return f"Processed {param} using custom_tool"
        raise ValueError(f"Unknown tool: {name}")
