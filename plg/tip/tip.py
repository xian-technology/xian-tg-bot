import html

import constants as con

from plugin import TGBFPlugin
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler


class Tip(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.tip_callback, block=False))

    @TGBFPlugin.public
    @TGBFPlugin.send_typing
    async def tip_callback(self, update: Update, context: CallbackContext):
        if len(context.args) < 1:
            await update.message.reply_text(await self.get_info())
            return

        reply = update.message.reply_to_message

        if not reply:
            msg = f"{con.ERROR} Tip a user by replying to his message"
            await update.message.reply_text(msg)
            return

        amount = context.args[0]

        try:
            # Check if amount is valid
            amount = float(amount)
        except:
            msg = f"{con.ERROR} Amount not valid"
            await update.message.reply_text(msg)
            return

        if amount.is_integer():
            amount = int(amount)

        to_user_id = reply.from_user.id
        from_user_id = update.effective_user.id

        from_wallet = await self.get_wallet(from_user_id)
        xian = await self.get_xian(from_wallet)

        usr_msg = str()
        if len(context.args) > 1:
            usr_msg = f"Message: {' '.join(context.args[1:])}"

        # Get address to which we want to tip
        to_address = (await self.get_wallet(to_user_id)).public_key

        message = await update.message.reply_text(f"{con.WAIT} Sending...")

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

        to_user = reply.from_user.first_name

        if update.effective_user.username:
            from_user = f"@{update.effective_user.username}"
        else:
            from_user = update.effective_user.first_name

        await message.edit_text(
            f"{con.MONEY} {html.escape(to_user)} received <code>{amount}</code> XIAN\n{link}",
            disable_web_page_preview=True)

        try:
            # Notify user about tip
            await context.bot.send_message(
                to_user_id,
                f"You received <code>{amount}</code> XIAN from {from_user}\n{link}\n\n{usr_msg}",
                disable_web_page_preview=True)
            self.log.info(f"User ID {to_user_id} notified about tip of {amount} XIAN")
        except Exception as e:
            self.log.info(f"User ID {to_user_id} could not be notified about tip: {e} - {update}")
