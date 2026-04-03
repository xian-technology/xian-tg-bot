import json
import logging
import os
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from threading import RLock
from typing import Any, Optional, Union

ConfigPath = Union[str, Path]
Validator = Optional[Callable[[dict[str, Any]], None]]


class ConfigError(RuntimeError):
    """Raised when a configuration file cannot be read or written."""


class ConfigKeyError(KeyError):
    """Raised when a requested configuration key is missing."""


class ConfigManager:
    """Minimal JSON-backed configuration store with defensive I/O semantics."""

    def __init__(self, config_file: ConfigPath, validator: Validator = None):
        if not config_file:
            raise ConfigError("No config file provided")

        self._cfg_file = Path(config_file)
        self._validator = validator
        self._cfg: dict[str, Any] = {}
        self._lock = RLock()
        self.reload()

    @property
    def path(self) -> Path:
        return self._cfg_file

    def reload(self) -> None:
        """Refresh in-memory state from disk."""
        with self._lock:
            try:
                if self._cfg_file.is_file():
                    with self._cfg_file.open(encoding="utf-8") as fh:
                        data = json.load(fh)
                        if not isinstance(data, dict):
                            raise ConfigError(f"Config root must be a JSON object: {self._cfg_file}")
                else:
                    data = {}

                if self._validator:
                    self._validator(data)

                self._cfg = data
            except Exception as exc:
                raise ConfigError(f"Cannot read configuration '{self._cfg_file}': {exc}") from exc

    def get(self, *keys: str | Iterable[str], default: Any = None, raise_if_missing: bool = False) -> Any:
        """Return the value for ``keys`` or ``default`` when not found."""
        if not keys:
            return self.snapshot()

        path = self._flatten_keys(keys)

        with self._lock:
            cursor: Any = self._cfg
            for idx, key in enumerate(path):
                if not isinstance(cursor, dict) or key not in cursor:
                    if raise_if_missing:
                        raise ConfigKeyError(f"Missing key path {path[: idx + 1]} in {self._cfg_file}")
                    return default
                cursor = cursor[key]
            return cursor

    def set(self, value: Any, *keys: str | Iterable[str]) -> None:
        """Persist ``value`` at ``keys`` and flush to disk atomically."""
        if not keys:
            raise ConfigError("Cannot set value without a key path")

        path = self._flatten_keys(keys)

        with self._lock:
            cursor = self._cfg
            for key in path[:-1]:
                next_cursor = cursor.get(key)
                if not isinstance(next_cursor, dict):
                    next_cursor = {}
                    cursor[key] = next_cursor
                cursor = next_cursor

            cursor[path[-1]] = value
            self._write()

    def remove(self, *keys: str | Iterable[str]) -> None:
        """Delete a key path and persist changes."""
        if not keys:
            raise ConfigError("Cannot remove value without a key path")

        path = self._flatten_keys(keys)

        with self._lock:
            cursor = self._cfg
            parents: list[tuple[dict[str, Any], str]] = []

            for key in path[:-1]:
                if key not in cursor or not isinstance(cursor[key], dict):
                    raise ConfigKeyError(f"Missing key path {path} in {self._cfg_file}")
                parents.append((cursor, key))
                cursor = cursor[key]

            if path[-1] not in cursor:
                raise ConfigKeyError(f"Missing key {path[-1]} in {self._cfg_file}")

            del cursor[path[-1]]
            self._cleanup_empty_branches(parents)
            self._write()

    def snapshot(self) -> dict[str, Any]:
        """Return a shallow copy of the current configuration."""
        with self._lock:
            return dict(self._cfg)

    # Internal helpers -------------------------------------------------

    def _cleanup_empty_branches(self, parents: Iterable[tuple[dict[str, Any], str]]) -> None:
        """Remove empty dictionaries left behind by deletes."""
        for parent, key in reversed(list(parents)):
            child = parent[key]
            if isinstance(child, dict) and not child:
                del parent[key]
            else:
                break

    def _write(self) -> None:
        """Write the JSON dictionary to disk atomically."""
        temp_name = None
        try:
            self._cfg_file.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tmp:
                json.dump(self._cfg, tmp, indent=4, sort_keys=True)
                tmp.flush()
                os.fsync(tmp.fileno())
                temp_name = tmp.name
            os.replace(temp_name, self._cfg_file)
        except Exception as exc:
            if temp_name:
                try:
                    os.unlink(temp_name)
                except OSError:
                    logging.debug("Failed to remove temp config file '%s'", temp_name)
            raise ConfigError(f"Cannot write configuration '{self._cfg_file}': {exc}") from exc

    @staticmethod
    def _flatten_keys(keys: tuple[str | Iterable[str], ...]) -> tuple[str, ...]:
        flattened: list[str] = []
        for key in keys:
            if isinstance(key, (list, tuple)):
                flattened.extend(str(k) for k in key)
            else:
                flattened.append(str(key))

        if not flattened:
            raise ConfigError("Key path cannot be empty")
        return tuple(flattened)
