"""/rain and /bless: split an amount over recently active chat members.

Both commands parse the same arguments, read the same active users and pay
them through one con_multisend call; they differ only in which active users
receive a share (see :meth:`MultisendPlugin.distribution`).
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from telegram import Update
from telegram.ext import CallbackContext, CommandHandler

import constants as con
from logging_setup import error_context, transaction_context
from plugin import TGBFPlugin
from transactions import parse_amount, split_amount, submission_accepted
from utils import sqlite_utc

UNKNOWN_CONTRACT = (
    f'{con.ERROR} Unknown contract. Make sure you added this token to '
    f'your token list first with <code>/tokens add contract_name</code>'
)
TIME_UNITS = {"m": "minutes", "h": "hours"}


@dataclass(frozen=True)
class Request:
    contract: str
    ticker: str
    amount_total: Decimal
    since: datetime


@dataclass(frozen=True)
class Distribution:
    message: Any  # the chat message that reports progress
    addresses: list[str]
    amount_single: Decimal
    text: str


def find_token(tokens: Sequence[Sequence[Any]], name: str) -> tuple[str, str] | None:
    """(contract, ticker) by contract name (con_*/currency) or by ticker."""
    if name.lower().startswith(('con_', 'currency')):
        column, wanted = 1, name.lower()
    else:
        column, wanted = 2, name.upper()
    for token in tokens:
        if token[column] == wanted:
            return token[1], token[2]
    return None


def parse_since(text: str, now: datetime) -> datetime | str:
    """Start of the ``<number>m`` / ``<number>h`` look-back window, or the refusal text."""
    unit = TIME_UNITS.get(text[-1:].lower())
    if unit is None:
        return f"{con.ERROR} Allowed time units are <code>m</code> (minute) and <code>h</code> (hour)"
    try:
        frame = float(text[:-1])
        if not frame > 0:
            raise ValueError('Only positive time frames are allowed')
        # NaN, infinity and windows reaching before year 1 are not representable
        return now - timedelta(**{unit: frame})
    except (ValueError, OverflowError):
        return f"{con.ERROR} Time frame not valid!"


def parse_amount_total(text: str) -> Decimal | None:
    try:
        amount = parse_amount(text)
    except Exception:
        return None
    return amount if amount > 0 else None


def share(amount_total: Decimal, recipients: int) -> Decimal | str:
    """Each recipient's share at four places, or the refusal text."""
    amount_single = split_amount(amount_total, recipients)
    if amount_single <= 0:
        return f"{con.ERROR} Amount is too small to split at four decimal places"
    return amount_single


class MultisendPlugin(TGBFPlugin):
    """Base of the Rain and Bless plugins."""

    async def init(self) -> None:
        await self.add_handler(CommandHandler(self.handle, self.rain_callback, block=False))

    async def distribution(self, context: CallbackContext, user_data: list[Any],
                           request: Request, message: Any) -> Distribution | None:
        """Choose the paid recipients; None after reporting why nobody is paid."""
        raise NotImplementedError

    @TGBFPlugin.public()
    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def rain_callback(self, update: Update, context: CallbackContext) -> None:
        # Don't deal with edited messages
        if not update.message:
            return
        if len(context.args) not in (2, 3):
            await update.message.reply_text(await self.get_info())
            return

        user_id = update.message.from_user.id
        xian = await self.get_xian(wallet=await self.get_wallet(user_id))
        message = await update.message.reply_text(f"{con.WAIT} Preparing ...")

        request = await self._request(user_id, context.args, message)
        if request is None:
            return
        user_data = await self._active_users(update.effective_chat.id, request.since, user_id, message)
        if user_data is None:
            return
        distribution = await self.distribution(context, user_data, request, message)
        if distribution is None:
            return
        if await self._ensure_allowance(xian, request, distribution):
            await self._send(xian, request, distribution)

    async def _request(self, user_id: int, args: list[str], message: Any) -> Request | None:
        """Parse ``[token] <amount> <time frame>``; None after reporting the problem."""
        token: tuple[str, str] | None = ('currency', 'XIAN')
        if len(args) == 3:
            token = find_token(await self.get_plugin('tokens').get_tokens(user_id), args[0])
        if token is None:
            await message.edit_text(UNKNOWN_CONTRACT)
            return None

        amount_total = parse_amount_total(args[-2])
        if amount_total is None:
            await message.edit_text(f"{con.ERROR} Amount not valid!")
            return None

        since = parse_since(args[-1], datetime.now(UTC))
        if isinstance(since, str):
            await message.edit_text(since)
            return None
        return Request(token[0], token[1], amount_total, since)

    async def _active_users(self, chat_id: int, since: datetime, user_id: int, message: Any) -> list[Any] | None:
        """Users who wrote in this chat since ``since``, without the sender."""
        sql = await self.get_resource("select_active.sql", plugin="active")
        active = await self.exec_sql(sql, chat_id, sqlite_utc(since), plugin="active")

        if not active["success"] or not active["data"]:
            msg = f"{con.ERROR} Could not determine last active users"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(msg)
            return None

        user_data = [u for u in active["data"] if u[0] != user_id]
        if not user_data:
            await message.edit_text(f"{con.ERROR} No users found for given time frame")
            return None
        return user_data

    async def _fail(self, message: Any, error: Exception) -> None:
        self.log.bind(**error_context(error), handler='rain_callback').error('Rain callback failed')
        await self.notify(error)
        await message.edit_text(f"{con.ERROR} {html.escape(str(error))}")

    async def _ensure_allowance(self, xian: Any, request: Request, distribution: Distribution) -> bool:
        """Approve exactly what this distribution transfers when the allowance is short."""
        message = distribution.message
        multisend_contract = self.cfg.get("contract")
        try:
            approved_amount = await xian.get_approved_amount(multisend_contract, token=request.contract)
            self.log.debug("Token allowance checked")
        except Exception as e:
            await self._fail(message, e)
            return False

        amount_needed = distribution.amount_single * len(distribution.addresses)
        if approved_amount >= amount_needed:
            return True

        try:
            approve = await xian.approve(multisend_contract, token=request.contract, amount=amount_needed)
            self.log.bind(**transaction_context(approve)).debug("Transaction response received")
        except Exception as e:
            await self._fail(message, e)
            return False

        if not submission_accepted(approve):
            await message.edit_text(f"{con.ERROR} Can not approve contract!")
            return False
        success, result = await self.confirm_tx(xian, approve)
        if not success:
            await message.edit_text(f"{con.STOP} Approval failed: {html.escape(str(result))}")
            return False
        return True

    async def _send(self, xian: Any, request: Request, distribution: Distribution) -> None:
        message = distribution.message
        kwargs = {
            "addresses": distribution.addresses,
            "amount": distribution.amount_single,
            "contract": request.contract,
        }
        try:
            # Execute contract to send tokens
            send = await xian.send_tx(self.cfg.get("contract"), self.cfg.get("function"), kwargs)
            self.log.bind(**transaction_context(send)).debug("Transaction response received")
        except Exception as e:
            await self._fail(message, e)
            return

        if not submission_accepted(send):
            await message.edit_text(f"{con.STOP} {html.escape(str(send.message))}")
            return

        success, result = await self.confirm_tx(xian, send)
        if success:
            explorer_url = self.cfg_global.get('xian', 'explorer')
            link = f'<a href="{explorer_url}/tx/{send.tx_hash}">View Transaction</a>'
            await message.edit_text(f"{distribution.text}\n\n{link}", disable_web_page_preview=True)
        else:
            await message.edit_text(f"{con.STOP} {html.escape(str(result))}")
