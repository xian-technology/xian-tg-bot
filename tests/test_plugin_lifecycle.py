"""Plugin removal must detach every command and live HTTP route it owns."""

import importlib
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from config import ConfigManager
from plugin import TGBFPlugin
from web import WebAppWrapper


@pytest.fixture
def bot_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, TGBFPlugin]:
    # The Application registry is real; it is never initialized or connected.
    monkeypatch.delitem(sys.modules, "main", raising=False)
    main = importlib.import_module("main")
    bot = main.TelegramBot()
    bot.bot = Application.builder().token("123:offline-test").build()
    bot.cfg = ConfigManager(tmp_path / "global.json")
    bot.web = WebAppWrapper(Path("res"))
    from plg.send.send import Send

    plugin = Send(bot)
    bot.plugins[plugin.name] = plugin
    bot.plugin_manifests[plugin.name] = plugin.manifest
    return bot, plugin


@pytest.mark.parametrize("group", [None, 0, 7])
async def test_disabling_plugin_removes_every_alias_in_shared_group(
    bot_plugin: tuple[Any, TGBFPlugin],
    group: int | None,
) -> None:
    bot, plugin = bot_plugin
    callback = AsyncMock()
    aliases = [CommandHandler(name, callback) for name in ("send", "withdraw", "transfer")]
    other = CommandHandler("unrelated", callback)
    bot.bot.add_handler(other, 99)
    for handler in aliases:
        await plugin.add_handler(handler, group=group)

    success, _ = await bot.disable_plugin(plugin.name)

    assert success
    assert [handler for handlers in bot.bot.handlers.values() for handler in handlers] == [other]
    assert plugin.name not in bot.plugins
    assert plugin.name not in bot.plugin_manifests
    assert not plugin.handlers


async def test_removing_one_alias_keeps_other_aliases_and_their_cleanup(
    bot_plugin: tuple[Any, TGBFPlugin],
) -> None:
    bot, plugin = bot_plugin
    callback = AsyncMock()
    first = CommandHandler("send", callback)
    second = CommandHandler("withdraw", callback)
    await plugin.add_handler(first, group=7)
    await plugin.add_handler(second, group=7)

    await plugin.remove_handler(first)
    assert bot.bot.handlers[7] == [second]
    await plugin.remove_handler(first)  # Repeated removal is harmless.
    assert bot.bot.handlers[7] == [second]

    assert (await bot.disable_plugin(plugin.name))[0]
    assert not bot.bot.handlers


@pytest.mark.parametrize("registered_before_start", [True, False])
def test_http_route_removal_detaches_live_and_staged_routes(registered_before_start: bool) -> None:
    wrapper = WebAppWrapper(Path("res"))

    def endpoint() -> dict[str, str]:
        return {"owner": "first"}

    def replacement() -> dict[str, str]:
        return {"owner": "replacement"}

    def unrelated() -> dict[str, bool]:
        return {"ok": True}

    if registered_before_start:
        wrapper.add_endpoint("/owned", endpoint)
    wrapper.run()
    if not registered_before_start:
        wrapper.add_endpoint("/owned", endpoint)
    wrapper.add_endpoint("/unrelated", unrelated)
    with TestClient(wrapper.app) as client:
        assert client.get("/owned").json() == {"owner": "first"}
        wrapper.remove_endpoint("/owned")
        assert client.get("/owned").status_code == 404
        assert client.get("/unrelated").json() == {"ok": True}
        wrapper.remove_endpoint("/owned")
        wrapper.add_endpoint("/owned", replacement)
        assert client.get("/owned").json() == {"owner": "replacement"}

    # Building a fresh server cannot resurrect a removed staging route.
    wrapper.remove_endpoint("/owned")
    wrapper.run()
    with TestClient(wrapper.app) as client:
        assert client.get("/owned").status_code == 404


async def test_pattern_handlers_share_group_zero_without_losing_cleanup(
    bot_plugin: tuple[Any, TGBFPlugin],
) -> None:
    bot, plugin = bot_plugin
    first = CallbackQueryHandler(AsyncMock(), pattern="^first$")
    second = CallbackQueryHandler(AsyncMock(), pattern="^second$")
    await plugin.add_handler(first)
    await plugin.add_handler(second)
    assert bot.bot.handlers[0] == [first, second]
    assert (await bot.disable_plugin(plugin.name))[0]
    assert not bot.bot.handlers


def test_http_route_removed_before_start_does_not_appear() -> None:
    wrapper = WebAppWrapper(Path("res"))
    wrapper.add_endpoint("/owned", lambda: {"owner": "first"})
    wrapper.remove_endpoint("/owned")
    wrapper.run()
    with TestClient(wrapper.app) as client:
        assert client.get("/owned").status_code == 404
