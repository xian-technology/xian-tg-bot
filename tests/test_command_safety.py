"""Fund-moving commands resolve recipients, claims and confirmations exactly once."""

import asyncio
import inspect
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from xian_py.models import TransactionReceipt, TransactionSubmission
from xian_py.wallet import Wallet

ROOT = Path(__file__).parents[1]


def submission() -> TransactionSubmission:
    return TransactionSubmission.from_dict({
        "submitted": True, "accepted": True, "tx_hash": "ABC123", "mode": "checktx", "message": "accepted",
    })


def receipt(*, success: bool = True, result: Any = None) -> TransactionReceipt:
    return TransactionReceipt.from_lookup({
        "success": success, "tx_hash": "ABC123", "message": "confirmed",
        "execution": {"status": 0 if success else 1, "result": result},
    })


def plugin_instance(cls: Any, name: str) -> Any:
    from main import TelegramBot

    plugin = cls.__new__(cls)
    plugin._name = name
    plugin._tgb = TelegramBot()
    plugin.notify = AsyncMock()
    plugin._cfg_global = SimpleNamespace(get=lambda *args: "http://127.0.0.1:8080")
    return plugin


def config(values: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(get=lambda *keys: values.get(keys[0]))


def resource(plugin: str, filename: str) -> str:
    return (ROOT / "plg" / plugin / "res" / filename).read_text()


def edited_message() -> SimpleNamespace:
    message = SimpleNamespace(edit_text=AsyncMock())
    message.edit_text.return_value = message
    return message


# --- /send recipient resolution -------------------------------------------

@pytest.mark.parametrize("simulation", [
    {"status": 1, "result": "Simulation error: contract con_name_service_final does not exist"},
    {"status": 0, "result": None},
    {"status": 0, "result": "not-an-address"},
    # A failed lookup's result is its error text, whatever that text looks like
    {"status": 1, "result": Wallet("3" * 64).public_key},
])
async def test_send_never_uses_unresolved_name_as_recipient(
    simulation: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plg.send.send as send_module

    plugin = plugin_instance(send_module.Send, "send")
    plugin._cfg = config({"xns_contract": "con_name_service_final", "xns_function": "get_main_name_to_address"})
    client = SimpleNamespace(
        send=AsyncMock(return_value=submission()),
        simulate=AsyncMock(return_value=simulation),
        wait_for_tx=AsyncMock(return_value=receipt()),
    )
    monkeypatch.setattr(send_module, "simulate_tx_async", AsyncMock(return_value=simulation), raising=False)
    plugin.get_wallet = AsyncMock(return_value=Wallet("1" * 64))
    plugin.get_xian = AsyncMock(return_value=client)
    message = edited_message()
    update = SimpleNamespace(message=SimpleNamespace(
        from_user=SimpleNamespace(id=1), reply_text=AsyncMock(return_value=message),
    ))

    await inspect.unwrap(send_module.Send.send_callback)(plugin, update, SimpleNamespace(args=["1", "alice"]))

    client.send.assert_not_awaited()
    assert "Not a valid address" in message.edit_text.call_args.args[0]


async def test_name_lookup_transport_failure_resolves_nothing() -> None:
    from plg.send.send import Send

    plugin = plugin_instance(Send, "send")
    plugin._cfg = config({"xns_contract": "con_xns", "xns_function": "lookup"})
    client = SimpleNamespace(simulate=AsyncMock(side_effect=ConnectionError("node down")))
    assert await plugin.resolve_name(client, "alice") is None


async def test_send_resolves_registered_name_to_its_address() -> None:
    from plg.send.send import Send

    plugin = plugin_instance(Send, "send")
    plugin._cfg = config({"xns_contract": "con_name_service_final", "xns_function": "get_main_name_to_address"})
    address = Wallet("2" * 64).public_key
    client = SimpleNamespace(
        send=AsyncMock(return_value=submission()),
        simulate=AsyncMock(return_value={"status": 0, "result": address}),
        wait_for_tx=AsyncMock(return_value=receipt()),
    )
    plugin.get_wallet = AsyncMock(return_value=Wallet("1" * 64))
    plugin.get_xian = AsyncMock(return_value=client)
    message = edited_message()
    update = SimpleNamespace(message=SimpleNamespace(
        from_user=SimpleNamespace(id=1), reply_text=AsyncMock(return_value=message),
    ))

    await inspect.unwrap(Send.send_callback)(plugin, update, SimpleNamespace(args=["1", "alice"]))

    assert client.send.await_args.args[1] == address
    client.simulate.assert_awaited_once_with(
        "con_name_service_final", "get_main_name_to_address", {"name": "alice"},
    )


async def test_failed_transfer_reason_is_rendered_as_text_not_markup() -> None:
    from plg.send.send import Send

    plugin = plugin_instance(Send, "send")
    hostile = '<a href="https://phish.example">recover funds</a>'
    client = SimpleNamespace(
        send=AsyncMock(return_value=submission()),
        wait_for_tx=AsyncMock(return_value=receipt(success=False, result=hostile)),
    )
    plugin.get_wallet = AsyncMock(return_value=Wallet("1" * 64))
    plugin.get_xian = AsyncMock(return_value=client)
    message = edited_message()
    update = SimpleNamespace(message=SimpleNamespace(
        from_user=SimpleNamespace(id=1), reply_text=AsyncMock(return_value=message),
    ))

    await inspect.unwrap(Send.send_callback)(
        plugin, update, SimpleNamespace(args=["1", Wallet("2" * 64).public_key]),
    )

    text = message.edit_text.call_args.args[0]
    assert "<a href" not in text
    assert "&lt;a href=&quot;https://phish.example&quot;&gt;" in text


# --- /testnet faucet ------------------------------------------------------

async def test_concurrent_faucet_claims_pay_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from plg.testnet.testnet import Testnet

    monkeypatch.chdir(tmp_path)
    plugin = plugin_instance(Testnet, "testnet")
    plugin._cfg = config({"amount": 100, "threshold": 20, "days_waiting": 1})

    async def slow_receipt(*args: Any, **kwargs: Any) -> TransactionReceipt:
        await asyncio.sleep(0.05)
        return receipt()

    client = SimpleNamespace(
        get_balance=AsyncMock(return_value=0),
        send=AsyncMock(return_value=submission()),
        wait_for_tx=AsyncMock(side_effect=slow_receipt),
    )
    plugin.get_testnet_instance = AsyncMock(return_value=client)
    messages = [edited_message() for _ in range(3)]
    updates = [SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock(return_value=m))) for m in messages]
    wallet = Wallet("1" * 64)

    await asyncio.gather(*(plugin.handle_claim(update, wallet) for update in updates))

    assert client.send.await_count == 1
    texts = [m.edit_text.call_args.args[0] for m in messages]
    assert sum("Claimed" in text for text in texts) == 1
    assert sum("wait" in text for text in texts) == 2


# --- /rain and /bless recipient selection ---------------------------------

def test_active_users_are_unique_per_user_even_after_renaming() -> None:
    now = datetime.now(UTC)
    with sqlite3.connect(":memory:") as conn:
        conn.execute(resource("active", "create_active.sql"))
        rows = [
            (1, 10, "@old_name", "hello", now - timedelta(minutes=5)),
            (1, 10, "@new_name", "again", now - timedelta(minutes=1)),
            (1, 11, "@other", "hi", now - timedelta(minutes=2)),
            (1, 12, "@cmd", "/rain 1 1h", now - timedelta(minutes=2)),
        ]
        for group, user, name, text, when in rows:
            conn.execute(
                "INSERT INTO active (group_id, user_id, user_name, msg_text, date_time) VALUES (?, ?, ?, ?, ?)",
                (group, user, name, text, when.strftime("%Y-%m-%d %H:%M:%S")),
            )
        since = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        selected = conn.execute(resource("active", "select_active.sql"), (1, since)).fetchall()

    assert sorted(user for user, _ in selected) == [10, 11]
    assert dict(selected)[10] == "@new_name"


async def test_rain_splits_total_over_paid_recipients_only() -> None:
    from plg.rain.rain import Rain

    plugin = plugin_instance(Rain, "rain")
    client = SimpleNamespace(
        get_approved_amount=AsyncMock(return_value=1000),
        send_tx=AsyncMock(return_value=submission()),
        wait_for_tx=AsyncMock(return_value=receipt()),
    )
    plugin.get_xian = AsyncMock(return_value=client)
    plugin.get_wallet = AsyncMock(side_effect=lambda user_id: Wallet(str(user_id) * 64))
    plugin.get_resource = AsyncMock(return_value="SELECT users")
    plugin.exec_sql = AsyncMock(return_value={"success": True, "data": [(2, "a"), (3, "b"), (4, "c")]})
    plugin._cfg = config({"user_limit": 2, "contract": "con_multisend", "function": "send"})
    message = edited_message()
    update = SimpleNamespace(
        message=SimpleNamespace(from_user=SimpleNamespace(id=1), reply_text=AsyncMock(return_value=message)),
        effective_chat=SimpleNamespace(id=1),
    )

    await inspect.unwrap(Rain.rain_callback)(plugin, update, SimpleNamespace(args=["10", "1h"]))

    since = plugin.exec_sql.await_args.args[2]
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", since)
    kwargs = client.send_tx.await_args.args[2]
    assert len(kwargs["addresses"]) == 2
    assert kwargs["amount"] * len(kwargs["addresses"]) == 10


# --- /all broadcast -------------------------------------------------------

async def test_broadcast_confirmation_matches_handler_and_requires_owner() -> None:
    from plg.all.all import All

    plugin = plugin_instance(All, "all")
    plugin._cfg = config({"admins": [5], "sleep": 0})
    plugin._cfg_global = SimpleNamespace(get=lambda *args: 99)
    plugin.add_handler = AsyncMock()
    await plugin.init()
    pattern = plugin.add_handler.await_args_list[1].args[0].pattern
    button = plugin.confirm_button().inline_keyboard[0][0]
    assert pattern.match(button.callback_data)

    plugin.get_resource = AsyncMock(return_value="SELECT users")
    plugin.exec_sql_global = AsyncMock(return_value={"success": True, "data": [(7,), (8,)]})

    def callback_update(user_id: int) -> SimpleNamespace:
        return SimpleNamespace(
            message=None,
            effective_user=SimpleNamespace(id=user_id),
            callback_query=SimpleNamespace(
                data=button.callback_data, message=SimpleNamespace(text_html="hello"),
                answer=AsyncMock(), edit_message_text=AsyncMock(),
            ),
        )

    stranger = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    await plugin.send_callback(callback_update(6), stranger)
    stranger.bot.send_message.assert_not_awaited()

    owner = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    await plugin.send_callback(callback_update(5), owner)
    assert owner.bot.send_message.await_count == 2


# --- /lottery -------------------------------------------------------------

async def test_lottery_join_answers_once_and_escapes_display_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from plg.lottery.lottery import Lottery

    monkeypatch.chdir(tmp_path)
    plugin = plugin_instance(Lottery, "lottery")
    plugin._cfg = config({"contract": "con_lottery"})
    plugin.get_wallet = AsyncMock(return_value=Wallet("1" * 64))
    client = SimpleNamespace(
        send_tx=AsyncMock(return_value=submission()),
        wait_for_tx=AsyncMock(return_value=receipt()),
    )
    plugin.get_xian = AsyncMock(return_value=client)
    plugin.remove_msg_after = AsyncMock()
    await plugin.kv_set("42", "<code>User Pool:</code> <code>0</code>")
    reply = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1, username=None, first_name='<a href="x">Mallory</a>'),
        callback_query=SimpleNamespace(
            id="q", data="lottery_42_add",
            message=SimpleNamespace(reply_text=reply, edit_caption=AsyncMock()),
        ),
    )
    context = SimpleNamespace(bot=SimpleNamespace(answer_callback_query=AsyncMock()))

    await plugin.lottery_action_callback(update, context)

    assert context.bot.answer_callback_query.await_count == 1
    text = reply.await_args_list[0].args[0]
    assert '<a href="x">' not in text and "&lt;a href=&quot;x&quot;&gt;Mallory&lt;/a&gt;" in text


# --- /buy and /sell confirmation ------------------------------------------

@pytest.mark.parametrize("side", ["buy", "sell"])
async def test_trade_confirmation_executes_once(side: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from plg.buy.buy import Buy
    from plg.sell.sell import Sell

    monkeypatch.chdir(tmp_path)
    plugin = plugin_instance(Buy if side == "buy" else Sell, side)
    plugin._cfg = json.loads((ROOT / "plg" / side / "cfg" / f"{side}.json").read_text())
    plugin.get_resource = AsyncMock(side_effect=lambda filename, *a, **k: resource(side, filename))
    assert (await plugin.exec_sql(resource(side, f"create_{side}.sql")))["success"]
    assert (await plugin.exec_sql(
        resource(side, f"insert_{side}.sql"), "1", 7, "con_a", "A", "con_b", "B", b"2.5",
    ))["success"]
    plugin.get_wallet = AsyncMock()

    async def slow_receipt(*args: Any, **kwargs: Any) -> TransactionReceipt:
        await asyncio.sleep(0.05)
        return receipt(result=[1, 2])

    client = SimpleNamespace(
        send_tx=AsyncMock(return_value=submission()),
        get_approved_amount=AsyncMock(return_value=100),
        wait_for_tx=AsyncMock(side_effect=slow_receipt),
    )
    plugin.get_xian = AsyncMock(return_value=client)

    def press(user_id: int) -> tuple[SimpleNamespace, SimpleNamespace]:
        update = SimpleNamespace(
            effective_message=SimpleNamespace(edit_text=AsyncMock()),
            effective_user=SimpleNamespace(id=user_id),
            callback_query=SimpleNamespace(data=f"{side}_1", id="1"),
        )
        return update, SimpleNamespace(bot=SimpleNamespace(answer_callback_query=AsyncMock()))

    callback = getattr(plugin, f"confirm_{side}_callback")
    await callback(*press(8))  # another user's press must not execute or consume the order
    client.send_tx.assert_not_awaited()
    await asyncio.gather(*(callback(*press(7)) for _ in range(3)))

    assert client.send_tx.await_count == 1
    plugin.get_wallet.assert_awaited_once_with(7)

