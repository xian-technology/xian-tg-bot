from plugin import TGBFPlugin
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler


class About(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.about_callback, block=False))

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def about_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        msg = await update.message.reply_text(
            await self.get_info(),
            disable_web_page_preview=True
        )

        if not self.is_private(update.message):
            await self.remove_msg_after(update.message, msg, after_secs=20)
