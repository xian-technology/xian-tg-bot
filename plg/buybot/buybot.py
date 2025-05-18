import gc
import json
import asyncio
import websockets

import utils as utl
import constants as con

from plugin import TGBFPlugin
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler
from xian_py.encoding import decode_str
from datetime import datetime


class Buybot(TGBFPlugin):
    """Plugin for monitoring and displaying DEX transactions"""

    async def init(self):
        # Load configuration with proper defaults to avoid None values
        self.watched_chats = self.cfg.get("watched_chats") or []
        self.watched_contracts = self.cfg.get("watched_contracts") or ["con_dex_v2"]
        self.watched_functions = self.cfg.get("watched_functions") or [
            "swapExactTokenForTokenSupportingFeeOnTransferTokens",
            "swapExactTokenForToken",
            "swapTokenForExactToken"
        ]

        # Cache for token symbols and pair information
        self.token_symbols_cache = {}
        self.pair_info_cache = {}

        # Add command handler for managing the buybot
        await self.add_handler(CommandHandler(self.handle, self.buybot_callback, block=False))

        # Start websocket connection for event listening
        asyncio.create_task(self.websocket_loop())

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    @TGBFPlugin.owner()  # Restrict to bot owner
    async def buybot_callback(self, update: Update, context: CallbackContext):
        """Handle buybot command to manage settings"""
        if not update.message:
            return

        # Ensure our lists are initialized
        if self.watched_chats is None:
            self.watched_chats = []
        if self.watched_contracts is None:
            self.watched_contracts = ["con_dex_v2"]
        if self.watched_functions is None:
            self.watched_functions = ["swapExactTokenForTokenSupportingFeeOnTransferTokens",
                                      "swapExactTokenForToken",
                                      "swapTokenForExactToken"]

        if not context.args:
            await update.message.reply_text(await self.get_info())
            return

        subcommand = context.args[0].lower()

        # Get current chat_id and thread_id if in a topic
        chat_id = update.effective_chat.id
        thread_id = update.message.message_thread_id

        # Create a chat entry that includes both chat_id and thread_id if in a topic
        chat_entry = {"chat_id": chat_id, "thread_id": thread_id} if thread_id else chat_id

        if subcommand == 'start':
            # Check if chat is already in the watched chats
            is_watched = False
            for existing_entry in self.watched_chats:
                if isinstance(existing_entry, dict) and existing_entry.get("chat_id") == chat_id:
                    if existing_entry.get("thread_id") == thread_id:
                        is_watched = True
                        break
                elif existing_entry == chat_id and not thread_id:
                    is_watched = True
                    break

            # Add current chat/thread to watched chats
            if not is_watched:
                self.watched_chats.append(chat_entry)
                await update.message.reply_text(f"{con.DONE} Buy-bot started in this " +
                                                ("topic" if thread_id else "chat"))
                self.cfg.set(self.watched_chats, "watched_chats")
            else:
                await update.message.reply_text(f"{con.INFO} Buy-bot already active in this " +
                                                ("topic" if thread_id else "chat"))

        elif subcommand == 'stop':
            # Find and remove the current chat/thread from watched chats
            removed = False
            for i, existing_entry in enumerate(self.watched_chats.copy()):
                if isinstance(existing_entry, dict) and existing_entry.get("chat_id") == chat_id:
                    if existing_entry.get("thread_id") == thread_id:
                        self.watched_chats.pop(i)
                        removed = True
                        break
                elif existing_entry == chat_id and not thread_id:
                    self.watched_chats.pop(i)
                    removed = True
                    break

            if removed:
                await update.message.reply_text(f"{con.DONE} Buy-bot stopped in this " +
                                                ("topic" if thread_id else "chat"))
                self.cfg.set(self.watched_chats, "watched_chats")
            else:
                await update.message.reply_text(f"{con.INFO} Buy-bot not active in this " +
                                                ("topic" if thread_id else "chat"))

        elif subcommand == 'status':
            # Check if this chat/thread is being watched
            is_active = False
            for existing_entry in self.watched_chats:
                if isinstance(existing_entry, dict) and existing_entry.get("chat_id") == chat_id:
                    if existing_entry.get("thread_id") == thread_id:
                        is_active = True
                        break
                elif existing_entry == chat_id and not thread_id:
                    is_active = True
                    break

            status = f"{con.GREEN} Active" if is_active else f"{con.RED} Inactive"
            contracts = ", ".join(self.watched_contracts)
            functions = ", ".join(f for f in self.watched_functions)

            msg = (
                f"<b>Buy-Bot Status</b>\n\n"
                f"Status: {status}\n"
                f"Watched Contracts: <code>{contracts}</code>\n"
                f"Watched Functions: <code>{functions}</code>\n"
                f"Total Active Chats: {len(self.watched_chats)}"
            )
            await update.message.reply_text(msg)

        # Rest of the method remains the same
        elif subcommand == 'add' and len(context.args) > 1:
            # Add contract or function to watch
            item_type = context.args[1].lower()
            if item_type == 'contract' and len(context.args) > 2:
                contract = context.args[2]
                if contract not in self.watched_contracts:
                    self.watched_contracts.append(contract)
                    self.cfg.set(self.watched_contracts, "watched_contracts")
                    await update.message.reply_text(f"{con.DONE} Added contract {contract} to watchlist")
                else:
                    await update.message.reply_text(f"{con.INFO} Contract {contract} already in watchlist")
            elif item_type == 'function' and len(context.args) > 2:
                function = context.args[2]
                if function not in self.watched_functions:
                    self.watched_functions.append(function)
                    self.cfg.set(self.watched_functions, "watched_functions")
                    await update.message.reply_text(f"{con.DONE} Added function {function} to watchlist")
                else:
                    await update.message.reply_text(f"{con.INFO} Function {function} already in watchlist")
            else:
                await update.message.reply_text(await self.get_info())

        elif subcommand == 'remove' and len(context.args) > 1:
            # Remove contract or function from watch
            item_type = context.args[1].lower()
            if item_type == 'contract' and len(context.args) > 2:
                contract = context.args[2]
                if contract in self.watched_contracts:
                    self.watched_contracts.remove(contract)
                    self.cfg.set(self.watched_contracts, "watched_contracts")
                    await update.message.reply_text(f"{con.DONE} Removed contract {contract} from watchlist")
                else:
                    await update.message.reply_text(f"{con.INFO} Contract {contract} not in watchlist")
            elif item_type == 'function' and len(context.args) > 2:
                function = context.args[2]
                if function in self.watched_functions:
                    self.watched_functions.remove(function)
                    self.cfg.set(self.watched_functions, "watched_functions")
                    await update.message.reply_text(f"{con.DONE} Removed function {function} from watchlist")
                else:
                    await update.message.reply_text(f"{con.INFO} Function {function} not in watchlist")
            else:
                await update.message.reply_text(await self.get_info())
        else:
            await update.message.reply_text(await self.get_info())

    async def websocket_loop(self):
        """Establish WebSocket connection to listen for DEX events"""
        retry_attempts = 0
        max_retries = 5
        base_wait_time = 1

        while True:
            try:
                self.log.info('Initiating buybot websocket connection...')

                # Get node URL from global config
                uri = self.cfg_global.get('xian', 'node')

                if uri.startswith('https://'):
                    uri = uri.replace('https://', 'wss://')
                elif uri.startswith('http://'):
                    uri = uri.replace('http://', 'ws://')
                else:
                    self.log.error("Unsupported URI scheme in node URL.")
                    return

                uri += '/websocket'

                async with websockets.connect(uri) as ws:
                    await self.on_open(ws)
                    try:
                        async for message in ws:
                            await self.on_message(message)
                            # Reset retry attempts on successful message
                            retry_attempts = 0
                    except websockets.ConnectionClosed as e:
                        self.log.warning(f'WebSocket connection closed: {e.code}, {e.reason}')
            except Exception as e:
                self.log.error(f'Websocket error: {e}')
                gc.collect()

                retry_attempts += 1
                if retry_attempts > max_retries:
                    self.log.error(f'Max retries reached. Stopping buybot websocket loop.')
                    break

                # Exponential backoff, cap at 60 seconds
                wait_secs = min(base_wait_time * (2 ** (retry_attempts - 1)), 60)
                self.log.info(f'Websocket reconnect after {wait_secs} seconds')
                await asyncio.sleep(wait_secs)

    async def on_open(self, ws):
        """Handle WebSocket connection open"""
        self.log.info("Buybot websocket connection opened")

        # Subscribe to Tx events
        subscribe_message = {
            "jsonrpc": "2.0",
            "method": "subscribe",
            "id": 0,
            "params": {
                "query": "tm.event='Tx'"
            }
        }

        await ws.send(json.dumps(subscribe_message))
        self.log.info("Sent subscription message for Tx events")

    async def on_message(self, msg):
        """Process incoming WebSocket messages"""
        try:
            # Parse the message
            msg_json = json.loads(msg)

            if not msg_json.get('result'):
                return

            # Process transaction data
            await self.process_transaction(msg_json['result'])

        except Exception as e:
            self.log.error(f'Error processing websocket message: {e}')
            await self.notify(e)

    def format_current_time_for_display(self):
        """Format current time as a list for display in deadline field"""
        now = datetime.utcnow()
        return [now.year, now.month, now.day, now.hour, now.minute]

    async def process_transaction(self, tx_data):
        """Process transaction data to check for DEX events"""
        try:
            # Check if this is a transaction result
            if 'data' not in tx_data or 'value' not in tx_data['data'] or 'TxResult' not in tx_data['data']['value']:
                return

            tx_result = tx_data['data']['value']['TxResult']

            # Get transaction hash
            tx_hash = None
            tx_events = tx_data.get('events', {})
            if tx_events and 'tx.hash' in tx_events:
                tx_hash_event = tx_events['tx.hash']
                tx_hash = tx_hash_event[0] if isinstance(tx_hash_event, list) else tx_hash_event

            # Extract result data
            if 'result' not in tx_result or 'data' not in tx_result['result']:
                return

            # Decode the data
            data = tx_result['result']['data']
            try:
                decoded_data = json.loads(decode_str(data))

                # First check for direct function calls
                if isinstance(decoded_data, dict) and 'contract' in decoded_data and isinstance(
                        decoded_data['contract'], dict):
                    contract = decoded_data['contract'].get('name')

                    # Check if this is a contract we're watching
                    if contract in self.watched_contracts:
                        function = decoded_data.get('function')

                        # Check if this is a function we're watching
                        if function in self.watched_functions:
                            arguments = decoded_data.get('kwargs', {})

                            # We found a matching direct function call!
                            self.log.info(f"Found DEX function call: {contract}.{function}")
                            await self.send_dex_notification(contract, function, arguments,
                                                             decoded_data.get('hash', tx_hash))
                            return  # Process only once

                # Look for Swap events in the transaction
                if 'events' in decoded_data:
                    for event in decoded_data['events']:
                        # Check if the caller contract is one we're watching
                        if event.get('caller') in self.watched_contracts and event.get('event') == 'Swap':
                            # We found a DEX swap event!
                            self.log.info(f"Found DEX swap event: {event['caller']}.{event['event']}")

                            # Prepare notification
                            contract = event.get('caller')

                            # Try to determine the specific function based on event data
                            function = "swapExactTokenForToken"  # Default function name

                            # For Swap events, try to determine the exact function that was called
                            swap_data = event.get('data', {})

                            # Safely get values and convert to float if they're strings
                            amount0In = swap_data.get('amount0In', 0)
                            amount0In = float(amount0In) if isinstance(amount0In, str) else amount0In

                            amount0Out = swap_data.get('amount0Out', 0)
                            amount0Out = float(amount0Out) if isinstance(amount0Out, str) else amount0Out

                            amount1In = swap_data.get('amount1In', 0)
                            amount1In = float(amount1In) if isinstance(amount1In, str) else amount1In

                            amount1Out = swap_data.get('amount1Out', 0)
                            amount1Out = float(amount1Out) if isinstance(amount1Out, str) else amount1Out

                            # Compare the float values
                            if amount0In > 0 and not amount0Out > 0:
                                # Token0 -> Token1 swap
                                function = "swapExactTokenForToken"
                            elif amount0Out > 0 and not amount0In > 0:
                                # Token1 -> Token0 swap
                                function = "swapTokenForExactToken"

                            # Check if it might be a supporting fee on transfer function
                            if 'fee' in str(decoded_data).lower() or 'tax' in str(decoded_data).lower():
                                function = "swapExactTokenForTokenSupportingFeeOnTransferTokens"

                            # Determine which token is being swapped
                            pair_id = event.get('data_indexed', {}).get('pair')
                            to_address = event.get('data_indexed', {}).get('to')

                            # Determine tokens being swapped (with proper type conversion)
                            src_token = "currency"
                            dst_token = "con_usdc"

                            # Safely get values and convert to float if they're strings
                            amount0In = swap_data.get('amount0In', 0)
                            amount0In = float(amount0In) if isinstance(amount0In, str) else amount0In

                            amount1In = swap_data.get('amount1In', 0)
                            amount1In = float(amount1In) if isinstance(amount1In, str) else amount1In

                            # Compare the float values
                            if amount0In > 0:
                                src_token = "con_usdc"
                                dst_token = "currency"

                            # Format amounts as fixed numbers (keep as strings)
                            amount_in = str(swap_data.get('amount0In', 0) or swap_data.get('amount1In', 0))
                            amount_out = str(swap_data.get('amount0Out', 0) or swap_data.get('amount1Out', 0))

                            arguments = {
                                "amountIn": {"__fixed__": amount_in},
                                "amountOutMin": {"__fixed__": amount_out},
                                "pair": pair_id,
                                "src": src_token,
                                "to": to_address,
                                "deadline": {"__time__": self.format_current_time_for_display()}
                            }

                            await self.send_dex_notification(contract, function, arguments,
                                                             decoded_data.get('hash', tx_hash))
                            return  # Process only once
            except Exception as e:
                self.log.debug(f"Failed to decode transaction data: {e}")
                return

        except Exception as e:
            self.log.error(f'Error processing transaction data: {e}')
            await self.notify(e)

    async def get_token_symbol(self, contract_address):
        """Get token symbol with caching for better performance"""
        if contract_address in self.token_symbols_cache:
            return self.token_symbols_cache[contract_address]

        # Special case for currency contract
        if contract_address.lower() == "currency":
            self.token_symbols_cache[contract_address] = "XIAN"
            return "XIAN"

        try:
            # Get token symbol from blockchain
            xian = await self.get_xian()
            ticker = xian.get_state(
                contract_address,
                'metadata',
                'token_symbol'
            )

            if ticker:
                # Cache the result
                self.token_symbols_cache[contract_address] = ticker
                return ticker

            # Fallback to formatted contract name
            fallback = contract_address.replace("con_", "").upper()
            self.token_symbols_cache[contract_address] = fallback
            return fallback

        except Exception as e:
            self.log.debug(f"Error getting token symbol for {contract_address}: {e}")
            fallback = contract_address.replace("con_", "").upper()
            return fallback

    async def get_pair_info(self, pair_id):
        """Get information about a trading pair with caching"""
        # Return from cache if available
        if pair_id in self.pair_info_cache:
            return self.pair_info_cache[pair_id]

        # Get pairs from config
        watched_pairs = self.cfg.get("watched_pairs") or []

        # Convert to dictionary for quick lookup
        pairs = {}
        for pair in watched_pairs:
            if 'id' in pair and 'token0' in pair and 'token1' in pair and 'symbol0' in pair and 'symbol1' in pair:
                pairs[pair['id']] = (
                    pair['token0'],
                    pair['token1'],
                    pair['symbol0'],
                    pair['symbol1']
                )

        # Default pairs if none in config
        if not pairs:
            pairs = {
                1: ("con_usdc", "currency", "XUSDC", "XIAN"),
                2: ("con_poop_coin", "currency", "POOP", "XIAN")
            }

        if pair_id in pairs:
            self.pair_info_cache[pair_id] = pairs[pair_id]
            return pairs[pair_id]

        # For unknown pairs, return None
        return None

    async def get_token_for_pair(self, pair_id, is_source=True):
        """Get token contract for a pair ID using config"""
        # Get pairs from config
        watched_pairs = self.cfg.get("watched_pairs") or []

        # Convert to dictionary for quick lookup
        pairs = {}
        for pair in watched_pairs:
            if 'id' in pair and 'token0' in pair and 'token1' in pair:
                pairs[pair['id']] = (pair['token0'], pair['token1'])

        # Default pairs if none in config
        if not pairs:
            pairs = {
                1: ("con_usdc", "currency")
            }

        if pair_id in pairs:
            if is_source:
                return pairs[pair_id][0]
            else:
                return pairs[pair_id][1]

        return "Unknown"

    async def send_dex_notification(self, contract, function, arguments, tx_hash):
        """Send DEX event notification to all watched chats with improved formatting"""
        try:
            # Determine tokens involved in the swap
            src_token = arguments.get("src", "")
            pair_id = arguments.get("pair", 1)

            # Get pair information if available
            pair_info = await self.get_pair_info(pair_id)
            token_symbol = None

            # For XIAN-XUSDC pair (pair_id 1), we want to show transactions where people BUY XIAN
            if pair_id == 1:  # XIAN-XUSDC pair
                # Skip if selling XIAN for XUSDC (src = currency)
                if src_token.upper() == "CURRENCY":
                    self.log.info("Skipping XUSDC buy transaction - buybot only shows XIAN buys")
                    return

                # Continue if buying XIAN with XUSDC (src = con_usdc)
                token_symbol = "XIAN"
                spent_token = "XUSDC"
                got_token = "XIAN"
            else:
                # For other pairs, try to determine tokens based on pair_info or direct lookup
                if pair_info:
                    token0, token1, symbol0, symbol1 = pair_info

                    # Skip if not spending XIAN (src != currency)
                    if src_token.upper() != "CURRENCY":
                        self.log.info(f"Skipping sell transaction for pair {pair_id} - buybot only shows buys")
                        return

                    # We're buying token0 or token1 with XIAN
                    token_symbol = symbol0 if token0 != "currency" else symbol1
                    spent_token = "XIAN"
                    got_token = token_symbol
                else:
                    # Try to get symbols directly from contracts
                    src_symbol = await self.get_token_symbol(src_token)

                    # Skip if not buying with XIAN or selling XIAN
                    if src_token.upper() != "CURRENCY" and src_symbol != "XIAN":
                        self.log.info(f"Skipping transaction with non-XIAN source token: {src_symbol}")
                        return

                    # Find the destination token (the one being bought)
                    dst_token = "currency" if src_token != "currency" else await self.get_token_for_pair(pair_id,
                                                                                                         is_source=False)

                    token_symbol = await self.get_token_symbol(dst_token)
                    spent_token = src_symbol
                    got_token = token_symbol

            # Extract amounts
            amount_in_obj = arguments.get("amountIn", {})
            amount_in = amount_in_obj.get("__fixed__", "0") if isinstance(amount_in_obj, dict) else str(amount_in_obj)

            amount_out_obj = arguments.get("amountOutMin", {})
            amount_out = amount_out_obj.get("__fixed__", "0") if isinstance(amount_out_obj, dict) else str(
                amount_out_obj)

            # Convert to floats for easier formatting
            try:
                amount_in_float = float(amount_in)
                amount_out_float = float(amount_out)
            except:
                amount_in_float = 0
                amount_out_float = 0

            # Get buyer address
            buyer_address = arguments.get("to", "Unknown")
            short_address = buyer_address[:6] + "..." + buyer_address[-4:] if len(buyer_address) > 10 else buyer_address

            # Improved emoji scaling - logarithmic scale for more reasonable emoji counts
            def calculate_emoji_count(amount, base=2, multiplier=3, min_count=3, max_count=15):
                if amount <= 0:
                    return min_count
                import math
                count = base + int(math.log10(max(1, amount)) * multiplier)
                return max(min_count, min(max_count, count))

            # Dynamic emoji count based on amount
            emoji_count = calculate_emoji_count(amount_out_float if token_symbol == "XIAN" else amount_in_float)
            emoji_line = "🟢" * emoji_count

            # Format buy message
            title = f"{token_symbol} Buy!"

            # Format amounts using utils.format_float to remove trailing zeros
            spent_amount = utl.format_float(amount_in_float)
            got_amount = utl.format_float(amount_out_float)

            # Split action text into two lines for better readability
            spent_text = f"💸 Spent <code>{spent_amount}</code> {spent_token}"
            got_text = f"💰 Got <code>{got_amount}</code> {got_token}"

            # Get links
            explorer_url = self.cfg_global.get('xian', 'explorer')
            tx_link = f"{explorer_url}/tx/{tx_hash}"
            address_link = f"{explorer_url}/addresses/{buyer_address}"

            # Build the message
            message = (
                f"<b>{title}</b>\n"
                f"{emoji_line}\n\n"
                f"{spent_text}\n"
                f"{got_text}\n\n"
                f"👤 <a href='{address_link}'>Trader ({short_address})</a> / <a href='{tx_link}'>TX</a>"
            )

            # Send to all watched chats
            if self.watched_chats is None:
                self.watched_chats = []

            # Send to all watched chats
            for chat_entry in self.watched_chats:
                try:
                    # Check if this is a chat_id or a dict with chat_id and thread_id
                    chat_id = None
                    thread_id = None

                    if isinstance(chat_entry, dict):
                        chat_id = chat_entry.get("chat_id")
                        thread_id = chat_entry.get("thread_id")
                    else:
                        chat_id = chat_entry

                    # Send message with thread_id if available
                    await self.tgb.bot.updater.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        message_thread_id=thread_id,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                    self.log.info(f"Sent DEX notification to chat {chat_id}" +
                                  (f" thread {thread_id}" if thread_id else ""))
                except Exception as e:
                    self.log.error(f"Failed to send DEX notification to chat {chat_entry}: {e}")

        except Exception as e:
            self.log.error(f'Error sending DEX notification: {e}')
            await self.notify(e)

    async def get_token_for_pair(self, pair_id, is_source=True):
        """Placeholder method to get token contract for a pair ID"""
        pairs = {
            1: ("con_usdc", "currency"),  # token0, token1
            2: ("con_poop_coin", "currency"),
            # Add more known pairs as needed
        }

        if pair_id in pairs:
            if is_source:
                return pairs[pair_id][0]
            else:
                return pairs[pair_id][1]

        return "Unknown"
