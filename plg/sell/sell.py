import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

import constants as con
import utils as utl
from dex_quote import sell_min_out
from logging_setup import error_context, transaction_context
from plugin import TGBFPlugin
from transactions import (
    decode_trade_amount,
    encode_trade_amount,
    parse_amount,
    submission_accepted,
    trade_amount,
    trade_deadline,
)


class Sell(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(
            self.handle, 
            self.sell_callback, 
            block=False)
        )
        await self.add_handler(CallbackQueryHandler(
            self.confirm_sell_callback,
            pattern=f"^{self.name}_",
            block=False)
        )

        # Create table if it doesn't exist
        if not await self.table_exists("sell_transactions"):
            sql = await self.get_resource("create_sell.sql")
            await self.exec_sql(sql)

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

        pair_or_ticker = context.args[0]
        amount = context.args[1]

        message = await update.message.reply_text(f"{con.WAIT} Preparing ...")

        try:
            # Check if amount is valid
            amount = parse_amount(amount)

            if amount <= 0:
                raise ValueError('Amount can not be negative')
        except:
            msg = f"{con.ERROR} Amount not valid"
            await message.edit_text(msg)
            return

        # Handle single ticker vs pair format
        if "-" not in pair_or_ticker:
            # Single ticker provided, construct pair
            ticker = pair_or_ticker.upper()
            if ticker == "XIAN":
                pair = "xian-xusdc"  # XIAN is sold for USDC
            else:
                pair = f"{ticker.lower()}-xian"  # Other tokens are sold for XIAN
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
        sell_symbol, buy_symbol = pair.split('-')
        sell_symbol = sell_symbol.upper()
        buy_symbol = buy_symbol.upper()

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

        # Store transaction data in SQLite
        stored = await self.exec_sql(
            await self.get_resource("insert_sell.sql"),
            id, user_id, sell_contract, sell_symbol, buy_contract, buy_symbol, encode_trade_amount(amount)
        )
        if not stored["success"]:
            await message.edit_text(f"{con.ERROR} Could not prepare order, please try again")
            return

        await message.edit_text(
            f"{con.MONEY} Sell {amount} {sell_symbol} for {buy_symbol}",
            reply_markup = self.confirm_sell_button(id))

    def confirm_sell_button(self, id: int):
        menu = utl.build_menu(
            [InlineKeyboardButton(
                f"{con.DONE} Confirm SELL",
                callback_data=f'{self.name}_{id}'
            )], 1
        )
        return InlineKeyboardMarkup(menu)

    async def confirm_sell_callback(self, update: Update, context: CallbackContext):
        self.log.debug("Trade confirmation callback received")
        if not update.callback_query.data.startswith(self.name):
            return

        callback_data = update.callback_query.data.split('_')
        id = callback_data[1]

        # Claim the pending order: a confirmation executes at most once, and
        # only for the user who created it
        claim_sql = await self.get_resource('claim_sell.sql')
        res = await self.exec_sql(claim_sql, id, update.effective_user.id)
        if not res["success"] or not res["data"]:
            await context.bot.answer_callback_query(
                update.callback_query.id,
                f"{con.ERROR} Order not available"
            )
            return

        row = res["data"][0]
        user_id = row[1]
        sell_contract = row[2]
        sell_symbol = row[3]
        buy_contract = row[4]
        buy_symbol = row[5]
        amount = decode_trade_amount(row[6])

        wallet = await self.get_wallet(user_id)
        xian = await self.get_xian(wallet=wallet)

        contract = self.cfg.get('contract')

        message = update.effective_message

        try:
            # The helper reverts if the sale yields less than this
            min_out = await sell_min_out(
                xian,
                dex=self.cfg.get('dex_contract'),
                pairs=self.cfg.get('pairs_contract'),
                sell_token=sell_contract,
                buy_token=buy_contract,
                amount=amount,
                slippage=parse_amount(self.cfg.get('slippage_percent')),
            )
            approved_amount = await xian.get_approved_amount(contract, token=sell_contract)
            self.log.debug("Token allowance checked")
        except Exception as e:
            self.log.bind(**error_context(e), handler='confirm_sell_callback').error('Confirm sell callback failed')
            await self.notify(e)
            await message.edit_text(f"{con.ERROR} {html.escape(str(e))}")
            return

        if approved_amount < amount:
            try:
                # Approve exactly the amount being sold
                approve = await xian.approve(contract, token=sell_contract, amount=amount)
                self.log.bind(**transaction_context(approve)).debug("Transaction response received")
            except Exception as e:
                self.log.bind(**error_context(e), handler='confirm_sell_callback').error('Confirm sell callback failed')
                await self.notify(e)
                await message.edit_text(f"{con.ERROR} {html.escape(str(e))}")
                return

            tx_hash = approve.tx_hash

            if submission_accepted(approve):
                try:
                    success, result = await self.confirm_tx(xian, approve)
                    if not success:
                        await message.edit_text(f"{con.ERROR} Approval failed: {html.escape(str(result))}")
                        return
                except TimeoutError:
                    await message.edit_text(f"{con.ERROR} Approval transaction timeout")
                    return
            else:
                await message.edit_text(f"{con.ERROR} {html.escape(str(approve.message))}")
                return

        try:
            sell = await xian.send_tx(
                contract,
                "sell",
                kwargs={
                    "sell_token": sell_contract,
                    "buy_token": buy_contract,
                    "amount": amount,
                    "min_out": min_out,
                    "deadline": trade_deadline(),
                }
            )
            self.log.bind(**transaction_context(sell)).debug("Transaction response received")
        except Exception as e:
            self.log.bind(**error_context(e), handler='confirm_sell_callback').error('Confirm sell callback failed')
            await self.notify(e)
            await message.edit_text(f"{con.ERROR} {html.escape(str(e))}")
            return

        tx_hash = sell.tx_hash

        if submission_accepted(sell):
            async def tx_result(success: bool, result):
                if success:
                    explorer_url = self.cfg_global.get('xian', 'explorer')
                    link = f'<a href="{explorer_url}/tx/{tx_hash}">View Transaction</a>'

                    sold, bought = (trade_amount(value) for value in result)
                    price = utl.format_float(bought / sold)

                    await message.edit_text(
                        f"<code>"
                        f"← Sold {sold:f} {sell_symbol}\n"
                        f"→ Bought {bought:f} {buy_symbol}\n"
                        f"$ Price {price}"
                        f"</code>\n{link}",
                        disable_web_page_preview=True
                    )
                else:
                    await message.edit_text(f"{con.ERROR} {html.escape(str(result))}")

            success, _ = await self.confirm_tx(xian, sell, tx_result)
        else:
            success = False
            await message.edit_text(f"{con.ERROR} {html.escape(str(sell.message))}")

        await context.bot.answer_callback_query(
            update.callback_query.id,
            f"{con.DONE} Sold {amount} {sell_symbol}" if success else f"{con.ERROR} Sell failed"
        )
