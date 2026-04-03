from telegram import Chat, Update
from telegram.error import ChatMigrated
from telegram.ext import CallbackContext, MessageHandler, filters

import constants as con
from plugin import TGBFPlugin


class Manage(TGBFPlugin):

    async def init(self):
        await self.add_handler(MessageHandler(filters.ALL, self.manage_callback, block=False))

    async def manage_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return
        if update.message.chat.type == Chat.PRIVATE:
            return

        usr = update.effective_user
        msg = update.effective_message

        if not usr:
            return

        chat_id = update.effective_chat.id
        user_id = update.effective_user.id

        try:
            # If admin posts a message, then we can ignore it
            chat_member = await context.bot.get_chat_member(chat_id, user_id)
            if chat_member.status in ['administrator', 'creator']:
                return

        except ChatMigrated as e:
            # Handle group migration to supergroup
            new_chat_id = e.new_chat_id
            self.log.info(f"Chat migrated from {chat_id} to {new_chat_id}")

            # Use the new chat ID for the API call
            try:
                chat_member = await context.bot.get_chat_member(new_chat_id, user_id)
                if chat_member.status in ['administrator', 'creator']:
                    return
            except Exception as inner_e:
                self.log.error(f'Error getting chat member with new chat ID: {inner_e}')
                await self.notify(f"Chat migration handled but error occurred: {inner_e}")
                return

        except Exception as e:
            self.log.error(f'Error getting chat member: {e}')
            await self.notify(e)
            return

        try:
            # Are we in the right topic?
            if msg.message_thread_id == self.cfg.get('thread_id'):
                # Is user allowed to post?
                if usr.id not in self.cfg.get('allowed_user_list'):
                    # Educate user
                    info_msg = await msg.reply_text(
                        f"{con.ERROR} You are not allowed to post in this thread!"
                    )

                    # Remove user message
                    await msg.delete()

                    # Remove bot message
                    remove_after = self.cfg.get('remove_after_secs')
                    await self.remove_msg_after(info_msg, after_secs=remove_after)
        except Exception as e:
            self.log.error(f'Can not delete message: {e} - UPDATE: {update}')
            await self.notify(e)
