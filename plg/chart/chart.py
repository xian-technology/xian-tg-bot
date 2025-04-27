import io
import json

import pandas as pd
import constants as con
import plotly.io as pio
import plotly.graph_objects as go

from telegram import Update
from telegram.ext import CallbackContext, CommandHandler
from datetime import datetime, timedelta
from plugin import TGBFPlugin


class Chart(TGBFPlugin):
    """Plugin for displaying candlestick charts using XIAN DEX GraphQL data"""

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.chart_callback, block=False))

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    @TGBFPlugin.whitelist()
    async def chart_callback(self, update: Update, context: CallbackContext):
        """Handle the candlestick chart command"""
        # Don't deal with edited messages
        if not update.message:
            return

        # Set default values when no arguments provided
        if not context.args:
            context.args = ["XIAN-XUSDC", "72h"]
        elif len(context.args) == 1:
            # One argument - could be a timeframe or a ticker/pair
            arg = context.args[0]

            # Check if it's a timeframe (ends with h, d, or m and has numbers)
            if (arg.lower().endswith(('h', 'd', 'm')) and
                    any(c.isdigit() for c in arg)):
                # It's a timeframe, use default pair
                context.args = ["XIAN-XUSDC", arg]
            else:
                # It's a ticker or pair
                if '-' not in arg:
                    # It's a ticker, construct ticker-XIAN pair
                    context.args = [f"{arg.upper()}-XIAN", "72h"]
                else:
                    # It's already a pair, add default timeframe
                    context.args = [arg, "72h"]
        elif len(context.args) == 2:
            # If first argument is just a ticker (no dash), construct ticker-XIAN pair
            if '-' not in context.args[0]:
                context.args[0] = f"{context.args[0].upper()}-XIAN"
        else:
            await update.message.reply_text(
                await self.get_info()
            )
            return

        # Parse trading pair symbol (e.g., "XIANUSDT")
        pair_str = context.args[0].upper()

        # Split the pair string (e.g., "XIANUSDT" -> "XIAN", "USDT")
        if "-" in pair_str:
            base_symbol, quote_symbol = pair_str.split("-")
        else:
            base_symbol = pair_str
            quote_symbol = "XUSDC"  # Default

        # Default to 1h candles for last 100 hours if no timeframe specified
        interval_minutes = 60  # 1 hour
        limit = 100  # 100 intervals

        # Parse timeframe if provided
        if len(context.args) == 2:
            timeframe = context.args[1].lower()
            if timeframe.endswith('h'):
                try:
                    hours = int(timeframe[:-1])
                    limit = hours
                    interval_minutes = 60  # 1 hour candles
                except ValueError:
                    await update.message.reply_text(
                        f"{con.ERROR} Invalid timeframe format. Use [number]h for hours"
                    )
                    return
            elif timeframe.endswith('d'):
                try:
                    days = int(timeframe[:-1])
                    limit = days
                    interval_minutes = 1440  # Daily candles (24 hours)
                except ValueError:
                    await update.message.reply_text(
                        f"{con.ERROR} Invalid timeframe format. Use [number]d for days"
                    )
                    return
            elif timeframe.endswith('m'):
                try:
                    minutes = int(timeframe[:-1])
                    limit = minutes
                    interval_minutes = 1  # Minute candles
                except ValueError:
                    await update.message.reply_text(
                        f"{con.ERROR} Invalid timeframe format. Use [number]m for minutes"
                    )
                    return
            else:
                await update.message.reply_text(
                    f"{con.ERROR} Invalid timeframe format. Use h for hours, d for days, or m for minutes"
                )
                return

        try:
            # Find the pair by symbols
            pair = await self.find_pair_by_symbols(base_symbol, quote_symbol)

            if not pair:
                await update.message.reply_text(f"{con.ERROR} Trading pair {base_symbol}-{quote_symbol} not found")
                return

            # Determine if base token is token0 or token1
            base_is_token0 = pair.get('token0_symbol', '').upper() == base_symbol.upper()

            # Get swap events
            events = await self.fetch_swap_events(pair['id'])

            if not events:
                await update.message.reply_text(f"{con.ERROR} No swap events found for {base_symbol}-{quote_symbol}")
                return

            # Process into candles
            candles = self.process_swap_events(events, interval_minutes, limit, base_is_token0)

            if not candles:
                await update.message.reply_text(
                    f"{con.ERROR} Could not generate candlesticks for {base_symbol}-{quote_symbol}")
                return

            # Create DataFrame for Plotly
            df = pd.DataFrame(candles)

            # Current price for price line
            current_price = df['close'].values[-1]

            # Calculate min and max price for better y-axis scaling (add 5% padding)
            y_min = min(df['low']) * 0.95
            y_max = max(df['high']) * 1.05

            # Define colors
            up_color = 'rgb(0, 171, 107)'  # Green for bullish candles
            down_color = 'rgb(255, 73, 73)'  # Red for bearish candles
            bg_color = 'rgb(25, 25, 40)'  # Dark blue background
            grid_color = 'rgba(255, 255, 255, 0.1)'  # Subtle grid
            text_color = 'rgba(255, 255, 255, 0.9)'  # White text
            volume_color = 'rgba(59, 152, 173, 0.5)'  # Blue for volume

            # Create a new figure with subplots (price and volume)
            from plotly.subplots import make_subplots
            fig = make_subplots(
                rows=2,
                cols=1,
                shared_xaxes=True,
                vertical_spacing=0.03,
                row_heights=[0.8, 0.2],
                subplot_titles=(f"{base_symbol}-{quote_symbol} {context.args[1] if len(context.args) > 1 else '72h'}",
                                "Volume")
            )

            # Add candlestick trace
            fig.add_trace(
                go.Candlestick(
                    x=df['time'],
                    open=df['open'],
                    high=df['high'],
                    low=df['low'],
                    close=df['close'],
                    increasing=dict(line=dict(color=up_color), fillcolor=up_color),
                    decreasing=dict(line=dict(color=down_color), fillcolor=down_color),
                    name='Price'
                ),
                row=1, col=1
            )

            # Calculate and add volume bars
            colors = [up_color if df['close'][i] >= df['open'][i] else down_color for i in range(len(df))]
            fig.add_trace(
                go.Bar(
                    x=df['time'],
                    y=df['volume'],
                    marker_color=colors,
                    name='Volume',
                    opacity=0.7
                ),
                row=2, col=1
            )

            # Add moving averages if we have enough data
            if len(df) >= 20:
                df['MA20'] = df['close'].rolling(window=20).mean()
                fig.add_trace(
                    go.Scatter(
                        x=df['time'],
                        y=df['MA20'],
                        line=dict(color='rgba(255, 207, 0, 0.7)', width=2),
                        name='20-period MA'
                    ),
                    row=1, col=1
                )

            if len(df) >= 50:
                df['MA50'] = df['close'].rolling(window=50).mean()
                fig.add_trace(
                    go.Scatter(
                        x=df['time'],
                        y=df['MA50'],
                        line=dict(color='rgba(144, 238, 144, 0.7)', width=2),
                        name='50-period MA'
                    ),
                    row=1, col=1
                )

            # Add current price line
            fig.add_shape(
                type="line",
                x0=df['time'].min(),
                x1=df['time'].max(),
                y0=current_price,
                y1=current_price,
                line=dict(color='rgba(102, 204, 255, 0.8)', width=2, dash="dot"),
                row=1, col=1
            )

            # Update layout with modern styling
            fig.update_layout(
                paper_bgcolor=bg_color,
                plot_bgcolor=bg_color,
                font=dict(
                    family="Arial, sans-serif",
                    size=13,
                    color=text_color
                ),
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.05,
                    xanchor="center",
                    x=0.5,
                    bgcolor="rgba(0,0,0,0.3)",
                    bordercolor="rgba(255,255,255,0.2)",
                    font=dict(color=text_color)
                ),
                margin=dict(l=60, r=60, t=80, b=60),
                height=700,  # Taller chart for better visibility
                hovermode="x unified",
                xaxis_rangeslider_visible=False
            )

            # Style the main price chart
            fig.update_xaxes(
                row=1, col=1,
                gridcolor=grid_color,
                zerolinecolor=grid_color,
                showspikes=True,
                spikethickness=1,
                spikedash="solid",
                spikecolor="rgba(255,255,255,0.4)",
                spikemode="across"
            )

            fig.update_yaxes(
                row=1, col=1,
                gridcolor=grid_color,
                zerolinecolor=grid_color,
                title=f"Price ({quote_symbol})",
                titlefont=dict(size=13),
                showspikes=True,
                spikethickness=1,
                spikedash="solid",
                spikecolor="rgba(255,255,255,0.4)",
                spikemode="across",
                range=[y_min, y_max]  # Set y-axis range with padding
            )

            # Style the volume chart
            fig.update_xaxes(
                row=2, col=1,
                gridcolor=grid_color,
                zerolinecolor=grid_color
            )

            fig.update_yaxes(
                row=2, col=1,
                gridcolor=grid_color,
                zerolinecolor=grid_color,
                title=f"Volume ({base_symbol})",
                titlefont=dict(size=13)
            )

            # Calculate 24h volume (if we have enough data)
            hours_24_ago = datetime.utcnow() - timedelta(hours=24)
            volume_24h = sum(candle['volume'] for candle in candles
                             if candle['time'] >= hours_24_ago)

            caption = (
                f"<code>Last price: {current_price:,.8g} {quote_symbol}</code>\n"
                f"<code>24h Volume: {volume_24h:,.2f} {base_symbol}</code>"
            )

            # Send chart as photo with higher resolution
            await update.message.reply_photo(
                photo=io.BufferedReader(io.BytesIO(pio.to_image(fig, format='png', scale=2))),
                caption=caption
            )

        except Exception as e:
            await update.message.reply_text(f"{con.ERROR} Error creating chart: {e}")
            self.log.error(f"Chart error: {e}")
            await self.notify(e)

    async def fetch_pairs(self):
        pairs_query = await self.get_resource("get_pairs.gql")
        result = await self.fetch_graphql(pairs_query)
        pairs = []

        if not result.get('data', {}).get('allEvents', {}).get('edges'):
            return pairs

        for edge in result['data']['allEvents']['edges']:
            if isinstance(edge['node']['dataIndexed'], str):
                data_indexed = json.loads(edge['node']['dataIndexed'])
            else:
                data_indexed = edge['node']['dataIndexed']

            if isinstance(edge['node']['data'], str):
                pair_data = json.loads(edge['node']['data'])
            else:
                pair_data = edge['node']['data']

            pairs.append({
                'id': pair_data['pair'],
                'token0': data_indexed['token0'],
                'token1': data_indexed['token1']
            })

        return pairs

    async def fetch_token_symbols(self, token_contracts):
        """Fetch token symbols for multiple token contracts in one query"""
        if not token_contracts:
            return {}

        # Build a combined GraphQL query for all tokens
        query_parts = []
        for i, token in enumerate(token_contracts):
            query_parts.append(f"""
                symbol_{i}: allStates(condition: {{key: "{token}.metadata:token_symbol"}}) {{
                    nodes {{
                        key
                        value
                    }}
                }}
            """)

        full_query = f"query GetTokensMetadata {{{' '.join(query_parts)}}}"

        result = await self.fetch_graphql(full_query)

        token_symbols = {}
        for i, token in enumerate(token_contracts):
            symbol_data = result.get('data', {}).get(f'symbol_{i}', {}).get('nodes', [])
            if symbol_data and len(symbol_data) > 0:
                value = symbol_data[0].get('value')
                if value:
                    symbol = value if isinstance(value, str) else json.dumps(value)
                    token_symbols[token] = symbol

        return token_symbols

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

    async def fetch_swap_events(self, pair_id):
        """Fetch swap events for a specific pair"""
        query = await self.get_resource("get_swap_events.gql")
        result = await self.fetch_graphql(query, {'pairId': pair_id})
        return result.get('data', {}).get('allEvents', {}).get('edges', [])

    def process_swap_events(self, events, interval_minutes, limit, base_is_token0=True):
        """Convert swap events to candlestick data - similar to web implementation"""
        if not events:
            return []

        # Extract trades
        trades = []
        for edge in events:
            node = edge['node']

            # Parse data
            data_indexed = json.loads(node['dataIndexed']) if isinstance(node['dataIndexed'], str) else node[
                'dataIndexed']
            swap_data = json.loads(node['data']) if isinstance(node['data'], str) else node['data']

            # Parse the timestamp (ensure UTC)
            timestamp_str = node['created']
            timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))

            # Calculate price
            amount0_in = float(swap_data.get('amount0In', 0) or 0)
            amount0_out = float(swap_data.get('amount0Out', 0) or 0)
            amount1_in = float(swap_data.get('amount1In', 0) or 0)
            amount1_out = float(swap_data.get('amount1Out', 0) or 0)

            price = None
            volume = 0

            if amount0_out > 0 and amount1_in > 0:
                # Buying token0 with token1
                price = amount1_in / amount0_out
                # Volume in base token
                volume = amount0_out if base_is_token0 else amount1_in
            elif amount0_in > 0 and amount1_out > 0:
                # Selling token0 for token1
                price = amount1_out / amount0_in
                # Volume in base token
                volume = amount0_in if base_is_token0 else amount1_out

            # Invert price if base is token1 instead of token0
            if price is not None and not base_is_token0:
                price = 1 / price
                # Volume is already correctly calculated based on base_is_token0

            if price:
                trades.append({
                    'timestamp': timestamp,
                    'price': price,
                    'volume': volume
                })

        # Sort by timestamp (oldest first)
        trades.sort(key=lambda x: x['timestamp'])

        if not trades:
            return []

        # Get time boundaries for candles
        current_time = datetime.utcnow()
        start_time = current_time - timedelta(minutes=interval_minutes * limit)

        # Round to interval boundaries (as in web implementation)
        rounded_start = start_time.replace(
            minute=(start_time.minute // interval_minutes) * interval_minutes,
            second=0,
            microsecond=0
        )

        # Use current time for the end boundary to include in-progress candle
        current_interval_start = current_time.replace(
            minute=(current_time.minute // interval_minutes) * interval_minutes,
            second=0,
            microsecond=0
        )

        # Calculate the next interval after current for proper boundaries
        next_interval = current_interval_start + timedelta(minutes=interval_minutes)

        # Generate all intervals
        intervals = []
        current = rounded_start
        while current <= current_interval_start:
            intervals.append(current)
            current += timedelta(minutes=interval_minutes)

        # Add one more interval for the future (needed for proper interval boundaries)
        intervals.append(next_interval)

        # Process candles
        candles = []
        previous_close = None

        for i in range(len(intervals) - 1):
            interval_start = intervals[i]
            interval_end = intervals[i + 1]

            # For the current in-progress interval, use actual current time as boundary
            actual_end = current_time if interval_start == current_interval_start else interval_end

            # Find trades in this interval
            interval_trades = [t for t in trades
                               if interval_start <= t['timestamp'] < actual_end]

            if interval_trades:
                if previous_close is None:
                    # First candle with trades
                    candle = {
                        'time': interval_start,
                        'open': interval_trades[0]['price'],
                        'high': max(t['price'] for t in interval_trades),
                        'low': min(t['price'] for t in interval_trades),
                        'close': interval_trades[-1]['price'],
                        'volume': sum(t['volume'] for t in interval_trades)
                    }
                    previous_close = candle['close']
                else:
                    # Subsequent candle with trades
                    candle = {
                        'time': interval_start,
                        'open': previous_close,
                        'high': max([previous_close] + [t['price'] for t in interval_trades]),
                        'low': min([previous_close] + [t['price'] for t in interval_trades]),
                        'close': interval_trades[-1]['price'],
                        'volume': sum(t['volume'] for t in interval_trades)
                    }
                    previous_close = candle['close']
            elif previous_close is not None:
                # Empty candle - maintain price from previous candle
                candle = {
                    'time': interval_start,
                    'open': previous_close,
                    'high': previous_close,
                    'low': previous_close,
                    'close': previous_close,
                    'volume': 0
                }
            else:
                # Skip intervals until we find the first trade
                continue

            candles.append(candle)

        return candles