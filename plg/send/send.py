import constants as con

from plugin import TGBFPlugin
from xian_py.transaction import simulate_tx
from telegram.ext import CallbackContext, CommandHandler
from telegram import Update


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

        if len(context.args) not in (2, 3):
            await update.message.reply_text(
                await self.get_info()
            )
            return

        user_id = update.message.from_user.id
        from_wallet = await self.get_wallet(user_id)
        xian = await self.get_xian(wallet=from_wallet)

        contract = None
        ticker = None
        amount = None
        to = None

        message = await update.message.reply_text(f"{con.WAIT} Preparing ...")

        # Sending XIAN
        if len(context.args) == 2:
            contract = 'currency'
            ticker = 'XIAN'
            amount = context.args[0]
            to = context.args[1]

        # Sending token
        elif len(context.args) == 3:
            tokens = await self.get_plugin('tokens').get_tokens(user_id)

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
            to = context.args[2]

        if not contract:
            await message.edit_text(
                f'{con.ERROR} Unknown contract. Make sure you added this token to '
                f'your token list first with <code>/token add contract_name</code>'
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

        # Check if recipient is a contract
        if to.startswith('con_'):
            contract_data = xian.get_contract(to)

            if not contract_data:
                msg = f"{con.ERROR} Contract doesn't exist!"
                await message.edit_text(msg)
                return

        # Check if recipient is an address
        elif not from_wallet.is_valid_key(to):
            payload = {
                "contract": self.cfg.get('xns_contract'),
                "function": self.cfg.get('xns_function'),
                "kwargs": {"name": to},
                "sender": from_wallet.public_key
            }

            # Check if recipient an XNS name
            sim = simulate_tx(self.cfg_global.get('xian', 'node'), payload)

            if sim['result'] == 'None':
                msg = f"{con.ERROR} Not a valid address, contract or XNS name!"
                await message.edit_text(msg)
                return
            else:
                to = sim['result'].replace("'", '')

        await message.edit_text(f"{con.WAIT} Sending {ticker} ...")

        try:
            # Send token
            send = xian.send(amount, to, token=contract)
            self.log.debug(f'Send TX: {send}')
        except Exception as e:
            msg = f"SEND Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        tx_hash = send['tx_hash']

        if send['success']:
            async def tx_result(success: str, result: str):
                if success:
                    explorer_url = self.cfg_global.get('xian', 'explorer')
                    link = f'<a href="{explorer_url}/tx/{tx_hash}">View Transaction</a>'

                    await message.edit_text(
                        f"{con.MONEY} Sent <code>{amount}</code> {ticker}\n{link}",
                        disable_web_page_preview=True
                    )
                else:
                    await message.edit_text(f"{con.STOP} {result}")

            await self.plugins['event'].track_tx(tx_hash, tx_result)
        else:
            await message.edit_text(f"{con.STOP} {send['message']}")
