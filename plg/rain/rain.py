import html

import constants as con

from plugin import TGBFPlugin
from telegram import Update
from datetime import datetime, timedelta
from telegram.ext import CallbackContext, CommandHandler


class Rain(TGBFPlugin):

    STAMPS = [28, 22, 19, 18, 17, 16]

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.rain_callback, block=False))

    @TGBFPlugin.public
    @TGBFPlugin.send_typing
    async def rain_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        if not context.args or len(context.args) != 2:
            await update.message.reply_text(await self.get_info())
            return

        wallet = await self.get_wallet(update.effective_user.id)
        xian = await self.get_xian(wallet)

        amount_total = context.args[0]
        time_frame = context.args[1]

        try:
            # Check if amount is valid
            amount_total = float(amount_total)

            if amount_total < 0:
                raise ValueError('Amount can not be negative')
        except:
            msg = f"{con.ERROR} Amount not valid"
            await update.message.reply_text(msg)
            return

        if amount_total.is_integer():
            amount_total = int(amount_total)

        # Check if time unit is included and valid
        if not time_frame.lower().endswith(("m", "h")):
            msg = f"{con.ERROR} Allowed time units are <code>m</code> (minute) and <code>h</code> (hour)"
            await update.message.reply_text(msg)
            return

        t_frame = time_frame[:-1]
        t_unit = time_frame[-1:].lower()

        try:
            # Check if timeframe is valid
            t_frame = float(t_frame)
        except:
            msg = f"{con.ERROR} Time frame not valid"
            await update.message.reply_text(msg)
            return

        # Determine last valid date time for the airdrop
        if t_unit == "m":
            last_time = datetime.utcnow() - timedelta(minutes=t_frame)
        elif t_unit == "h":
            last_time = datetime.utcnow() - timedelta(hours=t_frame)
        else:
            msg = f"{con.ERROR} Unsupported time unit detected!"
            await update.message.reply_text(msg)
            return

        chat_id = update.effective_chat.id

        # Get all users that messaged until 'last_time'
        sql = await self.get_resource("select_active.sql", plugin="active")
        rain = await self.exec_sql(sql, chat_id, last_time, plugin="active")

        if not rain["success"] or not rain["data"]:
            msg = f"{con.ERROR} Could not determine last active users"
            self.log.error(msg)
            await self.notify(msg)
            await update.message.reply_text(msg)
            return

        # Exclude own user from users to airdrop on
        user_data = [u for u in rain["data"] if u[0] != update.effective_user.id]

        if len(user_data) < 1:
            msg = f"{con.ERROR} No users found for given time frame"
            await update.message.reply_text(msg)
            return

        msg = f"{con.RAIN} Initiating rain clouds..."
        message = await update.message.reply_text(msg)

        # Amount to airdrop to one user
        amount_single = float(f"{(amount_total / len(user_data)):.4f}")

        from_user = update.message.from_user
        from_username = "@" + from_user.username if from_user.username else from_user.first_name

        if amount_single.is_integer():
            amount_single = int(amount_single)

        msg = f"Rained <code>{amount_single}</code> XIAN each on following users:\n"

        suffix = ", "

        # List of addresses that will get the airdrop
        addresses = list()

        user_limit = self.cfg.get("user_limit")
        counter = 0

        for user in user_data:
            counter += 1

            if counter > user_limit:
                self.log.info(f"User limit of {user_limit} hit")
                break

            to_user_id = user[0]
            to_username = user[1]

            address = (await self.get_wallet(to_user_id)).public_key

            # Add address to list of addresses to rain on
            addresses.append(address)
            # Add username to output message
            msg += html.escape(to_username) + suffix

            self.log.info(
                f"User {to_username} ({to_user_id}) will be "
                f"rained on with {amount_single} XIAN to wallet {address}")

        # Remove last suffix
        msg = msg[:-len(suffix)]

        contract = self.cfg.get("contract")
        function = self.cfg.get("function")

        # Calculate stamp costs
        stamps_to_use = 0
        for a in range(len(addresses)):
            try:
                stamps_to_use += self.STAMPS[a]
            except IndexError:
                stamps_to_use += self.STAMPS[-1]

        kwargs = {
            "addresses": addresses,
            "amount": amount_single,
            "contract": "currency"
        }

        try:
            balance = xian.get_balance()
        except Exception as e:
            msg = f"GET_BALANCE Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        # Check if user has enough balance
        if balance < amount_total + 5:
            msg = f"{con.ERROR} Not enough XIAN to rain"
            await message.edit_text(msg)
            return

        try:
            # Approve sending tokens to contract
            xian.approve(contract)
        except Exception as e:
            msg = f"APPROVE Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        try:
            # Execute contract to send tokens
            success, tx_hash = xian.send_tx(contract, function, kwargs, stamps_to_use)
        except Exception as e:
            msg = f"SEND_TX Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        link = f'<a href="{xian.node_url}/tx?hash=0x{tx_hash}">View Transaction</a>'

        if success:
            await message.edit_text(
                f"{msg}\n\n{link}",
                disable_web_page_preview=True
            )

            for user in user_data:
                to_user_id = user[0]

                try:
                    # Notify user about tip
                    await context.bot.send_message(
                        to_user_id,
                        f"You received <code>{amount_single}</code> XIAN from {html.escape(from_username)}\n{link}",
                        disable_web_page_preview=True)
                    self.log.info(f"User {to_user_id} notified about rain of {amount_single} XIAN")
                except Exception as e:
                    self.log.info(f"User {to_user_id} could not be notified about rain: {e} - {update}")
        else:
            await message.edit_text(
                f"{con.STOP} Transaction failed\n{link}",
                disable_web_page_preview=True
            )
