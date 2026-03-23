from __future__ import annotations

from uuid import uuid4

from eugene.core import AppletBase, FieldSpec
from eugene.models import ScheduledTask, ToolDefinition


class SchedulerApplet(AppletBase):
    name = "scheduler"
    description = "Manage scheduled tasks."
    load = "eager"
    inject = "selective"
    can_disable = False

    class Config:
        fields = {
            "primary_channel": FieldSpec(default="web", description="Default delivery channel.", dynamic_source="dynamic:active_channels"),
        }

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(name="list_scheduled_tasks", description="List all scheduled tasks.", applet_name=self.name),
            ToolDefinition(
                name="create_scheduled_task",
                description="Create a scheduled task with a cron expression or run_at timestamp.",
                input_schema={"type": "object", "properties": {"name": {"type": "string"}, "prompt": {"type": "string"}, "cron": {"type": "string"}, "run_at": {"type": "string"}, "channel": {"type": "string"}, "session_id": {"type": "string"}}},
                applet_name=self.name,
            ),
            ToolDefinition(
                name="delete_scheduled_task",
                description="Delete a scheduled task by id.",
                input_schema={"type": "object", "properties": {"task_id": {"type": "string"}}},
                applet_name=self.name,
            ),
        ]

    async def handle_tool(self, name: str, arguments: dict) -> str | list[dict]:
        if name == "list_scheduled_tasks":
            return [task.model_dump() for task in self.services.scheduler.tasks.values()]
        if name == "create_scheduled_task":
            task = ScheduledTask(
                id=str(uuid4()),
                name=arguments["name"],
                prompt=arguments["prompt"],
                trigger_type="cron" if arguments.get("cron") else "date",
                trigger_value=arguments["cron"] if arguments.get("cron") else arguments["run_at"],
                origin_channel=arguments.get("channel") or self.config["primary_channel"],
                session_id=arguments.get("session_id"),
                applet_name=self.name,
            )
            await self.services.scheduler.register(task)
            return f"Scheduled task created: {task.id}"
        if name == "delete_scheduled_task":
            await self.services.scheduler.delete(arguments["task_id"])
            return "Scheduled task deleted."
        raise ValueError(name)
