import asyncio

import constants as con
import utils as utl

from plugin import TGBFPlugin
from telegram.ext import CallbackContext, CommandHandler, CallbackQueryHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup


class Sell(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.sell_callback, block=False))
        await self.add_handler(CallbackQueryHandler(self.confirm_callback, block=False))

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def sell_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        if len(context.args) != 2:
            await update.message.reply_text(
                await self.get_info()
            )
            return

        user_id = update.message.from_user.id

        pair = context.args[0]
        amount = context.args[1]

        message = await update.message.reply_text(f"{con.WAIT} Preparing ...")

        try:
            # Check if amount is valid
            amount = float(amount)

            if amount <= 0:
                raise ValueError('Amount can not be negative')
        except:
            msg = f"{con.ERROR} Amount not valid"
            await message.edit_text(msg)
            return

        if amount.is_integer():
            amount = int(amount)

        if "-" not in pair:
            await message.edit_text(
                f"{con.ERROR} Pair not valid. "
                f"Please use the format <code>[ticker#1]-[ticker#2]</code>")
            return

        # Split pair into tickers
        sell_symbol, buy_symbol = pair.split('-')
        sell_symbol = sell_symbol.upper()
        buy_symbol = buy_symbol.upper()

        # Retrieve contracts
        tokens = await self.get_plugin('tokens').get_tokens(user_id)

        buy_list = list()
        sell_list = list()

        buy_decimals = 0
        sell_decimals = 0

        for token in tokens:
            if token[2] == buy_symbol:
                buy_list.append(token[1])
                buy_decimals = token[3]
            if token[2] == sell_symbol:
                sell_list.append(token[1])
                sell_decimals = token[3]

        # Check if multiple matches found
        if len(sell_list) != 1:
            await message.edit_text(
                f"{con.ERROR} Multiple matches found for <code>{sell_symbol.upper()}</code>. "
                f"Please adjust your token list with /tokens"
            )
            return
        if len(buy_list) != 1:
            await message.edit_text(
                f"{con.ERROR} Multiple matches found for <code>{buy_symbol.upper()}</code>. "
                f"Please adjust your token list with /tokens"
            )
            return

        sell_contract = sell_list[0]
        buy_contract = buy_list[0]

        id = utl.id()

        self.kv_set(f"{id}_user", user_id)
        self.kv_set(f"{id}_sell_con", sell_contract)
        self.kv_set(f"{id}_sell_sym", sell_symbol)
        self.kv_set(f"{id}_sell_dec", sell_decimals)
        self.kv_set(f"{id}_buy_con", buy_contract)
        self.kv_set(f"{id}_buy_sym", buy_symbol)
        self.kv_set(f"{id}_buy_dec", buy_decimals)
        self.kv_set(f"{id}_amount", amount)

        await message.edit_text(
            f"{con.MONEY} Sell {amount} {sell_symbol} for {buy_symbol}",
            reply_markup = self.confirm_button(id))

    def confirm_button(self, id: int):
        menu = utl.build_menu(
            [InlineKeyboardButton(
                f"{con.DONE} Confirm selling",
                callback_data=f'{self.name}_{id}'
            )], 1
        )
        return InlineKeyboardMarkup(menu)

    async def confirm_callback(self, update: Update, context: CallbackContext):
        if not update.callback_query.data.startswith(self.name):
            return

        callback_data = update.callback_query.data.split('_')
        id = callback_data[1]

        user_id = self.kv_get(f"{id}_user")
        sell_contract = self.kv_get(f"{id}_sell_con")
        buy_contract = self.kv_get(f"{id}_buy_con")
        sell_symbol = self.kv_get(f"{id}_sell_sym")
        buy_symbol = self.kv_get(f"{id}_buy_sym")
        amount = self.kv_get(f"{id}_amount")

        if update.effective_user.id != user_id:
            return

        wallet = await self.get_wallet(user_id)
        xian = await self.get_xian(wallet=wallet)

        contract = self.cfg.get('contract')

        message = update.effective_message

        try:
            approved_amount = xian.get_approved_amount(contract, token=sell_contract)
            self.log.debug(f'Approved amount: {approved_amount}')
        except Exception as e:
            msg = f"GET APPROVED AMOUNT Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        if approved_amount < amount:
            try:
                approve = xian.approve(contract, token=sell_contract)
                self.log.debug(f'Approve TX: {approve}')

                if not approve['success']:
                    await message.edit_text(f"{con.ERROR} Can not approve contract!")
                    return
            except Exception as e:
                msg = f"APPROVE Error: {e}"
                self.log.error(msg)
                await self.notify(msg)
                await message.edit_text(f"{con.ERROR} {e}")
                return

            tx_hash = approve['tx_hash']

            if approve['success']:
                try:
                    success, result = await self.plugins['event'].track_tx(
                        tx_hash,
                        wait=True,
                        timeout=60
                    )
                    if not success:
                        await message.edit_text(f"{con.STOP} Approval failed: {result}")
                        return
                except asyncio.TimeoutError:
                    await message.edit_text(f"{con.ERROR} Approval transaction timeout")
                    return
            else:
                await message.edit_text(f"{con.STOP} {approve['message']}")
                return

        try:
            sell = xian.send_tx(
                contract,
                "sell",
                kwargs={
                    "sell_token": sell_contract,
                    "buy_token": buy_contract,
                    "amount": amount
                }
            )
            self.log.debug(f'Sell TX: {sell}')
        except Exception as e:
            msg = f"SELL Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        tx_hash = sell['tx_hash']

        if sell['success']:
            async def tx_result(success: str, result: str):
                if success:
                    explorer_url = self.cfg_global.get('xian', 'explorer')
                    link = f'<a href="{explorer_url}/tx/{tx_hash}">View Transaction</a>'

                    result = result.strip("'()").strip()
                    values = result.split(",")

                    sold = float(values[0].strip())
                    bought = float(values[1].strip())
                    price = utl.format_float(bought / sold)
                    sold = utl.format_float(sold)
                    bought = utl.format_float(bought)

                    await message.edit_text(
                        f"{con.MONEY} Sold <code>{sold}</code> {sell_symbol} for "
                        f"<code>{bought}</code> {buy_symbol} at a price of <code>{price}</code>\n{link}",
                        disable_web_page_preview=True
                    )
                else:
                    await message.edit_text(f"{con.STOP} {result}")

            await self.plugins['event'].track_tx(tx_hash, tx_result)
        else:
            await message.edit_text(f"{con.STOP} {sell['message']}")

        # Remove all keys with ID as prefix
        self.kv_del(id, is_prefix=True)

        await context.bot.answer_callback_query(
            update.callback_query.id,
            f"{con.DONE} Sold {amount} {sell_symbol}"
        )
