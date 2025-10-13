import ast

import constants as con

from telegram.ext import CallbackContext, CommandHandler
from xian_py.transaction import simulate_tx, simulate_tx_async
from plugin import TGBFPlugin
from telegram import Update


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

        # Retrieve payload string
        content = update.message.text.split(maxsplit=1)
        if len(content) > 1:
            payload_str = content[1]
        else:
            await update.message.reply_text(
                await self.get_info()
            )
            return

        try:
            # Attempt to evaluate the string as a Python literal
            payload = ast.literal_eval(payload_str)

            # Check if the result is a dictionary
            if not isinstance(payload, dict):
                raise ValueError
        except:
            error = f'{con.ERROR} Provided payload is not valid JSON'
            await update.message.reply_text(error)
            return

        try:
            # Simulate transaction
            simulate = await simulate_tx_async(node_url, payload)
            self.log.debug(f'Simulate TX: {simulate}')
        except Exception as e:
            msg = f"SIMULATE Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        status = True if simulate['status'] == 0 else False
        result = simulate['result']
        stamps = simulate['stamps_used']
        state = simulate['state']

        msg = (f'<b>Result of tx simulation</b>\n\n'
               f'Success:\n<pre>{status}</pre>\n\n'
               f'Stamps used:\n<pre>{stamps}</pre>\n\n'
               f'Returned result:\n<pre>{result}</pre>\n\n'
               f'State changes:\n<pre>{state}</pre>\n\n')

        await message.edit_text(msg)
