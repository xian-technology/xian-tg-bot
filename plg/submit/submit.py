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

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def submit_callback(self, update: telegram.Update, context: CallbackContext):
        if not isinstance(update, telegram.Update):
            return
        if not update.message:
            return

        stamps = 1000
        name = Path(update.message.document.file_name.lower()).stem

        params = dict()
        caption = update.message.caption

        if caption:
            caption = caption.replace(',', ' ')

            for param in caption.split(' '):
                p_lst = param.split('=')
                key = p_lst[0].strip()
                value = p_lst[1].strip()
                params[key] = value

            if 'stamps' in params:
                stamps = params['stamps']

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
        xian = await self.get_xian(from_wallet)

        try:
            deploy = xian.submit_contract(name, code, stamps)
        except Exception as e:
            msg = f"DEPLOY_CONTRACT Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        tx_hash = deploy['tx_hash']
        explorer_url = self.cfg_global.get('xian', 'explorer')
        link = f'<a href="{explorer_url}/tx/{tx_hash}">View Transaction</a>'

        if deploy['success']:
            await message.edit_text(
                f"{con.DONE} Contract <code>{name}</code> deployed\n{link}",
                disable_web_page_preview=True
            )
        else:
            await message.edit_text(f"{con.STOP} {deploy['message']}")
