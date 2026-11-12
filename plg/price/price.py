import html

from telegram import Update
from telegram.ext import CallbackContext, CommandHandler

import constants as con
import dex_pairs
from dex_candles import DaySummary, Timeframe, market, request_or_reply
from logging_setup import error_context
from plugin import TGBFPlugin
from utils import utc_now

XIAN_TOTAL_SUPPLY = 111_111_111


class Price(TGBFPlugin):
    """Plugin for displaying token price information using XIAN DEX GraphQL data"""

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.price_callback, block=False))

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    @TGBFPlugin.whitelist()
    async def price_callback(self, update: Update, context: CallbackContext):
        """Handle the price command"""
        # Don't deal with edited messages
        if not update.message:
            return
        request = await request_or_reply(update.message, context.args, "74h", self.get_info)
        if request is None:
            return

        message = await update.message.reply_text(f"{con.WAIT} Fetching price data...")
        try:
            await message.edit_text(await self.price_text(*request))
        except Exception as e:
            await message.edit_text(f"{con.ERROR} Error fetching price: {html.escape(str(e))}")
            self.log.bind(**error_context(e), handler='price_callback').error('Price callback failed')
            await self.notify(e)

    async def price_text(self, base_symbol: str, quote_symbol: str, timeframe: Timeframe) -> str:
        pair_name = f"{base_symbol}-{quote_symbol}"
        pair = await self.find_pair_by_symbols(base_symbol, quote_symbol)
        if not pair:
            return f"{con.ERROR} Trading pair {pair_name} not found"
        data = await market(await self.get_xian(), pair, base_symbol, timeframe, utc_now())
        if not data.window:
            return f"{con.ERROR} No trades for {pair_name} in the last {timeframe}"

        current_price = data.window[-1].close
        first_price = data.window[0].open
        price_change = current_price - first_price
        price_change_pct = (price_change / first_price) * 100
        direction = con.GREEN if price_change >= 0 else con.RED
        day = data.day or DaySummary(volume=0, high=current_price, low=current_price)

        lines = [
            f"{direction} <b>{pair_name}</b> {timeframe}\n",
            f"<code>Price:         {current_price:.6f} {quote_symbol}</code>",
            f"<code>Change:       {price_change:+.6f} {quote_symbol} ({price_change_pct:+.2f}%)</code>",
            f"<code>24h High:      {day.high:.6f} {quote_symbol}</code>",
            f"<code>24h Low:       {day.low:.6f} {quote_symbol}</code>",
            f"<code>24h Volume:    {day.volume:.2f} {base_symbol}</code>",
        ]
        if base_symbol == 'XIAN':
            lines.append(f"<code>Market Cap:    {current_price * XIAN_TOTAL_SUPPLY:,.0f} {quote_symbol}</code>")
        return "\n".join(lines)

    async def fetch_pairs(self):
        query = await self.get_resource("get_pairs.gql", plugin="chart")
        return await dex_pairs.pairs(self.fetch_graphql, query)

    async def fetch_token_symbols(self, token_contracts):
        """Fetch token symbols for multiple token contracts in one query"""
        return await dex_pairs.token_symbols(self.fetch_graphql, token_contracts)

    async def find_pair_by_symbols(self, base_symbol, quote_symbol='XUSDC'):
        """Find a pair by base and quote symbols"""
        # Get all pairs
        pairs = await self.fetch_pairs()

        if not pairs:
            return None

        # Get unique token contracts
        token_contracts = set()
        for pair in pairs:
            token_contracts.add(pair['token0'])
            token_contracts.add(pair['token1'])

        # Get token symbols
        token_symbols = await self.fetch_token_symbols(list(token_contracts))

        # Find the pair with matching symbols
        for pair in pairs:
            token0_symbol = token_symbols.get(pair['token0'], '').upper()
            token1_symbol = token_symbols.get(pair['token1'], '').upper()

            # Check for match in either direction
            if (token0_symbol == base_symbol.upper() and token1_symbol == quote_symbol.upper()) or \
                    (token1_symbol == base_symbol.upper() and token0_symbol == quote_symbol.upper()):
                # Store the symbols for later use
                pair['token0_symbol'] = token_symbols.get(pair['token0'], pair['token0'])
                pair['token1_symbol'] = token_symbols.get(pair['token1'], pair['token1'])
                return pair

        return None
