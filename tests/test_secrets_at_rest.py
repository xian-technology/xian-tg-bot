"""Signing keys never rest in plaintext: user wallets are encrypted, faucet keys come from secrets."""

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from xian_py.wallet import Wallet

import constants
from config import ConfigManager
from credentials import SecretError, read_secret
from plugin import TGBFPlugin
from wallet_crypto import WalletCipher, WalletKeyError, encrypt_plaintext_wallets

ROOT = Path(__file__).parents[1]
KEY_HEX = "11" * 32
OTHER_KEY_HEX = "22" * 32
PLAIN_KEY = "a" * 64


# --- secret sources --------------------------------------------------------

def test_secret_is_read_from_variable_or_file(tmp_path: Path) -> None:
    secret_file = tmp_path / "wallet.key"
    secret_file.write_text(KEY_HEX + "\n")
    assert read_secret("BOT_KEY", {"BOT_KEY": KEY_HEX}) == KEY_HEX
    assert read_secret("BOT_KEY", {"BOT_KEY_FILE": str(secret_file)}) == KEY_HEX
    assert read_secret("BOT_KEY", {}) is None


def test_secret_sources_are_unambiguous_and_must_exist(tmp_path: Path) -> None:
    with pytest.raises(SecretError, match="both"):
        read_secret("BOT_KEY", {"BOT_KEY": KEY_HEX, "BOT_KEY_FILE": str(tmp_path / "x")})
    with pytest.raises(SecretError, match="BOT_KEY_FILE"):
        read_secret("BOT_KEY", {"BOT_KEY_FILE": str(tmp_path / "missing")})
    empty = tmp_path / "empty"
    empty.write_text("\n")
    with pytest.raises(SecretError, match="empty"):
        read_secret("BOT_KEY", {"BOT_KEY_FILE": str(empty)})


# --- wallet encryption ----------------------------------------------------

def test_wallet_ciphertext_is_authenticated_and_bound_to_its_user() -> None:
    cipher = WalletCipher.from_hex(KEY_HEX)
    stored = cipher.encrypt(7, PLAIN_KEY)
    assert PLAIN_KEY not in stored and WalletCipher.is_encrypted(stored)
    assert stored != cipher.encrypt(7, PLAIN_KEY)  # fresh nonce per encryption
    assert cipher.decrypt(7, stored) == PLAIN_KEY
    with pytest.raises(WalletKeyError):
        cipher.decrypt(8, stored)  # a row copied to another user does not open
    with pytest.raises(WalletKeyError):
        WalletCipher.from_hex(OTHER_KEY_HEX).decrypt(7, stored)
    tampered = stored[:-2] + ("00" if stored[-2:] != "00" else "11")
    with pytest.raises(WalletKeyError):
        cipher.decrypt(7, tampered)
    with pytest.raises(WalletKeyError, match="not encrypted"):
        cipher.decrypt(7, PLAIN_KEY)  # plaintext rows are never accepted silently


@pytest.mark.parametrize("bad", ["", "11" * 31, "zz" * 32, "11" * 33])
def test_wallet_key_must_be_32_hex_bytes(bad: str) -> None:
    with pytest.raises(WalletKeyError):
        WalletCipher.from_hex(bad)


@pytest.fixture
def db_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TGBFPlugin:
    monkeypatch.setattr(constants, "DIR_DAT", tmp_path / "data")
    bot = SimpleNamespace(cfg=ConfigManager(tmp_path / "global.json"), wallet_cipher=WalletCipher.from_hex(KEY_HEX))
    plugin = TGBFPlugin(bot)
    plugin.notify = AsyncMock()
    return plugin


async def raw_wallet_rows(plugin: TGBFPlugin) -> list[tuple]:
    return (await plugin.exec_sql_global("SELECT user_id, address, privkey FROM wallets"))["data"]


async def test_new_wallet_is_stored_encrypted_and_reopens(db_plugin: TGBFPlugin) -> None:
    assert (await db_plugin.exec_sql_global(await db_plugin.get_resource_global("create_wallets.sql")))["success"]
    wallet = await db_plugin.get_wallet(23)
    [(user_id, address, stored)] = await raw_wallet_rows(db_plugin)
    assert (user_id, address) == (23, wallet.public_key)
    assert wallet.private_key not in stored and WalletCipher.is_encrypted(stored)
    assert (await db_plugin.get_wallet(23)).private_key == wallet.private_key


async def test_wallet_access_fails_closed_without_key(db_plugin: TGBFPlugin) -> None:
    assert (await db_plugin.exec_sql_global(await db_plugin.get_resource_global("create_wallets.sql")))["success"]
    db_plugin.tgb.wallet_cipher = None
    with pytest.raises(RuntimeError, match="[Ww]allet encryption key"):
        await db_plugin.get_wallet(23)
    assert await raw_wallet_rows(db_plugin) == []


def legacy_db(path: Path, rows: list[tuple[int, str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute((ROOT / "res" / "create_wallets.sql").read_text())
        conn.executemany("INSERT INTO wallets (user_id, address, privkey) VALUES (?, ?, ?)", rows)
    conn.close()


def stored_keys(path: Path) -> dict[int, str]:
    with sqlite3.connect(path) as conn:
        rows = dict(conn.execute("SELECT user_id, privkey FROM wallets").fetchall())
    conn.close()
    return rows


async def test_plaintext_rows_are_migrated_once(tmp_path: Path) -> None:
    cipher = WalletCipher.from_hex(KEY_HEX)
    first, second = Wallet("1" * 64), Wallet("2" * 64)
    db = tmp_path / "global.db"
    legacy_db(db, [
        (1, first.public_key, first.private_key),
        (2, second.public_key, cipher.encrypt(2, second.private_key)),
    ])

    assert await encrypt_plaintext_wallets(db, cipher) == 1
    migrated = stored_keys(db)
    assert all(WalletCipher.is_encrypted(value) for value in migrated.values())
    assert cipher.decrypt(1, migrated[1]) == first.private_key
    assert cipher.decrypt(2, migrated[2]) == second.private_key
    assert await encrypt_plaintext_wallets(db, cipher) == 0
    assert stored_keys(db) == migrated


async def test_migration_refuses_a_key_that_does_not_open_existing_rows(tmp_path: Path) -> None:
    first, second = Wallet("1" * 64), Wallet("2" * 64)
    db = tmp_path / "global.db"
    legacy_db(db, [
        (1, first.public_key, first.private_key),
        (2, second.public_key, WalletCipher.from_hex(KEY_HEX).encrypt(2, second.private_key)),
    ])
    before = stored_keys(db)
    with pytest.raises(WalletKeyError):
        await encrypt_plaintext_wallets(db, WalletCipher.from_hex(OTHER_KEY_HEX))
    assert stored_keys(db) == before


async def test_migration_rejects_rows_that_are_not_private_keys(tmp_path: Path) -> None:
    db = tmp_path / "global.db"
    legacy_db(db, [(1, "addr", "not-a-key")])
    with pytest.raises(WalletKeyError, match="user 1"):
        await encrypt_plaintext_wallets(db, WalletCipher.from_hex(KEY_HEX))
    assert stored_keys(db) == {1: "not-a-key"}


async def test_migration_without_database_is_a_no_op(tmp_path: Path) -> None:
    assert await encrypt_plaintext_wallets(tmp_path / "missing.db", WalletCipher.from_hex(KEY_HEX)) == 0
    assert not (tmp_path / "missing.db").exists()


async def test_bot_refuses_to_start_without_wallet_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from main import TelegramBot

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("XIAN_BOT_WALLET_KEY", raising=False)
    monkeypatch.delenv("XIAN_BOT_WALLET_KEY_FILE", raising=False)
    bot = TelegramBot()
    bot.load_plugins = AsyncMock()
    await bot.run(ConfigManager(tmp_path / "global.json"), "123:token")
    bot.load_plugins.assert_not_awaited()


async def test_bot_startup_encrypts_existing_wallets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from main import TelegramBot

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XIAN_BOT_WALLET_KEY", KEY_HEX)
    wallet = Wallet("1" * 64)
    db = tmp_path / constants.DIR_DAT / constants.FILE_DAT
    legacy_db(db, [(1, wallet.public_key, wallet.private_key)])
    bot = TelegramBot()
    assert await bot.prepare_wallet_storage()
    assert WalletCipher.from_hex(KEY_HEX).decrypt(1, stored_keys(db)[1]) == wallet.private_key


# --- backups --------------------------------------------------------------

def test_backup_never_includes_configured_secret_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from plg.backup.backup import Backup

    monkeypatch.chdir(tmp_path)
    wallet_key = tmp_path / "secrets" / "wallet.key"
    faucet_key = tmp_path / "faucet.key"
    monkeypatch.setenv("XIAN_BOT_WALLET_KEY_FILE", str(wallet_key))
    monkeypatch.setenv("XIAN_BOT_FAUCET_PRIVKEY_FILE", "faucet.key")
    plugin = Backup.__new__(Backup)
    assert plugin.should_exclude(str(wallet_key), wallet_key.name)
    assert plugin.should_exclude(str(Path("faucet.key")), faucet_key.name)
    assert not plugin.should_exclude(str(tmp_path / "dat" / "global.db"), "global.db")


def test_backup_exclusions_ignore_directories_above_the_backup_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A bot checked out under a directory called e.g. "tmp" or "log" (such as
    # /tmp/... on Linux) must still back up its own files; only directories
    # inside the backed-up tree are excluded by name.
    from plg.backup.backup import Backup

    bot_dir = tmp_path / "tmp" / "log" / "bot"
    monkeypatch.chdir(tmp_path)
    plugin = Backup.__new__(Backup)
    assert not plugin.should_exclude(str(bot_dir / "dat" / "global.db"), "global.db")
    assert not plugin.should_exclude(str(bot_dir / "dat"), "dat")
    assert plugin.should_exclude(str(bot_dir / "log"), "log")
    assert plugin.should_exclude(str(bot_dir / "node_modules"), "node_modules")
    assert plugin.should_exclude(str(bot_dir / ".env"), ".env")
    assert plugin.should_exclude(str(bot_dir / "bck" / "20260101.zip"), "20260101.zip")
    assert plugin.should_exclude(str(bot_dir / "main.pyc"), "main.pyc")


# --- faucet key -----------------------------------------------------------

def test_faucet_config_holds_no_key_and_no_network_id() -> None:
    cfg = json.loads((ROOT / "plg" / "testnet" / "cfg" / "testnet.json").read_text())
    assert "privkey" not in cfg
    assert cfg.get("chain_id") is None


async def test_faucet_signs_with_key_from_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    from main import TelegramBot
    from plg.testnet.testnet import Testnet

    plugin = Testnet.__new__(Testnet)
    plugin._name = "testnet"
    plugin._tgb = TelegramBot()
    plugin._cfg = SimpleNamespace(get=lambda key: {"testnet_node": "http://127.0.0.1:26657"}.get(key))
    plugin.get_xian = AsyncMock(return_value=SimpleNamespace(chain_id="local"))
    faucet = Wallet("3" * 64)
    monkeypatch.setenv("XIAN_BOT_FAUCET_PRIVKEY", faucet.private_key)
    await plugin.get_testnet_instance()
    assert plugin.get_xian.await_args.args[2].public_key == faucet.public_key


async def test_claim_without_faucet_key_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from main import TelegramBot
    from plg.testnet.testnet import Testnet

    monkeypatch.delenv("XIAN_BOT_FAUCET_PRIVKEY", raising=False)
    monkeypatch.delenv("XIAN_BOT_FAUCET_PRIVKEY_FILE", raising=False)
    plugin = Testnet.__new__(Testnet)
    plugin._name = "testnet"
    plugin._tgb = TelegramBot()
    plugin._cfg = SimpleNamespace(get=lambda key: None)
    plugin.notify = AsyncMock()
    plugin.get_xian = AsyncMock()
    message = SimpleNamespace(edit_text=AsyncMock())
    update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock(return_value=message)))
    await plugin.handle_claim(update, Wallet("1" * 64))
    plugin.get_xian.assert_not_awaited()
    assert "not configured" in message.edit_text.call_args.args[0]


async def test_bot_refuses_to_start_with_a_key_that_does_not_open_wallets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from main import TelegramBot

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XIAN_BOT_WALLET_KEY", OTHER_KEY_HEX)
    wallet = Wallet("1" * 64)
    db = tmp_path / constants.DIR_DAT / constants.FILE_DAT
    legacy_db(db, [(1, wallet.public_key, WalletCipher.from_hex(KEY_HEX).encrypt(1, wallet.private_key))])
    bot = TelegramBot()
    assert not await bot.prepare_wallet_storage()
    assert bot.wallet_cipher is None
