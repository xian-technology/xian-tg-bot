"""/balance, /simulate and /approve_contract as a user drives them."""

import inspect
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from telegram.ext import CommandHandler

from tests.test_command_safety import config, edited_message, plugin_instance, receipt, submission

LINK = '<a href="http://127.0.0.1:8080/tx/ABC123">View Transaction</a>'


async def command_of(plugin: Any) -> Any:
    handlers: list[Any] = []
    plugin.add_handler = AsyncMock(side_effect=lambda handler, *a, **k: handlers.append(handler))
    await plugin.init()
    [command] = [h for h in handlers if isinstance(h, CommandHandler)]
    return inspect.unwrap(command.callback)


def user_update(text: str = "", message: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        message=SimpleNamespace(from_user=SimpleNamespace(id=7), text=text,
                                reply_text=AsyncMock(return_value=message or edited_message())),
        effective_user=SimpleNamespace(id=7),
    )


# --- /balance ------------------------------------------------------------------

async def balance(tokens: list[Any], balances: dict[str, Any]) -> tuple[Any, Any]:
    from plg.balance.balance import Balance

    plugin = plugin_instance(Balance, "balance")
    plugin._cfg = config({})
    callback = await command_of(plugin)

    async def get_balance(contract: str) -> Any:
        value = balances[contract]
        if isinstance(value, Exception):
            raise value
        return value

    plugin.get_wallet = AsyncMock()
    plugin.get_xian = AsyncMock(return_value=SimpleNamespace(get_balance=AsyncMock(side_effect=get_balance)))
    plugin.get_plugin = lambda name: SimpleNamespace(get_tokens=AsyncMock(return_value=tokens))
    message = edited_message()
    await callback(plugin, user_update(message=message), SimpleNamespace())
    return plugin, message.edit_text


async def test_balances_list_xian_first_with_each_tokens_decimals() -> None:
    tokens = [(1, "con_abc", "A<B", 2), (2, "currency", "XIAN", 4), (3, "con_c", "C", 0)]
    _, edit = await balance(tokens, {"con_abc": Decimal("1.239"), "currency": 5, "con_c": Decimal("7.9")})
    edit.assert_awaited_once_with("<code>XIAN: 5.0000</code>\n<code>A&lt;B: 1.24</code>\n<code>C: 8</code>\n")


async def test_balance_lookup_failure_is_reported_and_notified() -> None:
    plugin, edit = await balance([(1, "con_abc", "ABC", 2)], {"con_abc": RuntimeError("node <down>")})
    edit.assert_awaited_once_with("‼ node &lt;down&gt;")
    plugin.notify.assert_awaited_once()


# --- /simulate -----------------------------------------------------------------

async def simulate(text: str, args: list[str], response: Any) -> tuple[Any, Any, Any]:
    import plg.simulate.simulate as simulate_module

    plugin = plugin_instance(simulate_module.Simulate, "simulate")
    plugin._cfg = config({})
    callback = await command_of(plugin)
    plugin.get_info = AsyncMock(return_value="usage")
    calls: list[Any] = []

    async def simulate_tx_async(node: str, payload: dict, vm_profile: str) -> Any:
        calls.append((node, payload, vm_profile))
        if isinstance(response, Exception):
            raise response
        return response

    original = simulate_module.simulate_tx_async
    simulate_module.simulate_tx_async = simulate_tx_async
    message = edited_message()
    update = user_update(text, message)
    try:
        await callback(plugin, update, SimpleNamespace(args=args))
    finally:
        simulate_module.simulate_tx_async = original
    return calls, update.message.reply_text, message.edit_text


async def test_simulate_without_payload_shows_usage() -> None:
    calls, reply, _ = await simulate("/simulate", [], {})
    reply.assert_awaited_once_with("usage")
    assert calls == []


@pytest.mark.parametrize("payload", ["{not json", "[1, 2]"])
async def test_simulate_needs_a_json_object(payload: str) -> None:
    calls, reply, _ = await simulate(f"/simulate {payload}", payload.split(), {})
    assert reply.await_args.args[0] == "‼ Provided payload is not valid JSON"
    assert calls == []


async def test_simulate_reports_result_chi_and_state() -> None:
    payload = '{"contract": "currency", "kwargs": {"amount": 1.5}}'
    response = {"status": 0, "result": "<ok>", "chi_used": 12, "state": [{"key": "k", "value": "v"}]}
    calls, _, edit = await simulate(f"/simulate {payload}", payload.split(), response)
    assert calls == [("http://127.0.0.1:8080", {"contract": "currency", "kwargs": {"amount": Decimal("1.5")}},
                      "xian_vm_v2")]
    text = edit.await_args.args[0]
    assert "Success:\n<pre>True</pre>" in text
    assert "Chi used:\n<pre>12</pre>" in text
    assert "Returned result:\n<pre>&lt;ok&gt;</pre>" in text
    assert "State changes:\n<pre>[{&#x27;key&#x27;: &#x27;k&#x27;, &#x27;value&#x27;: &#x27;v&#x27;}]</pre>" in text


async def test_simulate_escapes_state_changes() -> None:
    payload = '{"contract": "con_x"}'
    response = {"status": 0, "result": None, "chi_used": 1, "state": [{"key": "con_x.n:<b>", "value": "</pre>"}]}
    _, _, edit = await simulate(f"/simulate {payload}", [payload], response)
    text = edit.await_args.args[0]
    assert "<b>'" not in text and "'</pre>'" not in text
    assert "con_x.n:&lt;b&gt;" in text and "&lt;/pre&gt;" in text


async def test_simulate_marks_failed_execution() -> None:
    payload = '{"contract": "currency"}'
    response = {"status": 1, "result": "err", "chi_used": 3, "state": []}
    _, _, edit = await simulate(f"/simulate {payload}", [payload], response)
    assert "Success:\n<pre>False</pre>" in edit.await_args.args[0]


async def test_simulate_node_error_is_reported() -> None:
    payload = '{"contract": "currency"}'
    _, _, edit = await simulate(f"/simulate {payload}", [payload], RuntimeError("node <down>"))
    edit.assert_awaited_once_with("‼ node &lt;down&gt;")


# --- /approve_contract ---------------------------------------------------------

async def approve(args: list[str], **client: Any) -> tuple[Any, Any, Any]:
    from plg.approve.approve import Approve

    plugin = plugin_instance(Approve, "approve")
    callback = await command_of(plugin)
    plugin.get_info = AsyncMock(return_value="usage")
    plugin.get_wallet = AsyncMock()
    xian = SimpleNamespace(approve=client.get("approve") or AsyncMock(return_value=submission()),
                           wait_for_tx=client.get("wait") or AsyncMock(return_value=receipt()))
    plugin.get_xian = AsyncMock(return_value=xian)
    message = edited_message()
    update = user_update(message=message)
    await callback(plugin, update, SimpleNamespace(args=args))
    return xian, update.message.reply_text, message.edit_text


@pytest.mark.parametrize("args", [[], ["con_x", "currency"]])
async def test_approve_needs_contract_token_and_amount(args: list[str]) -> None:
    xian, reply, _ = await approve(args)
    reply.assert_awaited_once_with("usage")
    xian.approve.assert_not_awaited()


async def test_approve_refuses_an_invalid_amount() -> None:
    xian, reply, _ = await approve(["con_x", "currency", "lots"])
    reply.assert_awaited_once_with("‼ Amount is not valid")
    xian.approve.assert_not_awaited()


async def test_approve_reports_the_confirmed_approval() -> None:
    xian, _, edit = await approve(["con_x", "currency", "2.5"])
    xian.approve.assert_awaited_once_with("con_x", token="currency", amount=Decimal("2.5"))
    edit.assert_awaited_once_with(f"✅ Contract approved\n{LINK}", disable_web_page_preview=True)


@pytest.mark.parametrize(
    ("client", "shown"),
    [
        ({"approve": AsyncMock(side_effect=RuntimeError("<down>"))}, "‼ &lt;down&gt;"),
        ({"approve": AsyncMock(return_value=SimpleNamespace(
            submitted=True, accepted=False, tx_hash=None, message="no <chi>"))}, "❌ no &lt;chi&gt;"),
        ({"wait": AsyncMock(return_value=receipt(success=False, result="<x>"))}, "❌ &lt;x&gt;"),
    ],
)
async def test_failed_approval_shows_the_reason(client: dict, shown: str) -> None:
    _, _, edit = await approve(["con_x", "currency", "1"], **client)
    edit.assert_awaited_once_with(shown)
