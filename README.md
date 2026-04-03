# Eugene

Eugene is a single-process, single-user personal AI assistant built on FastAPI.
It combines:

- Multi-channel message ingress (web, Discord, Telegram)
- Applet-based capability routing and tool execution
- Working memory + persisted long-term memory
- Scheduled task automation
- A browser chat UI with operational status panels

## What Eugene Can Do Today

### Core assistant flow

- Receives messages from channel adapters and normalizes them into a unified message model.
- Resolves and parses attachments before prompting the model.
- Uses a router model to choose relevant applets for each message.
- Builds a prompt from:
	- Compiled personality profile
	- Always-on applet context blocks
	- Session working memory window (with optional summary)
	- Current user message + extracted attachment content
- Executes LLM tool calls in a loop up to `max_tool_depth`.
- Sends assistant responses back through the originating channel.
- Stores conversation history and long-term memory records in SQLite.

### Applet system

Eugene discovers applets from `./applets/*` at startup using each applet folder's `applet.toml` and `applet.py`.

Current built-in applets:

1. `clock`
	 - Always-on context injection.
	 - Adds current date/time in configured timezone and 12h/24h format.
	 - Eagerly loaded and cannot be disabled.

2. `memory`
	 - Always-on tools:
		 - `search_memory(query)`
		 - `summarize_working_memory(session_id)`
	 - Eagerly loaded and cannot be disabled.

3. `personality`
	 - Always-on tools:
		 - `read_personality()`
		 - `edit_personality(section, content)`
	 - Updates `personality.toml` and hot-reloads personality state.
	 - Eagerly loaded and cannot be disabled.

4. `filesystem`
	 - Selective tools for sandboxed file operations under configured `root_path`:
		 - `read_file`
		 - `write_file`
		 - `list_files`
		 - `move_file`
		 - `delete_file`
	 - Path traversal outside root is blocked.

5. `pdf_reader`
	 - Attachment handler for `.pdf` files.
	 - Extracts PDF text before model input.

6. `scheduler`
	 - Selective tools:
		 - `list_scheduled_tasks`
		 - `create_scheduled_task` (cron or one-time date)
		 - `delete_scheduled_task`
	 - Persists tasks and fires them through the normal message pipeline.
	 - Cannot be disabled.

### Channel support

Eugene auto-discovers channels from `./channels/*.py`.

Implemented channels:

- `web`
	- WebSocket chat at `/ws/{session_id}`.
	- Used by the built-in browser UI.

- `discord`
	- Uses `discord.py` client events.
	- Normal messages are ingested and replies are posted to the same channel.

- `telegram`
	- Uses `python-telegram-bot` polling.
	- Text messages are ingested and replies are sent to the originating chat.

Channel enablement and tokens are configured in `eugene.toml`.

### Memory and persistence

- Working memory
	- In-memory rolling per-session window (`working_memory_turns`).
	- Optional summary used when context threshold pressure is high.

- Long-term memory
	- Stores consolidated exchanges in SQLite (`long_term_memory`).
	- Retrieval supports:
		- Semantic search via Chroma + sentence-transformers when available.
		- SQL `LIKE` fallback otherwise.

- Structured facts
	- Key-value fact storage in SQLite (`structured_facts`).

- Conversation history
	- Every user/assistant turn stored in SQLite (`conversation_history`).
	- Exposed through REST endpoints for loading and deletion.

- Token usage
	- Logs model, prompt/completion tokens, timestamp, and origin in SQLite (`token_usage`).

- Scheduled tasks
	- Persisted in SQLite (`scheduled_tasks`) and reloaded on startup.

### Attachment handling

For incoming attachment references (paths):

- `.pdf` is parsed into text via `pdf_reader` applet handler.
- Text-like files are decoded and truncated to 8,000 chars for prompt injection.
- Images are acknowledged with metadata/path (not OCR'd by Eugene itself).
- Unknown binary types are represented as metadata placeholders.

PDF extraction backends:

- Preferred: `pdfplumber`
- Fallback: `pypdf`

### Web UI

The root URL `/` serves the built static UI from `./static`.

Current UI capabilities:

- API key gating (stored in browser localStorage).
- Multi-conversation session list with create/switch/delete.
- Real-time WebSocket chat per active session.
- File upload to `/api/upload` and attachment sending by server path.
- Attachment chips with local token estimate.
- Runtime inspector panels:
	- Health/provider status
	- Token usage summary
	- Scheduled tasks
	- Applet states
- Auto-refresh of management panels.

## HTTP and WebSocket API

All `/api/*` endpoints require API key via either:

- Header: `x-api-key: <key>`
- Query: `?api_key=<key>`

Endpoints:

- `GET /api/health`
	- Provider validity + channel statuses.

- `GET /api/config`
	- Effective config (with masked API key).

- `GET /api/applets`
	- List applets and runtime status.

- `POST /api/applets/{name}`
	- Enable/disable applet (`{"enabled": true|false}`), respecting non-disableable applets.

- `GET /api/applets/{name}/config`
	- Get applet config schema + current values.

- `POST /api/applets/{name}/config`
	- Update applet config (`{"values": {...}}`) and hot-reload instance.

- `GET /api/channels`
	- Channel enabled/connected/details.

- `GET /api/schedules`
	- All scheduled tasks.

- `GET /api/token-usage`
	- Latest 100 usage rows.

- `GET /api/history/{session_id}`
	- Session conversation history.

- `DELETE /api/history/{session_id}`
	- Remove history and clear working memory for session.

- `POST /api/upload`
	- Multipart upload, stores file under `eugene_data/uploads/`.

WebSocket:

- `GET /ws/{session_id}?api_key=<key>`
- Client sends JSON:
	- `{"text": "...", "attachments": ["path1", "path2"]}`
- Server responds JSON:
	- `{"type": "message.response", "text": "...", "metadata": {...}}`

## Project Structure

- `src/eugene/`
	- Core runtime, API router, services, data models, startup lifecycle.
- `applets/`
	- Capability modules discovered at runtime.
- `channels/`
	- Message ingress/egress adapters.
- `frontend/`
	- React + TypeScript + Vite source app.
- `static/`
	- Built frontend served by FastAPI.
- `eugene_data/`
	- Runtime data (SQLite DB, uploads, Chroma store, applet config overrides).

## Configuration

### `eugene.toml`

Key runtime settings:

- `api_key`
- `default_model`
- `router_model`
- `fallback_model`
- `primary_channel`
- `max_tool_depth`
- `working_memory_turns`
- `context_window_threshold`
- `host`, `port`
- `filesystem_root`
- `[channels.<name>]` blocks (`enabled`, `token`, ...)

### `personality.toml`

Defines sections such as identity, style, and behavior that are compiled into the system prompt.

### Environment variables

Provider credentials are expected via environment vars based on configured model prefixes:

- `openai/*` -> `OPENAI_API_KEY`
- `anthropic/*` -> `ANTHROPIC_API_KEY`
- `gemini/*` -> `GEMINI_API_KEY` or `GOOGLE_API_KEY`
- `google/*` -> `GOOGLE_API_KEY`
- `groq/*` -> `GROQ_API_KEY`
- `nvidia_nim/*` -> `NVIDIA_NIM_API_KEY`
- `mistral/*` -> `MISTRAL_API_KEY`
- `xai/*` -> `XAI_API_KEY`
- `ollama/*` -> no key required

If required credentials are missing, startup fails with a clear configuration error.

## Running Eugene

Python requirement: 3.11+

Install dependencies:

```bash
uv sync
```

Run server directly:

```bash
uv run uvicorn eugene.main:app --host 127.0.0.1 --port 8000
```

Or use console entrypoint:

```bash
uv run eugene
```

Then open:

- UI: `http://127.0.0.1:8000/`
- API docs (if enabled by FastAPI defaults): `http://127.0.0.1:8000/docs`

## Frontend Development and Build

The checked-in `static/` folder contains a built frontend bundle.

For frontend development:

```bash
cd frontend
npm install
npm run dev
```

To rebuild static assets for FastAPI serving:

```bash
cd frontend
npm run build:static
```

Then copy `frontend/dist` contents into `static/` (or automate this in your own workflow).

## Current Constraints and Notes

- Designed for single-user operation.
- API auth is a static shared key from config.
- Scheduler runs in-process; external distributed scheduling is not implemented.
- Applet routing expects router model to return a JSON array of applet names.
- Tool call safety/permissioning is applet-defined (for example, filesystem sandbox root).
- The internal MCP manager currently tracks applet MCP lifecycle state but does not launch external MCP servers.

## Quick Capability Checklist

- [x] Web chat with session history
- [x] Discord + Telegram text channel integration
- [x] Attachment upload and text extraction pipeline
- [x] PDF parsing support
- [x] Applet discovery, config, enable/disable controls
- [x] Tool-call loop with max depth guard
- [x] Working and long-term memory persistence
- [x] Token usage logging
- [x] In-process scheduled tasks (cron/date)
- [x] Runtime management API
