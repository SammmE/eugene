from __future__ import annotations
from typing import Any
import psutil
from eugene.core import AppletBase, FieldSpec
from eugene.models import ToolDefinition

class SystemMonitorApplet(AppletBase):
    name = "system_monitor"
    description = "Monitors system resources like CPU, Memory, and Disk Space, warning on low disk space."
    load = "lazy"
    inject = "selective"
    can_disable = True

    class Config:
        fields = {
            "disk_warning_threshold": FieldSpec(default=10.0, description="Warning threshold for disk space in GB"),
            "warning_channel": FieldSpec(default="telegram", description="Channel to send warnings (e.g., discord, telegram)")
        }

    async def on_load(self) -> None:
        self.logger.info("System Monitor applet loaded")
        try:
            if hasattr(self.services, "scheduler") and hasattr(self.services.scheduler, "add_job"):
                self.services.scheduler.add_job(
                    self.check_and_warn_disk_space,
                    "interval",
                    minutes=60,
                    id="system_monitor_disk_check"
                )
        except Exception as e:
            self.logger.warning(f"Could not hook into scheduler for periodic disk checks: {e}")

    async def check_and_warn_disk_space(self) -> None:
        try:
            disk = psutil.disk_usage('/')
            free_gb = disk.free / (1024**3)
            threshold = float(self.config.get("disk_warning_threshold", 10.0))
            if free_gb < threshold:
                channel = self.config.get("warning_channel", "telegram")
                message = f"⚠️ Low Disk Space Warning! Only {free_gb:.2f} GB left on root partition."
                self.logger.warning(message)
                
                # Emit event on the bus that personality/discord/telegram applets can hook into
                if hasattr(self.services, "event_bus"):
                    await self.services.event_bus.publish(
                        "system.warning", 
                        {"source": self.name, "message": message, "channel": channel}
                    )
        except Exception as e:
            self.logger.error(f"Error checking disk space: {e}")

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="get_system_stats",
                description="Returns current CPU, memory, and disk usage statistics.",
                input_schema={
                    "type": "object",
                    "properties": {}
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="check_disk_space",
                description="Returns the current disk space remaining.",
                input_schema={
                    "type": "object",
                    "properties": {}
                },
                applet_name=self.name,
            )
        ]

    async def handle_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "get_system_stats":
            cpu = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            return {
                "cpu_percent": cpu,
                "memory_total_gb": round(mem.total / (1024**3), 2),
                "memory_used_gb": round(mem.used / (1024**3), 2),
                "memory_percent": mem.percent,
                "disk_total_gb": round(disk.total / (1024**3), 2),
                "disk_free_gb": round(disk.free / (1024**3), 2),
                "disk_percent": disk.percent
            }
        elif name == "check_disk_space":
            disk = psutil.disk_usage('/')
            free_gb = disk.free / (1024**3)
            return f"Free disk space on root: {free_gb:.2f} GB ({100 - disk.percent:.1f}% free)"
            
        raise ValueError(f"Unknown tool: {name}")
