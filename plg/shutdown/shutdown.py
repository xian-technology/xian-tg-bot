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
        if not update.message:
            return

        msg = f"{con.BYE} Shutting down..."
        await update.message.reply_text(msg)
        self.log.info(msg)

        # Schedule shutdown with a small delay to ensure message is sent
        asyncio.create_task(self.delayed_shutdown(0.5))

    async def delayed_shutdown(self, delay: float):
        """Execute shutdown after a short delay"""
        await asyncio.sleep(delay)
        await self.tgb.shutdown()
