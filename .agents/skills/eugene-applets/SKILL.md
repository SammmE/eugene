---
name: eugene-applets
description: Create, modify, and integrate native Eugene Applets to add capabilities, tools, background jobs, and context injections securely. Use when the user asks to "create an applet", "build an integration for Eugene", "add a native tool", "add a scheduled job in Eugene", or when native Python execution is preferred over an external MCP server.
---

# Eugene Applet Development

Applets are native Python plugins that run inside Eugene's process. They are the most powerful extension point.

## Applets vs. MCP

- **Applet**: Deep Eugene integration — event bus, context injection, routing, scheduling. Write in Python.
- **MCP**: External process sandboxing, other languages, tools with no need for Eugene internals.

## Creating an Applet

1. Create `applets/<applet_name>/applet.py` and `applets/<applet_name>/applet.toml`
2. Subclass `AppletBase` from `eugene.core`
3. Declare all config fields in the inner `Config` class using `FieldSpec`
4. Restart the Eugene server — applets are discovered at startup

**Full API reference**: [applet-reference.md](references/applet-reference.md)  
**Boilerplate to start from**: [applet.py template](assets/applet-template/applet.py)

## Configuration & Environment Variables

All applet config uses `Config.fields` with `FieldSpec`. Values are sourced in this priority order (highest wins):

1. **Environment variables** — `{APPLET_NAME}_{FIELD_NAME}` (uppercase, e.g. `EMAIL_MANAGER_IMAP_PASSWORD`)
2. User overrides — `eugene_data/applet_configs/{name}.json`
3. `applet.toml` values
4. `FieldSpec` defaults

> **Rule:** `.env` is for **secrets only** (passwords, API keys, tokens). Everything else — hosts, ports, limits, flags — belongs in `applet.toml`.

**`.env.example`** documents the env var for every built-in applet — use it as reference.

### applet.toml format

Every applet folder needs an `applet.toml`:

```toml
[applet.my_applet]
description = "What this applet does"
my_field = "default_value"
```

The TOML key must match the directory name exactly.

## Development Workflow

1. Use the [template](assets/applet-template/applet.py) as a starting point
2. Declare secrets in `.env` using the `APPLET_NAME_FIELD` convention
3. Restart Eugene to load (`uvicorn eugene.main:app --reload` or kill/restart)
4. Test via the web UI or by asking Eugene to use the tool
