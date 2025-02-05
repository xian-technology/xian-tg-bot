import io
import pandas as pd
import constants as con
import plotly.io as pio
import plotly.graph_objects as go

from dextrade.models import DexTradeConfig
from dextrade.api import DexTradeAPI
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler
from plugin import TGBFPlugin


class Chart(TGBFPlugin):
    """Plugin for displaying candlestick charts using DexTrade API"""

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.chart_callback, block=False))

    @TGBFPlugin.logging()
    @TGBFPlugin.send_typing()
    async def chart_callback(self, update: Update, context: CallbackContext):
        """Handle the candlestick chart command"""
        # Don't deal with edited messages
        if not update.message:
            return

        if len(context.args) not in [1, 2]:
            await update.message.reply_text(
                await self.get_info()
            )
            return

        # Initialize API client from env file
        try:
            client = DexTradeAPI(DexTradeConfig(
                login_token=self.cfg.get('api-token'),
                secret=self.cfg.get('api-secret'),
                )
            )
        except ValueError as e:
            await update.message.reply_text(f"{con.ERROR} API configuration error: {e}")
            return

        # Parse trading pair
        pair = context.args[0].upper()

        # Default to 1h candles for last 100 hours if no timeframe specified
        period = "60"
        limit = 100

        # Parse timeframe if provided
        if len(context.args) == 2:
            timeframe = context.args[1].lower()
            if timeframe.endswith('h'):
                try:
                    limit = int(timeframe[:-1])
                    period = "60"  # 1 hour candles
                except ValueError:
                    await update.message.reply_text(
                        f"{con.ERROR} Invalid timeframe format. Use <number>h for hours"
                    )
                    return
            elif timeframe.endswith('d'):
                try:
                    limit = int(timeframe[:-1])
                    period = "D"  # Daily candles
                except ValueError:
                    await update.message.reply_text(
                        f"{con.ERROR} Invalid timeframe format. Use <number>d for days"
                    )
                    return
            else:
                await update.message.reply_text(
                    f"{con.ERROR} Invalid timeframe format. Use h for hours or d for days"
                )
                return

        message = await update.message.reply_text(f"{con.WAIT} Fetching candlestick data...")

        try:
            # Get candlestick data
            candles = client.get_candlesticks(pair=pair, period=period, limit=limit)

            if not candles:
                await message.edit_text(f"{con.ERROR} No data available for {pair}")
                return

            # Process candlestick data
            # Note: Price values are scaled by 10^8, volume by 10^6
            df = pd.DataFrame(candles)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            df['open'] = df['open'] / 1e8
            df['high'] = df['high'] / 1e8
            df['low'] = df['low'] / 1e8
            df['close'] = df['close'] / 1e8
            df['volume'] = df['volume'] / 1e6

            # Create candlestick chart
            fig = go.Figure(data=[go.Candlestick(
                x=df['time'],
                open=df['open'],
                high=df['high'],
                low=df['low'],
                close=df['close']
            )])

            # Update layout
            fig.update_layout(
                title=dict(
                    text=f"{pair} Candlestick Chart",
                    x=0.5,
                    font=dict(
                        size=24
                    ),
                ),
                yaxis_title="Price",
                xaxis_title="Time",
                paper_bgcolor='rgb(233,233,233)',
                plot_bgcolor='rgb(233,233,233)',
                yaxis=dict(
                    gridcolor="rgb(215, 215, 215)",
                    zerolinecolor="rgb(215, 215, 215)"
                ),
                xaxis=dict(
                    gridcolor="rgb(215, 215, 215)",
                    rangeslider=dict(visible=False)
                ),
                height=600,
                showlegend=False
            )

            # Add horizontal line at current price
            current_price = df['close'].iloc[-1]
            fig.add_hline(
                y=current_price,
                line_dash="dot",
                line_color="green",
                opacity=0.5
            )

            # Send chart as photo
            await message.delete()
            await update.message.reply_photo(
                photo=io.BufferedReader(io.BytesIO(pio.to_image(fig, format='png'))),
                caption=(
                    f"{con.INFO} Last price: {current_price:.8f}\n"
                    f"{con.INFO} 24h Volume: {df['volume'].sum():.2f}"
                )
            )

        except Exception as e:
            await message.edit_text(f"{con.ERROR} Error creating chart: {e}")
            self.log.error(f"Chart error: {e}")
            await self.notify(e)

    async def get_info(self):
        """Return help message for the plugin"""
        return (
            "<b>Create candlestick chart for a trading pair</b>\n\n"
            "◾️ Show 1-hour candles for last 100 hours:\n"
            f"<code>/{self.handle} BTCUSDT</code>\n\n"
            "◾️ Show 1-hour candles for specific number of hours:\n"
            f"<code>/{self.handle} BTCUSDT 24h</code>\n\n"
            "◾️ Show daily candles for specific number of days:\n"
            f"<code>/{self.handle} BTCUSDT 30d</code>"
        )