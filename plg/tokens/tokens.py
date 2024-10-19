import constants as con

from plugin import TGBFPlugin
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler


class Tokens(TGBFPlugin):

    async def init(self):
        if not await self.table_exists("tokens"):
            sql = await self.get_resource("create_tokens.sql")
            await self.exec_sql(sql)

        await self.add_handler(CommandHandler(self.handle, self.tokens_callback, block=False))

    @TGBFPlugin.logging()
    @TGBFPlugin.private()
    @TGBFPlugin.send_typing()
    async def tokens_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        # No arguments
        if len(context.args) == 0:
            await update.message.reply_text(await self.get_info())
            return

        user_id = update.message.from_user.id

        # Get all tokens for current user
        sql = await self.get_resource("select_tokens.sql")
        tokens = await self.exec_sql(sql, user_id)

        # Check if contract name is saved
        async def is_contract_present(name: str):
            for token in tokens['data']:
                if token[1] == name:
                    return True
            return False

        # One argument
        if len(context.args) == 1:

            # List all tokens
            if context.args[0].lower() == 'list':
                msg = 'Contract - Ticker - Decimal places\n'
                for token in tokens['data']:
                    msg += (f'<code>{token[1]}</code> - '
                            f'<code>{token[2]}</code> - '
                            f'<code>{token[3]}</code>\n')
                if msg != 'Contract - Ticker - Decimal places\n':
                    await update.message.reply_text(msg)
                else:
                    await update.message.reply_text(
                        f'{con.INFO} Your token list is empty. '
                        f'You can add tokens with <code>/tokens contract_name</code>'
                    )
                return
            else:
                await update.message.reply_text(await self.get_info())
                return

        # Two arguments
        if len(context.args) == 2:
            lvl1 = context.args[0].lower()  # Sub-Command
            lvl2 = context.args[1].lower()  # Contract Name

            # Add token
            if lvl1 == 'add':
                if not lvl2.startswith(('con_', 'currency')):
                    msg = f"{con.ERROR} Not a valid contract name!"
                    await update.message.reply_text(msg)
                    return

                if await is_contract_present(lvl2):
                    msg = f"{con.ERROR} Contract already added!"
                    await update.message.reply_text(msg)
                    return

                xian = await self.get_xian()

                if lvl2 == 'currency':
                    ticker = 'XIAN'
                else:
                    ticker = xian.get_state(
                        lvl2,
                        'metadata',
                        'token_symbol'
                    )

                if not ticker:
                    msg = f"{con.ERROR} Unknown contract!"
                    await update.message.reply_text(msg)
                    return

                # Insert token into DB
                sql = await self.get_resource("insert_tokens.sql")
                decimals = self.cfg.get('default_decimals')
                await self.exec_sql(sql, user_id, lvl2, ticker.upper(), decimals)
                await update.message.reply_text(f"{con.STARS} Token contract added!")
                return

            # Remove token
            if lvl1 == 'remove':
                if not lvl2.startswith(('con_', 'currency')):
                    msg = f"{con.ERROR} Not a valid contract name!"
                    await update.message.reply_text(msg)
                    return

                if not await is_contract_present(lvl2):
                    msg = f"{con.ERROR} Contract is unknown!"
                    await update.message.reply_text(msg)
                    return

                # Delete token from DB
                sql = await self.get_resource("delete_tokens.sql")
                await self.exec_sql(sql,user_id, lvl2)
                await update.message.reply_text(f"{con.STARS} Token contract removed!")
                return

            # Decimal places
            if lvl1 == 'decimals':
                if ':' in lvl2:
                    if not await is_contract_present(lvl2.split(':')[0]):
                        msg = f"{con.ERROR} Contract is unknown!"
                        await update.message.reply_text(msg)
                        return

                    try:
                        contract, decimals = lvl2.split(':')
                        int(decimals)
                    except:
                        await update.message.reply_text(f"{con.ERROR} Wrong data!")
                        return

                    # Update decimal places of a contract
                    sql = await self.get_resource("update_tokens.sql")
                    await self.exec_sql(sql, int(decimals), user_id, contract)
                    await update.message.reply_text(f"{con.STARS} Token contract removed!")
                    return

                else:
                    await update.message.reply_text(await self.get_info())

            else:
                await update.message.reply_text(await self.get_info())

        else:
            await update.message.reply_text(await self.get_info())
