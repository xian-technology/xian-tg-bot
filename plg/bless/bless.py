import html
from typing import Any

from telegram.ext import CallbackContext

import constants as con
from logging_setup import error_context
from multisend import Distribution, MultisendPlugin, Request, share


class Bless(MultisendPlugin):
    """Split the amount over active users with xian.org in their name."""

    async def distribution(self, context: CallbackContext, user_data: list[Any],
                           request: Request, message: Any) -> Distribution | None:
        message = await message.edit_text(f"{con.RAIN} Blessing users...")

        user_limit = self.cfg.get("user_limit")
        addresses = []
        names = []
        # Check users in order until enough eligible ones are found
        for to_user_id, to_username in user_data:
            try:
                if not await self._named_after_xian_org(context, to_user_id):
                    continue
                addresses.append((await self.get_wallet(to_user_id)).public_key)
            except Exception as e:
                self.log.bind(**error_context(e), handler='rain_callback').error('Rain callback failed')
                continue
            names.append(html.escape(to_username))
            if len(addresses) >= user_limit:
                self.log.warning(f"User limit of {user_limit} hit")
                break

        if not addresses:
            await message.edit_text(f"{con.ERROR} No users found with XIAN.ORG in name")
            return None

        amount_single = share(request.amount_total, len(addresses))
        if isinstance(amount_single, str):
            await message.edit_text(amount_single)
            return None

        text = (f"Blessed {len(addresses)} users with <code>{amount_single}</code> {request.ticker}:\n"
                f"{', '.join(names)}")
        return Distribution(message, addresses, amount_single, text)

    async def _named_after_xian_org(self, context: CallbackContext, user_id: int) -> bool:
        chat = await context.bot.get_chat(user_id)
        full_name = f"{chat.first_name} {chat.last_name or ''}".strip()
        eligible = "xian.org" in full_name.lower()
        self.log.bind(user_id=user_id).info("Recipient eligible" if eligible else "Recipient skipped")
        return eligible
