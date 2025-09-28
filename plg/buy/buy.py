import asyncio

import constants as con
import utils as utl

from plugin import TGBFPlugin
from telegram.ext import CallbackContext, CommandHandler, CallbackQueryHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup


class Buy(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(
            self.handle,
            self.buy_callback,
            block=False)
        )

        await self.add_handler(CallbackQueryHandler(
            self.confirm_buy_callback,
            pattern=f"^{self.name}_",
            block=False)
        )

        # Create table if it doesn’t exist
        if not await self.table_exists("buy_transactions"):
            sql = await self.get_resource("create_buy.sql")
            await self.exec_sql(sql)

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

        pair_or_ticker = context.args[0]
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

        # Handle single ticker vs pair format
        if "-" not in pair_or_ticker:
            # Single ticker provided, construct pair
            ticker = pair_or_ticker.upper()
            if ticker == "XIAN":
                pair = "xian-xusdc"  # XIAN is traded against USDC
            else:
                pair = f"{ticker.lower()}-xian"  # Other tokens are traded against XIAN
        else:
            # Pair format provided, use as-is
            pair = pair_or_ticker
            # Validate pair format
            if pair.count('-') != 1:
                await message.edit_text(
                    f"{con.ERROR} Pair not valid. "
                    f"Please use the format <code>[ticker#1]-[ticker#2]</code> or just <code>[ticker]</code>")
                return

        # Split pair into tickers
        buy_symbol, sell_symbol = pair.split('-')
        buy_symbol = buy_symbol.upper()
        sell_symbol = sell_symbol.upper()

        # Retrieve contracts
        tokens = await self.get_plugin('tokens').get_tokens(user_id)

        buy_list = list()
        sell_list = list()

        for token in tokens:
            if token[2].upper() == buy_symbol:
                buy_list.append(token[1])
            if token[2].upper() == sell_symbol:
                sell_list.append(token[1])

        # Check if tokens are known
        if len(sell_list) == 0:
            await message.edit_text(
                f"{con.ERROR} No token contract found for <code>{sell_symbol.upper()}</code>. "
                f"Please adjust your token list with /tokens"
            )
            return
        if len(buy_list) == 0:
            await message.edit_text(
                f"{con.ERROR} No token contract found for <code>{buy_symbol.upper()}</code>. "
                f"Please adjust your token list with /tokens"
            )
            return
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

        # Store transaction data in SQLite
        await self.exec_sql(
            await self.get_resource("insert_buy.sql"),
            id, user_id, buy_contract, buy_symbol, sell_contract, sell_symbol, amount
        )

        await message.edit_text(
            f"{con.MONEY} Buy {amount} {buy_symbol} for {sell_symbol}",
            reply_markup=self.confirm_buy_button(id))

    def confirm_buy_button(self, id: int):
        menu = utl.build_menu(
            [InlineKeyboardButton(
                f"{con.DONE} Confirm BUY",
                callback_data=f'{self.name}_{id}'
            )], 1
        )
        return InlineKeyboardMarkup(menu)

    async def confirm_buy_callback(self, update: Update, context: CallbackContext):
        self.log.debug(f'Data - update.callback_query.data: {update.callback_query.data}')
        if not update.callback_query.data.startswith(self.name):
            return

        callback_data = update.callback_query.data.split('_')
        id = callback_data[1]

        # Retrieve transaction data from DB
        select_sql = await self.get_resource('select_buy.sql')
        res = await self.exec_sql(select_sql, id)
        if not res["success"] or not res["data"]:
            await update.effective_message.edit_text(f"{con.ERROR} Transaction data not found")
            return

        row = res["data"][0]
        user_id = row[1]
        buy_contract = row[2]
        buy_symbol = row[3]
        sell_contract = row[4]
        sell_symbol = row[5]
        amount = row[6]

        if update.effective_user.id != user_id:
            return

        wallet = await self.get_wallet(user_id)
        xian = await self.get_xian(wallet=wallet)

        contract = self.cfg.get('contract')

        message = update.effective_message

        event_plugin = self.plugins['event']
        if not event_plugin.is_node_connected():
            await event_plugin.force_reconnect()

        try:
            approved_amount = await xian.get_approved_amount(contract, token=sell_contract)
            self.log.debug(f'Approved amount: {approved_amount}')
        except Exception as e:
            msg = f"GET APPROVED AMOUNT Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        if approved_amount < amount:
            try:
                approve = await xian.approve(contract, token=sell_contract)
                self.log.debug(f'Approve TX: {approve}')
            except Exception as e:
                msg = f"APPROVE Error: {e}"
                self.log.error(msg)
                await self.notify(msg)
                await message.edit_text(f"{con.ERROR} {e}")
                return

            tx_hash = approve['tx_hash']

            if approve['success']:
                try:
                    success, result = await event_plugin.track_tx(
                        tx_hash,
                        wait=True
                    )
                    if not success:
                        await message.edit_text(f"{con.ERROR} Approval failed: {result}")
                        return
                except asyncio.TimeoutError:
                    await message.edit_text(f"{con.ERROR} Approval transaction timeout")
                    return
            else:
                await message.edit_text(f"{con.ERROR} {approve['message']}")
                return

        try:
            buy = await xian.send_tx(
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

        if buy['success']:
            async def tx_result(success: str, result: str):
                if success:
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
                        f"<code>"
                        f"→ Bought {bought} {buy_symbol}\n"
                        f"← Sold {sold} {sell_symbol}\n"
                        f"$ Price {price}"
                        f"</code>\n{link}",
                        disable_web_page_preview=True
                    )
                else:
                    await message.edit_text(f"{con.ERROR} {result}")

            await event_plugin.track_tx(tx_hash, tx_result)
        else:
            await message.edit_text(f"{con.ERROR} {buy['message']}")

        if amount.is_integer:
            amount = int(amount)

        await context.bot.answer_callback_query(
            update.callback_query.id,
            f"{con.DONE} Bought {amount} {buy_symbol}"
        )
