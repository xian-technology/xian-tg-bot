"""Cursor pagination for BDS connection queries."""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

FetchGraphql = Callable[..., Awaitable[dict[str, Any]]]


async def connection_nodes(
    fetch_graphql: FetchGraphql, query: str, field: str,
) -> AsyncIterator[dict[str, Any]]:
    after = None
    while True:
        response = await fetch_graphql(query, variables={"after": after})
        connection = response["data"][field]
        for node in connection["nodes"]:
            yield node
        page_info = connection["pageInfo"]
        if not page_info["hasNextPage"]:
            return
        next_cursor = page_info["endCursor"]
        if not next_cursor or next_cursor == after:
            raise ValueError(f"GraphQL returned an invalid cursor for {field}")
        after = next_cursor
