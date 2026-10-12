from pathlib import Path

import pytest
from xian_py.hybrid import AccountTransitionDraft
from xian_py.wallet import Wallet


@pytest.fixture
def custody(tmp_path: Path) -> tuple[Wallet, AccountTransitionDraft, Path]:
    legacy = Wallet()
    draft = AccountTransitionDraft("local-test", legacy.public_key, 0)
    path = tmp_path / "active.json"
    path.write_text(draft.export_backup("active", "backup test password"))
    return legacy, draft, path


def test_user_custody_preserves_database_address(
    custody: tuple[Wallet, AccountTransitionDraft, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from custody import user_wallet

    legacy, draft, path = custody
    monkeypatch.setenv("XIAN_BOT_ACTIVE_BACKUP_DIR", str(path.parent))
    monkeypatch.setenv("XIAN_BOT_BACKUP_PASSWORD", "backup test password")
    target = path.with_name(legacy.public_key + ".json")
    path.rename(target)
    assert (
        user_wallet(legacy.public_key, legacy.private_key, "local-test").public_key
        == legacy.public_key
    )
    target.write_text(draft.export_backup("recovery", "backup test password"))
    with pytest.raises(ValueError, match="cannot spend"):
        user_wallet(legacy.public_key, legacy.private_key, "local-test")
    with pytest.raises(ValueError, match="invalid custody address"):
        user_wallet("../../bad", legacy.private_key, "local-test")


async def test_hybrid_address_view_does_not_export_legacy_seed(
    custody: tuple[Wallet, AccountTransitionDraft, Path],
) -> None:
    import inspect
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from xian_py.hybrid import HybridWallet

    from plg.address.address import Address
    legacy, draft, path = custody
    handler = SimpleNamespace(get_wallet=AsyncMock(return_value=HybridWallet(draft.active)),
                              is_private=lambda _: True)
    message = SimpleNamespace(reply_photo=AsyncMock())
    update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=1))
    context = SimpleNamespace(args=[], user_data={"privkey": "old secret"})
    await inspect.unwrap(Address.address_callback)(handler, update, context)
    assert context.user_data == {}
    assert "reply_markup" not in message.reply_photo.call_args.kwargs
    assert legacy.public_key in message.reply_photo.call_args.kwargs["caption"]
