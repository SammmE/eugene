# Eugene

Single-user personal AI assistant built as one FastAPI process.

## Start

Set provider credentials for the models in `eugene.toml`, then run:

```bash
uv run uvicorn eugene.main:app --host 127.0.0.1 --port 8000
```

Alternative console entrypoint:

```bash
uv run eugene
```

## Notes

- Web chat is available at `/`
- REST management API is under `/api/*`
- WebSocket chat is under `/ws/{session_id}`
- Applets live in `./applets`
- Channels live in `./channels`
