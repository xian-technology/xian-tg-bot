import asyncio

import constants as con
import utils as utl

from plugin import TGBFPlugin
from telegram.ext import CallbackContext, CommandHandler, CallbackQueryHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup


class Buy(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.buy_callback, block=False))
        await self.add_handler(CallbackQueryHandler(self.confirm_callback, block=False))

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def buy_callback(self, update: Update, context: CallbackContext):
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

        # Split pair into tickers
        buy_symbol, sell_symbol = pair.split('-')
        buy_symbol = buy_symbol.upper()
        sell_symbol = sell_symbol.upper()

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
        if len(buy_list) != 1:
            await message.edit_text(
                f"{con.ERROR} Multiple matches found for <code>{buy_symbol.upper()}</code>. "
                f"Please adjust your token list with /tokens"
            )
            return
        if len(sell_list) != 1:
            await message.edit_text(
                f"{con.ERROR} Multiple matches found for <code>{sell_symbol.upper()}</code>. "
                f"Please adjust your token list with /tokens"
            )
            return

        buy_contract = buy_list[0]
        sell_contract = sell_list[0]

        id = utl.id()

        self.kv_set(f"{id}_user", user_id)
        self.kv_set(f"{id}_buy_con", buy_contract)
        self.kv_set(f"{id}_buy_sym", buy_symbol)
        self.kv_set(f"{id}_buy_dec", buy_decimals)
        self.kv_set(f"{id}_sell_con", sell_contract)
        self.kv_set(f"{id}_sell_sym", sell_symbol)
        self.kv_set(f"{id}_sell_dec", sell_decimals)
        self.kv_set(f"{id}_amount", amount)

        await message.edit_text(
            f"{con.MONEY} Buy {amount} {buy_symbol} for {sell_symbol}",
            reply_markup = self.confirm_button(id))

    def confirm_button(self, id: int):
        menu = utl.build_menu(
            [InlineKeyboardButton(
                f"{con.DONE} Confirm buying",
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
        buy_contract = self.kv_get(f"{id}_buy_con")
        sell_contract = self.kv_get(f"{id}_sell_con")
        buy_symbol = self.kv_get(f"{id}_buy_sym")
        sell_symbol = self.kv_get(f"{id}_sell_sym")
        amount = self.kv_get(f"{id}_amount")

        if update.effective_user.id != user_id:
            return

        wallet = await self.get_wallet(user_id)
        xian = await self.get_xian(wallet=wallet)

        contract = self.cfg.get('contract')

        message = update.effective_message

        try:
            xian.approve(contract, token=sell_contract, amount=100000000)
            self.log.debug(f'Approve TX: {xian.approve}')
        except Exception as e:
            msg = f"APPROVE Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        await asyncio.sleep(1)

        try:
            buy = xian.send_tx(
                contract,
                "buy",
                kwargs={
                    "buy_token": buy_contract,
                    "sell_token": sell_contract,
                    "amount": amount
                }
            )
            self.log.debug(f'Buy TX: {buy}')
        except Exception as e:
            msg = f"BUY Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        tx_hash = buy['tx_hash']

        async def tx_result(success: str, result: str):
            if not success:
                await message.edit_text(f"{con.STOP} {result}")
            else:
                explorer_url = self.cfg_global.get('xian', 'explorer')
                link = f'<a href="{explorer_url}/tx/{tx_hash}">View Transaction</a>'

                result = result.strip("'()").strip()
                values = result.split(",")

                sold = float(values[0].strip())
                bought = float(values[1].strip())
                price = utl.format_float(sold / bought)
                sold = utl.format_float(sold)
                bought = utl.format_float(bought)

                await message.edit_text(
                    f"{con.MONEY} Bought <code>{bought}</code> {buy_symbol} for "
                    f"<code>{sold}</code> {sell_symbol} to a price of <code>{price}</code>\n{link}",
                    disable_web_page_preview=True
                )

        if not buy['success']:
            await message.edit_text(f"{con.STOP} {buy['message']}")
        else:
            await self.plugins['event'].track_tx(tx_hash, tx_result)

        # Remove all keys with ID as prefix
        self.kv_del(id, is_prefix=True)

        await context.bot.answer_callback_query(
            update.callback_query.id,
            f"{con.ERROR} Bought {amount} {buy_symbol}"
        )
