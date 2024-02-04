import constants as con

from plugin import TGBFPlugin
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler
from xian_tools.wallet import key_is_valid


class Send(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.send_callback, block=False))

    @TGBFPlugin.send_typing
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

            if amount < 0:
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
            await message.edit_text(f"{con.ERROR} {e}")
            msg = f"SEND Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            return

        # Get transaction hash
        tx_hash = send["result"]["hash"]

        try:
            # Get transaction details
            tx = xian.get_tx(tx_hash)
        except Exception as e:
            msg = f"GET_TX Error: {e}"
            await message.edit_text(f"{con.ERROR} {e}")
            self.log.error(msg)
            await self.notify(msg)
            return

        if 'error' in tx:
            e = tx['error']
            msg = f"TX Error: {e}"
            await message.edit_text(f"{con.ERROR} {e}")
            self.log.error(msg)
            await self.notify(msg)
            return

        link = f'<a href="{xian.node_url}/tx?hash=0x{tx_hash}">View Transaction</a>'

        await message.edit_text(
            f"{con.MONEY} Sent <code>{amount}</code> XIAN\n{link}",
            disable_web_page_preview=True
        )
