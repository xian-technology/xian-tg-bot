import websockets
import asyncio
import json
import gc

from plugin import TGBFPlugin
from functools import partial
from xian_py.encoding import decode_str
from typing import Callable


class Event(TGBFPlugin):
    # Key = Tx hash, Value = Callable - function to call
    execute = dict()
    event = str()

    async def init(self):
        self.event = self.cfg.get('event')
        asyncio.create_task(self.websocket_loop())

    async def websocket_loop(self):
        retry_attempts = 0
        max_retries = self.cfg.get('max_retries')
        base_wait_time = self.cfg.get('base_wait_time')

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
                        return  # Or handle the error appropriately

                    uri += '/websocket'

                async with websockets.connect(uri) as ws:
                    await self.on_open(ws)
                    try:
                        async for message in ws:
                            await self.on_message(ws, message)
                            # Reset retry attempts on successful message
                            retry_attempts = 0
                    except websockets.ConnectionClosed as e:
                        await self.on_close(ws, e.code, e.reason)
            except Exception as e:
                await self.on_error(e)
                gc.collect()

                retry_attempts += 1
                if retry_attempts > max_retries:
                    self.log.error(f'Max retries reached. Stopping websocket loop.')
                    break

                # Exponential backoff, cap at 60 seconds
                wait_secs = min(base_wait_time * (2 ** (retry_attempts - 1)), 60)
                self.log.info(f'Websocket reconnect after {wait_secs} seconds')
                await asyncio.sleep(wait_secs)

    async def on_message(self, ws, msg):
        self.log.info(f'Event {self.event}: {msg}')

        msg = json.loads(msg)

        if not msg['result']:
            return

        tx_hash_from_event = msg['result']['events']['tx.hash']

        try:
            for tx_hash in list(self.execute.keys()):
                if tx_hash in tx_hash_from_event:
                    data = msg['result']['data']['value']['TxResult']['result']['data']
                    decoded_data = json.loads(decode_str(data))
                    status = decoded_data['status']
                    result = decoded_data['result']

                    if self.execute[tx_hash]:
                        callback = self.execute[tx_hash]
                        success = True if status == 0 else False
                        result_data = ' ' if result == 'None' else result

                        if asyncio.iscoroutinefunction(callback):
                            # If it's an async function, await it
                            await callback(success=success, result=result_data)
                        else:
                            # If it's a regular function, just call it
                            callback(success=success, result=result_data)

                    del self.execute[tx_hash]
        except Exception as e:
            self.log.error(e)
            await self.notify(e)

    async def on_error(self, error):
        self.log.error(f'Websocket error: {error}')

    async def on_close(self, ws, status_code, msg):
        self.log.info(f'Websocket connection closed with code {status_code} and message {msg}')

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

    async def track_tx(self, tx_hash: str, function_to_call: Callable):
        self.execute[tx_hash] = function_to_call
