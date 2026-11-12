"""Faucet claims, testnet sends and contract submission as the user sees them."""

import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from xian_py.wallet import Wallet

from tests.test_command_safety import config, edited_message, plugin_instance, receipt, submission

ADDRESS = Wallet("1" * 64).public_key


def rejected(message: str) -> SimpleNamespace:
    return SimpleNamespace(submitted=True, accepted=False, tx_hash=None, message=message)


# --- /testnet claim ----------------------------------------------------------

async def claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, faucet: Any = None,
                balance: int = 0) -> tuple[Any, Any, Any]:
    from plg.testnet.testnet import Testnet

    monkeypatch.chdir(tmp_path)
    plugin = plugin_instance(Testnet, "testnet")
    plugin._cfg = config({"amount": 100, "threshold": 20, "days_waiting": 1})
    client = SimpleNamespace(
        get_balance=AsyncMock(return_value=balance),
        send=AsyncMock(return_value=submission()),
        wait_for_tx=AsyncMock(return_value=receipt()),
    )
    plugin.get_testnet_instance = faucet or AsyncMock(return_value=client)
    message = edited_message()
    await plugin._claim(message, ADDRESS)
    return plugin, client, message.edit_text


async def test_confirmed_claim_is_paid_and_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, client, edit = await claim(tmp_path, monkeypatch)
    client.send.assert_awaited_once()
    assert client.send.await_args.args[1] == ADDRESS
    edit.assert_awaited_once_with("ℹ Claimed 100 tXIAN to your bot wallet")
    assert await plugin.kv_get(ADDRESS)


async def test_wallet_above_threshold_cannot_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, client, edit = await claim(tmp_path, monkeypatch, balance=21)
    client.send.assert_not_awaited()
    edit.assert_awaited_once_with("‼ Your bot wallet already has more than 20 tXIAN")
    assert not await plugin.kv_get(ADDRESS)


@pytest.mark.parametrize(
    ("send", "wait", "shown"),
    [
        (AsyncMock(return_value=rejected("bad <nonce>")), None, "‼ Transaction failed: bad &lt;nonce&gt;"),
        (None, AsyncMock(return_value=receipt(success=False, result="<reverted>")),
         "‼ Claim not confirmed: &lt;reverted&gt;"),
        (AsyncMock(side_effect=RuntimeError("node <down>")), None, "‼ node &lt;down&gt;"),
    ],
)
async def test_failed_payment_is_not_recorded_as_a_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, send: Any, wait: Any, shown: str,
) -> None:
    from plg.testnet.testnet import Testnet

    monkeypatch.chdir(tmp_path)
    plugin = plugin_instance(Testnet, "testnet")
    plugin._cfg = config({"amount": 100, "threshold": 20, "days_waiting": 1})
    client = SimpleNamespace(
        get_balance=AsyncMock(return_value=0),
        send=send or AsyncMock(return_value=submission()),
        wait_for_tx=wait or AsyncMock(return_value=receipt()),
    )
    plugin.get_testnet_instance = AsyncMock(return_value=client)
    message = edited_message()

    await plugin._claim(message, ADDRESS)

    message.edit_text.assert_awaited_once_with(shown)
    assert not await plugin.kv_get(ADDRESS)


async def test_unconfigured_faucet_is_reported_without_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plg.testnet.testnet import FaucetNotConfiguredError

    plugin, _, edit = await claim(
        tmp_path, monkeypatch, faucet=AsyncMock(side_effect=FaucetNotConfiguredError("<unset>")),
    )
    edit.assert_awaited_once_with("‼ The faucet is not configured")
    plugin.notify.assert_awaited_once_with("Faucet key unavailable: &lt;unset&gt;")


async def test_unexpected_claim_error_is_generic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin, _, edit = await claim(tmp_path, monkeypatch, faucet=AsyncMock(side_effect=KeyError("x")))
    edit.assert_awaited_once_with("‼ An unexpected error occurred")
    plugin.notify.assert_awaited_once()


# --- /testnet send ------------------------------------------------------------

RECIPIENT = Wallet("2" * 64).public_key


async def send_testnet(to: str = RECIPIENT, balance: int = 50, send: Any = None,
                       wait: Any = None, xian: Any = None) -> tuple[Any, Any, Any]:
    from decimal import Decimal

    from plg.testnet.testnet import Testnet

    plugin = plugin_instance(Testnet, "testnet")
    plugin._cfg = config({"testnet_node": "http://node", "chain_id": "c", "explorer": "http://ex"})
    client = SimpleNamespace(
        get_balance=AsyncMock(return_value=balance),
        send=send or AsyncMock(return_value=submission()),
        wait_for_tx=wait or AsyncMock(return_value=receipt()),
    )
    plugin.get_xian = xian or AsyncMock(return_value=client)
    message = edited_message()
    update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock(return_value=message)))
    await plugin.handle_send(update, Wallet("1" * 64), Decimal("10"), to)
    return plugin, client, message.edit_text


async def test_testnet_send_reports_the_confirmed_transfer() -> None:
    plugin, client, edit = await send_testnet()
    plugin.get_xian.assert_awaited_once()
    assert plugin.get_xian.await_args.args[:2] == ("http://node", "c")
    client.send.assert_awaited_once()
    assert client.send.await_args.args[1] == RECIPIENT
    edit.assert_awaited_once_with(
        '✅ Sent <code>10</code> tXIAN\n<a href="http://ex/tx/ABC123">View Transaction</a>',
        disable_web_page_preview=True,
    )


@pytest.mark.parametrize(
    ("kwargs", "shown"),
    [
        ({"to": "not-an-address"}, "‼ Not a valid address!"),
        ({"send": AsyncMock(side_effect=RuntimeError("<down>"))}, "‼ &lt;down&gt;"),
        ({"send": AsyncMock(return_value=rejected("no <chi>"))}, "❌ no &lt;chi&gt;"),
        ({"xian": AsyncMock(side_effect=KeyError("x"))}, "‼ An unexpected error occurred"),
    ],
)
async def test_testnet_send_failures_are_reported(kwargs: dict, shown: str) -> None:
    _, _, edit = await send_testnet(**kwargs)
    edit.assert_awaited_once_with(shown)


async def test_testnet_send_needs_enough_balance() -> None:
    _, client, edit = await send_testnet(balance=9)
    client.send.assert_not_awaited()
    assert edit.await_args.args[0].startswith(
        "‼ Insufficient balance!\nYou need 10 tXIAN but only have 9 tXIAN."
    )


async def test_testnet_send_shows_a_failed_execution() -> None:
    _, _, edit = await send_testnet(wait=AsyncMock(return_value=receipt(success=False, result="<x>")))
    edit.assert_awaited_once_with("‼ &lt;x&gt;", disable_web_page_preview=True)


# --- contract submission -------------------------------------------------------

async def submit(caption: str | None, file_name: str = "Con_Token.py", deploy: Any = None,
                 wait: Any = None) -> tuple[Any, Any, Any]:
    import plg.submit.submit as submit_module

    plugin = plugin_instance(submit_module.Submit, "submit")
    client = SimpleNamespace(
        submit_contract=deploy or AsyncMock(return_value=submission()),
        wait_for_tx=wait or AsyncMock(return_value=receipt()),
    )
    plugin.get_wallet = AsyncMock()
    plugin.get_xian = AsyncMock(return_value=client)
    message = edited_message()

    async def download_to_memory(out: Any) -> None:
        out.write(b"x = 1\n")

    update = SimpleNamespace(
        message=SimpleNamespace(
            document=SimpleNamespace(file_name=file_name),
            caption=caption,
            reply_text=AsyncMock(return_value=message),
            effective_attachment=SimpleNamespace(get_file=AsyncMock(
                return_value=SimpleNamespace(download_to_memory=download_to_memory),
            )),
        ),
        effective_user=SimpleNamespace(id=7),
    )
    original_update = submit_module.telegram.Update
    submit_module.telegram.Update = SimpleNamespace
    try:
        await inspect.unwrap(submit_module.Submit.submit_callback)(plugin, update, SimpleNamespace())
    finally:
        submit_module.telegram.Update = original_update
    return client, update.message.reply_text, message.edit_text


@pytest.mark.parametrize(
    ("caption", "file_name", "deployed"),
    [(None, "Con_Token.py", "con_token"), ("name=con_other", "Con_Token.py", "con_other")],
)
async def test_contract_is_deployed_under_its_file_or_caption_name(
    caption: str | None, file_name: str, deployed: str,
) -> None:
    client, _, edit = await submit(caption, file_name)
    client.submit_contract.assert_awaited_once_with(deployed, "x = 1\n")
    assert edit.await_args.args[0] == (
        f'✅ Contract <code>{deployed}</code> deployed\n'
        '<a href="http://127.0.0.1:8080/tx/ABC123">View Transaction</a>'
    )


@pytest.mark.parametrize(("caption", "file_name"), [(None, "token.py"), ("name=token", "con_token.py")])
async def test_names_without_con_prefix_are_refused(caption: str | None, file_name: str) -> None:
    client, reply, _ = await submit(caption, file_name)
    client.submit_contract.assert_not_awaited()
    reply.assert_awaited_once_with("‼ Contract name needs to start with 'con_'")


@pytest.mark.parametrize(
    ("deploy", "wait", "shown"),
    [
        (AsyncMock(side_effect=RuntimeError("bad <code>")), None, "‼ bad &lt;code&gt;"),
        (AsyncMock(return_value=rejected("too <few> chi")), None, "❌ too &lt;few&gt; chi"),
        (None, AsyncMock(return_value=receipt(success=False, result="<lint>")), "❌ &lt;lint&gt;"),
    ],
)
async def test_failed_deployment_shows_the_escaped_reason(deploy: Any, wait: Any, shown: str) -> None:
    _, _, edit = await submit(None, deploy=deploy, wait=wait)
    assert edit.await_args.args[0] == shown


async def test_non_update_and_edited_messages_are_ignored() -> None:
    import plg.submit.submit as submit_module

    plugin = plugin_instance(submit_module.Submit, "submit")
    plugin.get_xian = AsyncMock()
    callback = inspect.unwrap(submit_module.Submit.submit_callback)
    await callback(plugin, object(), SimpleNamespace())
    edited = submit_module.telegram.Update(update_id=1)
    await callback(plugin, edited, SimpleNamespace())
    plugin.get_xian.assert_not_awaited()
