from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from utils import parse_utc_datetime, utc_now


def _swap_event(created: str) -> dict[str, Any]:
    return {
        "node": {
            "created": created,
            "data": {
                "amount0In": 0,
                "amount0Out": 2,
                "amount1In": 4,
                "amount1Out": 0,
            },
        }
    }


def _recent_z_timestamp() -> str:
    return (utc_now() - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")


def _load_plugins() -> tuple[Any, Any]:
    from plg.chart.chart import Chart
    from plg.price.price import Price

    return Chart, Price


def test_parse_utc_datetime_normalizes_z_naive_and_offsets() -> None:
    assert parse_utc_datetime("2026-04-23T12:00:00Z") == datetime(
        2026, 4, 23, 12, 0, tzinfo=UTC
    )
    assert parse_utc_datetime("2026-04-23T12:00:00") == datetime(
        2026, 4, 23, 12, 0, tzinfo=UTC
    )
    assert parse_utc_datetime("2026-04-23T14:00:00+02:00") == datetime(
        2026, 4, 23, 12, 0, tzinfo=UTC
    )


def test_price_24h_volume_accepts_z_timestamps() -> None:
    _, Price = _load_plugins()
    plugin = Price.__new__(Price)

    volume = plugin.calculate_24h_volume_from_trades(
        [_swap_event(_recent_z_timestamp())],
        base_is_token0=True,
    )

    assert volume == pytest.approx(2)


def test_price_candles_accept_z_timestamps() -> None:
    _, Price = _load_plugins()
    plugin = Price.__new__(Price)

    candles = plugin.process_swap_events(
        [_swap_event(_recent_z_timestamp())],
        interval_minutes=1,
        limit=10,
        base_is_token0=True,
    )

    assert candles
    assert all(candle["time"].tzinfo is UTC for candle in candles)


def test_chart_candles_accept_z_timestamps() -> None:
    Chart, _ = _load_plugins()
    plugin = Chart.__new__(Chart)

    candles = plugin.process_swap_events(
        [_swap_event(_recent_z_timestamp())],
        interval_minutes=1,
        limit=10,
        base_is_token0=True,
    )

    assert candles
    assert all(candle["time"].tzinfo is UTC for candle in candles)
