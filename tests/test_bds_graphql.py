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


async def test_pairs_decode_json_text_and_documents() -> None:
    import dex_pairs

    async def graphql(query: str) -> dict[str, Any]:
        assert query == "PAIRS"
        return {"data": {"allEvents": {"edges": [
            {"node": {"dataIndexed": '{"token0":"con_a","token1":"currency"}', "data": '{"pair":1}'}},
            {"node": {"dataIndexed": {"token0": "con_b", "token1": "currency"}, "data": {"pair": 2}}},
        ]}}}

    assert await dex_pairs.pairs(graphql, "PAIRS") == [
        {"id": 1, "token0": "con_a", "token1": "currency"},
        {"id": 2, "token0": "con_b", "token1": "currency"},
    ]


async def test_no_pairs_and_no_symbols() -> None:
    import dex_pairs

    async def graphql(query: str) -> dict[str, Any]:
        return {"data": {"allEvents": {"edges": []}, "symbol_0": {"nodes": []}}}

    assert await dex_pairs.pairs(graphql, "PAIRS") == []
    assert await dex_pairs.token_symbols(graphql, []) == {}
    assert await dex_pairs.token_symbols(graphql, ["con_a"]) == {}


async def test_non_text_symbols_keep_their_json_spelling() -> None:
    import dex_pairs

    async def graphql(query: str) -> dict[str, Any]:
        return {"data": {"symbol_0": {"nodes": [{"value": "123"}]}}}

    assert await dex_pairs.token_symbols(graphql, ["con_a"]) == {"con_a": "123"}
