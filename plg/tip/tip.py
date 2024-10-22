import html

import constants as con

from plugin import TGBFPlugin
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler


class Tip(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.tip_callback, block=False))

    @TGBFPlugin.public()
    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def tip_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        if len(context.args) not in (1, 2):
            await update.message.reply_text(
                await self.get_info()
            )
            return

        reply = update.message.reply_to_message

        if not reply:
            msg = f"{con.ERROR} Tip a user by replying to his message"
            await update.message.reply_text(msg)
            return

        contract = None
        ticker = None
        amount = None

        to_user_id = reply.from_user.id
        from_user_id = update.effective_user.id

        message = await update.message.reply_text(f"{con.WAIT} Preparing ...")

        # Tipping XIAN
        if len(context.args) == 1:
            contract = 'currency'
            ticker = 'XIAN'
            amount = context.args[0]

        # Tipping token
        elif len(context.args) == 2:
            tokens = await self.get_plugin('tokens').get_tokens(from_user_id)

            # It is a contract
            if context.args[0].lower().startswith(('con_', 'currency')):
                for token in tokens:
                    if token[1] == context.args[0].lower():
                        contract = token[1]
                        ticker = token[2]
                        break

            # It is a ticker
            else:
                for token in tokens:
                    if token[2] == context.args[0].upper():
                        contract = token[1]
                        ticker = token[2]
                        break

            amount = context.args[1]

        if not contract:
            await message.edit_text(
                f'{con.ERROR} Unknown contract. Make sure you added this token to '
                f'your token list first with <code>/tokens add contract_name</code>'
            )
            return

        try:
            # Check if amount is valid
            amount = float(amount)

            if amount <= 0:
                raise ValueError('Amount can not be negative')
        except:
            msg = f"{con.ERROR} Amount not valid"
            await message.edit_text(msg)
            return

        if amount.is_integer():
            amount = int(amount)

        from_wallet = await self.get_wallet(from_user_id)
        xian = await self.get_xian(from_wallet)

        # Get address to which we want to tip
        to_address = (await self.get_wallet(to_user_id)).public_key

        await message.edit_text(f"{con.WAIT} Sending...")

        try:
            # Send token
            send = xian.send(amount, to_address, token=contract)
            self.log.debug(f'Tip TX: {send}')
        except Exception as e:
            msg = f"TIP Error: {e}"
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

                to_user = reply.from_user.first_name

                if update.effective_user.username:
                    from_user = f"@{update.effective_user.username}"
                else:
                    from_user = update.effective_user.first_name

                await message.edit_text(
                    f"{con.MONEY} {html.escape(to_user)} received <code>{amount}</code> {ticker}\n{link}",
                    disable_web_page_preview=True
                )

                try:
                    # Notify user about tip
                    await context.bot.send_message(
                        to_user_id,
                        f"You received <code>{amount}</code> {ticker} from {from_user}\n{link}",
                        disable_web_page_preview=True
                    )
                    self.log.info(f"User ID {to_user_id} notified about tip of {amount} {ticker}")
                except Exception as ex:
                    self.log.warning(f"User ID {to_user_id} could not be notified about tip: {ex} - {update}")

        if not send['success']:
            await message.edit_text(f"{con.STOP} {send['message']}")
        else:
            await self.plugins['event'].track_tx(tx_hash, tx_result)
