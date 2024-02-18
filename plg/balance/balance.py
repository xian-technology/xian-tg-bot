import constants as con

from plugin import TGBFPlugin
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler


class Balance(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.balance_callback, block=False))

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def balance_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        wallet = await self.get_wallet(update.effective_user.id)
        xian = await self.get_xian(wallet)

        message = await update.message.reply_text(f"{con.WAIT} Retrieving balance ...")

        try:
            # Get balance
            balance = xian.get_balance()
        except Exception as e:
            await message.edit_text(f"{con.ERROR} {e}")
            msg = f"GET_BALANCE Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            return

        await message.edit_text(f"XIAN: <code>{balance}</code>")
