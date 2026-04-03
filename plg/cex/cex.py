from telegram import Update
from telegram.ext import CallbackContext, CommandHandler

from plugin import TGBFPlugin


class Cex(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.cex_callback, block=False))

    @TGBFPlugin.send_typing()
    async def cex_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        await update.message.reply_text(
            await self.get_info()
        )
