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
    @TGBFPlugin.owner()
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

        if subcommand == 'start':
            # Parse optional minimum value
            min_value = None
            if len(context.args) > 1:
                try:
                    min_value = float(context.args[1])
                    if min_value <= 0:
                        await update.message.reply_text(f"{con.ERROR} Minimum value must be greater than 0")
                        return
                except ValueError:
                    await update.message.reply_text(f"{con.ERROR} Invalid minimum value. Please provide a number.")
                    return

            # Create a chat entry that includes chat_id, thread_id (if in topic), and min_value (if specified)
            chat_entry = {"chat_id": chat_id}
            if thread_id:
                chat_entry["thread_id"] = thread_id
            if min_value is not None:
                chat_entry["min_value"] = min_value

            # If no special attributes, just use chat_id for backward compatibility
            if not thread_id and min_value is None:
                chat_entry = chat_id

            # Check if chat is already in the watched chats
            is_watched = False
            existing_index = -1
            for i, existing_entry in enumerate(self.watched_chats):
                if isinstance(existing_entry, dict) and existing_entry.get("chat_id") == chat_id:
                    if existing_entry.get("thread_id") == thread_id:
                        is_watched = True
                        existing_index = i
                        break
                elif existing_entry == chat_id and not thread_id:
                    is_watched = True
                    existing_index = i
                    break

            # Add or update current chat/thread in watched chats
            if not is_watched:
                self.watched_chats.append(chat_entry)
                min_text = f" (min: {min_value} XIAN)" if min_value else ""
                await update.message.reply_text(f"{con.DONE} Buy-bot started in this " +
                                                ("topic" if thread_id else "chat") + min_text)
                self.cfg.set(self.watched_chats, "watched_chats")
            else:
                # Update existing entry with new min_value if provided
                if min_value is not None:
                    if isinstance(self.watched_chats[existing_index], dict):
                        self.watched_chats[existing_index]["min_value"] = min_value
                    else:
                        # Convert simple chat_id to dict format
                        self.watched_chats[existing_index] = {"chat_id": chat_id, "min_value": min_value}
                    self.cfg.set(self.watched_chats, "watched_chats")
                    await update.message.reply_text(f"{con.DONE} Updated minimum value to {min_value} XIAN for this " +
                                                    ("topic" if thread_id else "chat"))
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

        elif subcommand == 'setmin' and len(context.args) > 1:
            # Set minimum value for current chat/thread
            try:
                min_value = float(context.args[1])
                if min_value <= 0:
                    await update.message.reply_text(f"{con.ERROR} Minimum value must be greater than 0")
                    return
            except ValueError:
                await update.message.reply_text(f"{con.ERROR} Invalid minimum value. Please provide a number.")
                return

            # Find and update the current chat/thread
            updated = False
            for i, existing_entry in enumerate(self.watched_chats):
                if isinstance(existing_entry, dict) and existing_entry.get("chat_id") == chat_id:
                    if existing_entry.get("thread_id") == thread_id:
                        self.watched_chats[i]["min_value"] = min_value
                        updated = True
                        break
                elif existing_entry == chat_id and not thread_id:
                    # Convert simple chat_id to dict format
                    self.watched_chats[i] = {"chat_id": chat_id, "min_value": min_value}
                    updated = True
                    break

            if updated:
                self.cfg.set(self.watched_chats, "watched_chats")
                await update.message.reply_text(f"{con.DONE} Set minimum value to {min_value} XIAN for this " +
                                                ("topic" if thread_id else "chat"))
            else:
                await update.message.reply_text(f"{con.ERROR} Buy-bot not active in this " +
                                                ("topic" if thread_id else "chat") + ". Use 'start' first.")

        elif subcommand == 'getmin':
            # Get minimum value for current chat/thread
            min_value = None
            for existing_entry in self.watched_chats:
                if isinstance(existing_entry, dict) and existing_entry.get("chat_id") == chat_id:
                    if existing_entry.get("thread_id") == thread_id:
                        min_value = existing_entry.get("min_value")
                        break
                elif existing_entry == chat_id and not thread_id:
                    min_value = None  # Old format doesn't have min_value
                    break

            if min_value is not None:
                await update.message.reply_text(f"Minimum value for this " +
                                                ("topic" if thread_id else "chat") + f": {min_value} XIAN")
            else:
                await update.message.reply_text(f"No minimum value set for this " +
                                                ("topic" if thread_id else "chat"))

        elif subcommand == 'status':
            # Check if this chat/thread is being watched
            is_active = False
            min_value = None
            for existing_entry in self.watched_chats:
                if isinstance(existing_entry, dict) and existing_entry.get("chat_id") == chat_id:
                    if existing_entry.get("thread_id") == thread_id:
                        is_active = True
                        min_value = existing_entry.get("min_value")
                        break
                elif existing_entry == chat_id and not thread_id:
                    is_active = True
                    break

            status = f"{con.GREEN} Active" if is_active else f"{con.RED} Inactive"
            contracts = ", ".join(self.watched_contracts)
            functions = ", ".join(f for f in self.watched_functions)
            min_text = f"\nMinimum Value: {min_value} XIAN" if min_value is not None else "\nMinimum Value: Not set"

            msg = (
                f"<b>Buy-Bot Status</b>\n\n"
                f"Status: {status}{min_text}\n"
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

        elif subcommand == 'addtoken' and len(context.args) > 1:
            # Add token filter for current chat/thread
            token_symbol = context.args[1].upper()

            # Find and update the current chat/thread
            updated = False
            for i, existing_entry in enumerate(self.watched_chats):
                if isinstance(existing_entry, dict) and existing_entry.get("chat_id") == chat_id:
                    if existing_entry.get("thread_id") == thread_id:
                        # Add to existing token list or create new one
                        if "allowed_tokens" not in self.watched_chats[i]:
                            self.watched_chats[i]["allowed_tokens"] = []
                        if token_symbol not in self.watched_chats[i]["allowed_tokens"]:
                            self.watched_chats[i]["allowed_tokens"].append(token_symbol)
                            updated = True
                        break
                elif existing_entry == chat_id and not thread_id:
                    # Convert simple chat_id to dict format and add token filter
                    self.watched_chats[i] = {
                        "chat_id": chat_id,
                        "allowed_tokens": [token_symbol]
                    }
                    updated = True
                    break

            if updated:
                self.cfg.set(self.watched_chats, "watched_chats")
                await update.message.reply_text(f"{con.DONE} Added {token_symbol} to allowed tokens for this " +
                                                ("topic" if thread_id else "chat"))
            else:
                await update.message.reply_text(f"{con.ERROR} Buy-bot not active in this " +
                                                ("topic" if thread_id else "chat") + ". Use 'start' first.")

        elif subcommand == 'removetoken' and len(context.args) > 1:
            # Remove token filter for current chat/thread
            token_symbol = context.args[1].upper()

            # Find and update the current chat/thread
            updated = False
            for i, existing_entry in enumerate(self.watched_chats):
                if isinstance(existing_entry, dict) and existing_entry.get("chat_id") == chat_id:
                    if existing_entry.get("thread_id") == thread_id:
                        allowed_tokens = self.watched_chats[i].get("allowed_tokens", [])
                        if token_symbol in allowed_tokens:
                            allowed_tokens.remove(token_symbol)
                            updated = True
                        break
                elif existing_entry == chat_id and not thread_id:
                    # No token filtering on simple chat_id entries
                    pass

            if updated:
                self.cfg.set(self.watched_chats, "watched_chats")
                await update.message.reply_text(f"{con.DONE} Removed {token_symbol} from allowed tokens for this " +
                                                ("topic" if thread_id else "chat"))
            else:
                await update.message.reply_text(f"{con.INFO} {token_symbol} was not in the allowed tokens list")

        elif subcommand == 'listtokens':
            # List allowed tokens for current chat/thread
            allowed_tokens = None
            for existing_entry in self.watched_chats:
                if isinstance(existing_entry, dict) and existing_entry.get("chat_id") == chat_id:
                    if existing_entry.get("thread_id") == thread_id:
                        allowed_tokens = existing_entry.get("allowed_tokens")
                        break
                elif existing_entry == chat_id and not thread_id:
                    allowed_tokens = None  # No filtering
                    break

            if allowed_tokens:
                token_list = ", ".join(allowed_tokens)
                await update.message.reply_text(f"Allowed tokens for this " +
                                                ("topic" if thread_id else "chat") + f": {token_list}")
            else:
                await update.message.reply_text(f"No token filtering active - showing all tokens for this " +
                                                ("topic" if thread_id else "chat"))

        elif subcommand == 'clearfilter':
            # Remove all token filtering for current chat/thread
            updated = False
            for i, existing_entry in enumerate(self.watched_chats):
                if isinstance(existing_entry, dict) and existing_entry.get("chat_id") == chat_id:
                    if existing_entry.get("thread_id") == thread_id:
                        if "allowed_tokens" in self.watched_chats[i]:
                            del self.watched_chats[i]["allowed_tokens"]
                            updated = True
                        break

            if updated:
                self.cfg.set(self.watched_chats, "watched_chats")
                await update.message.reply_text(f"{con.DONE} Cleared token filter - now showing all tokens for this " +
                                                ("topic" if thread_id else "chat"))
            else:
                await update.message.reply_text(f"{con.INFO} No token filter was active for this " +
                                                ("topic" if thread_id else "chat"))

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
                            await self.send_dex_notification(contract, function, arguments, tx_hash)
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

                            await self.send_dex_notification(contract, function, arguments, tx_hash)
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

    async def get_pair_tokens(self, pair_id):
        """Get token contracts for a pair by querying the blockchain directly"""
        if pair_id in self.pair_info_cache:
            return self.pair_info_cache[pair_id]

        try:
            xian = await self.get_xian()

            # Query the pair contract to get token0 and token1
            token0 = xian.get_state('con_pairs', 'pairs', str(pair_id), 'token0')
            token1 = xian.get_state('con_pairs', 'pairs', str(pair_id), 'token1')

            if token0 and token1:
                # Get symbols for both tokens
                symbol0 = await self.get_token_symbol(token0)
                symbol1 = await self.get_token_symbol(token1)

                pair_info = (token0, token1, symbol0, symbol1)
                # Cache the result
                self.pair_info_cache[pair_id] = pair_info
                return pair_info

        except Exception as e:
            self.log.debug(f"Error getting pair info for pair {pair_id}: {e}")

        return None

    async def get_pair_info(self, pair_id):
        """Get information about a trading pair - now uses blockchain lookup"""
        return await self.get_pair_tokens(pair_id)

    async def get_token_for_pair(self, pair_id, is_source=True):
        """Get token contract for a pair ID - now uses blockchain lookup"""
        pair_info = await self.get_pair_tokens(pair_id)

        if pair_info:
            token0, token1, _, _ = pair_info
            return token0 if is_source else token1

        return "Unknown"

    async def send_dex_notification(self, contract, function, arguments, tx_hash):
        """Send DEX event notification to all watched chats with improved formatting"""
        try:
            # Determine tokens involved in the swap
            src_token = arguments.get("src", "")
            pair_id = arguments.get("pair", 1)

            # Get pair information dynamically from blockchain
            pair_info = await self.get_pair_tokens(pair_id)

            if not pair_info:
                self.log.warning(f"Could not determine tokens for pair {pair_id}")
                return

            token0, token1, symbol0, symbol1 = pair_info
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
                # For other pairs, determine tokens based on src_token and pair info
                if src_token.upper() == "CURRENCY":
                    # Buying token0 or token1 with XIAN
                    # Determine which token is not XIAN/currency
                    if token0.upper() == "CURRENCY":
                        token_symbol = symbol1
                        got_token = symbol1
                    else:
                        token_symbol = symbol0
                        got_token = symbol0
                    spent_token = "XIAN"
                else:
                    # Skip if not buying with XIAN
                    src_symbol = await self.get_token_symbol(src_token)
                    self.log.info(f"Skipping transaction - not buying with XIAN (source: {src_symbol})")
                    return

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

            # Calculate price per token
            price_text = ""

            if amount_in_float > 0 and amount_out_float > 0:
                if pair_id == 1:  # XIAN-XUSDC pair
                    # Price = XUSDC spent / XIAN received (XUSDC per XIAN)
                    price_per_token = amount_in_float / amount_out_float
                    price_text = f"💲 Price: <code>{utl.format_float(price_per_token)}</code> XUSDC per XIAN"
                else:
                    # For other pairs, show XIAN spent per token received
                    price_per_token = amount_in_float / amount_out_float
                    price_text = f"💲 Price: <code>{utl.format_float(price_per_token)}</code> XIAN per {token_symbol}"

            # Determine the XIAN amount for minimum value checking
            if pair_id == 1:  # XIAN-XUSDC pair
                # For XIAN buys, check the XIAN amount being bought (amount_out)
                xian_amount = amount_out_float
            else:
                # For other pairs buying with XIAN, check the XIAN amount being spent (amount_in)
                xian_amount = amount_in_float

            # Get buyer address
            buyer_addr = arguments.get("to", "Unknown")
            short_address = buyer_addr[:4] + "..." + buyer_addr[-4:] if len(buyer_addr) > 10 else buyer_addr

            # Calculate USD value for emoji scaling
            usd_value = 0
            if pair_id == 1:  # XIAN-XUSDC pair
                # Use XUSDC amount as USD value
                usd_value = amount_in_float  # XUSDC spent to buy XIAN
            else:
                # For other pairs, estimate USD value using XIAN amount
                # You could fetch current XIAN/USD price here, or use a reasonable estimate
                # For now, using a simple estimation (adjust as needed)
                xian_usd_estimate = 0.045  # Estimate: $0.045 per XIAN (update this as needed)
                usd_value = xian_amount * xian_usd_estimate

            # Calculate emoji count: each 🟢 represents $5
            def calculate_emoji_count_usd(usd_amount, dollars_per_emoji=5, min_count=1, max_count=20):
                if usd_amount <= 0:
                    return min_count
                count = max(1, int(usd_amount / dollars_per_emoji))
                return min(max_count, count)

            # Dynamic emoji count based on USD value
            emoji_count = calculate_emoji_count_usd(usd_value)
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
            address_link = f"{explorer_url}/addresses/{buyer_addr}"

            # Build the message with price
            message = (
                f"<b>{title}</b>\n"
                f"{emoji_line}\n\n"
                f"{spent_text}\n"
                f"{got_text}\n"
            )

            # Add price information if available
            if price_text:
                message += f"{price_text}\n"

            message += f"\n👤 <a href='{address_link}'>Trader ({short_address})</a> / <a href='{tx_link}'>TX</a>"

            # Send to all watched chats
            if self.watched_chats is None:
                self.watched_chats = []

            # Send to all watched chats
            for chat_entry in self.watched_chats:
                try:
                    # Check if this is a chat_id or a dict with chat_id and thread_id
                    chat_id = None
                    thread_id = None
                    min_value = None
                    allowed_tokens = None

                    if isinstance(chat_entry, dict):
                        chat_id = chat_entry.get("chat_id")
                        thread_id = chat_entry.get("thread_id")
                        min_value = chat_entry.get("min_value")
                        allowed_tokens = chat_entry.get("allowed_tokens")
                    else:
                        chat_id = chat_entry

                    # Check minimum value threshold
                    if min_value is not None and xian_amount < min_value:
                        self.log.info(
                            f"Skipping notification for chat {chat_id} - "
                            f"trade amount {xian_amount} XIAN below minimum {min_value} XIAN")
                        continue

                    # Check token filtering
                    if allowed_tokens is not None and token_symbol not in allowed_tokens:
                        self.log.info(
                            f"Skipping notification for chat {chat_id} - "
                            f"{token_symbol} not in allowed tokens: {allowed_tokens}")
                        continue

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
