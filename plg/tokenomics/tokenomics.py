from plugin import TGBFPlugin
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler


class Tokenomics(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.tokenomics_callback, block=False))

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def tokenomics_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        msg = await update.message.reply_photo(
                photo=await self.get_img('tokenomics.jpg'),
                caption=f"<code>Total supply of XIAN: 111.111.111</code>"
            )

        if not self.is_private(update.message):
            await self.remove_msg_after(update.message, msg, after_secs=20)
