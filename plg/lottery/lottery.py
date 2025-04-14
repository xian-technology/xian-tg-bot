import os
import re
import asyncio

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
            await context.bot.send_message(
                update.message.chat_id,
                f"{con.ERROR} Contract needs to be 'currency' or start with 'con_'"
            )
            return

        # Validate amount
        try:
            amount = float(amount)

            if amount <= 0:
                raise ValueError('Amount can not be negative')

            if amount.is_integer():
                amount = int(amount)
        except Exception as e:
            await context.bot.send_message(
                update.message.chat_id,
                f"{con.ERROR} {e}"
            )
            return

        try:
            approved_amount = xian.get_approved_amount(lottery_contract, token=token_contract)
            self.log.debug(f'approved amount: {approved_amount}')
        except Exception as e:
            msg = f"GET APPROVED AMOUNT Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await context.bot.send_message(
                update.message.chat_id,
                f"{con.ERROR} {e}"
            )
            return

        if approved_amount < amount:
            try:
                approve = xian.approve(lottery_contract, token=token_contract)
                self.log.debug(f'Approve TX: {approve}')
            except Exception as e:
                msg = f"APPROVE Error: {e}"
                self.log.error(msg)
                await self.notify(msg)
                await context.bot.send_message(
                    update.message.chat_id,
                    f"{con.ERROR} {e}"
                )
                return

            tx_hash = approve['tx_hash']

            if approve['success']:
                try:
                    success, result = await self.plugins['event'].track_tx(
                        tx_hash,
                        wait=True
                    )
                    if not success:
                        await context.bot.send_message(
                            update.message.chat_id,
                            f"{con.ERROR} Approval failed: {result}")
                        return
                except asyncio.TimeoutError:
                    await context.bot.send_message(
                        update.message.chat_id,
                        f"{con.ERROR} Approval transaction timeout")
                    return
            else:
                await context.bot.send_message(
                    update.message.chat_id,
                    f"{con.ERROR} {approve['message']}")
                return

        kwargs = {
            'lottery_id': update.message.id,
            'token_contract': token_contract,
            'total_amount': amount
        }

        try:
            # Execute contract to start lottery
            send = xian.send_tx(lottery_contract, 'lottery_start', kwargs)
            self.log.debug(f'Lottery Start TX: {send}')
        except Exception as e:
            msg = f"Lottery Start Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await context.bot.send_message(
                update.message.chat_id,
                f"{con.ERROR} {e}"
            )
            return

        tx_hash = send['tx_hash']

        async def tx_result(success: str, result: str):
            if success:
                explorer_url = self.cfg_global.get('xian', 'explorer')
                link = f'<a href="{explorer_url}/tx/{tx_hash}">View Transaction on Explorer</a>'

                from_user = update.message.from_user
                creator = "@" + from_user.username if from_user.username else from_user.first_name
                creator = creator if creator.startswith('@') else f'<code>{creator}</code>'

                ticker = xian.get_state(
                    token_contract,
                    'metadata',
                    'token_symbol'
                )

                token = ticker if ticker else token_contract

                msg = (f'{con.LUCK} <b>New Lottery started!</b>\n\n'
                       f'<code>LotteryID:</code> <code>{lottery_id}</code>\n'
                       f'<code>From User:</code> {creator}\n'
                       f'<code>Deposited:</code> <code>{amount}</code> <code>{token}</code>\n'
                       f'<code>User Pool:</code> <code>0</code>\n\n'
                       f'By pressing the button "Participate!" you take part in the lottery '
                       f'and have a chance to win the deposited amount!')

                self.kv_set(str(lottery_id), msg)

                banner = os.path.join(self.get_res_path(), "banner.jpg")

                await context.bot.send_photo(
                    chat_id=update.message.chat_id,
                    photo=open(banner, "rb"),
                    caption=f'{msg}\n\n{link}',
                    reply_markup=self.lottery_buttons(lottery_id)
                )
            else:
                await context.bot.send_message(
                    update.message.chat_id,
                    f"{con.ERROR} {result}"
                )
                return

        if send['success']:
            await self.plugins['event'].track_tx(tx_hash, tx_result)
        else:
            await context.bot.send_message(
                update.message.chat_id,
                f"{con.ERROR} {send['message']}"
            )
            return

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

    def lottery_end_button(self, url: str):
        menu = utl.build_menu(
            [InlineKeyboardButton(
                f"{con.MONEY} Lottery ended! View winner {con.MONEY}",
                url=url
            )]
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
                            f'{con.HEART_GREEN} User {username} participates in the lottery. {link}',
                            disable_web_page_preview=True
                        )
                        old_msg = self.kv_get(str(lottery_id))
                        new_msg = self.update_user_pool(old_msg)
                        self.kv_set(str(lottery_id), new_msg)

                        await update.callback_query.message.edit_caption(
                            new_msg,
                            reply_markup=self.lottery_buttons(lottery_id)
                        )
                    else:
                        await update.callback_query.message.reply_text(
                            f'{con.ERROR} Could not add {username} to the lottery: '
                            f'<code>{result}</code>.',
                            disable_web_page_preview=True
                        )
                        return

                if send['success']:
                    await self.plugins['event'].track_tx(tx_hash, tx_result)
                    await context.bot.answer_callback_query(
                        update.callback_query.id,
                        f"{con.STARS} Transaction sent..."
                    )
                else:
                    await update.callback_query.message.reply_text(
                        f'{con.ERROR} Could not add {username} to the lottery: '
                        f'<code>{send["message"]}</code>.',
                        disable_web_page_preview=True
                    )
                    await context.bot.answer_callback_query(
                        update.callback_query.id,
                        f"{con.ERROR} Something went wrong..."
                    )
            except Exception as e:
                msg = f"LOTTERY REGISTER Error: {e}"
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

                        await update.callback_query.message.reply_text(
                            f'{con.HEART} Lottery ended and {winner} won! {link}',
                            disable_web_page_preview=True
                        )
                        await update.callback_query.message.edit_reply_markup(
                            reply_markup=self.lottery_end_button(
                                f"{explorer_url}/tx/{tx_hash}"
                            )
                        )
                    else:
                        await update.callback_query.message.reply_text(
                            f'{con.ERROR} Could not end the lottery: '
                            f'<code>{result}</code>.',
                            disable_web_page_preview=True
                        )
                        return

                if send['success']:
                    await self.plugins['event'].track_tx(tx_hash, tx_result)
                    await context.bot.answer_callback_query(
                        update.callback_query.id,
                        f"{con.STARS} Transaction sent..."
                    )
                else:
                    await update.callback_query.message.reply_text(
                        f'{con.ERROR} Could not end the lottery: '
                        f'<code>{send["message"]}</code>.',
                        disable_web_page_preview=True
                    )
                    await context.bot.answer_callback_query(
                        update.callback_query.id,
                        f"{con.ERROR} Something went wrong..."
                    )
                    return
            except Exception as e:
                msg = f"LOTTERY END Error: {e}"
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
            return

    # Function to update the "User Pool" value
    def update_user_pool(self, msg):
        # Regex to match the "User Pool" value
        pattern = r'(<code>User Pool:</code> <code>)(\d+)(</code>)'

        # Function to increment the matched value
        def replacer(match):
            current_value = int(match.group(2))  # Extract the integer value
            new_value = current_value + 1  # Increment it by 1
            return f'{match.group(1)}{new_value}{match.group(3)}'  # Reconstruct the string

        # Replace the old value with the updated value
        updated_msg = re.sub(pattern, replacer, msg)
        return updated_msg