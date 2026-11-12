import html
from typing import Any

from telegram import Update
from telegram.ext import CallbackContext, CommandHandler

import constants as con
from logging_setup import error_context
from plugin import TGBFPlugin
from transactions import trade_amount


class Balance(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.balance_callback, block=False))

    @TGBFPlugin.private()
    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def balance_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        user_id = update.message.from_user.id
        wallet = await self.get_wallet(user_id)
        xian = await self.get_xian(wallet=wallet)

        message = await update.message.reply_text(f"{con.WAIT} Retrieving balances ...")

        balances = ""

        try:
            tokens = await self.get_plugin('tokens').get_tokens(user_id)

            for token in tokens:
                ticker = token[2]
                decimals = token[3]
                balance = await xian.get_balance(contract=token[1])

                balance_str = self.format_balance(ticker, balance, decimals)

                if ticker == 'XIAN':
                    balances = f'<code>{balance_str}</code>\n' + balances
                else:
                    balances += f'<code>{balance_str}</code>\n'

        except Exception as e:
            await message.edit_text(f"{con.ERROR} {html.escape(str(e))}")
            self.log.bind(**error_context(e), handler='balance_callback').error('Balance callback failed')
            await self.notify(e)
            return

        await message.edit_text(balances)

    # TODO: Remove decimals if not present etc
    def format_balance(self, ticker: str, balance: Any, decimals: int):
        # Token symbols come from on-chain metadata and are rendered as HTML text.
        # SDK balances are ContractingDecimal, which has no fixed-point format.
        return f"{html.escape(str(ticker))}: {trade_amount(balance):.{decimals}f}"
