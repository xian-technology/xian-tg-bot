#!/usr/bin/env python3
"""Run a live local-node transaction smoke for the Telegram bot stack."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import websockets
from xian_py import XianAsync
from xian_py.wallet import Wallet

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from plg.event.event import parse_tx_event_message  # noqa: E402

DEFAULT_RPC_URL = "http://127.0.0.1:26657"
DEFAULT_FUNDED_PRIVATE_KEY = "1" * 64


def websocket_url(rpc_url: str) -> str:
    url = rpc_url.rstrip("/")
    if url.startswith("https://"):
        return f"wss://{url.removeprefix('https://')}/websocket"
    if url.startswith("http://"):
        return f"ws://{url.removeprefix('http://')}/websocket"
    raise ValueError(f"Unsupported RPC URL scheme: {rpc_url}")


def normalize_balance(value: Any) -> Decimal:
    return Decimal(str(value))


async def wait_for_tx_event(
    rpc_url: str,
    tx_hash_future: asyncio.Future[str],
    ready: asyncio.Event,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    subscribe_message = {
        "jsonrpc": "2.0",
        "method": "subscribe",
        "id": 0,
        "params": {"query": "tm.event='Tx'"},
    }
    ws_url = websocket_url(rpc_url)

    async with websockets.connect(ws_url, ping_interval=20, ping_timeout=30) as ws:
        await ws.send(json.dumps(subscribe_message))
        ready.set()
        tx_hash = (await tx_hash_future).upper()
        deadline = asyncio.get_running_loop().time() + timeout_seconds

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"Timed out waiting for Tx event for {tx_hash}")

            message = await asyncio.wait_for(ws.recv(), timeout=remaining)
            parsed = parse_tx_event_message(message)
            if parsed is None:
                continue

            event_hashes = [event_hash.upper() for event_hash in parsed.tx_hashes]
            if tx_hash not in event_hashes:
                continue

            return {
                "success": parsed.success,
                "result": parsed.result,
                "tx_hashes": parsed.tx_hashes,
            }


async def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    sender = Wallet(args.private_key)
    recipient = Wallet()
    tx_hash_future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    event_ready = asyncio.Event()
    event_task = asyncio.create_task(
        wait_for_tx_event(
            args.rpc_url,
            tx_hash_future,
            event_ready,
            timeout_seconds=args.timeout_seconds,
        )
    )

    await event_ready.wait()

    async with XianAsync(args.rpc_url, args.chain_id, sender) as client:
        await client.ensure_chain_id()
        chain_id = client.chain_id
        sender_before = normalize_balance(await client.get_balance(sender.public_key))
        recipient_before = normalize_balance(await client.get_balance(recipient.public_key))

        submission = await client.send(
            Decimal(args.amount),
            recipient.public_key,
            token="currency",
            mode="checktx",
            wait_for_tx=False,
        )
        if not submission.submitted or submission.accepted is not True or not submission.tx_hash:
            raise RuntimeError(f"Transaction was not accepted: {submission}")

        tx_hash_future.set_result(submission.tx_hash)
        receipt = await client.wait_for_tx(
            submission.tx_hash,
            timeout_seconds=args.timeout_seconds,
            poll_interval_seconds=0.25,
        )
        lookup = await client.get_tx(submission.tx_hash)
        event_result = await event_task

        recipient_after = normalize_balance(await client.get_balance(recipient.public_key))
        sender_after = normalize_balance(await client.get_balance(sender.public_key))

    amount = Decimal(args.amount)
    if not receipt.success:
        raise RuntimeError(f"wait_for_tx returned failed receipt: {receipt}")
    if not lookup.success:
        raise RuntimeError(f"get_tx returned failed receipt: {lookup}")
    if not event_result["success"]:
        raise RuntimeError(f"Tx event reported failure: {event_result}")
    if recipient_after - recipient_before != amount:
        raise RuntimeError(
            "Recipient balance did not increase by expected amount: "
            f"before={recipient_before} after={recipient_after} amount={amount}"
        )

    return {
        "ok": True,
        "chain_id": chain_id,
        "rpc_url": args.rpc_url,
        "tx_hash": submission.tx_hash,
        "sender": sender.public_key,
        "recipient": recipient.public_key,
        "amount": str(amount),
        "sender_balance_before": str(sender_before),
        "sender_balance_after": str(sender_after),
        "recipient_balance_before": str(recipient_before),
        "recipient_balance_after": str(recipient_after),
        "submitted": submission.submitted,
        "accepted": submission.accepted,
        "receipt_success": receipt.success,
        "lookup_success": lookup.success,
        "event_success": event_result["success"],
        "event_result": event_result["result"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send a real local Xian transaction and verify lookup plus Tx-event finality."
    )
    parser.add_argument("--rpc-url", default=DEFAULT_RPC_URL)
    parser.add_argument("--chain-id")
    parser.add_argument("--private-key", default=DEFAULT_FUNDED_PRIVATE_KEY)
    parser.add_argument("--amount", default="1")
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = asyncio.run(run_smoke(args))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
