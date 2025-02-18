import utils as utl
import asyncio

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
            menu = utl.build_menu([InlineKeyboardButton("Send to all users", callback_data=self.name)])
            return InlineKeyboardMarkup(menu)

        await update.message.reply_text(
            update.message.text_html[len(self.name) + 2:],
            reply_markup=confirm_button()
        )

    async def send_callback(self, update: Update, context: CallbackContext):
        if not update.callback_query.data.startswith(self.name):
            return

        sleep_time = self.cfg.get("sleep")
        msg_text = update.callback_query.message.text_html

        sql = await self.get_resource('select_users.sql')
        users = await self.exec_sql_global(sql, update.effective_user.id)

        for user_id in users['data']:
            await context.bot.send_message(user_id[0], msg_text)
            self.log.debug(f"Sent message to user {user_id[0]}")
            await asyncio.sleep(sleep_time)
