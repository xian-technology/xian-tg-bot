"""Every command that resolves tickers sees the native currency."""

from pathlib import Path
from types import SimpleNamespace

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
