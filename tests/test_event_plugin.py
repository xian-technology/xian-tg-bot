import asyncio
import json
from base64 import b64encode
from types import SimpleNamespace
from unittest.mock import AsyncMock

import websockets
from websockets.asyncio.server import ServerConnection

from plg.event.event import Event, is_websocket_open, parse_tx_event_message


def _encode_result(status: int, result: str = "None") -> str:
    return b64encode(json.dumps({"status": status, "result": result}).encode()).decode()


def _event_message(tx_hash: str | list[str], *, status: int = 0, result: str = "None") -> str:
    return json.dumps(
        {
            "result": {
                "events": {"tx.hash": tx_hash},
                "data": {
                    "value": {
                        "TxResult": {
                            "result": {
                                "data": _encode_result(status, result),
                            }
                        }
                    }
                },
            }
        }
    )


class DummyLog:
    def info(self, msg: str) -> None:
        pass

    def debug(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


def test_parse_tx_event_message_handles_list_hash_and_success() -> None:
    parsed = parse_tx_event_message(_event_message(["ABC123"]))

    assert parsed is not None
    assert parsed.tx_hashes == ["ABC123"]
    assert parsed.success is True
    assert parsed.result == " "


def test_parse_tx_event_message_handles_single_hash_and_failure() -> None:
    parsed = parse_tx_event_message(
        _event_message("abc123", status=1, result="insufficient balance")
    )

    assert parsed is not None
    assert parsed.tx_hashes == ["abc123"]
    assert parsed.success is False
    assert parsed.result == "insufficient balance"


def test_is_websocket_open_supports_websockets_16_state_attribute() -> None:
    assert is_websocket_open(SimpleNamespace(state=SimpleNamespace(name="OPEN"))) is True
    assert is_websocket_open(SimpleNamespace(state=SimpleNamespace(name="CLOSED"))) is False


async def test_event_on_message_resolves_waiting_future_case_insensitively() -> None:
    event = Event.__new__(Event)
    event.execute = {"ABC123": None}
    event.futures = {"ABC123": asyncio.get_running_loop().create_future()}
    event.pending_tx = {}
    event.event = "Tx"
    event.log = DummyLog()

    await event.on_message(None, _event_message("abc123"))

    assert event.futures == {}
    assert event.execute == {}


def test_subscription_ack_is_not_a_transaction() -> None:
    assert parse_tx_event_message('{"id": 0, "result": {}}') is None
    assert parse_tx_event_message('{"id": 0, "result": {"query": "tm.event=Tx"}}') is None


async def test_health_check_waits_for_pong() -> None:
    event = Event.__new__(Event)
    event.is_connected = True
    pong = asyncio.get_running_loop().create_future()
    event.ws = SimpleNamespace(state=SimpleNamespace(name="OPEN"), ping=AsyncMock(return_value=pong))
    event.log = DummyLog()
    task = asyncio.create_task(event.check_connection(None))
    await asyncio.sleep(0)
    assert not task.done()
    pong.set_result(0.1)
    await task


async def test_current_websocket_connects_and_resolves_transaction() -> None:
    from config import ConfigManager
    from main import TelegramBot

    subscribed = asyncio.Event()
    release = asyncio.Event()

    async def handler(ws: ServerConnection) -> None:
        request = json.loads(await ws.recv())
        assert request["method"] == "subscribe"
        await ws.send('{"id": 0, "result": {}}')
        subscribed.set()
        await release.wait()
        await ws.send(_event_message("abc123"))
        await ws.wait_closed()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        bot = TelegramBot()
        bot.cfg = ConfigManager("cfg/global.json")
        event = Event(bot)
        event._cfg = SimpleNamespace(get=lambda key, default=None: {
            "ws_masternode": f"ws://127.0.0.1:{port}",
        }.get(key, default))
        event.event = "Tx"
        event.ws_task = asyncio.create_task(event.websocket_loop())
        try:
            await asyncio.wait_for(subscribed.wait(), 2)
            waiter = asyncio.create_task(event.track_tx("ABC123", wait=True, timeout=2))
            await asyncio.sleep(0)
            release.set()
            assert await waiter == (True, " ")
        finally:
            release.set()
            await event.cleanup()
