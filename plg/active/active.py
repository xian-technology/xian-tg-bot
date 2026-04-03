from telegram import Chat, Update
from telegram.ext import CallbackContext, MessageHandler, filters

from plugin import TGBFPlugin


class Active(TGBFPlugin):

    async def init(self):
        if not await self.table_exists('active'):
            sql = await self.get_resource('create_active.sql')
            await self.exec_sql(sql)

        await self.add_handler(MessageHandler(filters.ALL, self.active_callback, block=False))

        # Job to clean entries about active users runs daily
        self.run_repeating(self.cleaner_callback, 86_400)

    async def active_callback(self, update: Update, context: CallbackContext):
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

    async def cleaner_callback(self, context: CallbackContext):
        sql = await self.get_resource('delete_active.sql')
        sql = sql.replace('?', str(self.cfg.get('remove_after_days')))
        await self.exec_sql(sql)
