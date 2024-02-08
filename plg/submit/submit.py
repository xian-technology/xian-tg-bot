import io
import telegram
import constants as con

from pathlib import Path
from plugin import TGBFPlugin
from telegram.ext import CallbackContext, MessageHandler, filters


class Submit(TGBFPlugin):

    async def init(self):
        await self.add_handler(
            MessageHandler(
                filters.Document.PY,
                self.submit_callback,
                block=False
            )
        )

    @TGBFPlugin.send_typing()
    async def submit_callback(self, update: telegram.Update, context: CallbackContext):
        if not isinstance(update, telegram.Update):
            return
        if not update.message:
            return

        contract_name = Path(update.message.document.file_name.lower()).stem

        # Validate name
        if not contract_name.startswith('con_'):
            msg = f"{con.ERROR} Contract name needs to start with 'con_'"
            await update.message.reply_text(msg)
            return

        message = await update.message.reply_text(f"{con.WAIT} Submitting contract ...")

        contract_bytes = io.BytesIO()
        file = await update.message.effective_attachment.get_file()
        await file.download_to_memory(out=contract_bytes)
        code = contract_bytes.getvalue().decode('utf-8')

        from_wallet = await self.get_wallet(update.effective_user.id)
        xian = await self.get_xian(from_wallet)

        try:
            success, tx_hash = xian.deploy_contract(contract_name, code)
        except Exception as e:
            msg = f"DEPLOY_CONTRACT Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        link = f'<a href="{xian.node_url}/tx?hash=0x{tx_hash}">View Transaction</a>'

        if success:
            await message.edit_text(
                f"{con.DONE} Contract <code>{contract_name}</code> deployed\n{link}",
                disable_web_page_preview=True
            )
        else:
            await message.edit_text(
                f"{con.STOP} Transaction failed\n{link}",
                disable_web_page_preview=True
            )
