import asyncio
import json
from base64 import b64encode
from types import SimpleNamespace

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


def test_is_websocket_open_supports_legacy_closed_attribute() -> None:
    assert is_websocket_open(SimpleNamespace(closed=False)) is True
    assert is_websocket_open(SimpleNamespace(closed=True)) is False


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
