---
name: eugene-applets
description: Create, modify, and integrate native Eugene Applets to add capabilities, tools, background jobs, and context injections securely. Use when the user asks to "create an applet", "build an integration for Eugene", "add a native tool", "add a scheduled job in Eugene", or when native Python execution is preferred over an external MCP server.
---

# Eugene Applet Development

Applets are native Python plugins for Eugene that execute directly in the same process space. They are the most powerful way to give Eugene new capabilities natively. 

## When to use Applets vs. MCP
- **Use Applets for:** Deep integrations with Eugene's core (modifying agent context, listening to event bus, handling message streams directly, interacting with channels like Discord/Telegram).
- **Use MCP for:** External system sandboxing, tools built in other languages, or functionality that doesn't need deep integration with the event bus or routing logic.

## Creating an Applet

1. Create a new directory in `applets/<applet_name>/`
2. Create `applets/<applet_name>/applet.py` 
3. Implement a subclass of `AppletBase` (from `eugene.core`)
4. Ensure your configuration fields are properly typed in the class `Config` subclass using `FieldSpec`.

### Resources
- **[Applet Reference](file:///c:/Users/sam/Code/eugene/.agents/skills/eugene-applets/references/applet-reference.md)**: Full API documentation for `AppletBase` and its lifecycle.
- **[Applet Template](file:///c:/Users/sam/Code/eugene/.agents/skills/eugene-applets/assets/applet-template/applet.py)**: A boilerplate file to start from.

## Development Workflow
1. Use the template as a boilerplate! 
2. Restart the Eugene server to load the new applet (use the `run_command` tool to stop and restart the uvicorn process). Note: Eugene dynamically loads `applet.py` files on startup.
