import utils as utl

from plugin import TGBFPlugin
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext, CommandHandler, CallbackQueryHandler


# TODO: How to best allow multiple users to do it?
class All(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.all_callback, block=False))
        await self.add_handler(CallbackQueryHandler(self.send_callback, block=False))

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def all_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        if len(context.args) == 0:
            await update.message.reply_text(
                await self.get_info(),
                disable_web_page_preview=True
            )
            return

        def confirm_button():
            menu = utl.build_menu([InlineKeyboardButton(f"Send to all users")])
            return InlineKeyboardMarkup(menu)

        await update.message.reply_text(
            update.message.text_html,
            reply_markup=confirm_button()
        )

    async def send_callback(self, update: Update, context: CallbackContext):
        if not update.callback_query.data.startswith(self.name):
            return

        sleep_time = self.cfg.get("sleep")
        msg_text = update.message.text_html

        # TODO: Iterate over all users and send message
