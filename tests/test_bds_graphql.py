"""Bot GraphQL reads match the BDS PostGraphile schema and its JSON-text values."""

import re
from typing import Any

import pytest

# BDS stores state values as JSON text; PostGraphile returns that text as-is.
STATE_VALUES = {"con_dex_demo_token": '"XDT"', "currency": '"XIAN"'}


async def fake_graphql(query: str, variables: Any = None) -> dict[str, Any]:
    aliases = re.findall(r'(symbol_\d+): allStates\(condition: \{key: "([^"]+)\.metadata', query)
    return {"data": {
        alias: {"nodes": [{"key": f"{token}.metadata:token_symbol", "value": STATE_VALUES[token]}]}
        for alias, token in aliases
    }}


@pytest.mark.parametrize(("module", "cls", "method"), [
    ("plg.price.price", "Price", "fetch_token_symbols"),
    ("plg.chart.chart", "Chart", "fetch_token_symbols_from_graphql"),
])
async def test_token_symbols_decode_bds_state_values(module: str, cls: str, method: str) -> None:
    plugin_cls = getattr(__import__(module, fromlist=[cls]), cls)
    plugin = plugin_cls.__new__(plugin_cls)
    plugin.fetch_graphql = fake_graphql

    symbols = await getattr(plugin, method)(["con_dex_demo_token", "currency"])

    assert symbols == {"con_dex_demo_token": "XDT", "currency": "XIAN"}


def swap(pair: int, created: str) -> dict[str, Any]:
    return {"node": {"dataIndexed": f'{{"to":"abc","pair":{pair}}}',
                     "data": '{"amount0In":{"__fixed__":"1.5"},"amount1In":0}', "created": created}}


@pytest.mark.parametrize(("module", "cls", "method"), [
    ("plg.price.price", "Price", "fetch_swap_events"),
    ("plg.chart.chart", "Chart", "fetch_swap_events_from_graphql"),
])
async def test_swap_events_query_current_schema_and_keep_one_pair(module: str, cls: str, method: str) -> None:
    queries = []

    async def graphql(query: str, variables: Any = None) -> dict[str, Any]:
        queries.append((query, variables))
        # Event rows expose createdAt; dataIndexed is JSON text and not filterable
        assert "createdAt" in query and "CREATED_AT_DESC" in query
        assert "contains" not in query and "CREATED_DESC" not in query
        return {"data": {"allEvents": {"edges": [
            swap(2, "2026-11-12T00:46:34+00:00"), swap(1, "2026-11-12T00:46:32+00:00"),
        ]}}}

    plugin_cls = getattr(__import__(module, fromlist=[cls]), cls)
    plugin = plugin_cls.__new__(plugin_cls)
    plugin._name = cls.lower()
    plugin.fetch_graphql = graphql

    edges = await getattr(plugin, method)(1)

    assert [edge["node"]["created"] for edge in edges] == ["2026-11-12T00:46:32+00:00"]
    assert len(queries) == 1


async def test_chart_renders_a_photo_for_recent_swaps() -> None:
    import inspect
    import json
    from datetime import timedelta
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from plg.chart.chart import Chart
    from utils import utc_now

    def trade(minutes_ago: int, amount_in: str, amount_out: str) -> dict[str, Any]:
        created = (utc_now() - timedelta(minutes=minutes_ago)).isoformat()
        data = {"amount0In": 0, "amount1In": {"__fixed__": amount_in},
                "amount0Out": {"__fixed__": amount_out}, "amount1Out": 0}
        return {"node": {"dataIndexed": json.dumps({"pair": 1}), "data": json.dumps(data), "created": created}}

    plugin = Chart.__new__(Chart)
    plugin.find_pair_by_symbols = AsyncMock(return_value={"id": 1, "token0_symbol": "XDT", "token1_symbol": "XIAN"})
    plugin.fetch_swap_events = AsyncMock(return_value=[trade(90, "2.02", "2"), trade(30, "1.01", "1")])
    message = SimpleNamespace(reply_text=AsyncMock(), reply_photo=AsyncMock())

    await inspect.unwrap(Chart.chart_callback)(
        plugin, SimpleNamespace(message=message), SimpleNamespace(args=["XDT-XIAN", "3h"]),
    )

    message.reply_text.assert_not_awaited()
    message.reply_photo.assert_awaited_once()
