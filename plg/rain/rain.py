import html

import constants as con

from plugin import TGBFPlugin
from telegram import Update
from datetime import datetime, timedelta, timezone
from telegram.ext import CallbackContext, CommandHandler


class Rain(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.rain_callback, block=False))

    @TGBFPlugin.public()
    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def rain_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        if len(context.args) not in (2, 3):
            await update.message.reply_text(
                await self.get_info()
            )
            return

        user_id = update.message.from_user.id
        wallet = await self.get_wallet(user_id)
        xian = await self.get_xian(wallet=wallet)

        contract = None
        ticker = None
        amount_total = None
        time_frame = None

        message = await update.message.reply_text(f"{con.WAIT} Preparing ...")

        # Tipping XIAN
        if len(context.args) == 2:
            contract = 'currency'
            ticker = 'XIAN'
            amount_total = context.args[0]
            time_frame = context.args[1]

        # Tipping token
        elif len(context.args) == 3:
            tokens = await self.get_plugin('tokens').get_tokens(user_id)

            # It is a contract
            if context.args[0].lower().startswith(('con_', 'currency')):
                for token in tokens:
                    if token[1] == context.args[0].lower():
                        contract = token[1]
                        ticker = token[2]
                        break

            # It is a ticker
            else:
                for token in tokens:
                    if token[2] == context.args[0].upper():
                        contract = token[1]
                        ticker = token[2]
                        break

            amount_total = context.args[1]
            time_frame = context.args[2]

        if not contract:
            await message.edit_text(
                f'{con.ERROR} Unknown contract. Make sure you added this token to '
                f'your token list first with <code>/tokens add contract_name</code>'
            )
            return

        try:
            # Check if amount is valid
            amount_total = float(amount_total)

            if amount_total <= 0:
                raise ValueError('Amount can not be negative')
        except:
            msg = f"{con.ERROR} Amount not valid!"
            await message.edit_text(msg)
            return

        if amount_total.is_integer():
            amount_total = int(amount_total)

        # Check if time unit is included and valid
        if not time_frame.lower().endswith(("m", "h")):
            msg = f"{con.ERROR} Allowed time units are <code>m</code> (minute) and <code>h</code> (hour)"
            await message.edit_text(msg)
            return

        t_frame = time_frame[:-1]
        t_unit = time_frame[-1:].lower()

        try:
            # Check if timeframe is valid
            t_frame = float(t_frame)

            if t_frame <= 0:
                raise ValueError('Negative values are not allowed')
        except:
            msg = f"{con.ERROR} Time frame not valid!"
            await message.edit_text(msg)
            return

        # Determine last valid date time for the airdrop
        if t_unit == "m":
            last_time = datetime.now(timezone.utc) - timedelta(minutes=t_frame)
        elif t_unit == "h":
            last_time = datetime.now(timezone.utc) - timedelta(hours=t_frame)
        else:
            msg = f"{con.ERROR} Unsupported time unit"
            await message.edit_text(msg)
            return

        chat_id = update.effective_chat.id

        # Get all users that messaged until 'last_time'
        sql = await self.get_resource("select_active.sql", plugin="active")
        rain = await self.exec_sql(sql, chat_id, last_time, plugin="active")

        if not rain["success"] or not rain["data"]:
            msg = f"{con.ERROR} Could not determine last active users"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(msg)
            return

        # Exclude own user from users to airdrop on
        user_data = [u for u in rain["data"] if u[0] != user_id]

        if len(user_data) < 1:
            msg = f"{con.ERROR} No users found for given time frame"
            await message.edit_text(msg)
            return

        msg = f"{con.RAIN} Initiating rain clouds..."
        message = await message.edit_text(msg)

        # Amount to airdrop to one user
        amount_single = float(f"{(amount_total / len(user_data)):.4f}")

        from_user = update.message.from_user
        from_username = "@" + from_user.username if from_user.username else from_user.first_name

        if amount_single.is_integer():
            amount_single = int(amount_single)

        msg = f"Rained <code>{amount_single}</code> {ticker} each on following users:\n"

        suffix = ", "

        # List of addresses that will get the airdrop
        addresses = list()

        user_limit = self.cfg.get("user_limit")
        counter = 0

        for user in user_data:
            counter += 1

            if counter > user_limit:
                self.log.warning(f"User limit of {user_limit} hit")
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
                f"rained on with {amount_single} {ticker} to wallet {address}")

        # Remove last suffix
        msg = msg[:-len(suffix)]

        multisend_contract = self.cfg.get("contract")
        multisend_function = self.cfg.get("function")

        kwargs = {
            "addresses": addresses,
            "amount": amount_single,
            "contract": contract
        }

        try:
            approved_amount = xian.get_approved_amount(multisend_contract, token=contract)
            self.log.debug(f'approved amount: {approved_amount}')
        except Exception as e:
            msg = f"GET APPROVED AMOUNT Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        if approved_amount < amount_total:
            try:
                # Approve sending tokens to contract
                approve = xian.approve(multisend_contract, token=contract)
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

        try:
            # Execute contract to send tokens
            send = xian.send_tx(multisend_contract, multisend_function, kwargs)
            self.log.debug(f'Rain TX: {send}')
        except Exception as e:
            msg = f"SEND Error: {e}"
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
                link = f'<a href="{explorer_url}/tx/{tx_hash}">View Transaction</a>'

                await message.edit_text(
                    f"{msg}\n\n{link}",
                    disable_web_page_preview=True
                )

        if not send['success']:
            await message.edit_text(f"{con.STOP} {send['message']}")
        else:
            await self.plugins['event'].track_tx(tx_hash, tx_result)
