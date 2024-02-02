import io
import segno
import qrcode_artistic

import utils as utl
import constants as con

from plugin import TGBFPlugin
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext, CommandHandler, CallbackQueryHandler


class Address(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.address_callback, block=False))
        await self.add_handler(CallbackQueryHandler(self.privkey_callback, block=False))

    @TGBFPlugin.send_typing
    async def address_callback(self, update: Update, context: CallbackContext):
        context.user_data.clear()

        wallet = await self.get_wallet(update.effective_user.id)

        b_out = io.BytesIO()
        if context.args and context.args[0].lower() == "profile":
            photos = await context.bot.getUserProfilePhotos(update.effective_user.id)

            if photos.photos:
                for photo in photos.photos:
                    img = io.BytesIO()

                    await (await photo[-1].get_file()).download_to_memory(out=img)

                    segno.make_qr(wallet.public_key).to_artistic(
                        background=img,
                        target=b_out,
                        border=1,
                        scale=10,
                        kind='png'
                    )
                    break
            else:
                segno.make_qr(wallet.public_key).save(b_out, border=1, scale=10, kind="png")
        else:
            segno.make_qr(wallet.public_key).save(b_out, border=1, scale=10, kind="png")

        if self.is_private(update.message):
            context.user_data["privkey"] = wallet.private_key

            await update.message.reply_photo(
                photo=b_out.getvalue(),
                caption=f"<code>{wallet.public_key}</code>",
                reply_markup=self.privkey_button_callback()
            )
        else:
            await update.message.reply_photo(
                photo=b_out.getvalue(),
                caption=f"<code>{wallet.public_key}</code>"
            )

    @TGBFPlugin.send_typing
    async def privkey_callback(self, update: Update, context: CallbackContext):
        if update.callback_query.data != self.name:
            return

        if "privkey" not in context.user_data:
            msg = f"Old message. Please execute command again"
            await context.bot.answer_callback_query(update.callback_query.id, msg)
            return

        message = update.callback_query.message
        privkey = context.user_data["privkey"]

        await message.edit_caption(
            caption=f"<b>Address</b>\n"
                    f"<code>{message.caption}</code>\n\n"
                    f"<b>Private Key</b>\n"
                    f"<code>{privkey}</code>"
        )

        await self.remove_msg_after(message, after_secs=600)

        msg = f"{con.WARNING} Message will be removed after 10 minutes {con.WARNING}"
        await context.bot.answer_callback_query(update.callback_query.id, msg)

    def privkey_button_callback(self):
        menu = utl.build_menu([InlineKeyboardButton("Show Private Key", callback_data=self.name)])
        return InlineKeyboardMarkup(menu)
