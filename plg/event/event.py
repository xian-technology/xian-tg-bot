import websockets
import asyncio
import json
import gc
import time

from plugin import TGBFPlugin
from xian_py.encoding import decode_str
from typing import Callable, Dict, Tuple, Optional


class Event(TGBFPlugin):
    # Key = Tx hash, Value = Callable - function to call
    execute = dict()
    # Store futures for transaction waiting
    futures: Dict[str, asyncio.Future] = dict()
    # Store pending transactions during reconnection
    pending_tx: Dict[str, Tuple[str, Optional[Callable], bool, int]] = dict()
    event = str()
    # Connection status
    is_connected = False
    last_message = 0
    ws_task = None
    ws = None

    async def init(self):
        self.event = self.cfg.get('event')
        self.ws_task = asyncio.create_task(self.websocket_loop())
        # Start health check
        self.run_repeating(self.check_connection, interval=30)

    async def cleanup(self):
        if self.ws_task:
            self.ws_task.cancel()

    async def check_connection(self, context):
        """Actively check connection health"""
        if self.is_connected and self.ws and not self.ws.closed:
            try:
                # Send a ping frame to verify connection
                ping_task = asyncio.create_task(self.ws.ping())
                await asyncio.wait_for(ping_task, timeout=5.0)
                self.log.debug("Health check: Connection is healthy")
            except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
                self.log.warning("Health check: Connection failed, forcing reconnect...")
                self.is_connected = False
                if self.ws_task:
                    self.ws_task.cancel()
                self.ws_task = asyncio.create_task(self.websocket_loop())
        elif self.is_connected:
            self.log.warning("Health check: Connection already closed, pending reconnect")
            self.is_connected = False

    async def websocket_loop(self):
        retry_attempts = 0
        max_retries = self.cfg.get('max_retries', 10)
        base_wait_time = self.cfg.get('base_wait_time', 2)

        while True:
            try:
                self.log.info(f'Initiating websocket connection...')

                if self.cfg.get('ws_masternode'):
                    uri = self.cfg.get('ws_masternode')
                else:
                    uri = self.cfg_global.get('xian', 'node')

                    if uri.startswith('https://'):
                        uri = uri.replace('https://', 'wss://')
                    elif uri.startswith('http://'):
                        uri = uri.replace('http://', 'ws://')
                    else:
                        self.log.error("Unsupported URI scheme in node URL.")
                        return

                    uri += '/websocket'

                self.log.info(f'Connecting to {uri}')

                # Store the websocket connection
                self.ws = await websockets.connect(
                    uri,
                    ping_interval=20,
                    ping_timeout=30,
                    close_timeout=5
                )

                self.is_connected = True
                self.last_message = time.time()
                await self.on_open(self.ws)

                # Resubscribe to any pending transactions
                if self.pending_tx:
                    self.log.info(f"Resubscribing to {len(self.pending_tx)} pending transactions")
                    for tx_hash, (tx_hash, func, wait, timeout) in self.pending_tx.items():
                        await self.track_tx(tx_hash, func, wait, timeout)
                    self.pending_tx.clear()

                # Reset retry attempts on successful connection
                retry_attempts = 0

                try:
                    async for message in self.ws:
                        self.last_message = time.time()
                        await self.on_message(self.ws, message)
                except websockets.ConnectionClosed as e:
                    self.is_connected = False
                    await self.on_close(self.ws, e.code, e.reason)
                    # Store pending transactions for resubscription
                    for tx_hash, func in self.execute.items():
                        if tx_hash not in self.pending_tx:
                            self.pending_tx[tx_hash] = (tx_hash, func, False, 30)
                finally:
                    self.is_connected = False
            except Exception as e:
                self.is_connected = False
                await self.on_error(e)
                gc.collect()

                retry_attempts += 1
                if retry_attempts > max_retries:
                    self.log.error(f'Max retries reached. Stopping websocket loop.')
                    # Fail all pending transactions
                    await self.fail_all_pending("Connection to node lost")
                    break

                # Exponential backoff, cap at 60 seconds
                wait_secs = min(base_wait_time * (2 ** (retry_attempts - 1)), 60)
                self.log.info(f'Websocket reconnect after {wait_secs} seconds')
                await asyncio.sleep(wait_secs)

    async def fail_all_pending(self, reason="Connection failed"):
        """Fail all pending transactions with the given reason"""
        for tx_hash in list(self.execute.keys()):
            callback = self.execute[tx_hash]
            if callback:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(success=False, result=reason)
                    else:
                        callback(success=False, result=reason)
                except Exception as e:
                    self.log.error(f"Error calling callback for {tx_hash}: {e}")
            del self.execute[tx_hash]

        for tx_hash, future in list(self.futures.items()):
            if not future.done():
                future.set_result((False, reason))
            del self.futures[tx_hash]

    async def on_message(self, ws, msg):
        self.log.info(f'Event {self.event}: {msg}')

        try:
            msg_json = json.loads(msg)

            if not msg_json.get('result'):
                return

            # Get transaction hash from event - this could be a string or list
            tx_hash_from_event = msg_json['result']['events'].get('tx.hash', [])

            # Convert to list if it's not already
            if not isinstance(tx_hash_from_event, list):
                tx_hash_from_event = [tx_hash_from_event]

            self.log.debug(f'Transaction hashes in event: {tx_hash_from_event}')
            self.log.debug(f'Tracked transactions: {list(self.execute.keys())}')

            # Normalize all hashes to uppercase for comparison
            tx_hash_from_event_upper = [h.upper() for h in tx_hash_from_event]

            for tx_hash in list(self.execute.keys()):
                # Normalize to uppercase for comparison
                tx_hash_upper = tx_hash.upper()

                if tx_hash_upper in tx_hash_from_event_upper:
                    self.log.debug(f'Found matching transaction: {tx_hash}')

                    try:
                        data = msg_json['result']['data']['value']['TxResult']['result']['data']
                        decoded_data = json.loads(decode_str(data))
                        status = decoded_data.get('status')
                        result = decoded_data.get('result', 'None')

                        success = True if status == 0 else False
                        result_data = ' ' if result == 'None' else result

                        self.log.debug(f'Transaction result: success={success}, result={result_data}')

                        # If there's a callback function, execute it
                        if self.execute[tx_hash]:
                            callback = self.execute[tx_hash]

                            if asyncio.iscoroutinefunction(callback):
                                # If it's an async function, await it
                                self.log.debug(f'Executing async callback for {tx_hash}')
                                await callback(success=success, result=result_data)
                            else:
                                # If it's a regular function, just call it
                                self.log.debug(f'Executing sync callback for {tx_hash}')
                                callback(success=success, result=result_data)

                        # If there's a future for this transaction, set its result
                        if tx_hash in self.futures:
                            future = self.futures[tx_hash]
                            if not future.done():
                                self.log.debug(f'Setting future result for {tx_hash}')
                                future.set_result((success, result_data))
                            del self.futures[tx_hash]

                        del self.execute[tx_hash]
                    except Exception as e:
                        self.log.error(f'Error processing transaction {tx_hash}: {e}')
        except Exception as e:
            self.log.error(f'Error in on_message: {e}')
            await self.notify(e)

    async def on_error(self, error):
        self.log.error(f'Websocket error: {error}')

    async def on_close(self, ws, status_code, msg):
        self.log.info(f'Websocket connection closed with code {status_code} and message {msg}')
        self.is_connected = False

    async def on_open(self, ws):
        self.log.info("Websocket connection opened")

        # Sending subscription message
        subscribe_message = {
            "jsonrpc": "2.0",
            "method": "subscribe",
            "id": 0,
            "params": {
                "query": f"tm.event='{self.event}'"
            }
        }

        await ws.send(json.dumps(subscribe_message))
        self.log.info("Sent subscription message")

    async def track_tx(self,
                       tx_hash: str,
                       function_to_call: Optional[Callable] = None,
                       wait: bool = False,
                       timeout: int = 30) -> Optional[Tuple[bool, str]]:
        """
        Register a callback function for a transaction and optionally wait for confirmation.

        Args:
            tx_hash: The transaction hash to track
            function_to_call: Optional callback function to call when transaction is confirmed
            wait: Whether to wait for transaction confirmation
            timeout: Maximum time to wait in seconds (only used if wait=True)

        Returns:
            If wait=True: Tuple of (success, result)
            If wait=False: None

        Raises:
            asyncio.TimeoutError: If wait=True and the transaction is not confirmed within the timeout
        """
        # If we're not connected, store for later resubscription
        if not self.is_connected:
            self.log.warning(f"Not connected! Adding tx {tx_hash} to pending list")
            self.pending_tx[tx_hash] = (tx_hash, function_to_call, wait, timeout)

            if wait:
                return (False, "Node connection unavailable")
            return None

        # Register the callback if provided
        if function_to_call:
            self.execute[tx_hash] = function_to_call
        elif wait:
            # If only waiting without a callback, create a dummy callback
            self.execute[tx_hash] = lambda success, result: None

        # If wait is requested, create a future and wait for it
        if wait:
            future = asyncio.Future()
            self.futures[tx_hash] = future

            try:
                self.log.debug(f'Waiting for transaction {tx_hash} with timeout {timeout}s')
                result = await asyncio.wait_for(future, timeout)
                self.log.debug(f'Wait completed for {tx_hash} with result: {result}')
                return result
            except asyncio.TimeoutError:
                # Remove the future if timeout occurs
                self.log.debug(f'Timeout waiting for transaction {tx_hash}')
                if tx_hash in self.futures:
                    del self.futures[tx_hash]
                if tx_hash in self.execute:
                    del self.execute[tx_hash]
                raise
            except Exception as e:
                self.log.error(f'Error waiting for transaction {tx_hash}: {e}')
                if tx_hash in self.futures:
                    del self.futures[tx_hash]
                if tx_hash in self.execute:
                    del self.execute[tx_hash]
                raise

        return None

    def is_node_connected(self):
        """Return current connection status"""
        return self.is_connected

    async def force_reconnect(self):
        """Force a reconnection to the websocket"""
        self.log.info("Forcing reconnection to websocket")
        self.is_connected = False
        if self.ws:
            await self.ws.close()
        if self.ws_task:
            self.ws_task.cancel()
        self.ws_task = asyncio.create_task(self.websocket_loop())