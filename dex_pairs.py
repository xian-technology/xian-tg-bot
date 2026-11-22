"""DEX pairs and token symbols from BDS GraphQL, shared by /price and /chart."""

from __future__ import annotations

import json
from typing import Any

from graphql_paging import FetchGraphql, connection_nodes


def _pair(node: dict[str, Any]) -> dict[str, Any]:
    indexed = node["dataIndexed"]
    data = node["data"]
    return {"id": data["pair"], "token0": indexed["token0"], "token1": indexed["token1"]}


async def pairs(fetch_graphql: FetchGraphql, query: str) -> list[dict[str, Any]]:
    """Every pair from its PairCreated event: id and both token contracts."""
    return [_pair(node) async for node in connection_nodes(fetch_graphql, query, "allEvents")]


def _symbol_query(token_contracts: list[str]) -> str:
    parts = [
        f'symbol_{i}: allStates(first: 1, condition: {{key: "{token}.metadata:token_symbol"}}) '
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
        value = nodes[0].get("value") if nodes else None
        if value:
            symbols[token] = value if isinstance(value, str) else json.dumps(value)
    return symbols
