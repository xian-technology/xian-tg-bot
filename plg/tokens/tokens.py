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
        await self.add_handler(CommandHandler('token', self.tokens_callback, block=False))

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
        tokens = await self.get_tokens(user_id)

        # Check if contract name is saved
        async def is_contract_present(name: str):
            for token in tokens:
                if token[1] == name:
                    return True
            return False

        # One argument
        if len(context.args) == 1:

            # List all tokens
            if context.args[0].lower() == 'list':
                msg = 'Contract - Ticker - Decimal places\n'
                for token in tokens:
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
                sql = await self.get_resource("insert_token.sql")
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
                sql = await self.get_resource("delete_token.sql")
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
                    sql = await self.get_resource("update_decimals.sql")
                    await self.exec_sql(sql, int(decimals), user_id, contract)
                    await update.message.reply_text(f"{con.STARS} Decimals updated!")
                    return

            if lvl1 == 'ticker':
                if ':' in lvl2:
                    if not await is_contract_present(lvl2.split(':')[0]):
                        msg = f"{con.ERROR} Contract is unknown!"
                        await update.message.reply_text(msg)
                        return

                    contract, ticker = lvl2.split(':')

                    if not ticker:
                        msg = f"{con.ERROR} Invalid ticker!"
                        await update.message.reply_text(msg)
                        return

                    # Update ticker of a contract
                    sql = await self.get_resource("update_ticker.sql")
                    await self.exec_sql(sql, ticker, user_id, contract)
                    await update.message.reply_text(f"{con.STARS} Ticker updated!")
                    return

                else:
                    await update.message.reply_text(await self.get_info())

            else:
                await update.message.reply_text(await self.get_info())

        else:
            await update.message.reply_text(await self.get_info())

    # Check if 'currency' is in the token list and if not, add it
    async def check_and_insert_currency(self, user_id: int):
        sql = await self.get_resource('select_by_contract.sql')
        result = await self.exec_sql(sql, user_id, 'currency')

        if not result['data']:
            decimals = self.cfg.get('default_decimals')
            sql = await self.get_resource('insert_token.sql')
            await self.exec_sql(sql, user_id, 'currency', 'XIAN', decimals)

    # Get all tokens for current user
    async def get_tokens(self, user_id: int) -> list:
        sql = await self.get_resource("select_tokens.sql")
        tokens = await self.exec_sql(sql, user_id)
        return tokens['data']
