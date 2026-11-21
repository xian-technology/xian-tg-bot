from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any


def is_numeric(string: str) -> bool:
    """ Also accepts '.' in the string. Function 'isnumeric()' doesn't """
    try:
        float(string)
        return True
    except ValueError:
        pass

    try:
        import unicodedata
        unicodedata.numeric(string)
        return True
    except (TypeError, ValueError):
        pass

    return False


def format_float(value: float | int | Decimal) -> str:
    """Display a price in fixed notation with at most two nonzero fractional digits."""
    if value == 0:
        return "0"
    whole, _, fraction = f"{Decimal(str(value)):f}".partition(".")
    digits = re.match(r"0*[1-9](?:0*[1-9])?", fraction)
    return f"{whole}.{digits[0]}" if digits else whole


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_utc_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def sqlite_utc(value: datetime) -> str:
    """Render a UTC instant in SQLite's CURRENT_TIMESTAMP text format."""
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def build_menu(
    buttons: list[Any],
    n_cols: int = 1,
    header_buttons: list[Any] | None = None,
    footer_buttons: list[Any] | None = None,
) -> list[Any]:
    """ Build button-menu for Telegram """
    menu = [buttons[i:i + n_cols] for i in range(0, len(buttons), n_cols)]

    if header_buttons:
        menu.insert(0, header_buttons)
    if footer_buttons:
        menu.append(footer_buttons)

    return menu


def id() -> int:
    import time
    return int(time.time() * 1000)


def random_id(length: int = 8) -> str:
    import random
    import string
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(random.choices(alphabet, k=length))


def get_external_ip(website: str = 'https://api.ipify.org/') -> str:
    import urllib.request
    return urllib.request.urlopen(website).read().decode("utf-8")
