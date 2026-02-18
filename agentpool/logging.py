"""
Structured logging for agentpool.

Console output is always human-readable with colors.
Optional file logging writes JSON lines for post-run analysis.

Usage:
    from agentpool.logging import get_logger
    logger = get_logger(__name__)
    logger.info("Starting agent", agent_id="worker-1", task="review auth")
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


COLORS = {
    "DEBUG": "\033[36m",     # cyan
    "INFO": "\033[32m",      # green
    "WARNING": "\033[33m",   # yellow
    "ERROR": "\033[31m",     # red
    "RESET": "\033[0m",
}

# Extra fields that get included in structured output
_EXTRA_FIELDS = ("agent_id", "worker_id", "task_id", "sandbox", "duration", "model")


class HumanFormatter(logging.Formatter):
    """Colored, readable log format for terminals."""

    def format(self, record: logging.LogRecord) -> str:
        level = record.levelname
        color = COLORS.get(level, "")
        reset = COLORS["RESET"]
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

        extras = ""
        for key in _EXTRA_FIELDS:
            val = getattr(record, key, None)
            if val is not None:
                extras += f" {key}={val}"

        return f"{color}{ts} [{level:>7}]{reset} {record.getMessage()}{extras}"


class JsonFormatter(logging.Formatter):
    """JSON lines format for machine parsing and performance analysis."""

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in _EXTRA_FIELDS:
            val = getattr(record, key, None)
            if val is not None:
                data[key] = val
        if record.exc_info and record.exc_info[1]:
            data["error"] = str(record.exc_info[1])
        return json.dumps(data)


# Track whether setup has already run to avoid clobbering handlers
_setup_done = False


def setup_logging(level: str = "INFO", log_file: Optional[Path] = None) -> None:
    """
    Configure the agentpool logging system.

    Console always uses human-readable colored output.
    If log_file is set, a second handler writes JSON lines to that file.

    Safe to call multiple times — only configures handlers on the first call.
    Subsequent calls update the log level without adding duplicate handlers.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        log_file: Optional path for JSON lines output
    """
    global _setup_done

    root = logging.getLogger("agentpool")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if _setup_done:
        return

    # Console handler — always human-readable
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(HumanFormatter())
    root.addHandler(console)

    # File handler — JSON lines for post-run analysis
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(log_file))
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)

    _setup_done = True


def get_logger(name: str) -> logging.Logger:
    """Get a logger under the agentpool namespace."""
    if not name.startswith("agentpool"):
        name = f"agentpool.{name}"
    return logging.getLogger(name)
