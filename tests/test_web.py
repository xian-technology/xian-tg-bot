import importlib
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from web import WebAppWrapper


@pytest.mark.parametrize("host", ["::", "::1"])
def test_web_app_wrapper_passes_configured_ipv6_host_to_uvicorn(host: str) -> None:
    wrapper = WebAppWrapper(res_path=Path("res"), host=host, port=5099)

    server = wrapper.run()

    assert server.config.host == host
    assert server.config.port == 5099


def test_web_app_wrapper_preserves_default_bind_host() -> None:
    wrapper = WebAppWrapper(res_path=Path("res"), port=5099)

    server = wrapper.run()

    assert server.config.host == "0.0.0.0"


async def test_telegram_bot_wires_configured_webserver_host(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin_module = types.ModuleType("plugin")

    class PluginDependencyError(Exception):
        pass

    class PluginLifecycleError(Exception):
        message = ""

    class PluginManifest:
        pass

    class TGBFPlugin:
        pass

    setattr(plugin_module, "PluginDependencyError", PluginDependencyError)
    setattr(plugin_module, "PluginLifecycleError", PluginLifecycleError)
    setattr(plugin_module, "PluginManifest", PluginManifest)
    setattr(plugin_module, "TGBFPlugin", TGBFPlugin)
    monkeypatch.setitem(sys.modules, "plugin", plugin_module)

    sys.modules.pop("main", None)
    main = importlib.import_module("main")

    captured: dict[str, Any] = {}

    class FakeConfig:
        def get(self, key: str, default: Any = None, raise_if_missing: bool = False) -> Any:
            values = {
                "admin_tg_id": 123456789,
                "webserver_host": "::1",
                "webserver_port": 5099,
            }
            return values.get(key, default)

    class FakeWebAppWrapper:
        def __init__(self, res_path: Path, host: str, port: int) -> None:
            captured["res_path"] = res_path
            captured["host"] = host
            captured["port"] = port

    class FakeSender:
        async def send_message(self, chat_id: int, text: str) -> None:
            raise main.InvalidToken("invalid token")

    class FakeUpdater:
        bot = FakeSender()

    class FakeBot:
        updater = FakeUpdater()

    class FakeBuilder:
        def defaults(self, defaults: object) -> "FakeBuilder":
            return self

        def token(self, token: str | None) -> "FakeBuilder":
            return self

        def build(self) -> FakeBot:
            return FakeBot()

    class FakeApplication:
        @staticmethod
        def builder() -> FakeBuilder:
            return FakeBuilder()

    async def load_plugins(self: object) -> None:
        return None

    async def shutdown(self: object) -> None:
        return None

    try:
        monkeypatch.setattr(main, "Application", FakeApplication)
        monkeypatch.setattr(main, "WebAppWrapper", FakeWebAppWrapper)
        monkeypatch.setattr(main.TelegramBot, "load_plugins", load_plugins)
        monkeypatch.setattr(main.TelegramBot, "shutdown", shutdown)

        bot = main.TelegramBot()
        await bot.run(FakeConfig(), "token")

        assert captured == {
            "res_path": main.con.DIR_RES,
            "host": "::1",
            "port": 5099,
        }
    finally:
        sys.modules.pop("main", None)
