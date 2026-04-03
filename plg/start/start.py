from telegram import Update
from telegram.ext import CallbackContext, CommandHandler

from plugin import TGBFPlugin


class Start(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.start_callback, block=False))

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def start_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        await update.message.reply_text(
            await self.get_info(),
            disable_web_page_preview=True
        )
