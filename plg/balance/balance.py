import constants as con

from plugin import TGBFPlugin
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler


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

        balances = str()

        try:
            tokens_plg = self.get_plugin('tokens')
            await tokens_plg.check_and_insert_currency(user_id)
            tokens = await tokens_plg.get_tokens(user_id)

            for token in tokens:
                ticker = token[2]
                decimals = token[3]#
                balance = xian.get_balance(contract=token[1])

                balance_str = self.format_balance(ticker, balance, decimals)

                if ticker == 'XIAN':
                    balances = f'<code>{balance_str}</code>\n' + balances
                else:
                    balances += f'<code>{balance_str}</code>\n'

        except Exception as e:
            await message.edit_text(f"{con.ERROR} {e}")
            msg = f"BALANCE Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            return

        await message.edit_text(balances)

    # TODO: Remove decimals if not present etc
    def format_balance(self, ticker: str, balance: str | float, decimals: int):
        return f"{ticker}: {balance:.{decimals}f}"
