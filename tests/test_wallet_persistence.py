"""Wallet identity is the durable unique row, including concurrent first use."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from xian_py.wallet import Wallet

import constants
from config import ConfigManager
from plugin import TGBFPlugin
from wallet_crypto import WalletCipher


@pytest.fixture
def db_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TGBFPlugin:
    monkeypatch.setattr(constants, "DIR_DAT", tmp_path / "data")
    bot = SimpleNamespace(cfg=ConfigManager(tmp_path / "global.json"), wallet_cipher=WalletCipher.from_hex("11" * 32))
    plugin = TGBFPlugin(bot)
    plugin.notify = AsyncMock()
    return plugin


async def create_wallet_table(plugin: TGBFPlugin) -> None:
    statement = await plugin.get_resource_global("create_wallets.sql")
    assert (await plugin.exec_sql_global(statement))["success"]


async def test_concurrent_first_use_returns_one_persisted_wallet(
    db_plugin: TGBFPlugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_wallet_table(db_plugin)
    original = db_plugin.exec_sql_global
    initial_reads = 0
    both_read = asyncio.Event()

    async def synchronized(sql: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal initial_reads
        result = await original(sql, *args, **kwargs)
        if sql.lstrip().upper().startswith("SELECT") and initial_reads < 2:
            initial_reads += 1
            if initial_reads == 2:
                both_read.set()
            await asyncio.wait_for(both_read.wait(), 3)
        return result

    monkeypatch.setattr(db_plugin, "exec_sql_global", synchronized)
    first, second = await asyncio.gather(db_plugin.get_wallet(23), db_plugin.get_wallet(23))
    rows = await original("SELECT user_id, address, privkey FROM wallets")
    assert rows["success"] and len(rows["data"]) == 1
    persisted = Wallet(db_plugin.tgb.wallet_cipher.decrypt(23, rows["data"][0][2]))
    assert first.public_key == second.public_key == persisted.public_key
    assert first.private_key == second.private_key == persisted.private_key
    assert (await db_plugin.get_wallet(23)).private_key == persisted.private_key
    db_plugin.notify.assert_not_awaited()


async def test_wallet_creation_failure_never_returns_an_unpersisted_identity(
    db_plugin: TGBFPlugin,
) -> None:
    await create_wallet_table(db_plugin)
    # A real SQLite write rejection, rather than a stubbed return value.
    assert (
        await db_plugin.exec_sql_global(
            "CREATE TRIGGER reject_wallet BEFORE INSERT ON wallets "
            "BEGIN SELECT RAISE(ABORT, 'test write rejection'); END"
        )
    )["success"]
    with pytest.raises(RuntimeError, match="[Ww]allet"):
        await db_plugin.get_wallet(23)
    assert (await db_plugin.exec_sql_global("SELECT * FROM wallets"))["data"] == []


async def test_wallet_lookup_failure_never_attempts_to_create_identity(
    db_plugin: TGBFPlugin,
) -> None:
    # Missing table is a real database error, not evidence that a user is new.
    with pytest.raises(RuntimeError, match="[Ww]allet"):
        await db_plugin.get_wallet(23)
    assert not await db_plugin.table_exists_global("wallets")


async def test_sql_returning_rows_are_drained_before_commit(db_plugin: TGBFPlugin) -> None:
    assert (await db_plugin.exec_sql_global("CREATE TABLE sample(value INTEGER)"))["success"]
    result = await db_plugin.exec_sql_global("INSERT INTO sample VALUES (23) RETURNING value")
    assert result == {"success": True, "data": [(23,)]}
    assert (await db_plugin.exec_sql_global("SELECT value FROM sample"))["data"] == [(23,)]
    db_plugin.notify.assert_not_awaited()


async def test_wallet_is_not_returned_if_inserted_row_is_removed(db_plugin: TGBFPlugin) -> None:
    await create_wallet_table(db_plugin)
    assert (
        await db_plugin.exec_sql_global(
            "CREATE TRIGGER remove_wallet AFTER INSERT ON wallets "
            "BEGIN DELETE FROM wallets WHERE user_id = NEW.user_id; END"
        )
    )["success"]
    with pytest.raises(RuntimeError, match="[Ww]allet lookup failed after creation"):
        await db_plugin.get_wallet(23)
    assert (await db_plugin.exec_sql_global("SELECT * FROM wallets"))["data"] == []


async def test_sql_constraint_failure_rolls_back_entire_statement(db_plugin: TGBFPlugin) -> None:
    assert (await db_plugin.exec_sql_global("CREATE TABLE sample(value INTEGER UNIQUE)"))["success"]
    assert (await db_plugin.exec_sql_global("INSERT INTO sample VALUES (23)"))["success"]
    rejected = await db_plugin.exec_sql_global("INSERT INTO sample VALUES (7), (7)")
    assert rejected["success"] is False
    assert (await db_plugin.exec_sql_global("SELECT value FROM sample"))["data"] == [(23,)]
