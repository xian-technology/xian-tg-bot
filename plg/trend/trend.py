import io
import constants as con
import plotly.io as pio
import plotly.graph_objs as go

from io import BytesIO
from datetime import datetime
from plugin import TGBFPlugin
from telegram import Update
from pytrends.request import TrendReq
from telegram.ext import CallbackContext, CommandHandler


class Trend(TGBFPlugin):

    async def init(self):
        await self.add_handler(CommandHandler(self.handle, self.trend_callback, block=False))

    @TGBFPlugin.send_typing()
    async def trend_callback(self, update: Update, context: CallbackContext):
        # Don't deal with edited messages
        if not update.message:
            return

        if not context.args or len(context.args) < 2:
            await update.message.reply_text(f'{await self.get_info()}')
            return

        # Time frame
        tf = context.args[-1]

        if tf != "all":
            now = datetime.today()
            date = self.get_date(now, tf)

            if not date:
                msg = f"{con.ERROR} Time frame not formatted correctly"
                await update.message.reply_text(msg)
                return
            else:
                tf = f"{str(date)[:10]} {str(now)[:10]}"

        # Remove time frame info from arguments
        context.args = context.args[:-1]

        # Check for brackets and combine keywords
        args = self.combine_args(context.args)

        if len(args) > 5:
            msg = f"{con.ERROR} You can only compare up to five search terms"
            await update.message.reply_text(msg)
            return

        try:
            pytrends = TrendReq(hl='en-US', tz=360)
            pytrends.build_payload(args, cat=0, timeframe=tf, geo='', gprop='')

            data = pytrends.interest_over_time()
        except Exception as e:
            await update.message.reply_text(str(e))
            self.log.error(e)
            await self.notify(e)
            return

        no_data = list()
        tr_data = list()
        for kw in args:
            if data.empty:
                no_data = args
                break

            if data.get(kw).empty:
                no_data.append(kw)
                continue

            tr_data.append(go.Scatter(x=data.get(kw).index, y=data.get(kw).values, name=kw))

        if no_data:
            msg = f"{con.ERROR} No data for search term(s): {', '.join(no_data)}"
            await update.message.reply_text(msg)

        if len(args) == len(no_data):
            return

        layout = go.Layout(
            title=dict(
                text="Google Trends - Interest Over Time",
                x=0.5,
                font=dict(
                    size=24
                ),
            ),
            legend=dict(
                orientation="h",
                yanchor="top",
                xanchor="center",
                y=1.12,
                x=0.5
            ),
            xaxis=dict(
                gridcolor="rgb(215, 215, 215)"
            ),
            yaxis=dict(
                title="Search Queries",
                showticklabels=False,
                gridcolor="rgb(215, 215, 215)",
                zerolinecolor="rgb(215, 215, 215)"
            ),
            paper_bgcolor='rgb(233,233,233)',
            plot_bgcolor='rgb(233,233,233)',
            showlegend=True)

        try:
            fig = go.Figure(data=tr_data, layout=layout)
        except Exception as e:
            await update.message.reply_text(str(e))
            self.log.error(e)
            await self.notify(e)
            return

        await update.message.reply_photo(io.BufferedReader(BytesIO(pio.to_image(fig, format="png"))))

    def combine_args(self, args):
        combine = list()
        new_args = list()
        for arg in args:
            if arg.startswith("("):
                combine.append(arg[1:])
                continue
            elif arg.endswith(")"):
                if combine:
                    arg = f"{' '.join(combine)} {arg[:len(arg) - 1]}"
                    combine.clear()
            elif combine:
                combine.append(arg)
                continue
            new_args.append(arg)
        return new_args

    def get_date(self, from_date, time_span):
        resolution = time_span.strip()[-1:].lower()
        time_frame = time_span.strip()[:-1]

        valid = "d,m,y"
        if resolution not in valid.split(","):
            return None

        if not time_frame.isnumeric():
            return None

        time_frame = int(time_frame)

        from datetime import timedelta

        if resolution == "d":
            t = from_date - timedelta(days=time_frame)
        elif resolution == "m":
            t = from_date - timedelta(days=time_frame * 30)
        elif resolution == "y":
            t = from_date - timedelta(days=time_frame * 365)
        else:
            return None

        return str(t)[:10]
