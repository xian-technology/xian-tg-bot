import io
import json
import asyncio
import pickledb

import pandas as pd
import constants as con
import plotly.io as pio
import plotly.graph_objects as go

from pathlib import Path
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
from plugin import TGBFPlugin


class Chart(TGBFPlugin):
    """Plugin for displaying candlestick charts using XIAN DEX GraphQL data with persistent caching"""

    async def init(self):
        # Register chart command
        await self.add_handler(
            CommandHandler(self.handle, self.chart_callback, block=False)
        )

        # Initialize persistent cache (auto_dump=False for batch writes)
        cache_path = Path(self.get_dat_path()) / 'cache.db'
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_db = pickledb.load(str(cache_path), False)

        # Schedule cache refresh every 5 minutes, run immediately
        self.run_repeating(
            self._refresh_cache_job,
            interval=300,
            first=5,
            name=f"{self.name}_cache_refresh"
        )

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    @TGBFPlugin.whitelist()
    async def chart_callback(self, update: Update, context: CallbackContext):
        """Handle candlestick chart command, using cache with live fallback"""
        if not update.message:
            return

        # Argument parsing ...
        if not context.args:
            context.args = ["XIAN-XUSDC", "72h"]
        elif len(context.args) == 1:
            arg = context.args[0]
            if (arg.lower().endswith(('h','d','m')) and any(c.isdigit() for c in arg)):
                context.args = ["XIAN-XUSDC", arg]
            else:
                if '-' not in arg:
                    context.args = [f"{arg.upper()}-XIAN", "72h"]
                else:
                    context.args = [arg, "72h"]
        elif len(context.args) == 2:
            if '-' not in context.args[0]:
                context.args[0] = f"{context.args[0].upper()}-XIAN"
        else:
            await update.message.reply_text(await self.get_info())
            return

        # Parse symbols
        pair_str = context.args[0].upper()
        if '-' in pair_str:
            base_symbol, quote_symbol = pair_str.split('-')
        else:
            base_symbol = pair_str
            quote_symbol = 'XUSDC'

        # Timeframe parsing
        interval_minutes, limit = 60, 100
        if len(context.args) == 2:
            tf = context.args[1].lower()
            try:
                if tf.endswith('h'):
                    limit = int(tf[:-1]); interval_minutes = 60
                elif tf.endswith('d'):
                    limit = int(tf[:-1]); interval_minutes = 1440
                elif tf.endswith('m'):
                    limit = int(tf[:-1]); interval_minutes = 1
                else:
                    raise ValueError
            except ValueError:
                await update.message.reply_text(
                    f"{con.ERROR} Invalid timeframe format. Use h, d or m"
                )
                return

        try:
            # Find pair
            pair = await self.find_pair_by_symbols(base_symbol, quote_symbol)
            if not pair:
                await update.message.reply_text(
                    f"{con.ERROR} Trading pair {base_symbol}-{quote_symbol} not found"
                )
                return

            base_is_token0 = pair.get('token0_symbol','').upper() == base_symbol.upper()

            # Get events
            events = await self.get_cached_swap_events(pair['id'])
            if not events:
                await update.message.reply_text(
                    f"{con.ERROR} No swap events found for {base_symbol}-{quote_symbol}"
                )
                return

            # Process candles
            candles = self.process_swap_events(events, interval_minutes, limit, base_is_token0)
            if not candles:
                await update.message.reply_text(
                    f"{con.ERROR} Could not generate candlesticks for {base_symbol}-{quote_symbol}"
                )
                return

            # Build DataFrame
            df = pd.DataFrame(candles)
            current_price = df['close'].iloc[-1]
            y_min = df['low'].min() * 0.95
            y_max = df['high'].max() * 1.05

            # Define colors
            up_color = 'rgb(0, 171, 107)'
            down_color = 'rgb(255, 73, 73)'
            bg_color = 'rgb(25, 25, 40)'
            grid_color = 'rgba(255, 255, 255, 0.1)'
            text_color = 'rgba(255, 255, 255, 0.9)'

            # Create figure
            fig = make_subplots(
                rows=2, cols=1, shared_xaxes=True,
                vertical_spacing=0.03, row_heights=[0.8,0.2]
            )

            # Candlestick
            fig.add_trace(
                go.Candlestick(
                    x=df['time'], open=df['open'], high=df['high'],
                    low=df['low'], close=df['close'],
                    increasing=dict(line=dict(color=up_color), fillcolor=up_color),
                    decreasing=dict(line=dict(color=down_color), fillcolor=down_color),
                    name='Price'
                ), row=1, col=1
            )

            # Volume bars
            colors = [up_color if df['close'][i] >= df['open'][i] else down_color for i in range(len(df))]
            fig.add_trace(
                go.Bar(
                    x=df['time'], y=df['volume'],
                    marker_color=colors, name='Volume', opacity=0.7
                ), row=2, col=1
            )

            # Moving averages
            if len(df) >= 20:
                df['MA20'] = df['close'].rolling(20).mean()
                fig.add_trace(
                    go.Scatter(
                        x=df['time'], y=df['MA20'],
                        line=dict(color='rgba(255, 207, 0, 0.7)', width=2),
                        name='20-period MA'
                    ), row=1, col=1
                )
            if len(df) >= 50:
                df['MA50'] = df['close'].rolling(50).mean()
                fig.add_trace(
                    go.Scatter(
                        x=df['time'], y=df['MA50'],
                        line=dict(color='rgba(144, 238, 144, 0.7)', width=2),
                        name='50-period MA'
                    ), row=1, col=1
                )

            # Current price line
            fig.add_shape(
                type='line', x0=df['time'].min(), x1=df['time'].max(),
                y0=current_price, y1=current_price,
                line=dict(color='rgba(102, 204, 255, 0.8)', width=2, dash='dot'),
                row=1, col=1
            )

            # Layout & styling
            fig.update_layout(
                title_text=f"{base_symbol}-{quote_symbol} {context.args[1]}",
                title_x=0.5,
                paper_bgcolor=bg_color,
                plot_bgcolor=bg_color,
                font=dict(family="Arial, sans-serif", size=13, color=text_color),
                legend=dict(
                    orientation='h', yanchor='bottom', y=1,
                    xanchor='center', x=0.5,
                    bgcolor='rgba(0,0,0,0.3)', bordercolor='rgba(255,255,255,0.2)',
                    font=dict(color=text_color)
                ),
                margin=dict(l=60, r=60, t=80, b=60),
                height=700, hovermode='x unified',
                xaxis_rangeslider_visible=False
            )

            # Price axes styling
            fig.update_xaxes(
                row=1, col=1,
                gridcolor=grid_color, zerolinecolor=grid_color,
                showspikes=True, spikethickness=1, spikedash='solid',
                spikecolor='rgba(255,255,255,0.4)', spikemode='across'
            )
            fig.update_yaxes(
                row=1, col=1,
                range=[y_min, y_max], gridcolor=grid_color, zerolinecolor=grid_color,
                title=f"Price ({quote_symbol})", titlefont=dict(size=13),
                showspikes=True, spikethickness=1, spikedash='solid',
                spikecolor='rgba(255,255,255,0.4)', spikemode='across'
            )

            # Volume axes styling
            fig.update_xaxes(row=2, col=1, gridcolor=grid_color, zerolinecolor=grid_color)
            fig.update_yaxes(
                row=2, col=1,
                gridcolor=grid_color, zerolinecolor=grid_color,
                title=f"Volume ({base_symbol})", titlefont=dict(size=13)
            )

            # Caption
            hours_24_ago = datetime.utcnow() - timedelta(hours=24)
            volume_24h = sum(c['volume'] for c in candles if c['time'] >= hours_24_ago)
            caption = (
                f"<code>Last price: {current_price:,.8g} {quote_symbol}</code>\n"
                f"<code>24h Volume: {volume_24h:,.2f} {base_symbol}</code>"
            )

            # Send image
            await update.message.reply_photo(
                photo=io.BufferedReader(io.BytesIO(pio.to_image(fig, format='png', scale=2))),
                caption=caption
            )

        except Exception as e:
            await update.message.reply_text(f"{con.ERROR} Error creating chart: {e}")
            self.log.error(f"Chart error: {e}")
            await self.notify(e)

    async def get_cached_pairs(self):
        pairs = self.cache_db.get('pairs')
        if not pairs:
            pairs = await self.fetch_pairs()
            if pairs:
                self.cache_db.set('pairs', pairs)
                self.cache_db.dump()
        return pairs or []

    async def get_cached_token_symbols(self):
        symbols = self.cache_db.get('token_symbols') or {}
        pairs = await self.get_cached_pairs()
        contracts = {p['token0'] for p in pairs} | {p['token1'] for p in pairs}
        missing = [c for c in contracts if c not in symbols]
        if missing:
            new = await self.fetch_token_symbols(missing)
            symbols.update(new)
            self.cache_db.set('token_symbols', symbols)
            self.cache_db.dump()
        return symbols

    async def get_cached_swap_events(self, pair_id):
        key = f"swaps:{pair_id}"
        if not self.cache_db.exists(key):
            events = await self.fetch_swap_events(pair_id)
            self.cache_db.set(key, events)
            self.cache_db.dump()
            return events
        return self.cache_db.get(key)

    async def _refresh_cache_job(self, context):
        try:
            pairs = await self.fetch_pairs()
            if pairs:
                self.cache_db.set('pairs', pairs)
            contracts = {p['token0'] for p in pairs} | {p['token1'] for p in pairs}
            token_symbols = await self.fetch_token_symbols(list(contracts))
            self.cache_db.set('token_symbols', token_symbols)
            tasks = [self.fetch_swap_events(p['id']) for p in pairs]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for p, ev in zip(pairs, results):
                if not isinstance(ev, Exception):
                    self.cache_db.set(f"swaps:{p['id']}", ev)
            self.cache_db.dump()
        except Exception as e:
            self.log.error(f"Cache refresh error: {e}")
            await self.notify(e)

    async def find_pair_by_symbols(self, base_symbol, quote_symbol='XUSDC'):
        pairs = await self.get_cached_pairs()
        if not pairs:
            return None
        token_symbols = await self.get_cached_token_symbols()
        for pair in pairs:
            t0 = token_symbols.get(pair['token0'], '').upper()
            t1 = token_symbols.get(pair['token1'], '').upper()
            if (t0 == base_symbol.upper() and t1 == quote_symbol.upper()) or \
               (t1 == base_symbol.upper() and t0 == quote_symbol.upper()):
                pair['token0_symbol'] = t0 or pair['token0']
                pair['token1_symbol'] = t1 or pair['token1']
                return pair
        return None

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