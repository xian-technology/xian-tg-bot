"""DEX pairs and token symbols from BDS GraphQL, shared by /price and /chart."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from utils import bds_state_value

FetchGraphql = Callable[[str], Awaitable[dict[str, Any]]]


def _pair(node: dict[str, Any]) -> dict[str, Any]:
    indexed = bds_state_value(node["dataIndexed"])
    data = bds_state_value(node["data"])
    return {"id": data["pair"], "token0": indexed["token0"], "token1": indexed["token1"]}


async def pairs(fetch_graphql: FetchGraphql, query: str) -> list[dict[str, Any]]:
    """Every pair from its PairCreated event: id and both token contracts."""
    result = await fetch_graphql(query)
    edges = result.get("data", {}).get("allEvents", {}).get("edges") or []
    return [_pair(edge["node"]) for edge in edges]


def _symbol_query(token_contracts: list[str]) -> str:
    parts = [
        f'symbol_{i}: allStates(condition: {{key: "{token}.metadata:token_symbol"}}) '
        "{ nodes { key value } }"
        for i, token in enumerate(token_contracts)
    ]
    return f"query GetTokensMetadata {{{' '.join(parts)}}}"


async def token_symbols(fetch_graphql: FetchGraphql, token_contracts: list[str]) -> dict[str, str]:
    """Symbols of the given token contracts in one query; tokens without one are left out."""
    if not token_contracts:
        return {}
    result = await fetch_graphql(_symbol_query(token_contracts))
    symbols = {}
    for i, token in enumerate(token_contracts):
        nodes = result.get("data", {}).get(f"symbol_{i}", {}).get("nodes", [])
        value = bds_state_value(nodes[0].get("value")) if nodes else None
        if value:
            symbols[token] = value if isinstance(value, str) else json.dumps(value)
    return symbols
