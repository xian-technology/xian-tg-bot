import html
import io
from pathlib import Path

import telegram
from telegram.ext import CallbackContext, MessageHandler, filters

import constants as con
from logging_setup import error_context, transaction_context
from plugin import TGBFPlugin
from transactions import submission_accepted


def parse_caption(caption: str | None) -> dict[str, str]:
    """Parse ``key=value`` pairs separated by spaces or commas."""
    params = {}
    for param in (caption or "").replace(',', ' ').split():
        key, separator, value = param.partition('=')
        if not separator or not key or not value or '=' in value:
            raise ValueError(f"Invalid caption parameter: {param!r}")
        params[key] = value
    return params


class Submit(TGBFPlugin):

    async def init(self):
        await self.add_handler(
            MessageHandler(
                filters.Document.PY,
                self.submit_callback,
                block=False
            )
        )

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def submit_callback(self, update: telegram.Update, context: CallbackContext):
        if not isinstance(update, telegram.Update):
            return
        if not update.message:
            return

        name = Path(update.message.document.file_name.lower()).stem

        try:
            params = parse_caption(update.message.caption)
        except ValueError:
            await update.message.reply_text(
                f"{con.ERROR} Caption must be <code>key=value</code> pairs, e.g. <code>name=con_token</code>"
            )
            return

        if 'name' in params:
            name = params['name']

        # Validate name
        if not name.startswith('con_'):
            msg = f"{con.ERROR} Contract name needs to start with 'con_'"
            await update.message.reply_text(msg)
            return

        message = await update.message.reply_text(f"{con.WAIT} Submitting contract ...")

        contract_bytes = io.BytesIO()
        file = await update.message.effective_attachment.get_file()
        await file.download_to_memory(out=contract_bytes)
        code = contract_bytes.getvalue().decode('utf-8')

        from_wallet = await self.get_wallet(update.effective_user.id)
        xian = await self.get_xian(wallet=from_wallet)

        try:
            deploy = await xian.submit_contract(name, code)
            self.log.bind(**transaction_context(deploy)).debug("Transaction response received")
        except Exception as e:
            self.log.bind(**error_context(e), handler='submit_callback').error('Submit callback failed')
            await self.notify(e)
            await message.edit_text(f"{con.ERROR} {html.escape(str(e))}")
            return

        tx_hash = deploy.tx_hash

        if submission_accepted(deploy):
            async def tx_result(success: str, result: str):
                if success:
                    explorer_url = self.cfg_global.get('xian', 'explorer')
                    link = f'<a href="{explorer_url}/tx/{tx_hash}">View Transaction</a>'

                    await message.edit_text(
                        f"{con.DONE} Contract <code>{name}</code> deployed\n{link}",
                        disable_web_page_preview=True
                    )
                else:
                    await message.edit_text(f"{con.STOP} {html.escape(str(result))}")

            await self.confirm_tx(xian, deploy, tx_result)
        else:
            await message.edit_text(f"{con.STOP} {html.escape(str(deploy.message))}")
