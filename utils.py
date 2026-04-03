from __future__ import annotations

from datetime import datetime
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


def format(
    value: Any,
    decimals: int | None = None,
    force_length: bool = False,
    template: Any = None,
    on_zero: Any = 0,
    on_none: Any = None,
    symbol: str | None = None,
) -> Any:
    """ Format a crypto coin value so that it isn't unnecessarily long """

    fiat = False

    if symbol and isinstance(symbol, str):
        pass
    if value is None:
        return on_none
    try:
        if isinstance(value, str):
            value = value.replace(",", "")
        numeric_value = float(value)
    except:
        return str(value)
    try:
        if isinstance(template, str):
            template = template.replace(",", "")
        comparison_value = float(template)
    except:
        comparison_value = numeric_value
    precision = int(decimals) if decimals is not None else None
    try:
        if float(value) == 0:
            return on_zero
    except:
        return str(value)

    if comparison_value < 1:
        if precision:
            rendered = "{1:.{0}f}".format(precision, numeric_value)
        else:
            rendered = f"{numeric_value:.8f}"
    elif comparison_value < 100:
        if precision:
            rendered = "{1:.{0}f}".format(precision, numeric_value)
        else:
            rendered = f"{numeric_value:.4f}"
    elif comparison_value < 10000:
        if precision:
            rendered = "{1:,.{0}f}".format(precision, numeric_value)
        else:
            rendered = f"{numeric_value:,.2f}"
    else:
        rendered = f"{numeric_value:,.0f}"

    if not force_length:
        cut_zeros = False

        if comparison_value >= 1:
            cut_zeros = True
        else:
            if fiat:
                cut_zeros = True

        if cut_zeros:
            while "." in rendered and rendered.endswith(("0", ".")):
                rendered = rendered[:-1]
    return rendered


def format_float(value: float | int) -> str:
    # Check if the number has no decimal part
    if value == int(value):
        return str(int(value))

    # Convert to string and split into integer and decimal parts
    str_value = str(value)
    parts = str_value.split('.')

    # If there's somehow no decimal part after splitting
    if len(parts) == 1:
        return parts[0]

    integer_part, decimal_part = parts

    # Find the position of the first non-zero digit
    first_non_zero_pos = 0
    while first_non_zero_pos < len(decimal_part) and decimal_part[first_non_zero_pos] == '0':
        first_non_zero_pos += 1

    # If all decimal digits are zeros or we reached the end
    if first_non_zero_pos >= len(decimal_part):
        return integer_part

    # Find the position of the second non-zero digit
    second_non_zero_pos = first_non_zero_pos + 1
    while second_non_zero_pos < len(decimal_part) and decimal_part[second_non_zero_pos] == '0':
        second_non_zero_pos += 1

    # Determine how many decimal places to keep
    if second_non_zero_pos >= len(decimal_part):
        # If there's only one non-zero digit, keep up to that position
        decimal_places_to_keep = first_non_zero_pos + 1
    else:
        # Otherwise keep up to the second non-zero digit
        decimal_places_to_keep = second_non_zero_pos + 1

    # Format the number with the required decimal places
    formatted_decimal = decimal_part[:decimal_places_to_keep]

    # Remove trailing zeros
    trimmed_decimal = formatted_decimal.rstrip('0')

    # If all decimal digits were trimmed, return just the integer part
    if trimmed_decimal == '':
        return integer_part

    return f"{integer_part}.{trimmed_decimal}"


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


def str2bool(value: str) -> bool:
    return value.lower() in ("yes", "true", "t", "1")


def split_msg(
    msg: str,
    max_len: int | None = None,
    split_char: str = "\n",
    only_one: bool = False,
) -> list[str]:
    """ Restrict message length to max characters as defined by Telegram """
    if not max_len:
        import constants as con
        max_len = con.MAX_TG_MSG_LEN

    if only_one:
        return [msg[:max_len][:msg[:max_len].rfind(split_char)]]

    remaining = msg
    messages: list[str] = []

    while len(remaining) > max_len:
        split_at = remaining[:max_len].rfind(split_char)
        message = remaining[:max_len][:split_at]
        messages.append(message)
        remaining = remaining[len(message):]
    else:
        messages.append(remaining)

    return messages


def encode_url(url: str) -> str:
    import urllib.parse as ul
    return ul.quote_plus(url)


def id() -> int:
    import time
    return int(time.time() * 1000)


def random_id(length: int = 8) -> str:
    import random
    import string
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(random.choices(alphabet, k=length))


def md5(input_str: str, to_int: bool = False) -> int | str:
    import hashlib
    md5_hash = hashlib.md5(input_str.encode("utf-8")).hexdigest()
    return int(md5_hash, 16) if to_int else md5_hash


def to_unix_time(date_time: datetime, millis: bool = False) -> int:
    seconds = (date_time - datetime(1970, 1, 1)).total_seconds()
    return int(seconds * 1000 if millis else seconds)


def from_unix_time(seconds: int | float, millis: bool = False) -> datetime:
    return datetime.utcfromtimestamp(seconds / 1000 if millis else seconds)


def get_ip() -> str:
    import socket
    return socket.gethostbyname(socket.gethostname())


def get_external_ip(website: str = 'https://api.ipify.org/') -> str:
    import urllib.request
    return urllib.request.urlopen(website).read().decode("utf-8")
