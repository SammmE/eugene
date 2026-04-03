# AppletBase Reference

Applets subclass `eugene.core.AppletBase`. They provide tools, inject context, intercept messages, and expose REST routes.

## Class Attributes & Config

```python
from eugene.core import AppletBase, FieldSpec

class MyApplet(AppletBase):
    name = "my_applet"              # Must match applet directory name
    description = "Does X"          # Used by the router to select this applet
    load = "lazy"                   # 'lazy' (on-demand) | 'eager' (startup)
    inject = "selective"            # 'always' | 'selective' | 'never'
    can_disable = True              # Whether the user can disable it
    supported_extensions: list[str] = []  # E.g. [".pdf"] for handle_file

    class Config:
        fields = {
            "api_key": FieldSpec(default="", description="Service API key"),
            "max_results": FieldSpec(default=10, description="Max results to return"),
        }
```

`self.config` is a `dict[str, Any]` — read values with `self.config.get("key", fallback)`.

### Config Priority (highest wins)

| Priority | Source |
|---|---|
| 1 (highest) | Environment variable `{APPLET_NAME}_{FIELD_NAME}` |
| 2 | User override `eugene_data/applet_configs/{name}.json` |
| 3 | `applet.toml` values |
| 4 | `FieldSpec` default |

> **Rule:** `.env` is for **secrets only** — passwords, API keys, tokens. Non-sensitive config (hosts, ports, limits) belongs in `applet.toml`.

**Environment variable convention**: uppercase applet name + `_` + uppercase field name.  
Examples: `EMAIL_MANAGER_IMAP_PASSWORD`, `CALENDAR_CALDAV_PASSWORD`.

Types are coerced to match the `FieldSpec` default (bool, int, float, or str).

### FieldSpec options

```python
FieldSpec(
    default=...,          # Any Python literal; type controls env var coercion
    description="...",    # Shown in the UI
    options=["a", "b"],   # Optional: restricts UI to a dropdown
    dynamic_source="dynamic:active_channels",  # Optional: UI populates from live data
)
```

## Lifecycle Methods

```python
async def on_load(self) -> None: ...    # Called once when applet loads
async def on_unload(self) -> None: ...  # Called on server shutdown
async def on_message(self, message: Message) -> None: ...   # Every user message
async def on_event(self, event: Event) -> None: ...         # Every event bus event
```

## Tools

```python
from eugene.models import ToolDefinition

def get_tools(self) -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="do_something",
            description="Does a thing.",
            input_schema={
                "type": "object",
                "properties": {"arg": {"type": "string"}},
                "required": ["arg"],
            },
            applet_name=self.name,
            inject="selective",  # 'always' | 'selective' | 'never'
        )
    ]

async def handle_tool(self, name: str, arguments: dict[str, Any]) -> Any:
    if name == "do_something":
        return f"Result: {arguments['arg']}"
    raise ValueError(f"Unknown tool: {name}")
```

Two runtime-injected keys are always present in `arguments`:
- `_runtime_session_id` — active session ID
- `_runtime_source_channel` — originating channel name

## Context Injection

```python
def get_context_injection(self) -> str:
    return "Injected into the system prompt for every request."
```

Only called when `inject = "always"`.

## File Handling

```python
from eugene.models import Attachment

async def handle_file(self, attachment_ref: str) -> Attachment | None:
    # attachment_ref is a file path string
    ...
```

Only called when `supported_extensions` contains the file's extension.

## REST Routes

```python
from fastapi import APIRouter

def get_routes(self) -> list[tuple[str, APIRouter]]:
    router = APIRouter()

    @router.get("/status")
    async def status():
        return {"ok": True}

    return [("/", router)]
    # Mounted at /applets/{applet_name}/
```

## Services (`self.services`)

| Attribute | Purpose |
|---|---|
| `self.services.event_bus` | Pub/sub — `await publish("event.name", {...})` |
| `self.services.memory` | Working memory — `search_memory`, `store_exchange` |
| `self.services.channels` | Deliver messages to web/Discord/Telegram |
| `self.services.scheduler` | Register/delete `ScheduledTask` objects |
| `self.services.applets` | Applet registry and instance access |
| `self.services.config` | Global `EugeneConfig` |
| `self.services.provider` | Call LLM directly |

## applet.toml format

Every applet folder must have an `applet.toml`:

```toml
[applet.my_applet]
description = "Short description shown in the UI"
# Config field values go here (optional — defaults come from FieldSpec)
api_key = ""
max_results = 10
```

The table name (`my_applet`) must match the applet directory name and `name` class attribute.
