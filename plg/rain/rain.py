import html
from typing import Any

from telegram.ext import CallbackContext

import constants as con
from multisend import Distribution, MultisendPlugin, Request, share


class Rain(MultisendPlugin):
    """Split the amount over every active user, up to the user limit."""

    async def distribution(self, context: CallbackContext, user_data: list[Any],
                           request: Request, message: Any) -> Distribution | None:
        user_limit = self.cfg.get("user_limit")
        if len(user_data) > user_limit:
            self.log.warning(f"User limit of {user_limit} hit")
            user_data = user_data[:user_limit]

        message = await message.edit_text(f"{con.RAIN} Initiating rain clouds...")

        # Only paid recipients share the total
        amount_single = share(request.amount_total, len(user_data))
        if isinstance(amount_single, str):
            await message.edit_text(amount_single)
            return None

        addresses = []
        for to_user_id, _ in user_data:
            addresses.append((await self.get_wallet(to_user_id)).public_key)
            self.log.bind(user_id=to_user_id).info("Recipient added to distribution")

        names = ", ".join(html.escape(user[1]) for user in user_data)
        text = f"Rained <code>{amount_single}</code> {request.ticker} each on following users:\n{names}"
        return Distribution(message, addresses, amount_single, text)
