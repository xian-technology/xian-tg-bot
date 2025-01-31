import sys
import asyncio
import constants as con

from telegram import Update
from telegram.ext import CallbackContext, CommandHandler
from plugin import TGBFPlugin


class Shutdown(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.shutdown_callback, block=False))

    @TGBFPlugin.owner(hidden=True)
    @TGBFPlugin.private(hidden=True)
    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def shutdown_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        msg = f"{con.BYE} Shutting down..."
        await update.message.reply_text(msg)
        self.log.info(msg)

        # First stop the bot and webserver gracefully
        if self.tgb.web:
            await self.tgb.web.stop()
        if self.tgb.bot:
            await self.tgb.bot.stop()
            await self.tgb.bot.shutdown()

        # Schedule actual shutdown after a small delay to allow message to be sent
        await asyncio.sleep(1)
        sys.exit(0)
