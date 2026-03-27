from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from loguru import logger


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(exception=record.exc_info).log(level, record.getMessage())


def setup_logging(*, level: str, log_file: str, rotation: str, retention: str, serialize: bool) -> None:
    logger.remove()

    logger.add(
        sys.stderr,
        level=level.upper(),
        enqueue=True,
        backtrace=True,
        diagnose=False,
        serialize=serialize,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}:{function}:{line}</cyan> | <level>{message}</level>",
    )

    file_path = Path(log_file)
    if not file_path.is_absolute():
        file_path = Path.cwd() / file_path
    file_path.parent.mkdir(parents=True, exist_ok=True)

    logger.add(
        str(file_path),
        level=level.upper(),
        enqueue=True,
        backtrace=True,
        diagnose=False,
        rotation=rotation,
        retention=retention,
        serialize=serialize,
    )

    intercept_handler = InterceptHandler()
    logging.root.handlers = [intercept_handler]
    logging.root.setLevel(logging.NOTSET)

    for name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "fastapi",
        "asyncio",
    ):
        logging.getLogger(name).handlers = [intercept_handler]
        logging.getLogger(name).propagate = False

    logger.bind(component="startup").info(
        "Logging configured level={level} file={file} json={json}",
        level=level.upper(),
        file=str(file_path),
        json=serialize,
    )


def preview(value: Any, max_len: int = 800) -> str:
    text = repr(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
