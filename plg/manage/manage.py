import constants as con

from plugin import TGBFPlugin
from telegram import Update, Chat
from telegram.ext import CallbackContext, MessageHandler, filters


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

        try:
            # Check if the topic is closed - indicating that an admin posted it
            if msg.forum_topic_closed:
                # No further action needed
                return

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
