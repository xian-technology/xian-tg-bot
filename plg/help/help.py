from collections import OrderedDict

from telegram import Update
from telegram.ext import CallbackContext, CommandHandler

from plugin import TGBFPlugin


class Help(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.init_callback, block=False))

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def init_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        categories = OrderedDict()

        for p in self.plugins.values():
            if p.category and p.description:
                des = f"/{p.handle} - {p.description}"

                if p.category not in categories:
                    categories[p.category] = [des]
                else:
                    categories[p.category].append(des)

        msg = "<b>Available Commands</b>\n\n"

        for category in sorted(categories):
            msg += f"◾️ {category}\n"

            for cmd in sorted(categories[category]):
                msg += f"{cmd}\n"

            msg += "\n"

        msg = await update.message.reply_text(msg, disable_web_page_preview=True)

        if not self.is_private(update.message):
            await self.remove_msg_after(update.message, msg, after_secs=20)
