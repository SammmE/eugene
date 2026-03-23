from __future__ import annotations

from pathlib import Path

from eugene.core import AppletBase, FieldSpec
from eugene.models import ToolDefinition


class FileSystemApplet(AppletBase):
    name = "filesystem"
    description = "Sandboxed file read and write tools."
    load = "eager"
    inject = "selective"
    mcp_start = "eager"
    requires_mcp = True

    class Config:
        fields = {
            "root_path": FieldSpec(default=".", description="Root filesystem path for sandboxed operations."),
        }

    def _resolve(self, relative_path: str) -> Path:
        root = Path(self.config["root_path"]).resolve()
        target = (root / relative_path).resolve()
        if root not in [target, *target.parents]:
            raise ValueError("Path escapes configured root_path")
        return target

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(name="read_file", description="Read a file from the sandboxed root.", input_schema={"type": "object", "properties": {"path": {"type": "string"}}}, applet_name=self.name),
            ToolDefinition(name="write_file", description="Write text to a file in the sandboxed root.", input_schema={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}, applet_name=self.name),
            ToolDefinition(name="list_files", description="List files in a directory in the sandboxed root.", input_schema={"type": "object", "properties": {"path": {"type": "string"}}}, applet_name=self.name),
            ToolDefinition(name="move_file", description="Move a file in the sandboxed root.", input_schema={"type": "object", "properties": {"source": {"type": "string"}, "destination": {"type": "string"}}}, applet_name=self.name),
            ToolDefinition(name="delete_file", description="Delete a file in the sandboxed root.", input_schema={"type": "object", "properties": {"path": {"type": "string"}}}, applet_name=self.name),
        ]

    async def handle_tool(self, name: str, arguments: dict) -> str | list[str]:
        path = self._resolve(arguments.get("path", "."))
        if name == "read_file":
            return path.read_text(encoding="utf-8")
        if name == "write_file":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(arguments["content"], encoding="utf-8")
            return f"Wrote {path}"
        if name == "list_files":
            return sorted(item.name for item in path.iterdir())
        if name == "move_file":
            source = self._resolve(arguments["source"])
            destination = self._resolve(arguments["destination"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
            return f"Moved {source} to {destination}"
        if name == "delete_file":
            path.unlink(missing_ok=True)
            return f"Deleted {path}"
        raise ValueError(name)
