"""Authenticated encryption of user wallet keys at rest.

Each private key is sealed with XChaCha20-Poly1305 (libsodium via PyNaCl) under
a 32-byte key from ``XIAN_BOT_WALLET_KEY``. The user id is authenticated as
associated data, so a ciphertext copied to another user's row does not open.
Stored form: ``xc1:`` followed by hex(nonce || ciphertext).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
from nacl.bindings import (
    crypto_aead_xchacha20poly1305_ietf_decrypt,
    crypto_aead_xchacha20poly1305_ietf_encrypt,
    crypto_aead_xchacha20poly1305_ietf_KEYBYTES,
    crypto_aead_xchacha20poly1305_ietf_NPUBBYTES,
)
from nacl.exceptions import CryptoError
from nacl.utils import random as random_bytes
from xian_py.wallet import Wallet

from credentials import WALLET_KEY, read_secret

PREFIX = "xc1:"
_AAD = b"xian-tg-bot/wallet/v1/"


class WalletKeyError(RuntimeError):
    """The wallet key is invalid or does not open a stored wallet."""


class WalletCipher:

    def __init__(self, key: bytes):
        if len(key) != crypto_aead_xchacha20poly1305_ietf_KEYBYTES:
            raise WalletKeyError("Wallet encryption key must be 32 bytes")
        self._key = key

    @classmethod
    def from_hex(cls, text: str) -> WalletCipher:
        try:
            return cls(bytes.fromhex(text))
        except ValueError as exc:
            raise WalletKeyError("Wallet encryption key must be 64 hex characters") from exc

    @staticmethod
    def is_encrypted(stored: str) -> bool:
        return stored.startswith(PREFIX)

    def encrypt(self, user_id: int, private_key: str) -> str:
        nonce = random_bytes(crypto_aead_xchacha20poly1305_ietf_NPUBBYTES)
        sealed = crypto_aead_xchacha20poly1305_ietf_encrypt(
            private_key.encode("ascii"), _aad(user_id), nonce, self._key,
        )
        return PREFIX + (nonce + sealed).hex()

    def decrypt(self, user_id: int, stored: str) -> str:
        if not self.is_encrypted(stored):
            raise WalletKeyError(f"Wallet of user {user_id} is not encrypted")
        try:
            blob = bytes.fromhex(stored.removeprefix(PREFIX))
            nonce = blob[:crypto_aead_xchacha20poly1305_ietf_NPUBBYTES]
            sealed = blob[crypto_aead_xchacha20poly1305_ietf_NPUBBYTES:]
            return crypto_aead_xchacha20poly1305_ietf_decrypt(
                sealed, _aad(user_id), nonce, self._key,
            ).decode("ascii")
        except (ValueError, CryptoError) as exc:
            raise WalletKeyError(f"Cannot open wallet of user {user_id} with the configured key") from exc


def _aad(user_id: int) -> bytes:
    return _AAD + str(int(user_id)).encode("ascii")


def load_wallet_cipher() -> WalletCipher | None:
    key = read_secret(WALLET_KEY)
    return WalletCipher.from_hex(key) if key is not None else None


async def encrypt_plaintext_wallets(db_path: Path, cipher: WalletCipher) -> int:
    """Encrypt legacy plaintext rows in one transaction; return how many changed.

    Every already-encrypted row must open with ``cipher``, so a wrong or rotated
    key is refused before anything is written.
    """
    if not await asyncio.to_thread(db_path.is_file):
        return 0

    async with aiosqlite.connect(db_path) as con:
        await con.execute("BEGIN IMMEDIATE")
        try:
            table = await (await con.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'wallets'"
            )).fetchone()
            rows = await (await con.execute("SELECT user_id, privkey FROM wallets")).fetchall() if table else []
            updates = []
            for user_id, stored in rows:
                if WalletCipher.is_encrypted(stored):
                    cipher.decrypt(user_id, stored)
                elif Wallet.is_valid_key(stored):
                    updates.append((cipher.encrypt(user_id, stored), user_id))
                else:
                    raise WalletKeyError(f"Wallet of user {user_id} holds no valid private key")
            await con.executemany("UPDATE wallets SET privkey = ? WHERE user_id = ?", updates)
            await con.commit()
        except BaseException:
            await con.rollback()
            raise
    return len(updates)
