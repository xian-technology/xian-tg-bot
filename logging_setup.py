"""Bounded, payload-free operational logging for the single-process bot."""

import errno
import json
import logging
import os
import re
import sys
import threading
import time
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, cast

MAX_RECORD_BYTES = 16 * 1024
ROTATION_SECONDS = 3600
_OWNED_LOG_NAME = re.compile(r"(?:bot(?:\.\d+)?|\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d{6})\.log\Z")
_CONTEXT_FIELDS = frozenset({
    "incident_id", "update_id", "handler", "exception_type", "frames", "query_type",
    "arg_count", "tx_hash", "status", "transaction_count", "pending_count", "close_code", "user_id", "chat_id", "http_status", "method", "error_code", "error_name",
})


def frame_locations(traceback: TracebackType | None) -> str:
    """Do not format exception messages, source lines, locals, or chained exceptions."""
    frames: list[str] = []
    while traceback is not None and len(frames) < 8:
        code = traceback.tb_frame.f_code
        frames.append(f"{Path(code.co_filename).name[:128]}:{traceback.tb_lineno}:{code.co_name[:128]}")
        traceback = traceback.tb_next
    return ";".join(frames)[:1024]


def error_context(error: BaseException | None, update: object = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "incident_id": uuid.uuid4().hex,
        "exception_type": _single_line(type(error).__name__, 128),
        "frames": _single_line(frame_locations(error.__traceback__), 1024) if error else "",
    }
    update_id = getattr(update, "update_id", None)
    if type(update_id) is int:
        result["update_id"] = _bounded_scalar(update_id)
    return result


def update_context(update: object, handler: str) -> dict[str, Any]:
    result: dict[str, Any] = {"handler": handler[:128]}
    update_id = getattr(update, "update_id", None)
    if type(update_id) is int:
        result["update_id"] = _bounded_scalar(update_id)
    return result


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _escape_character(character: str) -> str:
    if character in ("\n", "\r"):
        return r"\n" if character == "\n" else r"\r"
    if not (unicodedata.category(character).startswith("C") or character in "\u2028\u2029"):
        return character
    code = ord(character)
    if code < 256:
        return f"\\x{code:02x}"
    return f"\\u{code:04x}" if code <= 0xffff else f"\\U{code:08x}"


def _single_line(value: str, limit: int) -> str:
    return "".join(_escape_character(character) for character in value[:limit])


def _bounded_scalar(value: str | int | bool) -> str | int | bool:
    if type(value) is int and value.bit_length() > 256:
        return "[integer exceeds 256 bits]"
    return value


def _context_text(value: str | int | bool) -> str:
    return _single_line(str(_bounded_scalar(value)), 1024)


def _prepare_record(record: Any) -> bool:
    extra = record["extra"]
    if record["exception"] is not None:
        exception = record["exception"]
        extra.update(exception_type=exception.type.__name__,
                     frames=frame_locations(exception.traceback))
        record["exception"] = None
    context = {key: _context_text(value)
               for key, value in extra.items() if key in _CONTEXT_FIELDS
               and type(value) in (str, int, bool)}
    record["message"] = _single_line(record["message"], 8192)
    extra["context"] = json.dumps(context, ensure_ascii=True, sort_keys=True)
    return True


class BoundedLogFile:
    """Size and age retention, including idle periods; one process owns the directory."""

    def __init__(self, directory: Path, *, max_file_bytes: int = 50 * 1024 * 1024,
                 max_total_bytes: int = 250 * 1024 * 1024,
                 retention_seconds: float = 7 * 24 * 60 * 60,
                 cleanup_interval: float = 60) -> None:
        if not 0 < max_file_bytes <= max_total_bytes or retention_seconds <= 0 or cleanup_interval <= 0:
            raise ValueError("invalid log retention limits")
        self.directory = directory
        self.path = directory / "bot.log"
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.retention_seconds = retention_seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._current_size = 0
        self._opened_at: float | None = None
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.cleanup()
        self.worker = threading.Thread(target=self._maintain, args=(cleanup_interval,),
                                       name="bot-log-retention", daemon=True)
        self.worker.start()

    def _files(self) -> list[tuple[Path, os.stat_result]]:
        return [(path, path.stat()) for path in self.directory.glob("*.log")
                if _OWNED_LOG_NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink()]

    def _active_start(self, stat: os.stat_result) -> float:
        if self._opened_at is not None:
            return self._opened_at
        # Recover segment age after restart without scanning record bodies.
        with self.path.open("rb") as source:
            prefix = source.read(32).split(b" |", 1)[0].decode("ascii", errors="ignore")
        try:
            return datetime.fromisoformat(prefix).timestamp()
        except ValueError:
            return stat.st_mtime

    def _cleanup(self) -> None:
        cutoff = time.time() - self.retention_seconds
        archives: list[tuple[Path, os.stat_result]] = []
        current_size = 0
        for path, stat in self._files():
            started = self._active_start(stat) if path == self.path else stat.st_mtime
            if started <= cutoff or stat.st_size > self.max_file_bytes:
                path.unlink(missing_ok=True)
            elif path == self.path:
                current_size = stat.st_size
                self._opened_at = started
            else:
                archives.append((path, stat))
        self._current_size = current_size
        if not current_size:
            self._opened_at = None
        self._trim_archives(archives)

    def _trim_archives(self, archives: list[tuple[Path, os.stat_result]]) -> None:
        # Reserve an entire active segment: ordinary writes never scan the directory.
        budget = self.max_total_bytes - self.max_file_bytes
        total = sum(stat.st_size for _, stat in archives)
        for path, stat in sorted(archives, key=lambda item: item[1].st_mtime):
            if total <= budget:
                break
            path.unlink(missing_ok=True)
            total -= stat.st_size

    def _rotate(self) -> None:
        stamp = time.time_ns()
        archive = self.directory / f"bot.{stamp}.log"
        while archive.exists():
            stamp += 1
            archive = self.directory / f"bot.{stamp}.log"
        try:
            self.path.rename(archive)
        except FileNotFoundError:
            pass
        else:
            # Expire by the oldest record's age, not the last write/rotation time.
            started = self._opened_at if self._opened_at is not None else time.time()
            os.utime(archive, (started, started))
        self._current_size = 0
        self._opened_at = None

    def _rotation_due(self, now: float) -> bool:
        return self._opened_at is not None and now - self._opened_at >= ROTATION_SECONDS

    def cleanup(self) -> None:
        with self._lock:
            self._cleanup()
            if self._current_size and self._rotation_due(time.time()):
                self._rotate()
                self._cleanup()

    def _maintain(self, interval: float) -> None:
        while not self._stop.wait(interval):
            try:
                self.cleanup()
            except OSError as error:
                # Never include paths or exception text in this last-resort diagnostic.
                kind = error_context(error)["exception_type"]
                code = _context_text(error.errno) if type(error.errno) is int else "unknown"
                try:
                    sys.stderr.write(f"{utc_timestamp()} | ERROR | tg-bot/retention | Log retention cleanup failed | exception_type={kind} errno={code}\n")
                except (OSError, ValueError):
                    pass

    def write(self, message: str) -> None:
        limit = min(MAX_RECORD_BYTES, self.max_file_bytes)
        data = message[:limit + 1].encode("utf-8")
        if len(data) > limit:
            marker = b" [truncated]\n"
            data = data[:max(0, limit - len(marker))].decode("utf-8", errors="ignore").encode() + marker[:limit]
        with self._lock:
            now = time.time()
            if self._current_size and (self._rotation_due(now) or self._current_size + len(data) > self.max_file_bytes):
                self._rotate()
                self._cleanup()
            if not self._current_size:
                self._opened_at = now
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.path, flags, 0o600)
            with os.fdopen(descriptor, "ab") as output:
                output.write(data)
            self._current_size += len(data)

    def stop(self) -> None:
        self._stop.set()
        self.worker.join(timeout=2)


def configure_logging(level: str, into_file: bool, *, directory: Path = Path("log")) -> None:
    from loguru import logger

    logger.remove()
    options: dict[str, Any] = {
        "level": level,
        "format": "{time:YYYY-MM-DDTHH:mm:ss.SSS!UTC}Z | {level} | tg-bot/{name} | {message} | {extra[context]}",
        "filter": _prepare_record,
        "backtrace": False,
        "diagnose": False,
        "colorize": False,
    }
    logger.add(sys.stderr, **options)
    if into_file:
        logger.add(BoundedLogFile(directory), **options)


class OperationalHandler(logging.Handler):
    """Third-party logs can contain Telegram tokens in URLs; never forward their payloads."""

    def emit(self, record: logging.LogRecord) -> None:
        from loguru import logger

        context = _standard_error_context(record)
        context["handler"] = record.name[:128]
        message = "Third-party service event"
        if record.name in {"httpx", "uvicorn.access"}:
            message = "HTTP request event"
            context.update(_http_context(record))
        level = record.levelname if record.levelname in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} else "INFO"
        logger.bind(**context).log(level, message)


def configure_standard_logging() -> None:
    handler = OperationalHandler()
    logging.basicConfig(handlers=[handler], level=logging.INFO, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        target = logging.getLogger(name)
        target.handlers = []
        target.propagate = True


def _http_context(record: logging.LogRecord) -> dict[str, Any]:
    args = record.args
    if not isinstance(args, tuple) or len(args) != 5:
        return {}
    status = args[3] if record.name == "httpx" else args[4]
    method = args[0] if record.name == "httpx" else args[1]
    result: dict[str, Any] = {}
    if type(status) is int and 100 <= status <= 599:
        result["http_status"] = status
    if isinstance(method, str) and method in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}:
        result["method"] = method
    return result


def _standard_error_context(record: logging.LogRecord) -> dict[str, Any]:
    error = record.exc_info[1] if record.exc_info else None
    if error is None and isinstance(record.args, tuple):
        error = next((value for value in record.args[:8] if isinstance(value, BaseException)), None)
    if error is None:
        return {}
    result = error_context(error)
    if isinstance(error, OSError) and type(error.errno) is int:
        result["error_code"] = error.errno
        result["error_name"] = errno.errorcode.get(error.errno, "UNKNOWN")
    return result


def transaction_context(value: Any) -> dict[str, Any]:
    """Project only typed/public transaction identity and status; never response bodies."""
    if isinstance(value, dict):
        tx_hash = value.get("tx_hash")
        status = value.get("status")
    else:
        tx_hash = getattr(value, "tx_hash", None)
        status = getattr(value, "accepted", None)
    result: dict[str, Any] = {}
    if type(tx_hash) is str and re.fullmatch(r"[a-fA-F0-9]{1,128}", tx_hash):
        result["tx_hash"] = tx_hash
    if type(status) in (int, bool):
        result["status"] = _bounded_scalar(cast(int | bool, status))
    return result
