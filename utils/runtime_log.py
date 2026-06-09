from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


_CONFIGURED = False
_LOG_FILE: Path | None = None


class _SafeRotatingFileHandler(RotatingFileHandler):
    """Rotating handler resilient to Windows file-lock quirks."""

    def shouldRollover(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        if self.maxBytes <= 0:
            return False
        try:
            current_size = os.path.getsize(self.baseFilename) if os.path.exists(self.baseFilename) else 0
            rendered = f"{self.format(record)}\n"
            encoded = rendered.encode(self.encoding or "utf-8", errors="replace")
            return current_size + len(encoded) >= self.maxBytes
        except OSError:
            # If the log file is temporarily unavailable, keep using the current stream.
            return False

    def emit(self, record: logging.LogRecord) -> None:  # type: ignore[override]
        try:
            if self.shouldRollover(record):
                try:
                    self.doRollover()
                except OSError:
                    pass
            logging.FileHandler.emit(self, record)
        except OSError:
            # Logging must never break the application flow.
            return


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def get_log_file_path() -> Path:
    global _LOG_FILE
    if _LOG_FILE is None:
        log_dir = _project_root() / ".cache" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        _LOG_FILE = log_dir / "runtime.log"
    return _LOG_FILE


def _configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    log_file = get_log_file_path()
    handler = _SafeRotatingFileHandler(
        filename=log_file,
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
        delay=True,
    )
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    root = logging.getLogger("mcs")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.propagate = False
    logging.raiseExceptions = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    _configure_logging()
    return logging.getLogger(f"mcs.{name}")
