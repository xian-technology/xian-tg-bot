"""Configure safe logging before loading optional bot/plugin dependencies."""

import os
import sys
from pathlib import Path
from typing import Any

from logging_setup import BoundedLogFile, error_context, utc_timestamp


def record_fatal(error: BaseException, directory: Path = Path("log")) -> None:
    # This fallback uses only stdlib, including when loguru cannot be imported.
    incident = error_context(error)
    details = " ".join(f"{key}={value}" for key, value in incident.items())
    line = f"{utc_timestamp()} | CRITICAL | tg-bot/bootstrap | Bot startup or runtime failed | {details}\n"
    sink = BoundedLogFile(directory)
    try:
        sink.write(line)
    finally:
        sink.stop()


def run() -> Any:
    try:
        from logging_setup import configure_logging, configure_standard_logging

        configure_logging(os.getenv("LOG_LEVEL", "INFO"),
                          os.getenv("LOG_INTO_FILE", "true").lower() == "true")
        configure_standard_logging()
        from main import run as run_application

        return run_application()
    except Exception as error:
        _close_logger()
        record_fatal(error)
        return 1
    except SystemExit as error:
        if error.code not in (None, 0):
            _close_logger()
            record_fatal(error)
        raise
    finally:
        _close_logger()


def _close_logger() -> None:
    module = sys.modules.get("loguru")
    if module is not None:
        module.logger.remove()


if __name__ == "__main__":
    raise SystemExit(run())
