"""Every command that resolves tickers sees the native currency."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from config import ConfigManager


async def test_new_user_token_list_resolves_native_currency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plg.tokens.tokens import Tokens

    plugin = Tokens(SimpleNamespace(cfg=ConfigManager(tmp_path / "global.json")))
    monkeypatch.setattr(plugin, "get_dat_path", lambda plugin=None: tmp_path)
    assert (await plugin.exec_sql(await plugin.get_resource("create_tokens.sql")))["success"]

    # /buy and /sell trade against XIAN before the user ever ran /balance
    tokens = await plugin.get_tokens(42)

    assert [(token[1], token[2]) for token in tokens] == [("currency", "XIAN")]
    assert await plugin.get_tokens(42) == tokens


async def test_token_catalog_visits_all_cursor_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plg.tokens.tokens import Tokens

    calls: list[tuple[str, dict[str, str | None]]] = []

    async def resource(self: Tokens, name: str) -> str:
        return name

    async def fetch_graphql(
        self: Tokens, query: str, variables: dict[str, str | None]
    ) -> dict[str, Any]:
        calls.append((query, variables))
        first = variables["after"] is None
        return {
            "data": {
                "allContracts": {
                    "nodes": [{"name": "currency" if first else "con_token"}],
                    "pageInfo": {"hasNextPage": first, "endCursor": "second"},
                }
            }
        }

    monkeypatch.setattr(Tokens, "get_resource", resource)
    monkeypatch.setattr(Tokens, "fetch_graphql", fetch_graphql)
    plugin = Tokens(SimpleNamespace(cfg=ConfigManager(tmp_path / "global.json")))

    assert [name async for name in plugin._catalog_contracts()] == ["currency", "con_token"]
    assert calls == [
        ("get_all_tokens.gql", {"after": None}),
        ("get_all_tokens.gql", {"after": "second"}),
    ]
