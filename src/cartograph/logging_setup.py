"""Central logging configuration for Cartograph.

Wraps the stdlib `logging` module — this does not replace or restructure
anything; every existing module just does `logging.getLogger(__name__)` as
usual. Call `configure_logging()` once, early (the CLI does this before
building the graph), and both a console handler and a timestamped file
handler under `./logs/` are attached to the `cartograph` logger, which every
module's logger is a child of.

Callers are expected to prefix their own log messages with a bracketed
component tag (e.g. `"[scout] ..."`, `"[gate:tests] ..."`) so the combined
transcript stays greppable by component even though everything shares one
file.
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

LOG_DIR = Path("logs")

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(level: str = "INFO", thread_id: str | None = None) -> Path:
    """(Re)configure the `cartograph` logger with console + file handlers.

    Safe to call more than once (e.g. once per CLI invocation) — clears any
    handlers from a previous call first, so log lines are never duplicated.

    Args:
        level: Logging level name, e.g. "DEBUG", "INFO".
        thread_id: Included in the log filename so a run's transcript is
            easy to find later; a timestamp alone is used if omitted.

    Returns:
        Path to the log file created for this run.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"-{thread_id}" if thread_id else ""
    log_path = LOG_DIR / f"run{suffix}-{timestamp}.log"

    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    logger = logging.getLogger("cartograph")
    logger.setLevel(level.upper())
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.propagate = False

    return log_path
