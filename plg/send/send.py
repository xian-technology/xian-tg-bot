import constants as con

from plugin import TGBFPlugin
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler
from xian_py.wallet import key_is_valid


class Send(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.send_callback, block=False))
        await self.add_handler(CommandHandler('withdraw', self.send_callback, block=False))

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def send_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        if len(context.args) != 2:
            await update.message.reply_text(
                await self.get_info()
            )
            return

        amount = context.args[0]

        try:
            # Check if amount is valid
            amount = float(amount)

            if amount <= 0:
                raise ValueError('Amount can not be negative')
        except:
            msg = f"{con.ERROR} Amount not valid"
            await update.message.reply_text(msg)
            return

        if amount.is_integer():
            amount = int(amount)

        to_address = context.args[1]

        # Check if address is valid
        if not key_is_valid(to_address):
            msg = f"{con.ERROR} Not a valid address"
            await update.message.reply_text(msg)
            return

        message = await update.message.reply_text(f"{con.WAIT} Sending ...")

        from_wallet = await self.get_wallet(update.effective_user.id)
        xian = await self.get_xian(from_wallet)

        try:
            # Send token
            send = xian.send(amount, to_address)
        except Exception as e:
            msg = f"SEND Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        tx_hash = send['tx_hash']

        async def tx_result(success: str, result: str):
            if not success:
                await message.edit_text(f"{con.STOP} {result}")
            else:
                explorer_url = self.cfg_global.get('xian', 'explorer')
                link = f'<a href="{explorer_url}/tx/{tx_hash}">View Transaction</a>'

                await message.edit_text(
                    f"{con.MONEY} Sent <code>{amount}</code> XIAN\n{link}",
                    disable_web_page_preview=True
                )

        if not send['success']:
            await message.edit_text(f"{con.STOP} {send['message']}")
        else:
            await self.plugins['event'].track_tx(tx_hash, tx_result)
