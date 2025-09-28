import html
import asyncio

import constants as con

from plugin import TGBFPlugin
from telegram import Update
from datetime import datetime, timedelta, timezone
from telegram.ext import CallbackContext, CommandHandler


class Bless(TGBFPlugin):

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

        # Exclude own user from airdrop
        user_data = [u for u in rain["data"] if u[0] != user_id]

        if len(user_data) < 1:
            msg = f"{con.ERROR} No users found for given time frame"
            await message.edit_text(msg)
            return

        msg = f"{con.RAIN} Blessing users..."
        message = await message.edit_text(msg)

        user_limit = self.cfg.get("user_limit")
        eligible_users = 0
        addresses = []
        msg = str()
        suffix = ", "

        # Check all users until we find enough eligible ones
        for user in user_data:
            to_user_id = user[0]
            to_username = user[1]

            # Check if user has xian.org in name
            try:
                chat = await context.bot.get_chat(to_user_id)
                first_name = chat.first_name
                last_name = chat.last_name or ""
                full_name = f"{first_name} {last_name}".strip()

                if "xian.org" not in full_name.lower():
                    self.log.info(f"Skipping user {full_name} with ID {to_user_id}")
                    continue
                else:
                    self.log.info(f"User {full_name} with ID {to_user_id} is eligible")

                    # Add eligible user
                    address = (await self.get_wallet(to_user_id)).public_key
                    addresses.append(address)

                    msg += html.escape(to_username) + suffix

                    # Increment eligible user counter
                    eligible_users += 1

                    # Check if we've reached the limit
                    if eligible_users >= user_limit:
                        self.log.warning(f"User limit of {user_limit} hit")
                        break
            except Exception as e:
                self.log.error(f"Can't retrieve user info for user ID {to_user_id}: {e}")
                continue

        if not addresses:
            msg = f"{con.ERROR} No users found with XIAN.ORG in name"
            await message.edit_text(msg)
            return

        # Amount to airdrop to one user
        amount_single = float(f"{(amount_total / len(addresses)):.4f}")

        if amount_single.is_integer():
            amount_single = int(amount_single)

        msg = f"Blessed {len(addresses)} users with <code>{amount_single}</code> {ticker}:\n{msg}"

        # Remove last suffix
        msg = msg[:-len(suffix)]

        multisend_contract = self.cfg.get("contract")
        multisend_function = self.cfg.get("function")

        kwargs = {
            "addresses": addresses,
            "amount": amount_single,
            "contract": contract
        }

        event_plugin = self.plugins['event']
        if not event_plugin.is_node_connected():
            await event_plugin.force_reconnect()

        try:
            approved_amount = await xian.get_approved_amount(multisend_contract, token=contract)
            self.log.debug(f'Approved amount: {approved_amount}')
        except Exception as e:
            msg = f"GET APPROVED AMOUNT Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        if approved_amount < amount_total:
            try:
                # Approve sending tokens to contract
                approve = await xian.approve(multisend_contract, token=contract)
                self.log.debug(f'Approve: {approve}')

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
                    success, result = await event_plugin.track_tx(
                        tx_hash,
                        wait=True
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
            # Execute contract to send tokens
            send = await xian.send_tx(multisend_contract, multisend_function, kwargs)
            self.log.debug(f'Bless TX: {send}')
        except Exception as e:
            msg = f"SEND Error: {e}"
            self.log.error(msg)
            await self.notify(msg)
            await message.edit_text(f"{con.ERROR} {e}")
            return

        tx_hash = send['tx_hash']

        if send['success']:
            try:
                success, result = await event_plugin.track_tx(tx_hash, wait=True)
                if success:
                    explorer_url = self.cfg_global.get('xian', 'explorer')
                    link = f'<a href="{explorer_url}/tx/{tx_hash}">View Transaction</a>'
                    await message.edit_text(f"{msg}\n\n{link}", disable_web_page_preview=True)
                else:
                    await message.edit_text(f"{con.STOP} {result}")
            except asyncio.TimeoutError:
                await message.edit_text(f"{con.ERROR} Bless transaction timeout")
        else:
            await message.edit_text(f"{con.STOP} {send['message']}")
