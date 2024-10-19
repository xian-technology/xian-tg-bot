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
        xian = await self.get_xian(wallet)

        message = await update.message.reply_text(f"{con.WAIT} Retrieving balances ...")

        balances = str()

        try:
            # Make sure 'currency' contract is in the list of tokens
            sql = await self.get_resource('insert_currency.sql', 'tokens')
            await self.exec_sql(
                sql,
                user_id, 'currency', 'XIAN', 4,
                plugin='tokens'
            )

            sql = await self.get_resource('select_tokens.sql', 'tokens')
            tokens = await self.exec_sql(sql, user_id, plugin='tokens')

            for token in tokens['data']:
                ticker = token[2]
                decimals = token[3]
                balance = xian.get_balance(token[1])

                token_balance = self.format_balance(
                    ticker, balance, decimals
                )

                if ticker == 'XIAN':
                    balances = f'<code>{token_balance}</code>\n' + balances
                else:
                    balances += f'<code>{token_balance}</code>\n'

        except Exception as e:
            await message.edit_text(f"{con.ERROR} {e}")
            msg = f"GET_BALANCE Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            return

        await message.edit_text(balances)

    # TODO: Remove decimals if not present etc
    def format_balance(self, ticker: str, balance: str | float, decimals: int):
        return f"{ticker}: {balance:.{decimals}f}".format(ticker=ticker, decimals=decimals, balance=balance)
