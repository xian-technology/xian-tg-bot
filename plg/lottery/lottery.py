import os

import utils as utl
import constants as con

from plugin import TGBFPlugin
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext, CommandHandler, CallbackQueryHandler


class Lottery(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.lottery_callback, block=False))
        await self.add_handler(CallbackQueryHandler(self.lottery_action_callback, block=False))

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def lottery_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        if len(context.args) not in (1, 2):
            await update.message.reply_text(
                await self.get_info()
            )
            return

        message = await update.message.reply_text(f"{con.WAIT} Preparing ...")

        user_id = update.message.from_user.id
        wallet = await self.get_wallet(user_id)
        xian = await self.get_xian(wallet=wallet)

        lottery_contract = self.cfg.get("contract")

        lottery_id = update.message.id
        token_contract = ""
        amount = 0

        # Contract and amount provided
        if len(context.args) == 2:
            token_contract = context.args[0]
            amount = context.args[1]

        # Only amount provided
        elif len(context.args) == 1:
            token_contract = 'currency'
            amount = context.args[0]

        # Validate contract
        if token_contract != 'currency' and not token_contract.startswith('con_'):
            msg = f"{con.ERROR} Contract needs to be 'currency' or start with 'con_'"
            await message.edit_text(msg)
            return

        # Validate amount
        try:
            amount = float(amount)

            if amount <= 0:
                raise ValueError('Amount can not be negative')

            if amount.is_integer():
                amount = int(amount)
        except Exception as e:
            msg = f"{con.ERROR} {e}"
            await message.edit_text(msg)
            return

        try:
            approved_amount = xian.get_approved_amount(lottery_contract, token=token_contract)
            self.log.debug(f'approved amount: {approved_amount}')
        except Exception as e:
            msg = f"GET APPROVED AMOUNT Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        if approved_amount < amount:
            try:
                # Approve sending tokens to contract
                approve = xian.approve(lottery_contract, token=token_contract)
                self.log.debug(f'approve: {approve}')

                if not approve['success']:
                    await message.edit_text(f"{con.ERROR} Can not approve contract!")
                    return
            except Exception as e:
                msg = f"APPROVE Error: {e}"
                self.log.error(msg)
                await self.notify(msg)
                await message.edit_text(f"{con.ERROR} {e}")
                return

        kwargs = {
            'lottery_id': update.message.id,
            'token_contract': token_contract,
            'total_amount': amount
        }

        try:
            # Execute contract to send tokens
            send = xian.send_tx(lottery_contract, 'lottery_start', kwargs)
            self.log.debug(f'Lottery Start TX: {send}')
        except Exception as e:
            msg = f"Lottery Start Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        tx_hash = send['tx_hash']

        async def tx_result(success: str, result: str):
            if not success:
                await message.edit_text(f"{con.STOP} {result}")
            else:
                explorer_url = self.cfg_global.get('xian', 'explorer')
                link = f'<a href="{explorer_url}/tx/{tx_hash}">View Transaction on Explorer</a>'

                from_user = update.message.from_user
                creator = "@" + from_user.username if from_user.username else from_user.first_name

                await message.delete()

                # TODO: Replace contract name with ticker
                msg = (f'{con.LUCK} <b>New Lottery started!</b>\n\n'
                       f'Lottery-ID: <code>{lottery_id}</code>\n'
                       f'From User:  {creator}\n'
                       f'Deposited:  <code>{amount}</code> <code>{token_contract}</code>\n\n'
                       f'By pressing the button "Participate!" you take part in the lottery '
                       f'and have a chance to win the deposited amount!')

                banner = os.path.join(self.get_res_path(), "banner.jpg")

                await context.bot.send_photo(
                    chat_id=update.message.chat_id,
                    photo=open(banner, "rb"),
                    caption=f'{msg}\n\n{link}',
                    reply_markup=self.lottery_buttons(lottery_id)
                )

        if not send['success']:
            await message.edit_text(f"{con.STOP} {send['message']}")
        else:
            await self.plugins['event'].track_tx(tx_hash, tx_result)

    def lottery_buttons(self, lottery_id: int):
        menu = utl.build_menu(
            [InlineKeyboardButton(
                f"{con.DONE} Participate!",
                callback_data=f'{self.name}_{lottery_id}_add'
            ),
             InlineKeyboardButton(
                 f"{con.FINISH} End (only creator)",
                 callback_data=f'{self.name}_{lottery_id}_end'
             )], 2
        )
        return InlineKeyboardMarkup(menu)

    async def lottery_action_callback(self, update: Update, context: CallbackContext):
        if not update.callback_query.data.startswith(self.name):
            return

        callback_data = update.callback_query.data.split('_')
        lottery_id = int(callback_data[1])
        lottery_command = callback_data[2]

        lottery_contract = self.cfg.get("contract")

        user = update.effective_user
        wallet = await self.get_wallet(user.id)
        xian = await self.get_xian(wallet=wallet)

        username = f'@{user.username}' if user.username else user.first_name

        # Save wallet address and username / first name
        # Since we don't have that combination of data otherwise
        self.kv_set(wallet.public_key, username)

        # TODO: Update original message with number of users
        # PARTICIPATE in lottery
        if lottery_command == 'add':
            try:
                send = xian.send_tx(
                    lottery_contract,
                    'lottery_register',
                    {'lottery_id': lottery_id}
                )

                self.log.debug(f'Lottery Register TX: {send}')

                tx_hash = send['tx_hash']
                explorer_url = self.cfg_global.get('xian', 'explorer')
                link = f'<a href="{explorer_url}/tx/{tx_hash}">View Transaction</a>'

                async def tx_result(success: str, result: str):
                    if success:
                        await update.callback_query.message.reply_text(
                            f'{con.GREEN_HEART} User {username} participates in the lottery. {link}',
                            disable_web_page_preview=True
                        )
                    else:
                        await update.callback_query.message.reply_text(
                            f'{con.ERROR} Could not add {username} to the lottery: '
                            f'<code>{result}</code>. {link}',
                            disable_web_page_preview=True
                        )

                if send['success']:
                    await self.plugins['event'].track_tx(tx_hash, tx_result)
                    await context.bot.answer_callback_query(
                        update.callback_query.id,
                        f"{con.STARS} Transaction sent..."
                    )
                else:
                    await update.callback_query.message.reply_text(
                        f'{con.ERROR} Could not add {username} to the lottery: '
                        f'<code>{send["message"]}</code>. {link}',
                        disable_web_page_preview=True
                    )
                    await context.bot.answer_callback_query(
                        update.callback_query.id,
                        f"{con.ERROR} Something went wrong..."
                    )
            except Exception as e:
                msg = f"Lottery Register Error: {e}"
                self.log.error(msg)
                await self.notify(msg)
                await context.bot.answer_callback_query(
                    update.callback_query.id,
                    f"{con.ERROR} {e}"
                )

        # END lottery
        if lottery_command == 'end':
            try:
                send = xian.send_tx(
                    lottery_contract,
                    'lottery_end',
                    {'lottery_id': lottery_id}
                )

                self.log.debug(f'Lottery End TX: {send}')

                tx_hash = send['tx_hash']
                explorer_url = self.cfg_global.get('xian', 'explorer')
                link = f'<a href="{explorer_url}/tx/{tx_hash}">View Transaction</a>'

                async def tx_result(success: str, result: str):
                    if success:
                        result = result.replace("'", '')
                        winner_address = result.replace('Winner ', '')
                        winner_username = self.kv_get(winner_address)

                        if winner_username:
                            winner = winner_username
                        else:
                            winner = '<code>' + winner_address[:6] + '...' + '</code>'

                        # TODO: Edit original message with winner

                        await update.callback_query.message.reply_text(
                            f'{con.GREEN_HEART} Lottery ended and {winner} won! {link}',
                            disable_web_page_preview=True
                        )
                    else:
                        await update.callback_query.message.reply_text(
                            f'{con.ERROR} Could not end the lottery: '
                            f'<code>{result}</code>. {link}',
                            disable_web_page_preview=True
                        )

                if send['success']:
                    await self.plugins['event'].track_tx(tx_hash, tx_result)
                    await context.bot.answer_callback_query(
                        update.callback_query.id,
                        f"{con.STARS} Transaction sent..."
                    )
                else:
                    await update.callback_query.message.reply_text(
                        f'{con.ERROR} Could not end the lottery: '
                        f'<code>{send["message"]}</code>. {link}',
                        disable_web_page_preview=True
                    )
                    await context.bot.answer_callback_query(
                        update.callback_query.id,
                        f"{con.ERROR} Something went wrong..."
                    )
            except Exception as e:
                msg = f"Lottery End Error: {e}"
                self.log.error(msg)
                await self.notify(msg)
                await context.bot.answer_callback_query(
                    update.callback_query.id,
                    f"{con.ERROR} {e}"
                )

        # Unknown button command
        else:
            await context.bot.answer_callback_query(
                update.callback_query.id,
                f"{con.ERROR} Something went wrong..."
            )