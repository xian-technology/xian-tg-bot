from plugin import TGBFPlugin
from telegram import Update, Chat
from telegram.ext import CallbackContext, MessageHandler, filters


class Track(TGBFPlugin):

    async def init(self):
        if not await self.table_exists('track'):
            sql = await self.get_resource('create_track.sql')
            await self.exec_sql(sql)

        await self.add_handler(MessageHandler(filters.ALL, self.track_callback, block=False))

    async def track_callback(self, update: Update, context: CallbackContext):
        try:
            # Don't deal with edited messages
            if not update.message:
                return
            if update.message.chat.type == Chat.PRIVATE:
                return

            c = update.effective_chat
            u = update.effective_user
            m = update.effective_message

            if not u:
                return
            if u.is_bot:
                return

            await self.exec_sql(
                await self.get_resource('insert_active.sql'),
                c.id,
                c.title,
                c.link,
                u.id,
                '@' + u.username if u.username else u.first_name,
                m.id,
                len(m.text) if m.text else None,
                m.text if m.text else None
            )
        except Exception as e:
            self.log.error(f'Can not save activity: {e} - UPDATE: {update}')
            await self.notify(e)
