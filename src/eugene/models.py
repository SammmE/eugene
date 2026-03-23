from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TriggerKind(str, Enum):
    HUMAN = "human"
    SCHEDULED = "scheduled"


class Attachment(BaseModel):
    original_filename: str
    file_type: str
    content: str
    chunked: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    text: str
    source_channel: str
    session_id: str
    attachments: list[Attachment | str] = Field(default_factory=list)
    trigger: TriggerKind = TriggerKind.HUMAN
    metadata: dict[str, Any] = Field(default_factory=dict)


class Event(BaseModel):
    event_type: str
    payload: dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    applet_name: str
    inject: Literal["always", "selective", "never"] = "selective"

    def as_llm_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema or {"type": "object", "properties": {}},
            },
        }


class ToolCall(BaseModel):
    id: str | None = None
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LLMResult(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost: float = 0.0
    finish_reason: str | None = None


class ScheduledTask(BaseModel):
    id: str
    name: str
    trigger_type: Literal["cron", "date"]
    trigger_value: str
    prompt: str
    origin_channel: str | None = None
    session_id: str | None = None
    applet_name: str | None = None
    created_by: Literal["conversation", "applet"] = "conversation"
    metadata: dict[str, Any] = Field(default_factory=dict)
    active: bool = True


class AppletRecord(BaseModel):
    name: str
    description: str
    module_path: str
    folder_path: str
    enabled: bool = True
    load: Literal["eager", "lazy"] = "lazy"
    inject: Literal["always", "selective", "never"] = "selective"
    mcp_start: Literal["eager", "lazy"] = "lazy"
    can_disable: bool = True
    instance: Any | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    status: Literal["discovered", "loaded", "degraded", "disabled"] = "discovered"


class ChannelStatus(BaseModel):
    name: str
    enabled: bool = True
    connected: bool = False
    details: str = ""


class ConversationTurn(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ProviderCheckResult(BaseModel):
    ok: bool
    message: str
