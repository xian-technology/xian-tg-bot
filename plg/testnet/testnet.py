import time

import constants as con

from plugin import TGBFPlugin
from telegram import Update
from xian_py.wallet import Wallet
from telegram.ext import CallbackContext, CommandHandler
from datetime import datetime, timezone, timedelta


class Testnet(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.testnet_callback, block=False))

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def testnet_callback(self, update: Update, context: CallbackContext):
        if not update.message:
            return

        user_id = update.message.from_user.id
        user_wallet = await self.get_wallet(user_id)

        if len(context.args) == 0:
            # Show help text for no arguments
            await update.message.reply_text(
                await self.get_info()
            )
            return

        # Check for balance command
        if context.args[0].lower() == "balance":
            await self.handle_balance(update, user_wallet)
            return

        # Check for claim command
        if context.args[0].lower() == "claim":
            await self.handle_claim(update, user_wallet)
            return

        # Check for send command
        if context.args[0].lower() == "send":
            if len(context.args) != 3:
                await update.message.reply_text(
                    await self.get_info()
                )
                return

            try:
                amount = float(context.args[1])
                if amount <= 0:
                    await update.message.reply_text(f"{con.ERROR} Amount must be greater than 0")
                    return
            except ValueError:
                await update.message.reply_text(f"{con.ERROR} Invalid amount")
                return

            to_address = context.args[2]
            await self.handle_send(update, user_wallet, amount, to_address)
            return

        # If arguments provided but not in correct format
        await update.message.reply_text(
            await self.get_info()
        )

    async def handle_balance(self, update: Update, user_wallet: Wallet):
        """Handle showing testnet balance of user's bot wallet"""
        message = await update.message.reply_text(f"{con.WAIT} Retrieving balance...")

        try:
            # Get testnet instance with user's wallet
            testnet = await self.get_xian(
                self.cfg.get('testnet_node'),
                self.cfg.get('chain_id'),
                user_wallet
            )

            # Get user's balance
            balance = testnet.get_balance(user_wallet.public_key)

            await message.edit_text(
                f"{con.INFO} Balance: <code>{balance}</code> tXIAN\n"
            )

        except Exception as e:
            self.log.error(f"Unexpected error: {e}")
            await self.notify(e)
            await message.edit_text(f"{con.ERROR} An unexpected error occurred")

    async def handle_claim(self, update: Update, user_wallet: Wallet):
        """Handle claiming tokens to user's bot wallet"""
        message = await update.message.reply_text(f"{con.WAIT} Processing claim request...")

        try:
            # Get testnet instance with faucet wallet
            testnet = await self.get_testnet_instance()

            # Check if user can claim
            user_address = user_wallet.public_key
            await self.validate_claim(testnet, user_address)

            # Amount to send
            amount = self.cfg.get('amount')

            try:
                # Send testnet XIAN from faucet to user
                send = testnet.send(amount, user_address)

                if not send["success"]:
                    msg = f"CLAIM Error: {send['message']}"
                    self.log.error(msg)
                    await self.notify(msg)
                    await message.edit_text(f"{con.ERROR} Transaction failed: {send['message']}")
                    return

                self.log.debug(f'Claim TX: {send}')
            except Exception as e:
                msg = f"CLAIM Error: {e}"
                self.log.error(msg)
                await self.notify(msg)
                await message.edit_text(f"{con.ERROR} {str(e)}")
                return

            # Save claim time
            tz = timezone.utc
            current_dt_str = datetime.now(tz=tz).strftime("%Y-%m-%dT%H:%M:%S")
            self.kv_set(user_address, current_dt_str)

            await message.edit_text(
                f"{con.INFO} Claimed {amount} tXIAN to your bot wallet"
            )

        except ValueError as e:
            await message.edit_text(f"{con.ERROR} {str(e)}")
        except Exception as e:
            self.log.error(f"Unexpected error: {e}")
            await self.notify(e)
            await message.edit_text(f"{con.ERROR} An unexpected error occurred")

    async def handle_send(self, update: Update, from_wallet: Wallet, amount: float, to_address: str):
        """Handle sending tokens from user's bot wallet to another address"""
        message = await update.message.reply_text(f"{con.WAIT} Processing send request...")

        try:
            # Get testnet instance with user's wallet
            testnet = await self.get_xian(
                self.cfg.get('testnet_node'),
                self.cfg.get('chain_id'),
                from_wallet
            )

            # Validate target address
            if not from_wallet.is_valid_key(to_address):
                await message.edit_text(f"{con.ERROR} Not a valid address!")
                return

            # Check user's balance
            from_address = from_wallet.public_key
            balance = testnet.get_balance(from_address)

            if balance < amount:
                await message.edit_text(
                    f"{con.ERROR} Insufficient balance!\n"
                    f"You need {amount} tXIAN but only have {balance} tXIAN.\n"
                    f"Use <code>/testnet claim</code> to claim more tokens first."
                )
                return

            # Check if Event plugin is available
            event_plugin = self.plugins.get('event')

            try:
                # Send testnet XIAN from user's wallet
                send = testnet.send(amount, to_address)
                self.log.debug(f'Send TX: {send}')

                if not send.get("success"):
                    await message.edit_text(f"{con.ERROR} Transaction failed: {send.get('message', 'Unknown error')}")
                    return

            except Exception as e:
                msg = f"SEND Error: {e}"
                self.log.error(msg)
                await self.notify(msg)
                await message.edit_text(f"{con.ERROR} {str(e)}")
                return

            tx_hash = send.get("tx_hash")
            explorer_url = self.cfg_global.get('xian', 'explorer', "")

            # If we can't track the transaction, show immediate success
            if not event_plugin or not tx_hash or not event_plugin.is_node_connected():
                link_text = f'\n<a href="{explorer_url}/tx/{tx_hash}">View Transaction</a>' if explorer_url and tx_hash else ""
                await message.edit_text(
                    f"{con.DONE} Sent {amount} tXIAN from your bot wallet to\n"
                    f"<code>{to_address}</code>{link_text}",
                    disable_web_page_preview=True
                )
                return

            # Update message to show we're waiting for confirmation
            await message.edit_text(f"{con.WAIT} Sent {amount} tXIAN, waiting for confirmation...")

            # Create a flag to track if the callback was called
            callback_completed = False

            async def tx_result(success, result):
                nonlocal callback_completed
                callback_completed = True

                if success:
                    link_text = f'\n<a href="{explorer_url}/tx/{tx_hash}">View Transaction</a>' if explorer_url else ""
                    await message.edit_text(
                        f"{con.DONE} Sent {amount} tXIAN from your bot wallet to\n"
                        f"<code>{to_address}</code>{link_text}",
                        disable_web_page_preview=True
                    )
                else:
                    await message.edit_text(
                        f"{con.ERROR} Transaction failed: {result}\n"
                        f"Address: <code>{to_address}</code>"
                    )

            # Track the transaction
            try:
                # Set a reasonable timeout (20 seconds)
                timeout = 20
                self.log.info(f"Tracking transaction {tx_hash} with timeout {timeout}s")

                # Start tracking but don't wait
                event_plugin.track_tx(tx_hash, tx_result)

                # Wait for the callback to be called or timeout
                start_time = time.time()
                while not callback_completed and time.time() - start_time < timeout:
                    await asyncio.sleep(1)

                # If we timed out, update the message
                if not callback_completed:
                    link_text = f'\n<a href="{explorer_url}/tx/{tx_hash}">View Transaction</a>' if explorer_url else ""
                    await message.edit_text(
                        f"{con.INFO} Sent {amount} tXIAN to <code>{to_address}</code>, but confirmation is taking longer than expected. "
                        f"The transaction was submitted and should complete soon.{link_text}",
                        disable_web_page_preview=True
                    )
            except Exception as e:
                self.log.error(f"Error tracking transaction: {e}")
                link_text = f'\n<a href="{explorer_url}/tx/{tx_hash}">View Transaction</a>' if explorer_url else ""
                await message.edit_text(
                    f"{con.DONE} Sent {amount} tXIAN from your bot wallet to\n"
                    f"<code>{to_address}</code>{link_text}",
                    disable_web_page_preview=True
                )

        except Exception as e:
            self.log.error(f"Unexpected error: {e}")
            await self.notify(e)
            await message.edit_text(f"{con.ERROR} An unexpected error occurred")

    async def get_testnet_instance(self):
        """Get testnet instance with faucet wallet"""
        testnet_node = self.cfg.get('testnet_node')
        chain_id = self.cfg.get('chain_id')
        from_privkey = self.cfg.get('privkey')
        from_wallet = Wallet(from_privkey)

        testnet = await self.get_xian(testnet_node, chain_id, from_wallet)

        if chain_id is None:
            self.cfg.set('chain_id', testnet.chain_id)

        return testnet

    async def validate_claim(self, testnet, address: str):
        """Validate if address can claim tokens"""
        # Check current balance
        balance = testnet.get_balance(address)
        threshold = self.cfg.get('threshold')

        if balance > threshold:
            raise ValueError(f"Your bot wallet already has more than {threshold} tXIAN")

        # Check last claim time
        past_dt_str = self.kv_get(address)
        if past_dt_str:
            tz = timezone.utc
            ft = "%Y-%m-%dT%H:%M:%S"
            current_dt = datetime.now(tz=tz)
            past_dt = datetime.strptime(past_dt_str, ft).replace(tzinfo=tz)
            delta = current_dt - past_dt

            days_waiting = self.cfg.get('days_waiting')
            if delta.days < days_waiting:
                next_claim = past_dt + timedelta(days=days_waiting)
                time_left = next_claim - current_dt
                hours = int(time_left.total_seconds() // 3600)
                minutes = int((time_left.total_seconds() % 3600) // 60)
                raise ValueError(
                    f"You need to wait {hours} hours and {minutes} minutes before claiming again\n"
                    f"Next claim possible at: {next_claim.strftime('%Y-%m-%d %H:%M:%S')} UTC"
                )