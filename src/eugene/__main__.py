from __future__ import annotations

import uvicorn

from eugene.config import load_config


def main() -> None:
    config = load_config()
    uvicorn.run("eugene.main:app", host=config.host, port=config.port, reload=False)
