"""DEX price candles for /price and /chart from the node's BDS ``dex_candles`` query.

The node filters swaps by pair and time window and aggregates them into
buckets; the bot only parses the request and orients prices to the base token.
Buckets without trades are not returned.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import constants as con
from utils import parse_utc_datetime

DEFAULT_PAIR = "XIAN-XUSDC"
DEFAULT_QUOTE = "XIAN"
INTERVAL_SECONDS = {"m": 60, "h": 3600, "d": 86400}
# The node returns at most 1000 buckets; a window touches one more than it spans.
MAX_INTERVALS = 999


@dataclass(frozen=True)
class Timeframe:
    count: int
    unit: str

    @property
    def interval_seconds(self) -> int:
        return INTERVAL_SECONDS[self.unit]

    @property
    def span(self) -> timedelta:
        return timedelta(seconds=self.count * self.interval_seconds)

    def __str__(self) -> str:
        return f"{self.count}{self.unit}"


DAY = Timeframe(1, "d")


@dataclass(frozen=True)
class Candle:
    """One bucket priced in quote per base token; volume is in the base token."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class DaySummary:
    volume: float
    high: float
    low: float


@dataclass(frozen=True)
class Market:
    """Candles over the requested window and the last 24 hours of one pair."""

    window: list[Candle]
    day: DaySummary | None


class CandleClient(Protocol):
    async def list_dex_candles(self, market_id: Any, **options: Any) -> list[Any]: ...


def parse_timeframe(text: str) -> Timeframe:
    count, unit = text[:-1], text[-1:].lower()
    if unit not in INTERVAL_SECONDS or not count.isdigit():
        raise ValueError("Invalid timeframe. Use [number]m, [number]h or [number]d")
    if not 0 < int(count) <= MAX_INTERVALS:
        raise ValueError(f"Timeframe must cover 1 to {MAX_INTERVALS} intervals")
    return Timeframe(int(count), unit)


def _looks_like_timeframe(text: str) -> bool:
    return text.lower().endswith(tuple(INTERVAL_SECONDS)) and any(c.isdigit() for c in text)


def _parse_pair(text: str) -> tuple[str, str]:
    symbols = text.upper().split("-") if "-" in text else [text.upper(), DEFAULT_QUOTE]
    if len(symbols) != 2:
        raise ValueError("Invalid pair. Use BASE-QUOTE, for example XIAN-XUSDC")
    return symbols[0], symbols[1]


def parse_request(args: list[str], default_timeframe: str) -> tuple[str, str, Timeframe] | None:
    """Base symbol, quote symbol and timeframe of a command; None asks for help."""
    if len(args) > 2:
        return None
    if len(args) == 1 and _looks_like_timeframe(args[0]):
        args = [DEFAULT_PAIR, args[0]]
    base, quote = _parse_pair(args[0] if args else DEFAULT_PAIR)
    return base, quote, parse_timeframe(args[1] if len(args) == 2 else default_timeframe)


async def request_or_reply(
    message: Any, args: list[str] | None, default_timeframe: str,
    info: Callable[[], Awaitable[str]],
) -> tuple[str, str, Timeframe] | None:
    """Parse a /price or /chart request, or reply with the problem or the help text."""
    try:
        request = parse_request(args or [], default_timeframe)
    except ValueError as e:
        await message.reply_text(f"{con.ERROR} {e}")
        return None
    if request is None:
        await message.reply_text(await info())
    return request


def _oriented(candle: Any, base_is_token0: bool) -> Candle:
    # The node prices token1 per token0; a token1 base needs the reciprocal.
    time = parse_utc_datetime(candle.bucket_start)
    open_, high, low, close = (
        float(value) for value in (candle.open, candle.high, candle.low, candle.close)
    )
    if base_is_token0:
        return Candle(time, open_, high, low, close, float(candle.volume_token0))
    return Candle(time, 1 / open_, 1 / low, 1 / high, 1 / close, float(candle.volume_token1))


async def candles(
    client: CandleClient, pair_id: Any, timeframe: Timeframe, *, base_is_token0: bool,
    now: datetime,
) -> list[Candle]:
    """Candles of one pair over the last ``timeframe``, oldest first."""
    items = await client.list_dex_candles(
        pair_id,
        interval=timeframe.interval_seconds,
        limit=timeframe.count + 1,
        start=now - timeframe.span,
    )
    return [_oriented(item, base_is_token0) for item in items]


async def day_summary(
    client: CandleClient, pair_id: Any, *, base_is_token0: bool, now: datetime,
) -> DaySummary | None:
    """Volume, high and low over exactly the last 24 hours; None without trades."""
    day = await candles(client, pair_id, DAY, base_is_token0=base_is_token0, now=now)
    if not day:
        return None
    return DaySummary(
        volume=sum(candle.volume for candle in day),
        high=max(candle.high for candle in day),
        low=min(candle.low for candle in day),
    )


async def market(
    client: CandleClient, pair: dict[str, Any], base_symbol: str, timeframe: Timeframe,
    now: datetime,
) -> Market:
    """Window candles and 24h summary of a pair found by symbol, priced in its quote."""
    base_is_token0 = str(pair.get("token0_symbol", "")).upper() == base_symbol
    window = await candles(client, pair["id"], timeframe, base_is_token0=base_is_token0, now=now)
    if not window:
        return Market(window, None)
    return Market(
        window, await day_summary(client, pair["id"], base_is_token0=base_is_token0, now=now))
