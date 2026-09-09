import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from xian_py.models import TransactionReceipt, TransactionSubmission
from xian_py.wallet import Wallet

from plugin import TGBFPlugin
from transactions import confirm_submission


def submission(*, accepted: bool = True) -> TransactionSubmission:
    return TransactionSubmission.from_dict({
        "submitted": True, "accepted": accepted, "tx_hash": "ABC123",
        "mode": "checktx", "message": "accepted" if accepted else "rejected",
    })


def receipt(*, success: bool = True) -> TransactionReceipt:
    return TransactionReceipt.from_lookup({
        "success": success, "tx_hash": "ABC123", "message": "confirmed",
        "execution": {"status": 0 if success else 1, "result": "None" if success else "failed"},
    })


def plugin_instance(cls: Any = TGBFPlugin) -> Any:
    from main import TelegramBot

    plugin = cls.__new__(cls)
    plugin._name = "test"
    plugin._tgb = TelegramBot()
    return plugin


async def test_kv_round_trip_and_prefix_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    plugin = plugin_instance()
    assert await plugin.kv_set("user:1", {"value": 1})
    assert await plugin.kv_set("user:2", 0)
    assert await plugin.kv_set("other", False)
    # A fresh plugin reads the persisted values, not an in-memory cache.
    fresh = plugin_instance()
    assert await fresh.kv_get("user:1") == {"value": 1}
    assert set(await fresh.kv_all()) == {"user:1", "user:2", "other"}
    await fresh.kv_del("user:", is_prefix=True)
    assert await plugin.kv_all() == ["other"]
    assert await plugin.kv_del("other")
    assert await fresh.kv_get("other") is None


async def test_kv_concurrent_writes_share_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    plugin = plugin_instance()
    other = plugin_instance()
    other._tgb = plugin._tgb
    await asyncio.gather(*(p.kv_set(str(i), i) for i, p in enumerate([plugin, other] * 10)))
    assert len(await plugin.kv_all()) == 20


@pytest.mark.parametrize("accepted", [True, False])
async def test_send_command_handles_current_sdk_result(accepted: bool) -> None:
    from plg.send.send import Send

    plugin = plugin_instance(Send)
    wallet = Wallet("1" * 64)
    receiver = Wallet("2" * 64).public_key
    message = SimpleNamespace(edit_text=AsyncMock())
    update = SimpleNamespace(message=SimpleNamespace(
        from_user=SimpleNamespace(id=1), reply_text=AsyncMock(return_value=message),
    ))
    client = SimpleNamespace(
        send=AsyncMock(return_value=submission(accepted=accepted)),
        wait_for_tx=AsyncMock(return_value=receipt()),
    )
    plugin.get_wallet = AsyncMock(return_value=wallet)
    plugin.get_xian = AsyncMock(return_value=client)
    plugin._cfg_global = SimpleNamespace(get=lambda *args: "http://127.0.0.1:8080")
    plugin._tgb.plugins["event"] = SimpleNamespace(is_node_connected=lambda: True)
    await inspect.unwrap(Send.send_callback)(plugin, update, SimpleNamespace(args=["1", receiver]))
    text = message.edit_text.call_args.args[0]
    if accepted:
        assert "Sent" in text
        client.wait_for_tx.assert_awaited_once()
    else:
        assert "rejected" in text
        client.wait_for_tx.assert_not_awaited()


@pytest.mark.parametrize("success", [True, False])
async def test_confirmation_distinguishes_acceptance_from_execution(success: bool) -> None:
    client = SimpleNamespace(wait_for_tx=AsyncMock(return_value=receipt(success=success)))
    confirmed, result = await confirm_submission(client, submission())
    assert confirmed is success
    assert result == (" " if success else "failed")


async def test_confirmation_uses_existing_receipt_without_second_lookup() -> None:
    from dataclasses import replace

    client = SimpleNamespace(wait_for_tx=AsyncMock())
    sent = replace(submission(), finalized=True, receipt=receipt())
    assert await confirm_submission(client, sent) == (True, " ")
    client.wait_for_tx.assert_not_awaited()


async def test_confirmation_timeout_retains_hash_and_does_not_resubmit() -> None:
    client = SimpleNamespace(wait_for_tx=AsyncMock(side_effect=TimeoutError()), send=AsyncMock())
    success, result = await confirm_submission(client, submission())
    assert not success
    assert "ABC123" in result and "may still complete" in result
    client.send.assert_not_awaited()


async def test_confirmation_preserves_task_cancellation() -> None:
    client = SimpleNamespace(wait_for_tx=AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await confirm_submission(client, submission())


async def test_sdk_clients_share_session_and_discover_chain_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xian_py import XianAsync

    from config import ConfigManager

    cfg_path = tmp_path / "global.json"
    cfg_path.write_text('{"xian": {"node": "http://127.0.0.1:26657"}}')
    plugin = plugin_instance()
    plugin._cfg_global = ConfigManager(cfg_path)

    async def discover(client: XianAsync) -> None:
        client.chain_id = "xian-localnet-1"

    monkeypatch.setattr(XianAsync, "ensure_chain_id", discover)
    try:
        first = await plugin.get_xian()
        second = await plugin.get_xian()
        session = first.session
        assert session is second.session
        assert plugin.cfg_global.get("xian", "chain_id") == "xian-localnet-1"
        await first.close()
        assert not session.closed  # Individual clients don't own the shared session.
    finally:
        await plugin.tgb.close_xian_session()
    assert session.closed


async def test_custom_node_discovers_its_own_chain_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xian_py import XianAsync

    from config import ConfigManager

    cfg_path = tmp_path / "global.json"
    cfg_path.write_text('{"xian": {"node": "http://127.0.0.1:26657", "chain_id": "default-local"}}')
    plugin = plugin_instance()
    plugin._cfg_global = ConfigManager(cfg_path)

    async def discover(client: XianAsync) -> None:
        assert client.chain_id is None
        client.chain_id = "other-local"

    monkeypatch.setattr(XianAsync, "ensure_chain_id", discover)
    try:
        client = await plugin.get_xian(node="http://127.0.0.1:26658")
        assert client.chain_id == "other-local"
        assert plugin.cfg_global.get("xian", "chain_id") == "default-local"
    finally:
        await plugin.tgb.close_xian_session()


@pytest.mark.parametrize("operation,args", [
    ("send", (1, "2" * 64)),
    ("approve", ("con_example",)),
    ("submit_contract", ("con_example", "@export\ndef value():\n    return 1\n")),
])
async def test_real_sdk_submission_and_confirmation(
    operation: str, args: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xian_py import XianAsync
    from xian_py import transaction as transport

    # Only mock the RPC boundary; create/sign the actual current transaction.
    broadcast = AsyncMock(return_value={"result": {"code": 0, "hash": "ABC123"}})
    monkeypatch.setattr(transport, "broadcast_tx_wait_async", broadcast)
    async with XianAsync("http://127.0.0.1:26657", "xian-localnet-1", Wallet("1" * 64)) as client:
        monkeypatch.setattr(client, "_reserve_nonce", AsyncMock(return_value=1))
        monkeypatch.setattr(client, "wait_for_tx", AsyncMock(return_value=receipt()))
        sent = await getattr(client, operation)(*args, chi=1000)
        assert isinstance(sent, TransactionSubmission)
        assert sent.accepted is True
        assert await confirm_submission(client, sent) == (True, " ")
    broadcast.assert_awaited_once()


@pytest.mark.parametrize("success", [True, False])
async def test_faucet_only_records_claim_after_successful_execution(success: bool) -> None:
    from plg.testnet.testnet import Testnet

    plugin = plugin_instance(Testnet)
    client = SimpleNamespace(
        send=AsyncMock(return_value=submission()),
        wait_for_tx=AsyncMock(return_value=receipt(success=success)),
    )
    plugin.get_testnet_instance = AsyncMock(return_value=client)
    plugin.validate_claim = AsyncMock()
    plugin.kv_set = AsyncMock()
    plugin.notify = AsyncMock()
    plugin._cfg = SimpleNamespace(get=lambda key: 10)
    message = SimpleNamespace(edit_text=AsyncMock())
    update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock(return_value=message)))
    await plugin.handle_claim(update, Wallet("1" * 64))
    if success:
        plugin.kv_set.assert_awaited_once()
        assert "Claimed" in message.edit_text.call_args.args[0]
    else:
        plugin.kv_set.assert_not_awaited()
        assert "not confirmed" in message.edit_text.call_args.args[0]


@pytest.mark.parametrize("kind", ["rain", "bless"])
@pytest.mark.parametrize("needs_approval", [True, False])
async def test_multisend_confirms_transfer_after_any_approval(kind: str, needs_approval: bool) -> None:
    from dataclasses import replace

    from plg.bless.bless import Bless
    from plg.rain.rain import Rain

    cls = Rain if kind == "rain" else Bless
    plugin = plugin_instance(cls)
    approval = replace(submission(), tx_hash="APPROVAL")
    transfer = replace(submission(), tx_hash="TRANSFER")
    client = SimpleNamespace(
        get_approved_amount=AsyncMock(return_value=0 if needs_approval else 100),
        approve=AsyncMock(return_value=approval),
        send_tx=AsyncMock(return_value=transfer),
        wait_for_tx=AsyncMock(return_value=receipt()),
    )
    plugin.get_xian = AsyncMock(return_value=client)
    plugin.get_wallet = AsyncMock(return_value=Wallet("1" * 64))
    plugin.get_resource = AsyncMock(return_value="SELECT users")
    plugin.exec_sql = AsyncMock(return_value={"success": True, "data": [(2, "recipient")]})
    plugin._cfg = SimpleNamespace(get=lambda key: {
        "user_limit": 10, "contract": "con_multisend", "function": "send",
    }.get(key))
    plugin._cfg_global = SimpleNamespace(get=lambda *keys: "http://127.0.0.1:8080")
    plugin.notify = AsyncMock()
    message = SimpleNamespace(edit_text=AsyncMock())
    message.edit_text.return_value = message
    update = SimpleNamespace(
        message=SimpleNamespace(from_user=SimpleNamespace(id=1), reply_text=AsyncMock(return_value=message)),
        effective_chat=SimpleNamespace(id=1),
    )
    context = SimpleNamespace(args=["10", "1h"], bot=SimpleNamespace(
        get_chat=AsyncMock(return_value=SimpleNamespace(first_name="xian.org", last_name="")),
    ))
    await inspect.unwrap(cls.rain_callback)(plugin, update, context)
    hashes = [call.args[0] for call in client.wait_for_tx.await_args_list]
    assert hashes == (["APPROVAL", "TRANSFER"] if needs_approval else ["TRANSFER"])
    assert "TRANSFER" in message.edit_text.call_args.args[0]
    plugin.notify.assert_not_awaited()


async def test_smoke_propagates_connection_failure_without_hanging() -> None:
    from scripts.tx_smoke import wait_for_event_ready

    async def fail() -> None:
        raise ConnectionError("connection refused")

    task = asyncio.create_task(fail())
    with pytest.raises(ConnectionError, match="connection refused"):
        await asyncio.wait_for(wait_for_event_ready(task, asyncio.Event(), 30), 0.5)


async def test_smoke_bounds_connection_wait() -> None:
    from scripts.tx_smoke import wait_for_event_ready

    task = asyncio.create_task(asyncio.Event().wait())
    try:
        with pytest.raises(TimeoutError, match="connecting transaction websocket"):
            await wait_for_event_ready(task, asyncio.Event(), 0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
