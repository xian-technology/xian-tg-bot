import io
from dataclasses import asdict

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler

import constants as con
import dex_pairs
from dex_candles import Candle, Timeframe, market, request_or_reply
from logging_setup import error_context
from plugin import TGBFPlugin
from utils import utc_now


class Chart(TGBFPlugin):
    """Plugin for displaying candlestick charts from the node's DEX candles"""

    async def init(self):
        # Add the command handler
        await self.add_handler(CommandHandler(self.handle, self.chart_callback, block=False))

        # Create database tables if they don't exist
        await self.create_db_tables()

        # Perform initial data loading
        await self.load_initial_data()

        # Set up background jobs
        self.run_repeating(self.update_pairs_job, interval=303)  # Every 5 minutes
        self.run_repeating(self.update_tokens_job, interval=306)  # Every 5 minutes

    async def create_db_tables(self):
        """Create required database tables if they don't exist"""

        # Create pairs table
        if not await self.table_exists("chart_pairs"):
            pairs_sql = """
                        CREATE TABLE chart_pairs \
                        ( \
                            id         TEXT PRIMARY KEY, \
                            token0     TEXT NOT NULL, \
                            token1     TEXT NOT NULL, \
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        ) \
                        """
            await self.exec_sql(pairs_sql)
            self.log.info("Created chart_pairs table")

        # Create tokens table
        if not await self.table_exists("chart_tokens"):
            tokens_sql = """
                         CREATE TABLE chart_tokens \
                         ( \
                             address    TEXT PRIMARY KEY, \
                             symbol     TEXT NOT NULL, \
                             created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, \
                             updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                         ) \
                         """
            await self.exec_sql(tokens_sql)
            self.log.info("Created chart_tokens table")

        # Trades come from the node's dex_candles query; drop the old local trade cache.
        await self.exec_sql("DROP TABLE IF EXISTS chart_trades")

    async def load_initial_data(self):
        """Load initial data into the database on startup"""
        try:
            # First load pairs
            pairs = await self.fetch_pairs_from_graphql()
            if pairs:
                # Create a list of all token addresses
                token_addresses = set()
                for pair in pairs:
                    token_addresses.add(pair['token0'])
                    token_addresses.add(pair['token1'])

                    # Save pair to database
                    sql = """
                    INSERT OR REPLACE INTO chart_pairs (id, token0, token1)
                    VALUES (?, ?, ?)
                    """
                    await self.exec_sql(sql, pair["id"], pair["token0"], pair["token1"])

                self.log.info(f"Loaded {len(pairs)} pairs on startup")

                # Then load token symbols
                if token_addresses:
                    token_symbols = await self.fetch_token_symbols_from_graphql(list(token_addresses))
                    for address, symbol in token_symbols.items():
                        sql = """
                        INSERT OR REPLACE INTO chart_tokens (address, symbol, updated_at)
                        VALUES (?, ?, CURRENT_TIMESTAMP)
                        """
                        await self.exec_sql(sql, address, symbol)

                    self.log.info(f"Loaded {len(token_symbols)} token symbols on startup")

        except Exception as e:
            self.log.bind(**error_context(e), handler='load_initial_data').error('Load initial data failed')
            await self.notify(e)

    async def fetch_pairs_from_graphql(self):
        """Fetch pairs directly from GraphQL, without database interaction"""
        return await dex_pairs.pairs(self.fetch_graphql, await self.get_resource("get_pairs.gql"))

    async def fetch_token_symbols_from_graphql(self, token_contracts):
        """Fetch token symbols directly from GraphQL, without database interaction"""
        return await dex_pairs.token_symbols(self.fetch_graphql, token_contracts)

    async def update_pairs_job(self, context: CallbackContext):
        """Background job to update trading pairs"""
        try:
            # Fetch pairs from GraphQL
            pairs = await self.fetch_pairs_from_graphql()

            if not pairs:
                return

            # Update database
            for pair in pairs:
                sql = """
                INSERT OR REPLACE INTO chart_pairs (id, token0, token1)
                VALUES (?, ?, ?)
                """
                await self.exec_sql(sql, pair["id"], pair["token0"], pair["token1"])

            self.log.info(f"Updated {len(pairs)} trading pairs")

        except Exception as e:
            self.log.bind(**error_context(e), handler='update_pairs_job').error('Update pairs job failed')
            await self.notify(e)

    async def update_tokens_job(self, context: CallbackContext):
        """Background job to update token symbols"""
        try:
            # Get all token addresses from the pairs table
            sql = """
                  SELECT DISTINCT token0 \
                  FROM chart_pairs
                  UNION
                  SELECT DISTINCT token1 \
                  FROM chart_pairs \
                  """
            result = await self.exec_sql(sql)

            if not result["success"] or not result["data"]:
                return

            # Collect token addresses
            token_addresses = [row[0] for row in result["data"]]

            # Fetch token symbols
            token_symbols = await self.fetch_token_symbols_from_graphql(token_addresses)

            # Update database
            for address, symbol in token_symbols.items():
                sql = """
                INSERT OR REPLACE INTO chart_tokens (address, symbol, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """
                await self.exec_sql(sql, address, symbol)

            self.log.info(f"Updated {len(token_symbols)} token symbols")

        except Exception as e:
            self.log.bind(**error_context(e), handler='update_tokens_job').error('Update tokens job failed')
            await self.notify(e)

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    @TGBFPlugin.blacklist()
    async def chart_callback(self, update: Update, context: CallbackContext):
        """Handle the candlestick chart command"""
        # Don't deal with edited messages
        if not update.message:
            return
        request = await request_or_reply(update.message, context.args, "72h", self.get_info)
        if request is None:
            return
        try:
            await self.reply_chart(update.message, *request)
        except Exception as e:
            self.log.bind(**error_context(e), handler='chart_callback').error('Chart callback failed')

    async def reply_chart(self, message, base_symbol: str, quote_symbol: str, timeframe: Timeframe):
        pair_name = f"{base_symbol}-{quote_symbol}"
        pair = await self.find_pair_by_symbols(base_symbol, quote_symbol)
        if not pair:
            await message.reply_text(f"{con.ERROR} Trading pair {pair_name} not found")
            return
        data = await market(await self.get_xian(), pair, base_symbol, timeframe, utc_now())
        if not data.window:
            await message.reply_text(f"{con.INFO} No trades for {pair_name} in the last {timeframe}")
            return

        volume_24h = data.day.volume if data.day else 0
        caption = (
            f"<code>Last Price: {data.window[-1].close:,.8g} {quote_symbol}</code>\n"
            f"<code>24h Volume: {volume_24h:,.2f} {base_symbol}</code>"
        )
        image = chart_image(data.window, base_symbol, quote_symbol, timeframe)
        await message.reply_photo(photo=io.BufferedReader(io.BytesIO(image)), caption=caption)

    async def fetch_pairs(self):
        """Fetch pairs from database, or from GraphQL if not available"""
        # Try to get from database first
        sql = "SELECT id, token0, token1 FROM chart_pairs"
        result = await self.exec_sql(sql)

        if result["success"] and result["data"]:
            pairs = []
            for row in result["data"]:
                pairs.append({
                    'id': row[0],
                    'token0': row[1],
                    'token1': row[2]
                })
            return pairs

        # If not in database, fetch from GraphQL
        pairs = await self.fetch_pairs_from_graphql()

        # Save to database
        if pairs:
            for pair in pairs:
                sql = """
                INSERT OR REPLACE INTO chart_pairs (id, token0, token1)
                VALUES (?, ?, ?)
                """
                await self.exec_sql(sql, pair["id"], pair["token0"], pair["token1"])

        return pairs

    async def fetch_token_symbols(self, token_contracts):
        """Fetch token symbols from database, or from GraphQL if not available"""
        if not token_contracts:
            return {}

        token_symbols = {}
        missing_tokens = []

        # Try to get from database first
        for token in token_contracts:
            sql = "SELECT symbol FROM chart_tokens WHERE address = ?"
            result = await self.exec_sql(sql, token)

            if result["success"] and result["data"]:
                token_symbols[token] = result["data"][0][0]
            else:
                missing_tokens.append(token)

        # If any tokens missing from database, fetch from GraphQL
        if missing_tokens:
            new_symbols = await self.fetch_token_symbols_from_graphql(missing_tokens)

            # Save to database
            for token, symbol in new_symbols.items():
                sql = """
                INSERT OR REPLACE INTO chart_tokens (address, symbol, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """
                await self.exec_sql(sql, token, symbol)

            # Add to result
            token_symbols.update(new_symbols)

        return token_symbols

    async def find_pair_by_symbols(self, base_symbol, quote_symbol='XUSDC'):
        """Find a pair by base and quote symbols, using database if available"""
        # Try to find the pair in the database first
        sql = """
              SELECT p.id, p.token0, p.token1, t0.symbol as token0_symbol, t1.symbol as token1_symbol
              FROM chart_pairs p
                       JOIN chart_tokens t0 ON p.token0 = t0.address
                       JOIN chart_tokens t1 ON p.token1 = t1.address
              WHERE (t0.symbol = ? AND t1.symbol = ?) \
                 OR (t0.symbol = ? AND t1.symbol = ?) \
              """
        result = await self.exec_sql(
            sql,
            base_symbol.upper(), quote_symbol.upper(),
            quote_symbol.upper(), base_symbol.upper()
        )

        if result["success"] and result["data"]:
            pair_data = result["data"][0]
            pair = {
                'id': pair_data[0],
                'token0': pair_data[1],
                'token1': pair_data[2],
                'token0_symbol': pair_data[3],
                'token1_symbol': pair_data[4]
            }
            self.log.debug("Trading pair found in database")
            return pair

        # If not found in database, fetch from GraphQL
        self.log.debug(f"Pair {base_symbol}-{quote_symbol} not found in database, fetching from GraphQL")
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

                # Save to database
                await self.exec_sql(
                    "INSERT OR REPLACE INTO chart_pairs (id, token0, token1) VALUES (?, ?, ?)",
                    pair['id'], pair['token0'], pair['token1']
                )

                # Save token symbols
                await self.exec_sql(
                    "INSERT OR REPLACE INTO chart_tokens (address, symbol, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                    pair['token0'], pair['token0_symbol']
                )
                await self.exec_sql(
                    "INSERT OR REPLACE INTO chart_tokens (address, symbol, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                    pair['token1'], pair['token1_symbol']
                )

                return pair

        return None


def chart_image(window: list[Candle], base_symbol: str, quote_symbol: str, timeframe: Timeframe) -> bytes:
    """PNG candlestick and volume chart of the window."""
    df = pd.DataFrame([asdict(candle) for candle in window])

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

    tf = str(timeframe)

    # Create a new figure with subplots (price and volume)
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.8, 0.2],
        subplot_titles=(f"{base_symbol}-{quote_symbol} {tf}", "Volume")
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
        # Shape coordinates are not encoded like trace data: pass ISO text
        x0=df['time'].min().isoformat(),
        x1=df['time'].max().isoformat(),
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
        title=dict(text=f"Price ({quote_symbol})", font=dict(size=13)),
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
        title=dict(text=f"Volume ({base_symbol})", font=dict(size=13))
    )

    return pio.to_image(fig, format='png', scale=2)
