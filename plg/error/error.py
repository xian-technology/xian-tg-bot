import html

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import constants as con
from logging_setup import error_context
from plugin import TGBFPlugin


class Error(TGBFPlugin):

    async def init(self):
        self.tgb.bot.add_error_handler(self.init_callback)

    async def init_callback(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Correlate failures without forwarding wallet data, updates, or exception payloads."""
        incident = error_context(context.error, update)
        self.log.bind(**incident).error("Update handler failed")
        details = "\n".join(f"{key}={value}" for key, value in incident.items())
        message = f"{con.ALERT} Update handler failed\n<pre>{html.escape(details)}</pre>"
        await context.bot.send_message(
            chat_id=self.cfg_global.get('admin_tg_id'), text=message, parse_mode=ParseMode.HTML
        )
