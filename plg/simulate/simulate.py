import html
import json
from decimal import Decimal

from telegram import Update
from telegram.ext import CallbackContext, CommandHandler
from xian_py.transaction import simulate_tx_async

import constants as con
from logging_setup import error_context, transaction_context
from plugin import TGBFPlugin


class Simulate(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.simulate_callback, block=False))

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def simulate_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        if len(context.args) == 0:
            await update.message.reply_text(
                await self.get_info()
            )
            return

        message = await update.message.reply_text(f"{con.WAIT} Preparing ...")

        node_url = self.cfg_global.get('xian', 'node')

        # The payload is everything after the command; context.args is
        # non-empty, so the text has that second part
        payload_str = update.message.text.split(maxsplit=1)[1]

        try:
            payload = json.loads(payload_str, parse_float=Decimal)

            # Check if the result is a dictionary
            if not isinstance(payload, dict):
                raise ValueError
        except:
            error = f'{con.ERROR} Provided payload is not valid JSON'
            await update.message.reply_text(error)
            return

        try:
            # Simulate transaction
            simulate = await simulate_tx_async(node_url, payload, vm_profile="xian_vm_v2")
            self.log.bind(**transaction_context(simulate)).debug("Transaction response received")
        except Exception as e:
            self.log.bind(**error_context(e), handler='simulate_callback').error('Simulate callback failed')
            await self.notify(e)
            await message.edit_text(f"{con.ERROR} {html.escape(str(e))}")
            return

        status = True if simulate['status'] == 0 else False
        result = simulate['result']
        chi = simulate['chi_used']
        state = simulate['state']

        msg = (f'<b>Result of tx simulation</b>\n\n'
               f'Success:\n<pre>{status}</pre>\n\n'
               f'Chi used:\n<pre>{chi}</pre>\n\n'
               f'Returned result:\n<pre>{html.escape(str(result))}</pre>\n\n'
               f'State changes:\n<pre>{html.escape(str(state))}</pre>\n\n')

        await message.edit_text(msg)
