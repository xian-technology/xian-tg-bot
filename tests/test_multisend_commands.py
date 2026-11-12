"""/rain and /bless as a chat member drives them."""

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from telegram.ext import CommandHandler
from xian_py.wallet import Wallet

from tests.test_command_safety import config, plugin_instance, receipt, submission

KINDS = ["rain", "bless"]
SENDER = 1
LINK = '<a href="http://127.0.0.1:8080/tx/ABC123">View Transaction</a>'
TOKENS = [(1, "con_abc", "ABC")]


def address(user_id: int) -> str:
    return Wallet(f"{user_id:x}" * 64).public_key


def make_plugin(kind: str, user_limit: int = 10) -> Any:
    from plg.bless.bless import Bless
    from plg.rain.rain import Rain

    plugin = plugin_instance(Rain if kind == "rain" else Bless, kind)
    plugin._cfg = config({"user_limit": user_limit, "contract": "con_multisend", "function": "send"})
    return plugin


def rejected(message: str) -> SimpleNamespace:
    return SimpleNamespace(submitted=True, accepted=False, tx_hash=None, message=message)


class Chat:
    """Message objects that remember every text shown to the user."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.kwargs: list[dict] = []

    async def edit_text(self, text: str, **kwargs: Any) -> "Chat":
        self.texts.append(text)
        self.kwargs.append(kwargs)
        return self


async def run(kind: str, args: list[str], *, active: Any = None, names: dict | None = None,
              user_limit: int = 10, **client_overrides: Any) -> tuple[Any, SimpleNamespace, Chat, Any]:
    plugin = make_plugin(kind, user_limit)
    handlers: list[Any] = []
    plugin.add_handler = AsyncMock(side_effect=lambda handler, *a, **k: handlers.append(handler))
    await plugin.init()
    [command] = [h for h in handlers if isinstance(h, CommandHandler)]

    client = SimpleNamespace(
        get_approved_amount=AsyncMock(return_value=Decimal(1000)),
        approve=AsyncMock(return_value=submission()),
        send_tx=AsyncMock(return_value=submission()),
        wait_for_tx=AsyncMock(return_value=receipt()),
    )
    for key, value in client_overrides.items():
        setattr(client, key, value)
    plugin.get_xian = AsyncMock(return_value=client)
    plugin.get_wallet = AsyncMock(side_effect=lambda user_id: Wallet(f"{user_id:x}" * 64))
    plugin.get_plugin = lambda name: SimpleNamespace(get_tokens=AsyncMock(return_value=TOKENS))
    plugin.get_info = AsyncMock(return_value="usage")
    plugin.get_resource = AsyncMock(return_value="SELECT users")
    if active is None:
        active = {"success": True, "data": [(SENDER, "me"), (2, "a<b"), (3, "b"), (4, "c")]}
    plugin.exec_sql = AsyncMock(return_value=active)

    names = names if names is not None else {2: "A xian.org", 3: "B XIAN.ORG", 4: "C"}

    async def get_chat(user_id: int) -> SimpleNamespace:
        name = names[user_id]
        if isinstance(name, Exception):
            raise name
        first, _, last = name.partition(" ")
        return SimpleNamespace(first_name=first, last_name=last or None)

    chat = Chat()
    update = SimpleNamespace(
        message=SimpleNamespace(from_user=SimpleNamespace(id=SENDER), reply_text=AsyncMock(return_value=chat)),
        effective_chat=SimpleNamespace(id=-100),
    )
    context = SimpleNamespace(args=args, bot=SimpleNamespace(get_chat=AsyncMock(side_effect=get_chat)))
    await inspect.unwrap(command.callback)(plugin, update, context)
    return plugin, client, chat, update


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("args", [[], ["1"], ["a", "b", "c", "d"]])
async def test_wrong_argument_count_shows_usage(kind: str, args: list[str]) -> None:
    _, client, chat, update = await run(kind, args)
    update.message.reply_text.assert_awaited_once_with("usage")
    client.send_tx.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    ("args", "shown"),
    [
        (["xyz", "10", "1h"], "‼ Unknown contract. Make sure you added this token to your token list first "
                              "with <code>/tokens add contract_name</code>"),
        (["con_xyz", "10", "1h"], "‼ Unknown contract. Make sure you added this token to your token list "
                                  "first with <code>/tokens add contract_name</code>"),
        (["ten", "1h"], "‼ Amount not valid!"),
        (["0", "1h"], "‼ Amount not valid!"),
        (["10", "1s"], "‼ Allowed time units are <code>m</code> (minute) and <code>h</code> (hour)"),
        (["10", "xh"], "‼ Time frame not valid!"),
        (["10", "0m"], "‼ Time frame not valid!"),
        (["10", "-1h"], "‼ Time frame not valid!"),
    ],
)
async def test_invalid_requests_are_refused(kind: str, args: list[str], shown: str) -> None:
    plugin, client, chat, _ = await run(kind, args)
    assert chat.texts == [shown]
    plugin.exec_sql.assert_not_awaited()
    client.send_tx.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(("frame", "span"), [("90m", timedelta(minutes=90)), ("1.5H", timedelta(hours=1.5))])
async def test_recipients_are_users_active_within_the_time_frame(kind: str, frame: str, span: timedelta) -> None:
    plugin, _, _, _ = await run(kind, ["10", frame])
    sql, chat_id, since = plugin.exec_sql.await_args.args
    assert (sql, chat_id) == ("SELECT users", -100)
    since_dt = datetime.strptime(since, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    assert abs(datetime.now(UTC) - span - since_dt) < timedelta(seconds=5)
    assert plugin.exec_sql.await_args.kwargs == {"plugin": "active"}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("token", ["con_abc", "abc", "CON_ABC"])
async def test_tokens_are_found_by_contract_or_ticker(kind: str, token: str) -> None:
    _, client, _, _ = await run(kind, [token, "10", "1h"])
    assert client.send_tx.await_args.args[2]["contract"] == "con_abc"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("active", [{"success": False}, {"success": True, "data": []}])
async def test_unknown_activity_is_reported_to_the_owner(kind: str, active: dict) -> None:
    plugin, client, chat, _ = await run(kind, ["10", "1h"], active=active)
    assert chat.texts == ["‼ Could not determine last active users"]
    plugin.notify.assert_awaited_once_with("‼ Could not determine last active users")
    client.send_tx.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
async def test_the_sender_alone_is_no_audience(kind: str) -> None:
    _, client, chat, _ = await run(kind, ["10", "1h"], active={"success": True, "data": [(SENDER, "me")]})
    assert chat.texts == ["‼ No users found for given time frame"]
    client.send_tx.assert_not_awaited()


async def test_rain_pays_every_other_active_user_up_to_the_limit() -> None:
    _, client, chat, _ = await run("rain", ["10", "1h"], user_limit=2)
    contract, function, kwargs = client.send_tx.await_args.args
    assert (contract, function) == ("con_multisend", "send")
    assert kwargs == {"addresses": [address(2), address(3)], "amount": Decimal("5.0000"), "contract": "currency"}
    assert chat.texts == [
        "🌧 Initiating rain clouds...",
        f"Rained <code>5.0000</code> XIAN each on following users:\na&lt;b, b\n\n{LINK}",
    ]
    assert chat.kwargs[-1] == {"disable_web_page_preview": True}


async def test_bless_pays_only_users_named_after_xian_org() -> None:
    names = {2: "A xian.org", 3: RuntimeError("chat hidden"), 4: "xian.org fan"}
    _, client, chat, _ = await run("bless", ["ABC", "10", "1h"], names=names)
    kwargs = client.send_tx.await_args.args[2]
    assert kwargs == {"addresses": [address(2), address(4)], "amount": Decimal("5.0000"), "contract": "con_abc"}
    assert chat.texts == [
        "🌧 Blessing users...",
        f"Blessed 2 users with <code>5.0000</code> ABC:\na&lt;b, c\n\n{LINK}",
    ]


async def test_bless_stops_at_the_user_limit() -> None:
    names = {2: "A xian.org", 3: "B xian.org", 4: "C xian.org"}
    _, client, _, _ = await run("bless", ["10", "1h"], names=names, user_limit=2)
    assert client.send_tx.await_args.args[2]["addresses"] == [address(2), address(3)]


async def test_bless_without_eligible_users_sends_nothing() -> None:
    _, client, chat, _ = await run("bless", ["10", "1h"], names={2: "A", 3: "B", 4: "C"})
    assert chat.texts == ["🌧 Blessing users...", "‼ No users found with XIAN.ORG in name"]
    client.send_tx.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
async def test_amount_too_small_to_split_is_refused(kind: str) -> None:
    _, client, chat, _ = await run(kind, ["0.0001", "1h"])
    assert chat.texts[-1] == "‼ Amount is too small to split at four decimal places"
    client.send_tx.assert_not_awaited()


@pytest.mark.parametrize(
    ("kind", "allowance", "approves"),
    # rain splits 10 over 3 users (3.3333 each), bless over the 2 eligible (5 each)
    [("rain", "9.9999", False), ("rain", "9.9998", True), ("bless", "10", False), ("bless", "9.9999", True)],
)
async def test_allowance_is_topped_up_to_exactly_the_distribution(kind: str, allowance: str, approves: bool) -> None:
    needed = Decimal("9.9999") if kind == "rain" else Decimal("10.0000")
    _, client, _, _ = await run(kind, ["10", "1h"], get_approved_amount=AsyncMock(return_value=Decimal(allowance)))
    client.get_approved_amount.assert_awaited_once_with("con_multisend", token="currency")
    if approves:
        client.approve.assert_awaited_once_with("con_multisend", token="currency", amount=needed)
    else:
        client.approve.assert_not_awaited()
    client.send_tx.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    ("overrides", "shown", "notified"),
    [
        ({"get_approved_amount": AsyncMock(side_effect=RuntimeError("rpc <down>"))}, "‼ rpc &lt;down&gt;", True),
        ({"approve": AsyncMock(side_effect=RuntimeError("no <chi>"))}, "‼ no &lt;chi&gt;", True),
        ({"approve": AsyncMock(return_value=rejected("bad"))}, "‼ Can not approve contract!", False),
        ({"wait_for_tx": AsyncMock(return_value=receipt(success=False, result="<x>"))},
         "❌ Approval failed: &lt;x&gt;", False),
    ],
)
async def test_failed_allowance_stops_the_distribution(kind: str, overrides: dict, shown: str, notified: bool) -> None:
    overrides.setdefault("get_approved_amount", AsyncMock(return_value=Decimal(0)))
    plugin, client, chat, _ = await run(kind, ["10", "1h"], **overrides)
    assert chat.texts[-1] == shown
    client.send_tx.assert_not_awaited()
    assert plugin.notify.await_count == int(notified)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    ("overrides", "shown"),
    [
        ({"send_tx": AsyncMock(side_effect=RuntimeError("send <x>"))}, "‼ send &lt;x&gt;"),
        ({"send_tx": AsyncMock(return_value=rejected("too <few> chi"))}, "❌ too &lt;few&gt; chi"),
        ({"wait_for_tx": AsyncMock(return_value=receipt(success=False, result="<late>"))}, "❌ &lt;late&gt;"),
    ],
)
async def test_failed_distribution_shows_the_reason(kind: str, overrides: dict, shown: str) -> None:
    _, _, chat, _ = await run(kind, ["10", "1h"], **overrides)
    assert chat.texts[-1] == shown


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("frame", ["nanm", "infh", "1e12h", "1e8h"])
async def test_unrepresentable_time_frames_are_refused(kind: str, frame: str) -> None:
    plugin, _, chat, _ = await run(kind, ["10", frame])
    assert chat.texts == ["‼ Time frame not valid!"]
    plugin.exec_sql.assert_not_awaited()
