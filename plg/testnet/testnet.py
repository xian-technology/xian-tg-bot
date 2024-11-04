import constants as con

from plugin import TGBFPlugin
from telegram import Update
from xian_py.wallet import Wallet
from telegram.ext import CallbackContext, CommandHandler
from datetime import datetime, timezone


class Testnet(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.testnet_callback, block=False))

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def testnet_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        user_id = update.message.from_user.id
        to_wallet = await self.get_wallet(user_id)
        to_address = to_wallet.public_key

        testnet_node = self.cfg.get('testnet_node')
        chain_id = self.cfg.get('chain_id')

        from_privkey = self.cfg.get('privkey')
        from_wallet = Wallet(from_privkey)

        testnet = await self.get_xian(testnet_node, chain_id, from_wallet)

        balance = testnet.get_balance(to_address)
        threshold = self.cfg.get('threshold')

        # Check if address has more than threshold balance
        if balance > threshold:
            msg = f'{con.ERROR} You have more than {threshold} tXIAN'
            await update.message.reply_text(msg)
            return

        # Amount to send
        amount = self.cfg.get('amount')

        message = await update.message.reply_text(f"{con.WAIT} Sending {amount} tXIAN...")

        # Current datetime
        tz = timezone.utc
        ft = "%Y-%m-%dT%H:%M:%S"
        current_dt_str = datetime.now(tz=tz).strftime(ft)

        # Last sent datetime
        past_dt_str = self.kv_get(to_address)

        if past_dt_str:
            current_dt = datetime.strptime(current_dt_str, ft).replace(tzinfo=tz)
            past_dt = datetime.strptime(past_dt_str, ft).replace(tzinfo=tz)

            delta = current_dt - past_dt

            if delta.days < self.cfg.get('days_waiting'):
                msg = f"{con.ERROR} You need to wait at least one day"
                await message.edit_text(msg)
                return

        try:
            # Send testnet XIAN
            send = testnet.send(amount, to_address)
            self.log.debug(f'Send TX: {send}')
        except Exception as e:
            msg = f"SEND Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        # Save address with current datetime
        self.kv_set(to_address, current_dt_str)

        await message.edit_text(f"{con.INFO} Sent {amount} tXIAN")
