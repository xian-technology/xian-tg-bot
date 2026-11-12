"""/price and /chart read pair-filtered, time-bounded candles from the node's BDS."""

import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from xian_py.models import DexCandle

import constants as con
import dex_candles
from dex_candles import Candle, Timeframe, parse_request
from utils import utc_now

NOW = datetime(2026, 11, 12, 12, 30, tzinfo=UTC)


def dex_candle(start: str, o: str, h: str, low: str, c: str, v0: str, v1: str) -> DexCandle:
    return DexCandle.from_dict({
        "pair_id": 1, "bucket_start": start, "open": o, "high": h, "low": low, "close": c,
        "volume_token0": v0, "volume_token1": v1, "trade_count": 1,
    })


class FakeClient:
    def __init__(self, *pages: list[DexCandle]) -> None:
        self.pages = list(pages)
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    async def list_dex_candles(self, market_id: Any, **options: Any) -> list[DexCandle]:
        self.calls.append((market_id, options))
        return self.pages.pop(0)


@pytest.mark.parametrize(("args", "expected"), [
    ([], ("XIAN", "XUSDC", Timeframe(72, "h"))),
    (["30m"], ("XIAN", "XUSDC", Timeframe(30, "m"))),
    (["xdt"], ("XDT", "XIAN", Timeframe(72, "h"))),
    (["xdt-xusdc"], ("XDT", "XUSDC", Timeframe(72, "h"))),
    (["xdt", "7D"], ("XDT", "XIAN", Timeframe(7, "d"))),
    (["XIAN-XUSDC", "2h"], ("XIAN", "XUSDC", Timeframe(2, "h"))),
])
def test_request_arguments(args: list[str], expected: tuple[str, str, Timeframe]) -> None:
    assert parse_request(args, "72h") == expected


def test_too_many_arguments_ask_for_help() -> None:
    assert parse_request(["a", "b", "c"], "72h") is None


@pytest.mark.parametrize("args", [
    ["XDT", "2w"], ["XDT", "h"], ["XDT", "0h"], ["XDT", "-2h"], ["XDT", "1000h"], ["A-B-C"],
])
def test_invalid_requests_are_rejected(args: list[str]) -> None:
    with pytest.raises(ValueError):
        parse_request(args, "72h")


async def test_candles_query_one_pair_and_window_on_the_node() -> None:
    client = FakeClient([dex_candle("2026-11-12T11:00:00+00:00", "2", "3", "1", "2.5", "4", "10")])

    candles = await dex_candles.candles(client, 7, Timeframe(3, "h"), base_is_token0=True, now=NOW)

    assert client.calls == [(7, {"interval": 3600, "limit": 4, "start": NOW - timedelta(hours=3)})]
    assert candles == [Candle(datetime(2026, 11, 12, 11, tzinfo=UTC), 2.0, 3.0, 1.0, 2.5, 4.0)]


async def test_candles_quote_token1_bases_in_token0() -> None:
    client = FakeClient([dex_candle("2026-11-12T11:00:00Z", "2", "4", "1", "2.5", "4", "10")])

    [candle] = await dex_candles.candles(client, 7, Timeframe(3, "h"), base_is_token0=False, now=NOW)

    assert (candle.open, candle.high, candle.low, candle.close) == (0.5, 1.0, 0.25, 0.4)
    assert candle.volume == 10.0


async def test_day_summary_covers_exactly_the_last_24_hours() -> None:
    client = FakeClient([
        dex_candle("2026-11-11T00:00:00Z", "2", "3", "1", "2", "1", "2"),
        dex_candle("2026-11-12T00:00:00Z", "2", "5", "1.5", "4", "2", "8"),
    ])

    day = await dex_candles.day_summary(client, 7, base_is_token0=True, now=NOW)

    assert client.calls == [(7, {"interval": 86400, "limit": 2, "start": NOW - timedelta(days=1)})]
    assert day == dex_candles.DaySummary(volume=3.0, high=5.0, low=1.0)


async def test_day_summary_without_trades() -> None:
    day = await dex_candles.day_summary(FakeClient([]), 7, base_is_token0=True, now=NOW)

    assert day is None


def plugin_with_market(cls: Any, *pages: list[DexCandle]) -> Any:
    plugin = cls.__new__(cls)
    plugin.find_pair_by_symbols = AsyncMock(
        return_value={"id": 1, "token0_symbol": "XDT", "token1_symbol": "XIAN"})
    plugin.get_xian = AsyncMock(return_value=FakeClient(*pages))
    plugin.fetch_graphql = AsyncMock(side_effect=AssertionError("swaps are not read via GraphQL"))
    return plugin


def edited_text(edit: AsyncMock) -> str:
    assert edit.await_args is not None
    return str(edit.await_args.args[0])


def photo_caption(reply: AsyncMock) -> str:
    assert reply.await_args is not None
    return str(reply.await_args.kwargs["caption"])


def recent(hours_ago: int) -> str:
    return (utc_now() - timedelta(hours=hours_ago)).replace(
        minute=0, second=0, microsecond=0).isoformat()


async def test_price_reports_the_node_candles() -> None:
    from plg.price.price import Price

    window = [dex_candle(recent(2), "2", "2", "2", "2", "1", "2"),
              dex_candle(recent(1), "2", "3", "2", "3", "1", "3")]
    day = [dex_candle(recent(1), "2", "3", "2", "3", "2", "5")]
    plugin = plugin_with_market(Price, window, day)
    edited = AsyncMock()
    message = SimpleNamespace(reply_text=AsyncMock(return_value=SimpleNamespace(edit_text=edited)))

    await inspect.unwrap(Price.price_callback)(
        plugin, SimpleNamespace(message=message), SimpleNamespace(args=["XDT", "3h"]))

    text = edited_text(edited)
    assert "XDT-XIAN" in text and "3h" in text
    assert "Price:         3.000000 XIAN" in text
    assert "+1.000000 XIAN (+50.00%)" in text
    assert "24h Volume:    2.00 XDT" in text


async def test_price_without_trades_in_the_window() -> None:
    from plg.price.price import Price

    plugin = plugin_with_market(Price, [], [])
    edited = AsyncMock()
    message = SimpleNamespace(reply_text=AsyncMock(return_value=SimpleNamespace(edit_text=edited)))

    await inspect.unwrap(Price.price_callback)(
        plugin, SimpleNamespace(message=message), SimpleNamespace(args=["XDT", "3h"]))

    assert "No trades for XDT-XIAN in the last 3h" in edited_text(edited)


async def test_chart_renders_a_photo_from_node_candles() -> None:
    from plg.chart.chart import Chart

    window = [dex_candle(recent(2), "2", "2.1", "1.9", "2", "1", "2"),
              dex_candle(recent(1), "2", "3", "2", "3", "1", "3")]
    plugin = plugin_with_market(Chart, window, window)
    message = SimpleNamespace(reply_text=AsyncMock(), reply_photo=AsyncMock())

    await inspect.unwrap(Chart.chart_callback)(
        plugin, SimpleNamespace(message=message), SimpleNamespace(args=["XDT-XIAN", "3h"]))

    message.reply_text.assert_not_awaited()
    message.reply_photo.assert_awaited_once()
    assert "24h Volume: 2.00 XDT" in photo_caption(message.reply_photo)


def price_message() -> tuple[Any, AsyncMock]:
    edited = AsyncMock()
    return SimpleNamespace(reply_text=AsyncMock(return_value=SimpleNamespace(edit_text=edited))), edited


@pytest.mark.parametrize(("args", "reply"), [
    (["XDT", "2w"], "Invalid timeframe"),
    (["a", "b", "c"], "help text"),
])
async def test_price_rejects_bad_requests_before_querying(args: list[str], reply: str) -> None:
    from plg.price.price import Price

    plugin = plugin_with_market(Price)
    plugin.get_info = AsyncMock(return_value="help text")
    message, _ = price_message()

    await inspect.unwrap(Price.price_callback)(
        plugin, SimpleNamespace(message=message), SimpleNamespace(args=args))

    assert reply in message.reply_text.await_args.args[0]
    plugin.find_pair_by_symbols.assert_not_awaited()


async def test_price_reports_unknown_pairs_and_failures() -> None:
    from plg.price.price import Price

    plugin = plugin_with_market(Price)
    plugin.find_pair_by_symbols = AsyncMock(return_value=None)
    message, edited = price_message()
    await inspect.unwrap(Price.price_callback)(
        plugin, SimpleNamespace(message=message), SimpleNamespace(args=["XDT"]))
    assert edited_text(edited) == f"{con.ERROR} Trading pair XDT-XIAN not found"

    plugin.find_pair_by_symbols = AsyncMock(side_effect=RuntimeError("node <down>"))
    plugin.log = MagicMock()
    plugin.notify = AsyncMock()
    message, edited = price_message()
    await inspect.unwrap(Price.price_callback)(
        plugin, SimpleNamespace(message=message), SimpleNamespace(args=["XDT"]))
    assert edited_text(edited) == f"{con.ERROR} Error fetching price: node &lt;down&gt;"
    plugin.notify.assert_awaited_once()


async def test_price_shows_xian_market_cap_and_flat_day_without_recent_trades() -> None:
    from plg.price.price import Price

    plugin = plugin_with_market(Price, [dex_candle(recent(30), "3", "3", "2", "2", "1", "1")], [])
    plugin.find_pair_by_symbols = AsyncMock(
        return_value={"id": 1, "token0_symbol": "XIAN", "token1_symbol": "XUSDC"})
    message, edited = price_message()

    await inspect.unwrap(Price.price_callback)(
        plugin, SimpleNamespace(message=message), SimpleNamespace(args=["XIAN-XUSDC", "48h"]))

    text = edited_text(edited)
    assert text.startswith(f"{con.RED} <b>XIAN-XUSDC</b> 48h")
    assert "24h High:      2.000000 XUSDC" in text and "24h Volume:    0.00 XIAN" in text
    assert "Market Cap:    222,222,222 XUSDC" in text


async def test_chart_reports_unknown_pairs_empty_windows_and_failures() -> None:
    from plg.chart.chart import Chart

    plugin = plugin_with_market(Chart, [])
    message = SimpleNamespace(reply_text=AsyncMock(), reply_photo=AsyncMock())
    await inspect.unwrap(Chart.chart_callback)(
        plugin, SimpleNamespace(message=message), SimpleNamespace(args=["XDT", "3h"]))
    assert message.reply_text.await_args.args[0] == (
        f"{con.INFO} No trades for XDT-XIAN in the last 3h")

    plugin.find_pair_by_symbols = AsyncMock(return_value=None)
    await inspect.unwrap(Chart.chart_callback)(
        plugin, SimpleNamespace(message=message), SimpleNamespace(args=["XDT", "3h"]))
    assert message.reply_text.await_args.args[0] == f"{con.ERROR} Trading pair XDT-XIAN not found"

    plugin.find_pair_by_symbols = AsyncMock(side_effect=RuntimeError("down"))
    plugin.log = MagicMock()
    await inspect.unwrap(Chart.chart_callback)(
        plugin, SimpleNamespace(message=message), SimpleNamespace(args=["XDT", "3h"]))
    plugin.log.bind.assert_called_once()
    message.reply_photo.assert_not_awaited()


def test_chart_image_draws_moving_averages_for_long_windows() -> None:
    from plg.chart.chart import chart_image

    start = datetime(2026, 11, 1, tzinfo=UTC)
    window = [Candle(start + timedelta(hours=i), 2.0, 2.2 + i % 3, 1.8, 2.0 + i % 2, 1.0)
              for i in range(50)]

    image = chart_image(window, "XDT", "XIAN", Timeframe(50, "h"))

    assert image.startswith(b"\x89PNG")


async def test_chart_drops_the_old_local_trade_cache() -> None:
    from plg.chart.chart import Chart

    plugin = Chart.__new__(Chart)
    plugin.table_exists = AsyncMock(return_value=True)
    plugin.exec_sql = AsyncMock()

    await plugin.create_db_tables()

    plugin.exec_sql.assert_awaited_once_with("DROP TABLE IF EXISTS chart_trades")
