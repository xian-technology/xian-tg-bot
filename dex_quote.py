"""Price bounds for con_dex_helper trades from a fresh on-chain quote.

The helper takes the signer's bound (``max_input`` for buy, ``min_out`` for
sell) and reverts when execution would be worse, so the bound is quoted just
before signing and widened only by the configured slippage tolerance.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_DOWN, Decimal, localcontext
from typing import Any

from transactions import trade_amount

# Contract decimals carry at most 30 fractional digits; compute with enough
# precision that neither the tolerance nor the rounding to 30 places is lossy.
_SCALE = Decimal(1).scaleb(-30)
_PRECISION = 100


def _bound(quote: Decimal, factor: Decimal, rounding: str) -> Decimal:
    with localcontext(prec=_PRECISION):
        return (quote * factor).quantize(_SCALE, rounding=rounding).normalize()


class QuoteError(ValueError):
    """No usable quote: missing pair or a bound that is not positive."""


async def pair_id(client: Any, pairs: str, token_x: str, token_y: str) -> Any:
    token_a, token_b = sorted((token_x, token_y))
    pair = await client.get_state(pairs, "toks_to_pair", token_a, token_b)
    if pair is None:
        raise QuoteError("Pair does not exist")
    return pair


async def buy_max_input(
    client: Any, *, dex: str, pairs: str, buy_token: str, sell_token: str,
    amount: Decimal, slippage: Decimal,
) -> Decimal:
    """Most of ``sell_token`` the signer will spend to receive ``amount``."""
    pair = await pair_id(client, pairs, buy_token, sell_token)
    reserve_0, reserve_1, _ = await client.call(pairs, "getReserves", {"pair": pair})
    token_0 = await client.get_state(pairs, "pairs", pair, "token0")
    reserve_buy, reserve_sell = (reserve_0, reserve_1) if token_0 == buy_token else (reserve_1, reserve_0)
    fee_bps = await client.call(dex, "getTradeFeeBps", {})
    quote = trade_amount(await client.call(dex, "getAmountIn", {
        "amountOut": amount,
        "reserveIn": trade_amount(reserve_sell),
        "reserveOut": trade_amount(reserve_buy),
        "feeBps": fee_bps,
        # The helper's own quote: input and output on the tokens' precision grids
        "tokenIn": sell_token,
        "tokenOut": buy_token,
    }))
    max_input = _bound(quote, 1 + slippage / 100, ROUND_CEILING)
    if max_input <= 0:
        raise QuoteError("No positive input quote for this amount")
    return max_input


async def sell_min_out(
    client: Any, *, dex: str, pairs: str, sell_token: str, buy_token: str,
    amount: Decimal, slippage: Decimal,
) -> Decimal:
    """Least of ``buy_token`` the signer accepts for selling ``amount``."""
    pair = await pair_id(client, pairs, sell_token, buy_token)
    amounts = await client.call(dex, "getAmountsOut", {"amountIn": amount, "src": sell_token, "path": [pair]})
    quote = trade_amount(amounts[-1])
    min_out = _bound(quote, 1 - slippage / 100, ROUND_DOWN)
    if min_out <= 0:
        raise QuoteError("No positive output quote: amount too small for the pool")
    return min_out
