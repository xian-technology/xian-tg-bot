"""/buy and /sell: one con_dex_helper trade flow for both sides.

A command stores an order and offers a confirm button; the button claims the
order once, bounds the price from a fresh quote, tops up the allowance of the
sold token and submits the trade. The only per-side differences are the data
in :class:`TradeSide`.

Throughout, the *traded* token is the one bought (buy) or sold (sell); the
*counter* token is the other one. Orders and pairs name the traded token first.
"""

from __future__ import annotations

import html
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

import constants as con
import utils as utl
from dex_quote import buy_max_input, sell_min_out
from logging_setup import error_context, transaction_context
from plugin import TGBFPlugin
from transactions import (
    decode_trade_amount,
    encode_trade_amount,
    parse_amount,
    submission_accepted,
    trade_amount,
    trade_deadline,
)

TOKEN_LIST_HINT = "Please adjust your token list with /tokens"


@dataclass(frozen=True)
class TradeSide:
    name: str  # also the con_dex_helper function
    title: str
    past: str
    bound_name: str
    price_bound: Callable[..., Awaitable[Decimal]]
    # buy approves the bound (the most it may spend), sell the amount sold
    approves_bound: bool


BUY = TradeSide("buy", "Buy", "Bought", "max_input", buy_max_input, approves_bound=True)
SELL = TradeSide("sell", "Sell", "Sold", "min_out", sell_min_out, approves_bound=False)


@dataclass(frozen=True)
class Order:
    side: TradeSide
    user_id: int
    traded_contract: str
    traded_symbol: str
    counter_contract: str
    counter_symbol: str
    amount: Decimal

    @property
    def buy_token(self) -> str:
        return self.traded_contract if self.side is BUY else self.counter_contract

    @property
    def sell_token(self) -> str:
        return self.counter_contract if self.side is BUY else self.traded_contract

    @property
    def buy_symbol(self) -> str:
        return self.traded_symbol if self.side is BUY else self.counter_symbol

    @property
    def sell_symbol(self) -> str:
        return self.counter_symbol if self.side is BUY else self.traded_symbol


def order_symbols(pair_or_ticker: str) -> tuple[str, str] | None:
    """(traded, counter) symbols; a ticker trades against XIAN, XIAN against XUSDC."""
    if "-" not in pair_or_ticker:
        ticker = pair_or_ticker.upper()
        return (ticker, "XUSDC") if ticker == "XIAN" else (ticker, "XIAN")
    if pair_or_ticker.count("-") != 1:
        return None
    traded, counter = pair_or_ticker.upper().split("-")
    return traded, counter


def select_contracts(
    tokens: Sequence[Sequence[Any]], side: TradeSide, traded: str, counter: str,
) -> tuple[str, str] | str:
    """(traded, counter) contracts from the user's token list, or the refusal text.

    Missing contracts are reported for the sold token first, ambiguous tickers
    for the traded token first.
    """
    matches = {
        symbol: [token[1] for token in tokens if token[2].upper() == symbol]
        for symbol in (traded, counter)
    }
    sold, bought = (counter, traded) if side is BUY else (traded, counter)
    for symbol in (sold, bought):
        if not matches[symbol]:
            return f"{con.ERROR} No token contract found for <code>{symbol}</code>. {TOKEN_LIST_HINT}"
    for symbol in (traded, counter):
        if len(matches[symbol]) != 1:
            return f"{con.ERROR} Multiple matches found for <code>{symbol}</code>. {TOKEN_LIST_HINT}"
    return matches[traded][0], matches[counter][0]


def settled_summary(order: Order, result: Sequence[Any]) -> str:
    """Traded token line first; price is counter token per traded token."""
    sold, bought = (trade_amount(value) for value in result)
    sold_line = f"← Sold {sold:f} {order.sell_symbol}"
    bought_line = f"→ Bought {bought:f} {order.buy_symbol}"
    if order.side is BUY:
        lines, price = [bought_line, sold_line], sold / bought
    else:
        lines, price = [sold_line, bought_line], bought / sold
    return "\n".join([*lines, f"$ Price {utl.format_float(price)}"])


class DexTradePlugin(TGBFPlugin):
    """Base of the Buy and Sell plugins; subclasses set ``side``."""

    side: TradeSide

    async def init(self) -> None:
        await self.add_handler(CommandHandler(self.handle, self.order_callback, block=False))
        await self.add_handler(CallbackQueryHandler(
            self.confirm_callback, pattern=f"^{self.name}_", block=False,
        ))
        # Create table if it doesn't exist
        if not await self.table_exists(f"{self.side.name}_transactions"):
            await self.exec_sql(await self.get_resource(f"create_{self.side.name}.sql"))

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def order_callback(self, update: Update, context: CallbackContext) -> None:
        # Don't deal with edited messages
        if not update.message:
            return
        if len(context.args) != 2:
            await update.message.reply_text(await self.get_info())
            return

        user_id = update.message.from_user.id
        pair_or_ticker, amount_text = context.args
        message = await update.message.reply_text(f"{con.WAIT} Preparing ...")

        try:
            amount = parse_amount(amount_text)
            if amount <= 0:
                raise ValueError('Amount can not be negative')
        except Exception:
            await message.edit_text(f"{con.ERROR} Amount not valid")
            return

        symbols = order_symbols(pair_or_ticker)
        if symbols is None:
            await message.edit_text(
                f"{con.ERROR} Pair not valid. "
                f"Please use the format <code>[ticker#1]-[ticker#2]</code> or just <code>[ticker]</code>")
            return
        traded, counter = symbols

        tokens = await self.get_plugin('tokens').get_tokens(user_id)
        contracts = select_contracts(tokens, self.side, traded, counter)
        if isinstance(contracts, str):
            await message.edit_text(contracts)
            return

        id = utl.id()
        stored = await self.exec_sql(
            await self.get_resource(f"insert_{self.side.name}.sql"),
            id, user_id, contracts[0], traded, contracts[1], counter, encode_trade_amount(amount)
        )
        if not stored["success"]:
            await message.edit_text(f"{con.ERROR} Could not prepare order, please try again")
            return

        await message.edit_text(
            f"{con.MONEY} {self.side.title} {amount} {traded} for {counter}",
            reply_markup=self.confirm_button(id))

    def confirm_button(self, id: int) -> InlineKeyboardMarkup:
        menu = utl.build_menu(
            [InlineKeyboardButton(
                f"{con.DONE} Confirm {self.side.name.upper()}",
                callback_data=f'{self.name}_{id}'
            )], 1
        )
        return InlineKeyboardMarkup(menu)

    async def confirm_callback(self, update: Update, context: CallbackContext) -> None:
        self.log.debug("Trade confirmation callback received")
        if not update.callback_query.data.startswith(self.name):
            return

        order = await self._claim_order(update)
        if order is None:
            await context.bot.answer_callback_query(update.callback_query.id, f"{con.ERROR} Order not available")
            return

        xian = await self.get_xian(wallet=await self.get_wallet(order.user_id))
        message = update.effective_message
        bound = await self._bound_with_allowance(xian, order, message)
        if bound is None:
            return

        success = await self._submit(xian, order, bound, message)
        if success is None:
            return
        await context.bot.answer_callback_query(
            update.callback_query.id,
            f"{con.DONE} {self.side.past} {order.amount} {order.traded_symbol}"
            if success else f"{con.ERROR} {self.side.title} failed"
        )

    async def _claim_order(self, update: Update) -> Order | None:
        """Claim the pending order: a confirmation executes at most once, and
        only for the user who created it."""
        order_id = update.callback_query.data.split('_')[1]
        res = await self.exec_sql(
            await self.get_resource(f'claim_{self.side.name}.sql'), order_id, update.effective_user.id,
        )
        if not res["success"] or not res["data"]:
            return None
        row = res["data"][0]
        return Order(self.side, row[1], row[2], row[3], row[4], row[5], decode_trade_amount(row[6]))

    async def _fail(self, message: Any, error: Exception) -> None:
        handler = f'confirm_{self.side.name}_callback'
        self.log.bind(**error_context(error), handler=handler).error(f'Confirm {self.side.name} callback failed')
        await self.notify(error)
        await message.edit_text(f"{con.ERROR} {html.escape(str(error))}")

    async def _bound_with_allowance(self, xian: Any, order: Order, message: Any) -> Decimal | None:
        """The signed price bound, once the sold token's allowance covers the trade."""
        contract = self.cfg.get('contract')
        try:
            # The helper reverts when execution is worse than this bound, so a
            # price move after signing cannot cost more than the tolerance
            bound = await self.side.price_bound(
                xian,
                dex=self.cfg.get('dex_contract'),
                pairs=self.cfg.get('pairs_contract'),
                buy_token=order.buy_token,
                sell_token=order.sell_token,
                amount=order.amount,
                slippage=parse_amount(self.cfg.get('slippage_percent')),
            )
            approved_amount = await xian.get_approved_amount(contract, token=order.sell_token)
            self.log.debug("Token allowance checked")
        except Exception as e:
            await self._fail(message, e)
            return None

        needed = bound if self.side.approves_bound else order.amount
        if approved_amount < needed and not await self._approve(xian, contract, order.sell_token, needed, message):
            return None
        return bound

    async def _approve(self, xian: Any, contract: str, token: str, amount: Decimal, message: Any) -> bool:
        """Approve exactly ``amount``; True once the approval executed."""
        try:
            approve = await xian.approve(contract, token=token, amount=amount)
            self.log.bind(**transaction_context(approve)).debug("Transaction response received")
        except Exception as e:
            await self._fail(message, e)
            return False

        if not submission_accepted(approve):
            await message.edit_text(f"{con.ERROR} {html.escape(str(approve.message))}")
            return False
        success, result = await self.confirm_tx(xian, approve)
        if not success:
            await message.edit_text(f"{con.ERROR} Approval failed: {html.escape(str(result))}")
            return False
        return True

    async def _submit(self, xian: Any, order: Order, bound: Decimal, message: Any) -> bool | None:
        """Submit the trade and report it; None when it could not be sent."""
        try:
            submission = await xian.send_tx(
                self.cfg.get('contract'),
                self.side.name,
                kwargs={
                    "buy_token": order.buy_token,
                    "sell_token": order.sell_token,
                    "amount": order.amount,
                    self.side.bound_name: bound,
                    "deadline": trade_deadline(),
                }
            )
            self.log.bind(**transaction_context(submission)).debug("Transaction response received")
        except Exception as e:
            await self._fail(message, e)
            return None

        if not submission_accepted(submission):
            await message.edit_text(f"{con.ERROR} {html.escape(str(submission.message))}")
            return False

        tx_hash = submission.tx_hash

        async def tx_result(success: bool, result: Any) -> None:
            if success:
                explorer_url = self.cfg_global.get('xian', 'explorer')
                link = f'<a href="{explorer_url}/tx/{tx_hash}">View Transaction</a>'
                await message.edit_text(
                    f"<code>{settled_summary(order, result)}</code>\n{link}",
                    disable_web_page_preview=True
                )
            else:
                await message.edit_text(f"{con.ERROR} {html.escape(str(result))}")

        success, _ = await self.confirm_tx(xian, submission, tx_result)
        return success
