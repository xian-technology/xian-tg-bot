"""Load a user's existing address from explicitly configured encrypted custody."""

import os
from pathlib import Path

from xian_py.signers import load_signing_wallet


def user_wallet(address: str, private_key: str | None, chain_id: str | None):
    directory = os.environ.get("XIAN_BOT_ACTIVE_BACKUP_DIR")
    backup = None
    if directory:
        if len(address) != 64 or any(c not in "0123456789abcdef" for c in address):
            raise ValueError("invalid custody address")
        backup = Path(directory) / (address + ".json")
    return load_signing_wallet(
        private_key=None if backup else private_key,
        active_backup_file=backup,
        policy_version=os.environ.get("XIAN_ACCOUNT_POLICY_VERSION") if backup else None,
        backup_password=os.environ.get("XIAN_BOT_BACKUP_PASSWORD") if backup else None,
        expected_address=address,
        chain_id=chain_id,
    )
