import importlib


def test_transaction_plugins_import_with_current_xian_py() -> None:
    for module in (
        "plugin",
        "plg.event.event",
        "plg.buybot.buybot",
        "plg.send.send",
        "plg.testnet.testnet",
        "plg.tip.tip",
    ):
        importlib.import_module(module)
