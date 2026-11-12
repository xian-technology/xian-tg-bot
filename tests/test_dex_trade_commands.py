"""/buy and /sell as a Telegram user drives them: the command, then the confirm button."""

import inspect
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from telegram.ext import CallbackQueryHandler, CommandHandler

from tests.test_command_safety import plugin_instance, quoting_client, receipt, submission

ROOT = Path(__file__).parents[1]
SIDES = ["buy", "sell"]
# (contract, ticker, name) rows as the tokens plugin returns them
TOKENS = [(1, "con_abc", "ABC"), (2, "currency", "XIAN"), (3, "con_usdc", "XUSDC")]


def make_plugin(side: str) -> Any:
    from plg.buy.buy import Buy
    from plg.sell.sell import Sell

    plugin = plugin_instance(Buy if side == "buy" else Sell, side)
    plugin._cfg = json.loads((ROOT / "plg" / side / "cfg" / f"{side}.json").read_text())
    return plugin


async def registered(plugin: Any) -> tuple[Any, CallbackQueryHandler]:
    """The command callback and confirm handler the plugin registers on init."""
    handlers: list[Any] = []
    plugin.add_handler = AsyncMock(side_effect=lambda handler, *a, **k: handlers.append(handler))
    plugin.table_exists = AsyncMock(return_value=True)
    await plugin.init()
    command = next(h for h in handlers if isinstance(h, CommandHandler))
    confirm = next(h for h in handlers if isinstance(h, CallbackQueryHandler))
    return inspect.unwrap(command.callback), confirm


async def run_command(side: str, args: list[str], tokens: list[Any] = TOKENS,
                      stored: bool = True) -> tuple[Any, Any, Any]:
    plugin = make_plugin(side)
    command, _ = await registered(plugin)
    plugin.get_plugin = lambda name: SimpleNamespace(get_tokens=AsyncMock(return_value=tokens))
    plugin.get_info = AsyncMock(return_value="usage")
    plugin.get_resource = AsyncMock(side_effect=lambda filename: filename)
    plugin.exec_sql = AsyncMock(return_value={"success": stored})
    message = SimpleNamespace(edit_text=AsyncMock())
    update = SimpleNamespace(message=SimpleNamespace(
        from_user=SimpleNamespace(id=7), reply_text=AsyncMock(return_value=message),
    ))
    await command(plugin, update, SimpleNamespace(args=args))
    return plugin, update, message.edit_text


@pytest.mark.parametrize("side", SIDES)
async def test_confirm_button_handler_matches_only_this_plugin(side: str) -> None:
    plugin = make_plugin(side)
    _, confirm = await registered(plugin)
    assert confirm.pattern.match(f"{side}_1")
    assert not confirm.pattern.match(f"x{side}_1")


@pytest.mark.parametrize("side", SIDES)
async def test_new_trade_table_is_created_only_when_missing(side: str) -> None:
    plugin = make_plugin(side)
    plugin.add_handler = AsyncMock()
    plugin.get_resource = AsyncMock(side_effect=lambda filename: filename)
    plugin.exec_sql = AsyncMock()
    plugin.table_exists = AsyncMock(return_value=False)
    await plugin.init()
    plugin.table_exists.assert_awaited_once_with(f"{side}_transactions")
    plugin.exec_sql.assert_awaited_once_with(f"create_{side}.sql")

    plugin.exec_sql.reset_mock()
    plugin.table_exists = AsyncMock(return_value=True)
    await plugin.init()
    plugin.exec_sql.assert_not_awaited()


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("args", [[], ["abc"], ["abc", "1", "2"]])
async def test_wrong_argument_count_shows_usage(side: str, args: list[str]) -> None:
    _, update, edit = await run_command(side, args)
    update.message.reply_text.assert_awaited_once_with("usage")
    edit.assert_not_awaited()


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("amount", ["abc", "0", "-1"])
async def test_invalid_amount_is_refused(side: str, amount: str) -> None:
    plugin, _, edit = await run_command(side, ["abc", amount])
    edit.assert_awaited_once_with("‼ Amount not valid")
    plugin.exec_sql.assert_not_awaited()


@pytest.mark.parametrize("side", SIDES)
async def test_malformed_pair_is_refused(side: str) -> None:
    plugin, _, edit = await run_command(side, ["abc-xian-x", "1"])
    assert "Pair not valid" in edit.await_args.args[0]
    plugin.exec_sql.assert_not_awaited()


@pytest.mark.parametrize(
    ("side", "args", "row", "prompt"),
    [
        # A ticker trades against XIAN, and XIAN itself against XUSDC; the
        # first token of a pair is the one bought or sold.
        ("buy", ["abc", "2.5"], ("con_abc", "ABC", "currency", "XIAN"), "Buy 2.5 ABC for XIAN"),
        ("buy", ["xian", "1"], ("currency", "XIAN", "con_usdc", "XUSDC"), "Buy 1 XIAN for XUSDC"),
        ("buy", ["XUSDC-abc", "3"], ("con_usdc", "XUSDC", "con_abc", "ABC"), "Buy 3 XUSDC for ABC"),
        ("sell", ["abc", "2.5"], ("con_abc", "ABC", "currency", "XIAN"), "Sell 2.5 ABC for XIAN"),
        ("sell", ["xian", "1"], ("currency", "XIAN", "con_usdc", "XUSDC"), "Sell 1 XIAN for XUSDC"),
        ("sell", ["xusdc-ABC", "3"], ("con_usdc", "XUSDC", "con_abc", "ABC"), "Sell 3 XUSDC for ABC"),
    ],
)
async def test_order_is_stored_and_offered_for_confirmation(
    side: str, args: list[str], row: tuple[str, ...], prompt: str,
) -> None:
    plugin, _, edit = await run_command(side, args)
    sql, order_id, user_id, *stored_row, amount = plugin.exec_sql.await_args.args
    assert sql == f"insert_{side}.sql" and user_id == 7
    assert tuple(stored_row) == row
    assert amount == args[1].encode()
    text = edit.await_args.args[0]
    assert text == f"💸 {prompt}"
    [[button]] = edit.await_args.kwargs["reply_markup"].inline_keyboard
    assert button.callback_data == f"{side}_{order_id}"
    assert button.text == f"✅ Confirm {side.upper()}"


@pytest.mark.parametrize(
    ("side", "tokens", "problem"),
    [
        # Missing contracts are reported for the sold token first
        ("buy", [], "No token contract found for <code>XIAN</code>"),
        ("sell", [], "No token contract found for <code>ABC</code>"),
        ("buy", [(1, "con_abc", "ABC")], "No token contract found for <code>XIAN</code>"),
        ("buy", [(2, "currency", "XIAN")], "No token contract found for <code>ABC</code>"),
        # Ambiguous tickers are reported for the traded token first
        ("buy", TOKENS + [(4, "con_abc2", "abc"), (5, "c2", "XIAN")], "Multiple matches found for <code>ABC</code>"),
        ("sell", TOKENS + [(4, "con_abc2", "abc"), (5, "c2", "XIAN")], "Multiple matches found for <code>ABC</code>"),
        ("buy", TOKENS + [(5, "c2", "XIAN")], "Multiple matches found for <code>XIAN</code>"),
        ("sell", TOKENS + [(5, "c2", "XIAN")], "Multiple matches found for <code>XIAN</code>"),
    ],
)
async def test_unknown_or_ambiguous_tokens_are_refused(side: str, tokens: list[Any], problem: str) -> None:
    plugin, _, edit = await run_command(side, ["abc", "1"], tokens=tokens)
    assert edit.await_args.args[0] == f"‼ {problem}. Please adjust your token list with /tokens"
    plugin.exec_sql.assert_not_awaited()


@pytest.mark.parametrize("side", SIDES)
async def test_unstored_order_is_not_offered(side: str) -> None:
    _, _, edit = await run_command(side, ["abc", "1"], stored=False)
    edit.assert_awaited_once_with("‼ Could not prepare order, please try again")


# --- confirm button --------------------------------------------------------

async def press(side: str, client: SimpleNamespace, claimed: bool = True) -> tuple[Any, Any, Any]:
    plugin = make_plugin(side)
    _, confirm = await registered(plugin)
    plugin.get_resource = AsyncMock(side_effect=lambda filename: filename)
    data = [(1, 7, "con_a", "A", "con_b", "B", b"2.5")] if claimed else []
    plugin.exec_sql = AsyncMock(return_value={"success": True, "data": data})
    plugin.get_wallet = AsyncMock()
    plugin.get_xian = AsyncMock(return_value=client)
    message = SimpleNamespace(edit_text=AsyncMock())
    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=7),
                             callback_query=SimpleNamespace(data=f"{side}_1", id="q"))
    context = SimpleNamespace(bot=SimpleNamespace(answer_callback_query=AsyncMock()))
    await confirm.callback(update, context)
    return plugin, context.bot.answer_callback_query, message.edit_text


@pytest.mark.parametrize("side", SIDES)
async def test_unavailable_order_only_answers_the_button(side: str) -> None:
    client = quoting_client()
    plugin, popup, edit = await press(side, client, claimed=False)
    plugin.exec_sql.assert_awaited_once_with(f"claim_{side}.sql", "1", 7)
    popup.assert_awaited_once_with("q", "‼ Order not available")
    edit.assert_not_awaited()
    plugin.get_xian.assert_not_awaited()


@pytest.mark.parametrize(
    ("side", "lines", "popup_text"),
    [
        # receipt result is [sold, bought]; price is counter token per traded token
        ("buy", "→ Bought 2.5 A\n← Sold 2 B\n$ Price 0.8", "✅ Bought 2.5 A"),
        ("sell", "← Sold 2 A\n→ Bought 2.5 B\n$ Price 1.25", "✅ Sold 2.5 A"),
    ],
)
async def test_settled_trade_reports_amounts_price_and_link(side: str, lines: str, popup_text: str) -> None:
    client = quoting_client(get_approved_amount=AsyncMock(return_value=Decimal(1000)))
    _, popup, edit = await press(side, client)
    text = edit.await_args.args[0]
    assert text == f'<code>{lines}</code>\n<a href="http://127.0.0.1:8080/tx/ABC123">View Transaction</a>'
    assert edit.await_args.kwargs == {"disable_web_page_preview": True}
    popup.assert_awaited_once_with("q", popup_text)
    contract, function = client.send_tx.await_args.args
    assert (contract, function) == ("con_dex_helper", side)
    kwargs = client.send_tx.await_args.kwargs["kwargs"]
    assert (kwargs["buy_token"], kwargs["sell_token"]) == (
        ("con_a", "con_b") if side == "buy" else ("con_b", "con_a")
    )
    assert set(kwargs["deadline"]) == {"__time__"}


def failing(exc: Exception) -> AsyncMock:
    return AsyncMock(side_effect=exc)


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize(
    ("overrides", "shown"),
    [
        ({"get_approved_amount": failing(RuntimeError("rpc <down>"))}, "‼ rpc &lt;down&gt;"),
        ({"approve": failing(RuntimeError("no <chi>"))}, "‼ no &lt;chi&gt;"),
        (
            {"approve": AsyncMock(return_value=SimpleNamespace(
                submitted=True, accepted=False, tx_hash=None, message="bad <nonce>"))},
            "‼ bad &lt;nonce&gt;",
        ),
        ({"wait_for_tx": AsyncMock(return_value=receipt(success=False, result="<denied>"))},
         "‼ Approval failed: &lt;denied&gt;"),
        # An approval whose outcome is unknown is not traded on
        ({"wait_for_tx": failing(TimeoutError())},
         "‼ Approval failed: Unable to confirm transaction ABC123: TimeoutError. "
         "It may still complete; check the explorer before retrying."),
    ],
)
async def test_failed_quote_or_approval_stops_before_trading(side: str, overrides: dict, shown: str) -> None:
    client = quoting_client(**overrides)
    plugin, popup, edit = await press(side, client)
    assert edit.await_args.args[0] == shown
    client.send_tx.assert_not_awaited()
    popup.assert_not_awaited()


@pytest.mark.parametrize("side", SIDES)
async def test_quote_and_send_errors_notify_the_owner(side: str) -> None:
    plugin, _, _ = await press(side, quoting_client(get_approved_amount=failing(RuntimeError("x"))))
    plugin.notify.assert_awaited_once()

    client = quoting_client(get_approved_amount=AsyncMock(return_value=Decimal(1000)),
                            send_tx=failing(RuntimeError("send <x>")))
    plugin, popup, edit = await press(side, client)
    plugin.notify.assert_awaited_once()
    assert edit.await_args.args[0] == "‼ send &lt;x&gt;"
    popup.assert_not_awaited()


@pytest.mark.parametrize("side", SIDES)
async def test_rejected_trade_submission_is_reported(side: str) -> None:
    rejected = SimpleNamespace(submitted=True, accepted=False, tx_hash=None, message="too <few> chi")
    client = quoting_client(get_approved_amount=AsyncMock(return_value=Decimal(1000)),
                            send_tx=AsyncMock(return_value=rejected))
    _, popup, edit = await press(side, client)
    assert edit.await_args.args[0] == "‼ too &lt;few&gt; chi"
    popup.assert_awaited_once_with("q", f"‼ {side.capitalize()} failed")


@pytest.mark.parametrize("side", SIDES)
async def test_failed_trade_shows_the_reason(side: str) -> None:
    client = quoting_client(get_approved_amount=AsyncMock(return_value=Decimal(1000)),
                            wait_for_tx=AsyncMock(return_value=receipt(success=False, result="<late>")))
    _, popup, edit = await press(side, client)
    assert edit.await_args.args[0] == "‼ &lt;late&gt;"
    popup.assert_awaited_once_with("q", f"‼ {side.capitalize()} failed")


@pytest.mark.parametrize(("side", "allowance", "approves"), [
    ("sell", "2.5", False), ("sell", "2.4", True), ("buy", "2.1", False), ("buy", "2.09", True),
])
async def test_approval_tops_up_only_a_short_allowance(side: str, allowance: str, approves: bool) -> None:
    client = quoting_client(get_approved_amount=AsyncMock(return_value=Decimal(allowance)),
                            approve=AsyncMock(return_value=submission()))
    await press(side, client)
    assert client.approve.await_count == int(approves)
    client.send_tx.assert_awaited_once()
