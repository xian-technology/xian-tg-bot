import asyncio
import html
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from telegram import Update
from telegram.ext import CallbackContext, CommandHandler
from xian_py.wallet import Wallet

import constants as con
from logging_setup import error_context, transaction_context
from plugin import TGBFPlugin
from transactions import parse_amount, submission_accepted


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
                amount = parse_amount(context.args[1])
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
            balance = await testnet.get_balance(user_wallet.public_key)

            await message.edit_text(
                f"{con.INFO} Balance: <code>{balance}</code> tXIAN\n"
            )

        except Exception as e:
            self.log.bind(**error_context(e), handler='handle_balance').error('Handle balance failed')
            await self.notify(e)
            await message.edit_text(f"{con.ERROR} An unexpected error occurred")

    async def handle_claim(self, update: Update, user_wallet: Wallet):
        """Handle claiming tokens to user's bot wallet"""
        message = await update.message.reply_text(f"{con.WAIT} Processing claim request...")

        # Eligibility is checked before paying and recorded only after
        # confirmation, so one address's claims must not overlap.
        user_address = user_wallet.public_key
        async with self.claim_lock(user_address):
            await self._claim(message, user_address)

    def claim_lock(self, address: str) -> asyncio.Lock:
        locks = self.__dict__.setdefault("_claim_locks", {})
        return locks.setdefault(address, asyncio.Lock())

    async def _claim(self, message, user_address: str):
        try:
            # Get testnet instance with faucet wallet
            testnet = await self.get_testnet_instance()

            # Check if user can claim
            await self.validate_claim(testnet, user_address)

            # Amount to send
            amount = parse_amount(str(self.cfg.get('amount')))

            try:
                # Send testnet XIAN from faucet to user
                send = await testnet.send(amount, user_address)

                if not submission_accepted(send):
                    self.log.bind(**transaction_context(send)).error("Faucet transaction rejected")
                    await self.notify("Faucet transaction rejected; inspect transaction metadata in the log")
                    await message.edit_text(f"{con.ERROR} Transaction failed: {html.escape(str(send.message))}")
                    return

                success, result = await self.confirm_tx(testnet, send)
                if not success:
                    await message.edit_text(f"{con.ERROR} Claim not confirmed: {html.escape(str(result))}")
                    return

                self.log.bind(**transaction_context(send)).debug("Transaction response received")
            except Exception as e:
                self.log.bind(**error_context(e), handler='handle_claim').error('Handle claim failed')
                await self.notify(e)
                await message.edit_text(f"{con.ERROR} {html.escape(str(e))}")
                return

            # Save claim time
            tz = UTC
            current_dt_str = datetime.now(tz=tz).strftime("%Y-%m-%dT%H:%M:%S")
            await self.kv_set(user_address, current_dt_str)

            await message.edit_text(
                f"{con.INFO} Claimed {amount} tXIAN to your bot wallet"
            )

        except ValueError as e:
            await message.edit_text(f"{con.ERROR} {html.escape(str(e))}")
        except Exception as e:
            self.log.bind(**error_context(e), handler='handle_claim').error('Handle claim failed')
            await self.notify(e)
            await message.edit_text(f"{con.ERROR} An unexpected error occurred")

    async def handle_send(self, update: Update, from_wallet: Wallet, amount: Decimal, to_address: str):
        """Handle sending tokens from user's bot wallet to another address"""
        message = await update.message.reply_text(f"{con.WAIT} Sending...")

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
            balance = await testnet.get_balance(from_address)

            if balance < amount:
                await message.edit_text(
                    f"{con.ERROR} Insufficient balance!\n"
                    f"You need {amount} tXIAN but only have {balance} tXIAN.\n"
                    f"Use <code>/testnet claim</code> to claim more tokens first."
                )
                return

            try:
                # Send testnet XIAN from user's wallet
                send = await testnet.send(amount, to_address)
                self.log.bind(**transaction_context(send)).debug("Transaction response received")
            except Exception as e:
                self.log.bind(**error_context(e), handler='handle_send').error('Handle send failed')
                await self.notify(e)
                await message.edit_text(f"{con.ERROR} {html.escape(str(e))}")
                return

            tx_hash = send.tx_hash

            if submission_accepted(send):
                explorer_url = self.cfg.get('explorer')
                link = f'<a href="{explorer_url}/tx/{tx_hash}">View Transaction</a>'

                success, result = await self.confirm_tx(testnet, send)

                if success:
                    await message.edit_text(
                        f"{con.DONE} Sent <code>{amount}</code> tXIAN\n{link}",
                        disable_web_page_preview=True
                    )
                else:
                    await message.edit_text(
                        f"{con.ERROR} {html.escape(str(result))}",
                        disable_web_page_preview=True
                    )
            else:
                await message.edit_text(f"{con.STOP} {html.escape(str(send.message))}")

        except Exception as e:
            self.log.bind(**error_context(e), handler='handle_send').error('Handle send failed')
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
            self.cfg.set(testnet.chain_id, 'chain_id')

        return testnet

    async def validate_claim(self, testnet, address: str):
        """Validate if address can claim tokens"""
        # Check current balance
        balance = await testnet.get_balance(address)
        threshold = self.cfg.get('threshold')

        if balance > threshold:
            raise ValueError(f"Your bot wallet already has more than {threshold} tXIAN")

        # Check last claim time
        past_dt_str = await self.kv_get(address)
        if past_dt_str:
            tz = UTC
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
