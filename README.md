# Eugene

Eugene is a single-process personal AI assistant built with FastAPI, LiteLLM, modular applets, and channel adapters. It gives you one runtime that can accept messages from the web UI, Discord, Telegram, and a separate terminal client, then route each request through the tools and applets that make sense for that task.

The project is designed around a few core ideas:

- One host process owns the API, WebSocket transport, applet lifecycle, memory, scheduling, and channel delivery.
- Capabilities live in applets, so new tools and behaviors can be added without rebuilding the whole assistant.
- Configuration is split between non-secret TOML files and secret environment variables.
- The web frontend is a Vite/React app that builds into `static/` and is served directly by FastAPI.

## What Eugene Does

Eugene can act as a general assistant with attached tools for:

- multi-channel chat
- long-term and working memory
- scheduled and proactive tasks
- web search and page fetching
- PDF attachment extraction
- shell command execution when explicitly enabled
- sandboxed Python analysis when explicitly enabled
- email, calendar, weather, and Schoology integrations

At runtime, Eugene loads applets from `applets/`, channels from `channels/`, checks provider configuration, starts the event bus, mounts API routes, serves the frontend, and begins accepting messages over WebSockets and external channels.

## Architecture

High-level pieces:

- Backend: FastAPI application in `src/eugene/main.py`
- Core orchestration: services and applet/channel managers in `src/eugene/services.py`
- Config loading: `src/eugene/config.py`
- Public API: `src/eugene/api.py`
- Channels: `channels/`
- Applets: `applets/`
- Frontend: `frontend/`
- Built static UI: `static/`
- Runtime data: `eugene_data/`

Important runtime behavior:

- Eugene serves the built frontend from `static/`.
- The web chat transport uses `/ws/{session_id}` with API-key authentication.
- REST endpoints live under `/api`.
- Conversation history and token usage are stored in SQLite under `eugene_data/`.
- Long-term memory can use Chroma when available.
- Frontend auto-reload can watch `frontend/`, rebuild static assets, and notify connected browsers.

## Included Channels

The repository currently includes these channels:

- `web`: browser-based chat over WebSockets
- `discord`: Discord client via `discord.py`
- `telegram`: Telegram bot via `python-telegram-bot`

The primary channel is configured in `eugene.toml`, and individual channel enablement lives under `[channels.<name>]`.

## Included Applets

The repository currently ships with these applets:

- `user_prompt`: asks users follow-up questions; uses a modal flow on web and plain text prompts on chat channels
- `personality`: reads and updates Eugene's personality configuration
- `email_manager`: IMAP/SMTP email fetch, read, send, draft, move, and proactive polling
- `calendar`: CalDAV calendar listing and event creation
- `web_browser`: DuckDuckGo-backed search and URL fetching
- `shell_commander`: host shell execution, disabled by default
- `system_monitor`: CPU, memory, and disk monitoring with warning support
- `weather`: Open-Meteo weather lookup and contextual weather injection
- `scheduler`: recurring scheduled tasks
- `memory`: always-on working memory and long-term retrieval
- `clock`: timezone-aware time/date context
- `pdf_reader`: extracts text from PDF attachments before prompt assembly
- `python_repl`: sandboxed Python analysis with output capture and artifact retention, disabled by default
- `schoology`: Schoology feed and event access

## Requirements

- Python 3.11 or newer
- `uv` for the recommended Python workflow
- Node.js and npm if you want to build or iterate on the frontend
- At least one supported model provider API key that matches the models configured in `eugene.toml`

## Installation

Install Python dependencies:

```bash
uv sync
```

If you want to build the frontend manually:

```bash
cd frontend
npm install
npm run build
```

That writes the production UI bundle to `static/`.

## Configuration

Eugene uses two main configuration layers:

- `eugene.toml`: non-secret runtime configuration
- `.env`: secrets and tokens

### `eugene.toml`

This file controls things like:

- API key for the Eugene host
- default, router, and fallback model names
- host and port
- prompt compression settings
- memory and tool-call limits
- log settings
- frontend auto-reload behavior
- enabled channels and channel-local config

Example fields already present in the repo include:

- `api_key`
- `default_model`
- `router_model`
- `host`
- `port`
- `primary_channel`
- `max_tool_depth`
- `working_memory_turns`
- `frontendAutoReload`

### `.env`

`.env` is for secrets only. Eugene loads it automatically at startup.

Provider keys supported by the example env file include:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GOOGLE_API_KEY`
- `GEMINI_API_KEY`
- `GROQ_API_KEY`
- `NVIDIA_NIM_API_KEY`
- `MISTRAL_API_KEY`
- `XAI_API_KEY`

Channel secrets:

- `DISCORD_BOT_TOKEN`
- `TELEGRAM_BOT_TOKEN`

Applet secrets currently documented:

- `EMAIL_MANAGER_IMAP_USER`
- `EMAIL_MANAGER_IMAP_PASSWORD`
- `EMAIL_MANAGER_SMTP_USER`
- `EMAIL_MANAGER_SMTP_PASSWORD`
- `CALENDAR_CALDAV_USER`
- `CALENDAR_CALDAV_PASSWORD`
- `SCHOOLOGY_CONSUMER_KEY`
- `SCHOOLOGY_CONSUMER_SECRET`

## Quick Start

1. Copy `.env.example` to `.env`.
2. Fill in at least one provider API key that matches the model names in `eugene.toml`.
3. Set a real `api_key` in `eugene.toml`.
4. Optionally disable channels or applets you do not want to use yet.
5. Build the frontend if `static/` is missing or outdated.
6. Start Eugene.

Run the server with the package entrypoint:

```bash
uv run eugene
```

Or directly with Uvicorn:

```bash
uv run uvicorn eugene.main:app --host 127.0.0.1 --port 8000
```

Once running:

- web UI: `http://127.0.0.1:8000`
- API base: `http://127.0.0.1:8000/api`
- chat WebSocket: `ws://127.0.0.1:8000/ws/{session_id}?api_key=...`

## Frontend Development

For frontend-only iteration:

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server runs on `http://127.0.0.1:5173` and proxies `/api` and `/ws` back to the FastAPI server on port `8000`.

For production-style static output:

```bash
cd frontend
npm run build
```

The Vite config writes the build to `../static`.

## Termagotchi Client

The repository also includes a separate terminal client:

```bash
uv run eugene-termagotchi --api-key YOUR_API_KEY
```

What it does:

- connects to Eugene over the same WebSocket API
- keeps the assistant UI separate from server logs
- renders replies with Rich and Markdown
- maintains local pet state independently of Eugene's conversation identity

Useful local commands:

- `/feed`
- `/play`
- `/sleep`
- `/name NAME`
- `/help`
- `/clear`
- `/quit`

## API Surface

Current built-in API routes include:

- `GET /api/health`
- `GET /api/config`
- `GET /api/applets`
- `POST /api/applets/{name}`
- `GET /api/applets/{name}/config`
- `POST /api/applets/{name}/config`
- `GET /api/channels`
- `GET /api/schedules`
- `GET /api/triggers`
- `GET /api/token-usage`
- `GET /api/history/{session_id}`
- `DELETE /api/history/{session_id}`
- `POST /api/upload`

These endpoints require the Eugene API key, supplied either as `x-api-key` or `api_key`.

## Memory and Data

Runtime data is stored under `eugene_data/`, including:

- conversation history
- token usage data
- applet config overrides
- uploaded files
- logs
- termagotchi client save data

The memory service maintains:

- working memory windows for active sessions
- persisted chat history in SQLite
- long-term retrieval with Chroma when available

## Safety and Defaults

Some capabilities are intentionally off by default:

- `shell_commander.allow_execution = false`
- `python_repl.allow_execution = false`

That keeps higher-risk execution paths opt-in. If you enable them, review their applet config carefully and treat the Eugene host as a trusted environment.

## Repository Layout

```text
.
|-- applets/              # Applet modules and applet.toml files
|-- channels/             # Channel adapters
|-- frontend/             # React + TypeScript + Vite app
|-- src/eugene/           # Backend package
|-- static/               # Built frontend served by FastAPI
|-- eugene.toml           # Main runtime config
|-- personality.toml      # Personality settings
|-- .env.example          # Secret variable template
|-- mcp_registry.json     # MCP server registry
`-- README.md
```

## Development Notes

- `uv run eugene` is the simplest way to launch the host with the package entrypoint.
- If frontend auto-reload is enabled, Eugene can watch the frontend sources, rebuild static assets, and tell connected browsers to refresh.
- The router model selects relevant applets for each message before the main completion step.
- MCP servers can also be registered and exposed to routing/tool selection.

## Future Plans

- Improve observability with richer per-channel metrics, applet-level tracing, and clearer debugging dashboards.
- Expand channel reliability with stronger reconnect behavior, delivery retries, and better offline message handling.
- Add more robust memory controls, including retention policies, session export/import, and easier memory inspection tools.
- Introduce role-based API access patterns so admin and user capabilities can be scoped more safely.
- Strengthen sandboxing around execution-oriented applets with stricter policy controls and safer defaults.
- Add end-to-end and integration test coverage for routing, channel adapters, and applet orchestration paths.
- Improve deployment ergonomics with container-first docs, health-check guidance, and production configuration presets.


## License

No license file is currently present in the repository.
