"""Secrets come from the environment, never from tracked configuration.

A secret ``NAME`` is read from the variable ``NAME`` or from the file named by
``NAME_FILE`` (for container or service-manager secret files). Surrounding
whitespace is ignored.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

WALLET_KEY = "XIAN_BOT_WALLET_KEY"
FAUCET_PRIVKEY = "XIAN_BOT_FAUCET_PRIVKEY"
SECRET_NAMES = (WALLET_KEY, FAUCET_PRIVKEY)


class SecretError(RuntimeError):
    """A configured secret source is ambiguous, missing or empty."""


def read_secret(name: str, environ: Mapping[str, str] | None = None) -> str | None:
    environ = os.environ if environ is None else environ
    value = environ.get(name)
    path = environ.get(f"{name}_FILE")

    if value is not None and path is not None:
        raise SecretError(f"Set either {name} or {name}_FILE, not both")
    if path is not None:
        try:
            value = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise SecretError(f"Cannot read {name}_FILE: {exc.strerror}") from exc
        if not value.strip():
            raise SecretError(f"{name}_FILE is empty")
    return value.strip() if value is not None else None


def secret_files(names: Iterable[str] = SECRET_NAMES, environ: Mapping[str, str] | None = None) -> set[Path]:
    """Resolved paths of configured secret files (for exclusion from backups)."""
    environ = os.environ if environ is None else environ
    return {Path(environ[f"{name}_FILE"]).resolve() for name in names if environ.get(f"{name}_FILE")}
