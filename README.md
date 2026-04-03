# 🤖 Eugene

Eugene is a sophisticated, single-process AI agentic assistant designed for personal automation and high-level productivity. Built on FastAPI and leveraging the power of modern Large Language Models, Eugene provides a unified interface for interacting with your digital world across multiple channels.

Eugene is built to be **extensible**, **modular**, and **resilient**, with a focus on ease of configuration and advanced user interaction.

---

## 🚀 Key Features

### 📡 Unified Multi-Channel Ingress

Interact with Eugene from anywhere:

- **Web Dashboard**: A premium React-based interface with real-time WebSocket communication and operational inspector panels.
- **Discord**: Full integration using `discord.py` for rich messaging and channel-based interactions.
- **Telegram**: Fast, reliable communication via the `python-telegram-bot` API.

### 🧩 Standardized Applet Ecosystem

Eugene uses is a powerful, modular applet system. Capabilities are discovered and hot-reloaded at runtime:

- **Modular Logic**: Each applet defines its own tools, routes, and background tasks.
- **Granular Control**: Enable or disable applet capabilities on the fly via the management API.
- **Automatic Routing**: A specialized "Router Model" intelligently selects which applets are needed for each user request.

### 🧠 Advanced Memory & Context

- **Working Memory**: A rolling context window with optional intelligent summary compression.
- **Long-Term Memory**: Consolidated exchanges stored in **Chroma** (semantic vector store) or SQLite fallbacks.
- **Structured Facts**: A dedicated key-value persistence layer for important user preferences and data.

### 📑 Intelligent Attachment pipeline

Eugene processes more than just text:

- **PDF Extraction**: Native parsing of machine-generated PDFs using `pdfplumber`.
- **Text Processing**: Automatic decoding and truncation of code and text files for safe prompt injection.
- **Image Metadata**: Recognition of image uploads with path-based references for downstream processing.

---

## 🛠️ Built-in Capabilities (Applets)

Eugene ships with **12 primary applets** out of the box:

1. **`user_prompt`**: 🆕 Interactive questionnaires. Triggers step-by-step modals in the Web UI or formatted text prompts in Discord/Telegram.
2. **`personality`**: Manages Eugene's identity, tone, and behavior via a hot-reloaded `personality.toml`.
3. **`email_manager`**: Full IMAP/SMTP control. Search, read, and send emails with ease.
4. **`calendar`**: Integration with local/ICS calendars for meeting management and reminders.
5. **`web_browser`**: Real-time web searching and content extraction.
6. **`shell_commander`**: Sandboxed terminal execution and system command orchestration.
7. **`system_monitor`**: Status updates on network, CPU, memory, and disk usage.
8. **`weather`**: Real-time weather data and multi-day forecasts.
9. **`scheduler`**: Create recurring cron jobs or one-time scheduled tasks.
10. **`memory`**: Semantic search across past conversations and fact retrieval.
11. **`clock`**: Precise time/date and timezone-aware context.
12. **`pdf_reader`**: specialized extraction of structured data from PDF documents.

---

## ⚙️ Configuration & Security

Eugene follows a strict **Security-First** configuration model:

- **`.env` (Secrets Only)**: API keys, passwords, and sensitive tokens reside exclusively here.
- **`applet.toml` (Design/Metadata)**: Non-sensitive flags, hosts, and ports are kept in the applet directory.
- **Environment Overrides**: Any field in an applet's config can be overridden using the `{APPLET_NAME}_{FIELD_NAME}` environment variable convention (e.g., `EMAIL_MANAGER_IMAP_PASSWORD`).

### Quick Start: Environmental Setup

```bash
# Core API Keys
OPENAI_API_KEY="sk-..."
ANTHROPIC_API_KEY="sk-ant-..."

# Applet Secret Overrides
EMAIL_MANAGER_IMAP_PASSWORD="my-secure-password"
WEATHER_API_KEY="..."
```

---

## 🏗️ Technical Architecture

- **Backend**: FastAPI (Python 3.11+) + Uvicorn (ASGI).
- **Frontend**: React + TypeScript + Vite (served as a static bundle).
- **Communication**: WebSockets for real-time duplex chat and state updates.
- **Events**: A centralized Internal Event Bus for cross-applet communication (`applet.loaded`, `message.received`, etc.).
- **Data**: SQLite for relational data + Chroma for vector embeddings.

---

## 🚦 Getting Started

### Installation

```bash
# Install dependencies using uv (fastest)
uv sync
```

### Running Locally

```bash
# Start the server (includes auto-discovering applets and channels)
uv run uvicorn eugene.main:app --host 127.0.0.1 --port 8000
```

### UI Access

Open your browser and navigate to `http://127.0.0.1:8000`. Use the API key configured in `eugene.toml` to log in.

---

