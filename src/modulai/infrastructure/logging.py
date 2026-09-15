from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from modulai.infrastructure.config import LoggingConfig


def configure_logging(config: LoggingConfig, data_dir: Path) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if config.file_enabled:
        log_dir = data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_dir / config.file_name,
                maxBytes=2_000_000,
                backupCount=3,
                encoding="utf-8",
            )
        )

    for handler in handlers:
        handler.setFormatter(formatter)

    logging.basicConfig(
        level=getattr(logging, config.level),
        handlers=handlers,
        force=True,
    )
    # httpx registra URLs completas a nivel INFO; en Telegram la URL contiene el token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
