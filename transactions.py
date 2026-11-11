"""Transaction acceptance and finality for bot commands using the current SDK."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any

from xian_py import XianAsync
from xian_py.exception import XianException
from xian_py.models import TransactionSubmission
from xian_runtime_types.decimal import canonical_fixed
from xian_runtime_types.numeric_wire import decode_wire_numbers


def parse_amount(text: str | int) -> Decimal:
    return Decimal(canonical_fixed(text))


def encode_trade_amount(amount: Decimal) -> bytes:
    """Bind decimal text as bytes to preserve it in existing SQLite REAL columns."""
    return canonical_fixed(amount).encode("ascii")


def decode_trade_amount(stored: bytes | str | float | int) -> Decimal:
    text = stored.decode("ascii") if isinstance(stored, bytes) else str(stored)
    return parse_amount(text)


def trade_amount(value: Any) -> Decimal:
    """Read an SDK receipt value or a numeric field from an indexed trade."""
    if isinstance(value, dict):
        value = decode_wire_numbers(value)
    elif isinstance(value, float):
        # Historical index rows may contain an untagged fractional number.
        value = str(value)
    return Decimal(canonical_fixed(value))


def trade_deadline() -> dict[str, list[int]]:
    expires = datetime.now(UTC) + timedelta(minutes=5)
    return {"__time__": [*expires.timetuple()[:6], expires.microsecond]}


def split_amount(total: Decimal, recipients: int) -> Decimal:
    # Round down to the bot's four-place payout precision without overspending.
    units = Fraction(total) * 10_000 // recipients
    whole, fraction = divmod(units, 10_000)
    return Decimal(f"{whole}.{fraction:04d}")


def submission_accepted(submission: TransactionSubmission) -> bool:
    return (
        submission.submitted
        and submission.accepted is True
        and bool(submission.tx_hash)
    )


async def confirm_submission(
    client: XianAsync,
    submission: TransactionSubmission,
    *,
    timeout: float = 30,
) -> tuple[bool, Any]:
    """Resolve finality by hash, including transactions finalized before this call.

    A CheckTx acceptance is not execution success. Confirmation never resubmits
    a transaction, including when the lookup times out.
    """
    if not submission_accepted(submission):
        return False, submission.message or "Transaction was not accepted"
    assert submission.tx_hash is not None
    try:
        receipt = submission.receipt or await client.wait_for_tx(
            submission.tx_hash, timeout_seconds=timeout,
        )
    except (TimeoutError, XianException) as exc:
        return False, (
            f"Unable to confirm transaction {submission.tx_hash}: {exc or type(exc).__name__}. "
            "It may still complete; check the explorer before retrying."
        )
    result = (receipt.execution or {}).get("result", receipt.message)
    return receipt.success, " " if result is None or result == "None" else result
