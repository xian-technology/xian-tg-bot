import constants as con

from plugin import TGBFPlugin
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler


class Approve(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.appove_callback, block=False))

    @TGBFPlugin.send_typing
    async def appove_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        if not context.args or len(context.args) != 3:
            await update.message.reply_text(
                await self.get_info()
            )
            return

        contract = context.args[0]
        token = context.args[1]
        amount = context.args[2]

        try:
            # Validate amount
            amount = float(amount)
        except:
            msg = f"{con.ERROR} Amount is not valid"
            await update.message.reply_text(msg)
            return

        message = await update.message.reply_text(f"{con.WAIT} Approving contract...")

        wallet = await self.get_wallet(update.effective_user.id)
        xian = await self.get_xian(wallet)

        try:
            # Approve contract
            success, tx_hash = xian.approve(contract, token=token, amount=amount)
        except Exception as e:
            msg = f"APPROVE Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        link = f'<a href="{xian.node_url}/tx?hash=0x{tx_hash}">View Transaction</a>'

        if success:
            await message.edit_text(
                f"{con.DONE} Contract approved\n{link}",
                disable_web_page_preview=True
            )
        else:
            await message.edit_text(
                f"{con.STOP} Transaction failed\n{link}",
                disable_web_page_preview=True
            )
