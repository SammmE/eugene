from __future__ import annotations

from uuid import uuid4

from eugene.core import AppletBase, FieldSpec
from eugene.models import ProactiveTrigger, ScheduledTask, ToolDefinition


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
            ToolDefinition(name="list_proactive_triggers", description="List all proactive event-based triggers.", applet_name=self.name),
            ToolDefinition(
                name="scheduler",
                description="Legacy alias to create a scheduled task.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "time": {"type": "string"},
                        "content": {"type": "string"},
                        "name": {"type": "string"},
                        "cron": {"type": "string"},
                        "run_at": {"type": "string"},
                        "prompt": {"type": "string"},
                    },
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="create_scheduled_task",
                description="Create a scheduled task with a cron expression or run_at timestamp.",
                input_schema={"type": "object", "properties": {"name": {"type": "string"}, "prompt": {"type": "string"}, "cron": {"type": "string"}, "run_at": {"type": "string"}}},
                applet_name=self.name,
            ),
            ToolDefinition(
                name="delete_scheduled_task",
                description="Delete a scheduled task by id.",
                input_schema={"type": "object", "properties": {"task_id": {"type": "string"}}},
                applet_name=self.name,
            ),
            ToolDefinition(
                name="create_proactive_trigger",
                description=(
                    "Create an event-based proactive trigger after the user explicitly asks for ongoing automation. "
                    "Use exact trigger emitter names from the trigger catalog and keep filters minimal for efficiency."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "source_applet": {"type": "string"},
                        "signal_name": {"type": "string"},
                        "prompt": {"type": "string", "description": "What Eugene should do when the trigger fires."},
                        "match_required": {
                            "type": "object",
                            "description": "Exact key/value filters that must match the emitted payload.",
                            "additionalProperties": {"type": ["string", "number", "boolean"]},
                        },
                        "match_contains": {
                            "type": "object",
                            "description": "Case-insensitive substring filters keyed by payload field.",
                            "additionalProperties": {"type": "string"},
                        },
                        "cooldown_seconds": {"type": "integer"},
                        "origin_channel": {"type": "string"},
                    },
                    "required": ["name", "source_applet", "signal_name", "prompt"],
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="delete_proactive_trigger",
                description="Delete a proactive trigger by id.",
                input_schema={"type": "object", "properties": {"trigger_id": {"type": "string"}}, "required": ["trigger_id"]},
                applet_name=self.name,
            ),
        ]

    async def handle_tool(self, name: str, arguments: dict) -> str | list[dict]:
        if name == "list_scheduled_tasks":
            return [task.model_dump() for task in self.services.scheduler.tasks.values()]
        if name == "list_proactive_triggers":
            return [trigger.model_dump(mode="json") for trigger in self.services.proactive.triggers.values()]

        if name in {"create_scheduled_task", "scheduler"}:
            if name == "scheduler":
                action = str(arguments.get("action", "schedule")).lower()
                if action not in {"schedule", "create"}:
                    raise ValueError(f"Unsupported scheduler action: {action}")
                task_name = str(arguments.get("name") or "Scheduled task")
                task_prompt = str(arguments.get("content") or arguments.get("prompt") or "")
                task_cron = arguments.get("cron")
                task_run_at = arguments.get("time") or arguments.get("run_at")
            else:
                task_name = str(arguments["name"])
                task_prompt = str(arguments["prompt"])
                task_cron = arguments.get("cron")
                task_run_at = arguments.get("run_at")

            if not task_cron and not task_run_at:
                raise ValueError("Either 'cron' or 'run_at' (or 'time' for scheduler alias) is required")

            runtime_session_id = arguments.get("_runtime_session_id")
            runtime_source_channel = arguments.get("_runtime_source_channel")
            effective_session_id = runtime_session_id
            effective_channel = runtime_source_channel or self.config["primary_channel"]
            task = ScheduledTask(
                id=str(uuid4()),
                name=task_name,
                prompt=task_prompt,
                trigger_type="cron" if task_cron else "date",
                trigger_value=task_cron if task_cron else task_run_at,
                origin_channel=effective_channel,
                session_id=effective_session_id,
                applet_name=self.name,
                metadata={
                    "source_tool": name,
                    "resolved_channel": effective_channel,
                    "resolved_session_id": effective_session_id,
                    "runtime_session_id": runtime_session_id,
                    "runtime_source_channel": runtime_source_channel,
                },
            )
            await self.services.scheduler.register(task)
            return f"Scheduled task created: {task.id}"
        if name == "delete_scheduled_task":
            await self.services.scheduler.delete(arguments["task_id"])
            return "Scheduled task deleted."
        if name == "create_proactive_trigger":
            source_applet = str(arguments["source_applet"])
            signal_name = str(arguments["signal_name"])
            applet_record = self.services.applets.registry.get(source_applet)
            if applet_record is None or not applet_record.enabled:
                raise ValueError(f"Unknown or disabled source applet: {source_applet}")
            source_instance = await self.services.applets.load_applet(source_applet)
            available_signals = {definition.name for definition in source_instance.get_trigger_definitions()}
            if signal_name not in available_signals:
                raise ValueError(f"Signal '{signal_name}' is not exposed by applet '{source_applet}'")

            runtime_session_id = arguments.get("_runtime_session_id")
            runtime_source_channel = arguments.get("_runtime_source_channel")
            trigger = ProactiveTrigger(
                id=str(uuid4()),
                name=str(arguments["name"]),
                description=str(arguments.get("description") or ""),
                source_applet=source_applet,
                signal_name=signal_name,
                prompt=str(arguments["prompt"]),
                origin_channel=str(arguments.get("origin_channel") or runtime_source_channel or self.config["primary_channel"]),
                session_id=runtime_session_id,
                match_required=dict(arguments.get("match_required") or {}),
                match_contains=dict(arguments.get("match_contains") or {}),
                cooldown_seconds=int(arguments.get("cooldown_seconds") or 0),
                metadata={
                    "source_tool": name,
                    "runtime_session_id": runtime_session_id,
                    "runtime_source_channel": runtime_source_channel,
                },
            )
            await self.services.proactive.register(trigger)
            return f"Proactive trigger created: {trigger.id}"
        if name == "delete_proactive_trigger":
            await self.services.proactive.delete(str(arguments["trigger_id"]))
            return "Proactive trigger deleted."
        raise ValueError(name)
