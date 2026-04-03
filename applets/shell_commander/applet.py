from __future__ import annotations
from typing import Any
import subprocess
from eugene.core import AppletBase, FieldSpec
from eugene.models import ToolDefinition

class ShellCommanderApplet(AppletBase):
    name = "shell_commander"
    description = "Allows Eugene to execute arbitrary shell commands."
    load = "lazy"
    inject = "never"
    can_disable = True

    class Config:
        fields = {
            "allow_execution": FieldSpec(default=False, description="Must be set to True to allow execution. DANGEROUS!")
        }

    async def on_load(self) -> None:
        self.logger.info("Shell Commander applet loaded")

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="run_command",
                description="Run a terminal command on the host system. Returns stdout and stderr.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                    },
                    "required": ["command"]
                },
                applet_name=self.name,
            )
        ]

    async def handle_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "run_command":
            if not str(self.config.get("allow_execution", "")).lower() in ("true", "1", "yes"):
                return "Execution is disabled by configuration. Enable 'allow_execution' in setting for this applet."
            
            command = arguments.get("command")
            if not command:
                return "No command provided."
                
            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                output = f"EXIT CODE: {result.returncode}\n"
                if result.stdout:
                    output += f"\nSTDOUT:\n{result.stdout}"
                if result.stderr:
                    output += f"\nSTDERR:\n{result.stderr}"
                    
                return output
            except subprocess.TimeoutExpired:
                return "Execution error: Command timed out after 60 seconds."
            except Exception as e:
                return f"Execution error: {str(e)}"
        raise ValueError(f"Unknown tool: {name}")
