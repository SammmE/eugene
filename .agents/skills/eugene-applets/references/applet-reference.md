# AppletBase Reference

Applets are subclasses of `eugene.core.AppletBase`. They provide tools, intercept messages, inject context, and map REST API routes.

## Configuration & Metatdata
```python
from eugene.core import AppletBase, FieldSpec

class MyApplet(AppletBase):
    name = "my_applet"             # Must match the directory name
    description = "My amazing tool" # Short description
    load = "lazy"                  # 'lazy' (default) or 'eager'
    inject = "selective"           # 'always', 'never', or 'selective' (default)
    mcp_start = "lazy"             # 'lazy' or 'eager'
    supported_extensions: list[str] = [] # E.g., [".pdf", ".txt"] for handle_file support
    requires_mcp = False           # Does it depend on an MCP server?
    can_disable = True             # Can the user disable it from config?

    # Define dynamic config fields exposed to the UI/Frontend
    class Config:
        fields = {
            "api_key": FieldSpec(default="", description="Service API Key", options=None),
        }
```

## Lifecycle Methods
Override any of these async properties to hook into Eugene's event loop:

- `async def on_load(self) -> None:` First method called when the applet loads on server start.
- `async def on_unload(self) -> None:` Called during server shutdown context. Use to close DBs/sockets.
- `async def on_message(self, message: Message) -> None:` Hook into every user message received. 
- `async def on_event(self, event: Event) -> None:` Event bus global receiver.

## Working with Tools
To expose tools to Eugene, implement these methods:
```python
from eugene.models import ToolDefinition

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="do_something",
                description="Does a thing.",
                input_schema={
                    "type": "object",
                    "properties": {"arg1": {"type": "string"}}
                },
                applet_name=self.name,
            )
        ]

    async def handle_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "do_something":
            return f"Did the thing with {arguments['arg1']}"
        raise ValueError(f"Unknown tool: {name}")
```

## Handling Context & Files
- `def get_context_injection(self) -> str:` Returns arbitrary text to dynamically append to the initial system prompt hook.
- `async def handle_file(self, attachment_ref: str) -> Attachment | None:` Extract text or metadata from active attached files before they hit LLM processing.

## Available Services (`self.services`)
As a subclass of `AppletBase`, `self.services` provides access to global containers:
- `self.services.event_bus`: Pub/Sub messaging.
- `self.services.memory`: Access active conversation `WorkingMemory`.
- `self.services.channels`: Interact with connected Web/Discord clients.
- `self.services.scheduler`: Interface to add/remove cron jobs.
