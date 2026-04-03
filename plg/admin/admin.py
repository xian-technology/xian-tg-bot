from telegram import Update
from telegram.ext import CallbackContext, CommandHandler

import constants as con
from plugin import TGBFPlugin


class Admin(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(
            self.handle,
            self.admin_callback,
            block=False)
        )

    @TGBFPlugin.owner(hidden=True)
    @TGBFPlugin.private(hidden=True)
    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def admin_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        if len(context.args) < 2:
            await update.message.reply_text(await self.get_info())
            return

        sub_command = context.args[0].lower()
        plg_name = context.args[1].lower()

        if sub_command == 'disable':
            if plg_name in list(self.plugins.keys()):
                await self.tgb.disable_plugin(plg_name)
                await update.message.reply_text(f"{con.DONE} Plugin '{plg_name}' disabled")
            else:
                await update.message.reply_text(f"{con.WARNING} Plugin '{plg_name}' not available")

        elif sub_command == 'enable':
            worked, msg = await self.tgb.enable_plugin(plg_name)

            if worked:
                await update.message.reply_text(f"{con.DONE} Plugin '{plg_name}' enabled")
            else:
                await update.message.reply_text(f"{con.WARNING} Plugin '{plg_name}' not available")

        else:
            await update.message.reply_text(f'{con.WARNING} Unknown argument(s)')
