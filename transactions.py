"""Transaction acceptance and finality for bot commands using the current SDK."""

from typing import Any

from xian_py import XianAsync
from xian_py.exception import XianException
from xian_py.models import TransactionSubmission


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
