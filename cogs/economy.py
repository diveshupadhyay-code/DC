import re
import secrets
import random
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands, tasks
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from utils.db import db
from utils.helpers import BOT_OWNER_ID, ctx_owner

economy_col  = db["economy_global"]
trades_col   = db["trades"]
activity_col = db["server_activity"]
inv_col      = db["investments"]
items_col    = db["shop_items"]
loans_col    = db["loans"]

OWNER_ID       = str(BOT_OWNER_ID)
CURRENCY       = "HC"
CURRENCY_EMOJI = "<:cash:1528209977951649812>"
GENIE          = "<:genie:1528208048735125658>"

GOLD   = 0xF2C94C
GREEN  = 0x57F287
RED    = 0xED4245
AMBER  = 0xFEE75C
VIOLET = 0x8B7CF6
BLUE   = 0x5B8DEF
SLATE  = 0x2B2D31

DAILY_MIN     = 150
DAILY_MAX     = 350
WORK_BASE_MIN = 50
WORK_BASE_MAX = 150
WORK_CD       = 3600
DAILY_CD      = 86400
ROB_CD        = 300

MIN_BET         = 10
MAX_MONEY       = 1_000_000_000
MAX_TRADE       = 10_000_000
MAX_OPEN_TRADES = 5

LOAN_MIN          = 100
LOAN_MAX          = 10_000
LOAN_MIN_AGE_DAYS = 7

MAX_BUY        = 20
MAX_HOLD       = 99
CONFIRM_ABOVE  = 1000

ACTIVITY_BOOST_THRESHOLD   = 30
ACTIVITY_PENALTY_THRESHOLD = 5

rng = random.SystemRandom()

WORK_RESPONSES = [
    "You delivered packages and earned {amount} HC.",
    "You fixed a bug in production code for {amount} HC.",
    "You made chai for the whole office and got {amount} HC.",
    "You sold memes online and made {amount} HC.",
    "You drove an auto-rickshaw and collected {amount} HC.",
    "You tutored a student and earned {amount} HC.",
    "You won a gaming tournament prize of {amount} HC.",
    "You streamed and got {amount} HC in donations.",
    "You walked dogs around the block for {amount} HC.",
    "You helped someone with their resume for {amount} HC.",
]

INVESTMENTS = {
    "safe":     {"name": "Safe Bond",    "emoji": "🏦", "min_r": 0.05, "max_r": 0.12, "risk": 0.03, "loss": 0.05, "hours": 4,  "desc": "5-12% gain · 3% chance of loss · 4 hours"},
    "market":   {"name": "Stock Market", "emoji": "📈", "min_r": 0.10, "max_r": 0.35, "risk": 0.25, "loss": 0.20, "hours": 8,  "desc": "10-35% gain · 25% chance of loss · 8 hours"},
    "crypto":   {"name": "Crypto",       "emoji": "₿",  "min_r": 0.20, "max_r": 0.80, "risk": 0.45, "loss": 0.50, "hours": 12, "desc": "20-80% gain · 45% chance of loss · 12 hours"},
    "business": {"name": "Business",     "emoji": "🏢", "min_r": 0.15, "max_r": 0.50, "risk": 0.30, "loss": 0.30, "hours": 24, "desc": "15-50% gain · 30% chance of loss · 24 hours"},
}

SHOP = {
    "energy": {
        "name": "Energy Drink", "emoji": "⚡", "price": 350, "group": "power",
        "desc": "Your next work pays double.",
        "use": "You drank the Energy Drink. Your next work will pay double.",
    },
    "padlock": {
        "name": "Padlock", "emoji": "🔒", "price": 600, "group": "power",
        "desc": "Stops the next person who tries to rob you.",
        "use": "Your Padlock is on guard. It will stop the next robbery.",
    },
    "restpass": {
        "name": "Rest Pass", "emoji": "🛌", "price": 500, "group": "power",
        "desc": "Lets you work again right away.",
        "use": "You feel fully rested. You can work again now.",
    },
    "safetynet": {
        "name": "Safety Net", "emoji": "🛟", "price": 900, "group": "power",
        "desc": "Gives all your money back if your next investment loses.",
        "use": "Your Safety Net is ready for your next investment.",
    },
    "rose": {
        "name": "Rose", "emoji": "🌹", "price": 100, "group": "gift",
        "desc": "A small gift to say thank you.",
        "use": "You held up a rose. What a lovely moment.",
    },
    "teddy": {
        "name": "Teddy Bear", "emoji": "🧸", "price": 250, "group": "gift",
        "desc": "A soft friend for someone special.",
        "use": "You hugged your teddy bear. So warm and cozy.",
    },
    "trophy": {
        "name": "Trophy", "emoji": "🏆", "price": 1000, "group": "gift",
        "desc": "A shiny prize to show off or give away.",
        "use": "You placed your trophy on display. It shines bright.",
    },
    "crown": {
        "name": "Crown", "emoji": "👑", "price": 5000, "group": "gift",
        "desc": "The rarest item in the shop.",
        "use": "You put on the crown. Everyone looks up to you.",
    },
}

EFFECTS = {"energy": "boost_work", "padlock": "shield", "safetynet": "insurance"}


def hc(amount: int) -> str:
    return f"{amount:,} {CURRENCY}"


def card(title=None, description=None, color=GOLD, footer=None, icon=None) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    if footer:
        embed.set_footer(text=footer, icon_url=icon)
    return embed


def bad(text: str) -> discord.Embed:
    return card(description=f"⚠️  {text}", color=RED)


async def say(ctx, embed: discord.Embed, msg=None, **kwargs):
    if msg is not None:
        try:
            return await msg.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass
    return await ctx.reply(embed=embed, mention_author=False, **kwargs)


async def fail(ctx, text: str, msg=None):
    return await say(ctx, bad(text), msg)


class ConfirmView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=30)
        self.user_id = user_id
        self.answer = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("These buttons are not for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.answer = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.answer = False
        await interaction.response.defer()
        self.stop()


async def ask_confirm(ctx, embed: discord.Embed):
    view = ConfirmView(ctx.author.id)
    msg = await ctx.reply(embed=embed, view=view, mention_author=False)
    await view.wait()
    if view.answer is True:
        for child in view.children:
            child.disabled = True
        try:
            await msg.edit(view=view)
        except discord.HTTPException:
            pass
        return True, msg
    text = "Cancelled. Nothing was charged." if view.answer is False else "Time is up. Nothing was charged."
    await say(ctx, card(description=f"✖️  {text}", color=SLATE), msg)
    return False, msg


def parse_amount(text, all_value=None):
    if text is None:
        return None
    t = text.strip().lower().replace(",", "")
    if t in ("all", "max") and all_value is not None:
        return all_value
    if t.isascii() and t.isdigit() and len(t) <= 12:
        return int(t)
    return None


def find_item(text: str):
    t = re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).strip()
    if not t:
        return None
    if t in SHOP:
        return t
    squeezed = t.replace(" ", "")
    for key, item in SHOP.items():
        name = item["name"].lower()
        if t == name or squeezed == name.replace(" ", ""):
            return key
    return None


def parse_item(text):
    if not text:
        return None, 1
    parts = text.strip().lower().split()
    qty = 1
    if len(parts) > 1 and parts[-1].isascii() and parts[-1].isdigit() and len(parts[-1]) <= 3:
        qty = int(parts[-1])
        parts = parts[:-1]
    return find_item(" ".join(parts)), qty


def _aware(ts):
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _cd_left(ts, cooldown: int) -> int:
    if not ts:
        return 0
    ts = _aware(ts)
    return max(0, int(cooldown - (datetime.now(timezone.utc) - ts).total_seconds()))


def _fmt_cd(s: int) -> str:
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return f"{h}h {m}m" if h else (f"{m}m {sec}s" if m else f"{sec}s")


async def get_account(user_id: int) -> dict:
    return await economy_col.find_one_and_update(
        {"user_id": str(user_id)},
        {"$setOnInsert": {"wallet": 0, "bank": 0, "last_daily": None, "last_work": None, "total_earned": 0}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )


async def add_wallet(user_id, amount: int, earned: int = 0):
    inc = {"wallet": amount}
    if earned > 0:
        inc["total_earned"] = earned
    await economy_col.update_one({"user_id": str(user_id)}, {"$inc": inc}, upsert=True)


async def add_bank(user_id, amount: int):
    await economy_col.update_one({"user_id": str(user_id)}, {"$inc": {"bank": amount}}, upsert=True)


async def take_wallet(user_id, amount: int) -> bool:
    if amount <= 0:
        return False
    res = await economy_col.update_one(
        {"user_id": str(user_id), "wallet": {"$gte": amount}},
        {"$inc": {"wallet": -amount}},
    )
    return res.modified_count == 1


async def take_bank(user_id, amount: int) -> bool:
    if amount <= 0:
        return False
    res = await economy_col.update_one(
        {"user_id": str(user_id), "bank": {"$gte": amount}},
        {"$inc": {"bank": -amount}},
    )
    return res.modified_count == 1


async def move_wallet(from_id, to_id, amount: int) -> bool:
    if not await take_wallet(from_id, amount):
        return False
    try:
        await add_wallet(to_id, amount)
    except Exception:
        await add_wallet(from_id, amount)
        raise
    return True


async def claim_cooldown(uid: str, field: str, seconds: int) -> bool:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=seconds)
    res = await economy_col.update_one(
        {"user_id": uid, "$or": [{field: None}, {field: {"$lte": cutoff}}]},
        {"$set": {field: now}},
    )
    return res.modified_count == 1


async def get_items(uid: str) -> dict:
    doc = await items_col.find_one({"user_id": uid})
    return (doc or {}).get("items", {})


async def take_item(uid: str, key: str, qty: int) -> bool:
    if key not in SHOP or qty <= 0:
        return False
    res = await items_col.update_one(
        {"user_id": uid, f"items.{key}": {"$gte": qty}},
        {"$inc": {f"items.{key}": -qty}},
    )
    return res.modified_count == 1


async def give_item(uid: str, key: str, qty: int):
    if key not in SHOP or qty <= 0:
        return
    await items_col.update_one({"user_id": uid}, {"$inc": {f"items.{key}": qty}}, upsert=True)


async def has_loan(uid: str) -> bool:
    return await loans_col.find_one({"user_id": uid}, {"_id": 1}) is not None


async def settle_loan(user_id, source: str = "bank") -> int:
    uid = str(user_id)
    loan = await loans_col.find_one({"user_id": uid, "status": "active"})
    if not loan:
        return 0
    amount = loan["amount"]
    enough = await economy_col.find_one({"user_id": uid, source: {"$gte": amount}}, {"_id": 1})
    if not enough:
        return 0
    claimed = await loans_col.find_one_and_update(
        {"user_id": uid, "status": "active"},
        {"$set": {"status": "settling"}},
    )
    if not claimed:
        return 0
    taken = await take_bank(uid, amount) if source == "bank" else await take_wallet(uid, amount)
    if not taken:
        await loans_col.update_one({"user_id": uid}, {"$set": {"status": "active"}})
        return 0
    await add_bank(OWNER_ID, amount)
    await loans_col.delete_one({"user_id": uid})
    return amount


def loan_paid_card(amount: int) -> discord.Embed:
    return card(
        "✅  Loan Paid Back",
        f"Your loan of **{hc(amount)}** has been paid back.\nYou are debt free. Well done!",
        GREEN,
    )


async def _track_message(guild_id: int):
    now = datetime.now(timezone.utc)
    gid = str(guild_id)
    doc = await activity_col.find_one({"guild_id": gid})
    start = _aware(doc.get("window_start")) if doc else None

    if not start or (now - start).total_seconds() > 3600:
        await activity_col.update_one(
            {"guild_id": gid},
            {"$set": {"msg_count": 1, "window_start": now}},
            upsert=True,
        )
    else:
        await activity_col.update_one({"guild_id": gid}, {"$inc": {"msg_count": 1}})


async def _get_activity_multiplier(guild_id: int) -> tuple[float, str]:
    doc = await activity_col.find_one({"guild_id": str(guild_id)})
    if not doc:
        return 1.0, ""

    now = datetime.now(timezone.utc)
    start = _aware(doc.get("window_start"))
    if start and (now - start).total_seconds() > 3600:
        return 0.7, "Server was quiet — 0.7x earnings"

    count = doc.get("msg_count", 0)
    if count >= ACTIVITY_BOOST_THRESHOLD:
        return 1.5, f"Server is busy ({count} messages/hour) — 1.5x boost!"
    if count >= 15:
        return 1.2, f"Server is warming up ({count} messages/hour) — 1.2x boost"
    if count <= ACTIVITY_PENALTY_THRESHOLD:
        return 0.7, f"Server is quiet ({count} messages/hour) — 0.7x earnings"
    return 1.0, f"{count} messages/hour"


class Economy(commands.Cog):
    SLOTS     = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣", "⭐"]
    SLOT_MULT = {"💎": 5, "7️⃣": 4, "⭐": 3, "🍇": 2.5, "🍊": 2, "🍋": 1.5, "🍒": 1.2}

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        for col, field in (
            (economy_col, "user_id"),
            (inv_col, "user_id"),
            (items_col, "user_id"),
            (loans_col, "user_id"),
            (trades_col, "trade_id"),
        ):
            try:
                await col.create_index(field, unique=True)
            except Exception:
                pass
        self.loan_watch.start()

    async def cog_unload(self):
        self.loan_watch.cancel()

    async def cog_check(self, ctx):
        return ctx.guild is not None

    async def cog_after_invoke(self, ctx):
        try:
            paid = await settle_loan(ctx.author.id)
            if paid:
                await ctx.send(embed=loan_paid_card(paid))
        except Exception:
            pass

    @tasks.loop(seconds=60)
    async def loan_watch(self):
        try:
            loans = await loans_col.find({"status": "active"}).to_list(500)
            for loan in loans:
                paid = await settle_loan(loan["user_id"])
                if paid:
                    await self._tell_loan_paid(int(loan["user_id"]), paid)
        except Exception:
            pass

    @loan_watch.before_loop
    async def before_loan_watch(self):
        await self.bot.wait_until_ready()

    async def _tell_loan_paid(self, user_id: int, amount: int):
        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.HTTPException:
                return
        try:
            await user.send(embed=loan_paid_card(amount))
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        await _track_message(message.guild.id)

    @commands.command(aliases=["bal", "wallet", "cash"])
    async def balance(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        title = f"{member.display_name}'s Balance"

        if str(member.id) == OWNER_ID and str(ctx.author.id) != OWNER_ID:
            embed = card(title, "🔒  This account is private.", GOLD)
            embed.set_thumbnail(url=member.display_avatar.url)
            return await say(ctx, embed)

        acc   = await get_account(member.id)
        total = acc["wallet"] + acc["bank"]

        embed = card(title, color=GOLD, footer=f"All time earned: {hc(acc.get('total_earned', 0))}")
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name=f"{CURRENCY_EMOJI}  Wallet", value=f"**{hc(acc['wallet'])}**", inline=True)
        embed.add_field(name="🏦  Bank", value=f"**{hc(acc['bank'])}**", inline=True)
        embed.add_field(name="📊  Net Worth", value=f"**{hc(total)}**", inline=True)

        if member.id == ctx.author.id:
            loan = await loans_col.find_one({"user_id": str(member.id)})
            if loan:
                embed.add_field(name="💳  Loan Due", value=f"**{hc(loan['amount'])}**", inline=False)
        await say(ctx, embed)

    @commands.command()
    async def daily(self, ctx):
        uid = str(ctx.author.id)
        await get_account(ctx.author.id)

        if not await claim_cooldown(uid, "last_daily", DAILY_CD):
            acc = await get_account(ctx.author.id)
            left = _cd_left(acc.get("last_daily"), DAILY_CD)
            return await fail(ctx, f"You already took your daily reward. Come back in **{_fmt_cd(left)}**.")

        amount = rng.randint(DAILY_MIN, DAILY_MAX)
        await add_wallet(ctx.author.id, amount, earned=amount)
        await say(ctx, card(
            "🎁  Daily Reward",
            f"You received **{hc(amount)}**.",
            GOLD,
            footer="Come back tomorrow · Your balance works in every server",
        ))

    @commands.command()
    async def work(self, ctx):
        uid = str(ctx.author.id)
        await get_account(ctx.author.id)

        if not await claim_cooldown(uid, "last_work", WORK_CD):
            acc = await get_account(ctx.author.id)
            left = _cd_left(acc.get("last_work"), WORK_CD)
            return await fail(ctx, f"You are tired. Rest for **{_fmt_cd(left)}**.")

        mult, label = await _get_activity_multiplier(ctx.guild.id)
        amount = max(1, int(rng.randint(WORK_BASE_MIN, WORK_BASE_MAX) * mult))

        boosted = await economy_col.find_one_and_update(
            {"user_id": uid, "boost_work": True},
            {"$set": {"boost_work": False}},
        )
        if boosted:
            amount *= 2

        story = rng.choice(WORK_RESPONSES).format(amount=f"{amount:,}")
        await add_wallet(ctx.author.id, amount, earned=amount)
        acc = await get_account(ctx.author.id)

        embed = card(description=f"{CURRENCY_EMOJI}  {story}", color=GREEN, footer=f"Wallet: {hc(acc['wallet'])} · Next work in 1 hour")
        if boosted:
            embed.add_field(name="⚡  Energy Drink", value="Your earnings were doubled!", inline=False)
        if label:
            embed.add_field(name="Server Activity", value=label, inline=False)
        await say(ctx, embed)

    @commands.command(aliases=["activity"])
    async def serverstatus(self, ctx):
        mult, label = await _get_activity_multiplier(ctx.guild.id)
        doc   = await activity_col.find_one({"guild_id": str(ctx.guild.id)})
        count = doc.get("msg_count", 0) if doc else 0

        if mult >= 1.5:
            color, status = GREEN, "Busy"
        elif mult >= 1.0:
            color, status = AMBER, "Normal"
        else:
            color, status = RED, "Quiet"

        embed = card(f"Server Activity — {ctx.guild.name}", color=color, footer="Keep chatting to boost everyone's work pay!")
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Messages (1 hour)", value=f"{count}", inline=True)
        embed.add_field(name="Work Pay", value=f"**{mult}x**", inline=True)
        embed.add_field(
            name="How it works",
            value=(
                "30 or more messages/hour → **1.5x** work pay\n"
                "15 to 29 messages/hour → **1.2x** work pay\n"
                "6 to 14 messages/hour → **1.0x** normal pay\n"
                "0 to 5 messages/hour → **0.7x** work pay\n"
                "No chat for over 1 hour → **0.7x** work pay"
            ),
            inline=False,
        )
        await say(ctx, embed)

    @commands.command(aliases=["dep"])
    async def deposit(self, ctx, amount: str = None):
        p = ctx.clean_prefix
        if not amount:
            return await fail(ctx, f"Use `{p}deposit <amount>` or `{p}deposit all`.")
        acc = await get_account(ctx.author.id)
        amt = parse_amount(amount, acc["wallet"])
        if amt is None or amt <= 0:
            return await fail(ctx, "Please enter an amount above 0, like `500` or `all`.")

        res = await economy_col.update_one(
            {"user_id": str(ctx.author.id), "wallet": {"$gte": amt}},
            {"$inc": {"wallet": -amt, "bank": amt}},
        )
        if res.modified_count != 1:
            return await fail(ctx, f"Your wallet only has **{hc(acc['wallet'])}**.")

        acc = await get_account(ctx.author.id)
        await say(ctx, card(
            "🏦  Deposit Done",
            f"You moved **{hc(amt)}** to your bank.",
            GREEN,
            footer=f"Wallet: {hc(acc['wallet'])} · Bank: {hc(acc['bank'])}",
        ))

    @commands.command(aliases=["with"])
    async def withdraw(self, ctx, amount: str = None):
        p = ctx.clean_prefix
        if not amount:
            return await fail(ctx, f"Use `{p}withdraw <amount>` or `{p}withdraw all`.")
        acc = await get_account(ctx.author.id)
        amt = parse_amount(amount, acc["bank"])
        if amt is None or amt <= 0:
            return await fail(ctx, "Please enter an amount above 0, like `500` or `all`.")

        res = await economy_col.update_one(
            {"user_id": str(ctx.author.id), "bank": {"$gte": amt}},
            {"$inc": {"bank": -amt, "wallet": amt}},
        )
        if res.modified_count != 1:
            return await fail(ctx, f"Your bank only has **{hc(acc['bank'])}**.")

        acc = await get_account(ctx.author.id)
        await say(ctx, card(
            "💵  Withdraw Done",
            f"You moved **{hc(amt)}** to your wallet.",
            GREEN,
            footer=f"Wallet: {hc(acc['wallet'])} · Bank: {hc(acc['bank'])}",
        ))

    @commands.command(aliases=["pay", "send"])
    async def transfer(self, ctx, member: discord.Member = None, amount: str = None):
        p = ctx.clean_prefix
        if not member or not amount:
            return await fail(ctx, f"Use `{p}transfer @user <amount>`.")
        if member.id == ctx.author.id:
            return await fail(ctx, "You cannot send HC to yourself.")
        if member.bot:
            return await fail(ctx, "You cannot send HC to bots.")

        amt = parse_amount(amount)
        if amt is None or amt <= 0 or amt > MAX_MONEY:
            return await fail(ctx, "Please enter a proper amount above 0.")
        if await has_loan(str(ctx.author.id)):
            return await fail(ctx, "Please pay back your loan before sending HC to others.")

        if not await move_wallet(ctx.author.id, member.id, amt):
            acc = await get_account(ctx.author.id)
            return await fail(ctx, f"Your wallet only has **{hc(acc['wallet'])}**.")

        await say(ctx, card(
            "💸  Money Sent",
            f"{ctx.author.mention} sent **{hc(amt)}** to {member.mention}.",
            GOLD,
            footer="Works in every server",
        ))

    @commands.command(aliases=["store"])
    async def shop(self, ctx):
        p = ctx.clean_prefix
        acc = await get_account(ctx.author.id)

        def block(group: str) -> str:
            lines = []
            for key, item in SHOP.items():
                if item["group"] == group:
                    lines.append(f"{item['emoji']}  **{item['name']}** · `{item['price']:,} HC` · `{key}`\n> {item['desc']}")
            return "\n".join(lines)

        embed = card(
            f"{GENIE}  Happy Shop",
            f"Spend your HC on useful items or lovely gifts.\nGenie takes care of every purchase.",
            VIOLET,
            footer=f"Your wallet: {hc(acc['wallet'])}",
        )
        embed.add_field(name="✨  Power-ups", value=block("power"), inline=False)
        embed.add_field(name="🎀  Gifts and Keepsakes", value=block("gift"), inline=False)
        embed.add_field(
            name="How to use",
            value=(
                f"`{p}buy <item> [amount]` — buy something\n"
                f"`{p}inventory` — see what you own\n"
                f"`{p}use <item>` — use an item\n"
                f"`{p}gift @user <item> [amount]` — give an item to a friend"
            ),
            inline=False,
        )
        await say(ctx, embed)

    @commands.command()
    async def buy(self, ctx, *, text: str = None):
        p = ctx.clean_prefix
        key, qty = parse_item(text)
        if not key:
            return await fail(ctx, f"I could not find that item. Type `{p}shop` to see the list.\nExample: `{p}buy rose 2`")
        if qty < 1 or qty > MAX_BUY:
            return await fail(ctx, f"You can buy between 1 and {MAX_BUY} at a time.")

        uid   = str(ctx.author.id)
        item  = SHOP[key]
        total = item["price"] * qty

        owned = (await get_items(uid)).get(key, 0)
        if owned + qty > MAX_HOLD:
            return await fail(ctx, f"You can hold up to {MAX_HOLD} of each item. You already have {owned}.")

        acc = await get_account(ctx.author.id)
        if acc["wallet"] < total:
            return await fail(ctx, f"You need **{hc(total)}** but your wallet has **{hc(acc['wallet'])}**.")

        msg = None
        if total >= CONFIRM_ABOVE:
            ok, msg = await ask_confirm(ctx, card(
                f"{GENIE}  Please Confirm",
                f"{item['emoji']}  **{item['name']}** × {qty}\nPrice: **{hc(total)}**",
                VIOLET,
                footer="Press Confirm within 30 seconds",
            ))
            if not ok:
                return

        if not await take_wallet(uid, total):
            return await fail(ctx, "You do not have enough HC for this anymore.", msg)

        try:
            await give_item(uid, key, qty)
        except Exception:
            await add_wallet(uid, total)
            return await fail(ctx, "Something went wrong. Your HC was returned. Please try again.", msg)

        await add_bank(OWNER_ID, total)
        acc = await get_account(ctx.author.id)

        embed = card(
            f"{GENIE}  Purchase Complete",
            f"{item['emoji']}  **{item['name']}** × {qty}\nYou paid **{hc(total)}**.",
            GREEN,
            footer=f"Wallet: {hc(acc['wallet'])}",
        )
        embed.add_field(name="Next step", value=f"Use it with `{p}use {key}`\nor give it with `{p}gift @user {key}`", inline=False)
        await say(ctx, embed, msg)

    @commands.command(aliases=["pocket", "bag", "items"])
    async def inventory(self, ctx, member: discord.Member = None):
        p = ctx.clean_prefix
        member = member or ctx.author
        uid = str(member.id)

        owned = await get_items(uid)
        lines = []
        for key, item in SHOP.items():
            count = owned.get(key, 0)
            if count > 0:
                lines.append(f"{item['emoji']}  **{item['name']}** × {count}")

        embed = card(f"{member.display_name}'s Bag", color=VIOLET, footer=f"{p}shop to buy more · {p}use <item> to use")
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.description = "\n".join(lines) if lines else "Nothing here yet.\nVisit the shop to find something nice."

        acc = await get_account(member.id)
        active = []
        if acc.get("boost_work"):
            active.append("⚡  Double pay on your next work")
        if acc.get("shield"):
            active.append("🔒  Padlock is guarding you")
        if acc.get("insurance"):
            active.append("🛟  Safety Net is ready")
        if active:
            embed.add_field(name="Active Now", value="\n".join(active), inline=False)
        await say(ctx, embed)

    @commands.command()
    async def use(self, ctx, *, text: str = None):
        p = ctx.clean_prefix
        key, _ = parse_item(text)
        if not key:
            return await fail(ctx, f"I could not find that item. Use `{p}inventory` to see what you own.")

        uid  = str(ctx.author.id)
        item = SHOP[key]
        title = f"{item['emoji']}  {item['name']}"
        await get_account(ctx.author.id)

        if item["group"] == "gift":
            if (await get_items(uid)).get(key, 0) < 1:
                return await fail(ctx, f"You do not have a {item['name']}.")
            return await say(ctx, card(title, item["use"], VIOLET, footer="This keepsake stays in your bag"))

        if key == "restpass":
            acc = await get_account(ctx.author.id)
            if _cd_left(acc.get("last_work"), WORK_CD) == 0:
                return await fail(ctx, "You can already work. Save your Rest Pass for later.")
            if not await take_item(uid, key, 1):
                return await fail(ctx, f"You do not have a {item['name']}.")
            await economy_col.update_one({"user_id": uid}, {"$set": {"last_work": None}})
            return await say(ctx, card(title, item["use"], GREEN, footer=f"Use {p}work now"))

        field = EFFECTS[key]
        if not await take_item(uid, key, 1):
            return await fail(ctx, f"You do not have a {item['name']}.")
        res = await economy_col.update_one(
            {"user_id": uid, field: {"$ne": True}},
            {"$set": {field: True}},
        )
        if res.modified_count != 1:
            await give_item(uid, key, 1)
            return await fail(ctx, f"Your {item['name']} is already working. Wait until it is used.")
        await say(ctx, card(title, item["use"], GREEN, footer="Your item stays active until it is used"))

    @commands.command()
    async def gift(self, ctx, member: discord.Member = None, *, text: str = None):
        p = ctx.clean_prefix
        if not member or not text:
            return await fail(ctx, f"Use `{p}gift @user <item> [amount]`.\nExample: `{p}gift @Rohan rose 2`")
        if member.id == ctx.author.id:
            return await fail(ctx, "You cannot gift something to yourself.")
        if member.bot:
            return await fail(ctx, "You cannot gift something to a bot.")

        key, qty = parse_item(text)
        if not key:
            return await fail(ctx, f"I could not find that item. Use `{p}inventory` to see what you own.")
        if qty < 1 or qty > MAX_BUY:
            return await fail(ctx, f"You can gift between 1 and {MAX_BUY} at a time.")

        sender   = str(ctx.author.id)
        receiver = str(member.id)
        item     = SHOP[key]

        if (await get_items(receiver)).get(key, 0) + qty > MAX_HOLD:
            return await fail(ctx, f"{member.display_name} cannot hold that many {item['name']}.")
        if not await take_item(sender, key, qty):
            return await fail(ctx, f"You do not have {qty} × {item['name']}.")

        try:
            await give_item(receiver, key, qty)
        except Exception:
            await give_item(sender, key, qty)
            return await fail(ctx, "Something went wrong. Your item was returned. Please try again.")

        embed = card(
            f"{GENIE}  Gift Delivered",
            f"{ctx.author.mention} gave {member.mention}\n{item['emoji']}  **{item['name']}** × {qty}",
            VIOLET,
            footer="Genie delivered this gift with care",
        )
        embed.add_field(name="For the receiver", value=f"Use it with `{p}use {key}`", inline=False)
        await ctx.reply(
            content=member.mention,
            embed=embed,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False),
        )

    @commands.group(invoke_without_command=True)
    async def loan(self, ctx):
        p = ctx.clean_prefix
        uid = str(ctx.author.id)
        doc = await loans_col.find_one({"user_id": uid})

        if doc:
            acc    = await get_account(ctx.author.id)
            amount = doc["amount"]
            missing = max(0, amount - acc["bank"])
            embed = card(
                "💳  Your Loan",
                f"You owe **{hc(amount)}**.\nWhen your bank holds **{hc(amount)}**, the loan is paid back for you.",
                BLUE,
                footer=f"{p}loan pay to pay now from your wallet",
            )
            embed.add_field(name="Loan Due", value=f"**{hc(amount)}**", inline=True)
            embed.add_field(name="In Your Bank", value=f"**{hc(acc['bank'])}**", inline=True)
            embed.add_field(name="Still Needed", value=f"**{hc(missing)}**", inline=True)
            return await say(ctx, embed)

        embed = card(
            "🏦  Happy Bank Loans",
            f"Need a little help? Borrow up to **{hc(LOAN_MAX)}**.",
            BLUE,
            footer="You pay back only what you borrow",
        )
        embed.add_field(
            name="How it works",
            value=(
                f"• Borrow between **{hc(LOAN_MIN)}** and **{hc(LOAN_MAX)}**\n"
                "• The money comes to your wallet\n"
                "• You pay back the same amount, nothing extra\n"
                "• When your bank holds that amount, it is paid back for you\n"
                "• You can only have one loan at a time"
            ),
            inline=False,
        )
        embed.add_field(
            name="Good to know",
            value=(
                f"• Your Discord account must be {LOAN_MIN_AGE_DAYS} days old\n"
                "• You cannot send HC to others until the loan is paid"
            ),
            inline=False,
        )
        embed.add_field(name="Commands", value=f"`{p}loan take <amount>`\n`{p}loan pay`", inline=False)
        await say(ctx, embed)

    @loan.command(name="take", aliases=["borrow", "get"])
    async def loan_take(self, ctx, amount: str = None):
        p = ctx.clean_prefix
        uid = str(ctx.author.id)

        if uid == OWNER_ID:
            return await fail(ctx, "This account cannot take a loan.")
        if not amount:
            return await fail(ctx, f"Use `{p}loan take <amount>`. Example: `{p}loan take 5000`")

        amt = parse_amount(amount, LOAN_MAX)
        if amt is None or amt < LOAN_MIN or amt > LOAN_MAX:
            return await fail(ctx, f"You can borrow between **{hc(LOAN_MIN)}** and **{hc(LOAN_MAX)}**.")

        age = (datetime.now(timezone.utc) - ctx.author.created_at).days
        if age < LOAN_MIN_AGE_DAYS:
            return await fail(ctx, f"Your Discord account must be at least {LOAN_MIN_AGE_DAYS} days old to take a loan.")
        if await has_loan(uid):
            return await fail(ctx, f"You already have a loan. Check it with `{p}loan`.")

        ok, msg = await ask_confirm(ctx, card(
            "🏦  Please Confirm Your Loan",
            f"You will borrow **{hc(amt)}**.\nYou pay back **{hc(amt)}**. Nothing extra.\n\nWhen your bank holds **{hc(amt)}**, it is paid back for you.",
            BLUE,
            footer="Press Confirm within 30 seconds",
        ))
        if not ok:
            return

        await get_account(ctx.author.id)
        try:
            await loans_col.insert_one({
                "user_id": uid,
                "amount":  amt,
                "status":  "active",
                "ts":      datetime.now(timezone.utc),
            })
        except DuplicateKeyError:
            return await fail(ctx, f"You already have a loan. Check it with `{p}loan`.", msg)

        if not await take_bank(OWNER_ID, amt):
            await loans_col.delete_one({"user_id": uid})
            return await fail(ctx, "Loans are not available right now. Please try again later.", msg)

        try:
            await add_wallet(uid, amt)
        except Exception:
            await add_bank(OWNER_ID, amt)
            await loans_col.delete_one({"user_id": uid})
            return await fail(ctx, "Something went wrong. Please try again.", msg)

        acc = await get_account(ctx.author.id)
        await say(ctx, card(
            "✅  Loan Approved",
            f"**{hc(amt)}** is now in your wallet.\nPay it back by keeping **{hc(amt)}** in your bank.",
            GREEN,
            footer=f"Wallet: {hc(acc['wallet'])}",
        ), msg)

    @loan.command(name="pay", aliases=["repay"])
    async def loan_pay(self, ctx):
        uid  = str(ctx.author.id)
        loan = await loans_col.find_one({"user_id": uid})
        if not loan:
            return await fail(ctx, "You do not have a loan.")

        acc = await get_account(ctx.author.id)
        if acc["wallet"] < loan["amount"]:
            return await fail(ctx, f"You need **{hc(loan['amount'])}** in your wallet. You have **{hc(acc['wallet'])}**.")

        paid = await settle_loan(uid, source="wallet")
        if not paid:
            return await fail(ctx, "Could not complete the payment. Please try again.")
        await say(ctx, loan_paid_card(paid))

    @commands.group(invoke_without_command=True)
    async def trade(self, ctx):
        p = ctx.clean_prefix
        embed = card("🤝  Trade Center", "Swap HC safely with another member.", GOLD)
        embed.add_field(
            name="Commands",
            value=(
                f"`{p}trade offer @user <you give> <you want>`\n"
                f"`{p}trade list` — your open trades\n"
                f"`{p}trade accept <id>`\n"
                f"`{p}trade decline <id>`\n"
                f"`{p}trade cancel <id>`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Example",
            value=f"`{p}trade offer @Rohan 500 300`\nYou give 500 HC and want 300 HC back.\nYour 500 HC is kept safe until the trade ends.",
            inline=False,
        )
        await say(ctx, embed)

    @trade.command(name="offer")
    async def trade_offer(self, ctx, member: discord.Member = None, offer: str = None, want: str = None):
        p = ctx.clean_prefix
        if not member or offer is None or want is None:
            return await fail(ctx, f"Use `{p}trade offer @user <you give> <you want>`.\nExample: `{p}trade offer @Rohan 500 300`")
        if member.id == ctx.author.id:
            return await fail(ctx, "You cannot trade with yourself.")
        if member.bot:
            return await fail(ctx, "You cannot trade with bots.")

        offer_hc = parse_amount(offer)
        want_hc  = parse_amount(want)
        if not offer_hc or not want_hc or offer_hc <= 0 or want_hc <= 0:
            return await fail(ctx, "Both amounts must be proper numbers above 0.")
        if offer_hc > MAX_TRADE or want_hc > MAX_TRADE:
            return await fail(ctx, f"A trade can be at most **{hc(MAX_TRADE)}**.")

        uid = str(ctx.author.id)
        if await has_loan(uid):
            return await fail(ctx, "Please pay back your loan before making trades.")

        open_count = await trades_col.count_documents({"from_id": uid, "status": {"$in": ["pending", "processing"]}})
        if open_count >= MAX_OPEN_TRADES:
            return await fail(ctx, f"You can only have {MAX_OPEN_TRADES} open trades. Cancel one first.")

        if not await take_wallet(uid, offer_hc):
            acc = await get_account(ctx.author.id)
            return await fail(ctx, f"Your wallet only has **{hc(acc['wallet'])}**.")

        trade_id = None
        for _ in range(3):
            candidate = secrets.token_hex(4)
            try:
                await trades_col.insert_one({
                    "trade_id": candidate,
                    "from_id":  uid,
                    "to_id":    str(member.id),
                    "offer_hc": offer_hc,
                    "want_hc":  want_hc,
                    "status":   "pending",
                    "ts":       datetime.now(timezone.utc),
                    "guild_id": str(ctx.guild.id),
                })
                trade_id = candidate
                break
            except DuplicateKeyError:
                continue
            except Exception:
                break

        if not trade_id:
            await add_wallet(uid, offer_hc)
            return await fail(ctx, "Could not create the trade. Your HC was returned. Please try again.")

        embed = card(
            "🤝  Trade Offer Sent",
            f"{ctx.author.mention} → {member.mention}\n\nYou give: **{hc(offer_hc)}**\nYou want: **{hc(want_hc)}**\n\nTrade ID: `{trade_id}`",
            GOLD,
            footer=f"{member.display_name}: {p}trade accept {trade_id}",
        )
        await say(ctx, embed)

        try:
            notify = card(
                "🤝  New Trade Offer",
                f"**{ctx.author.display_name}** sent you a trade offer.\n\nThey give: **{hc(offer_hc)}**\nThey want: **{hc(want_hc)}** from you\n\nTrade ID: `{trade_id}`",
                GOLD,
                footer=f"{p}trade accept {trade_id}  or  {p}trade decline {trade_id}",
            )
            await member.send(embed=notify)
        except discord.HTTPException:
            pass

    @trade.command(name="accept")
    async def trade_accept(self, ctx, trade_id: str = None):
        p = ctx.clean_prefix
        if not trade_id:
            return await fail(ctx, f"Use `{p}trade accept <trade id>`.")

        tid = trade_id.strip().lower()
        uid = str(ctx.author.id)
        if not re.fullmatch(r"[0-9a-f]{6,8}", tid):
            return await fail(ctx, "That trade ID does not look right.")
        if await has_loan(uid):
            return await fail(ctx, "Please pay back your loan before accepting trades.")

        doc = await trades_col.find_one_and_update(
            {"trade_id": tid, "to_id": uid, "status": "pending"},
            {"$set": {"status": "processing"}},
        )
        if not doc:
            return await fail(ctx, "Trade not found, or it was not sent to you.")

        try:
            paid = await move_wallet(uid, doc["from_id"], doc["want_hc"])
        except Exception:
            await trades_col.update_one({"trade_id": tid}, {"$set": {"status": "pending"}})
            return await fail(ctx, "Something went wrong. Please try again.")

        if not paid:
            await trades_col.update_one({"trade_id": tid}, {"$set": {"status": "pending"}})
            return await fail(ctx, f"You need **{hc(doc['want_hc'])}** in your wallet to accept this trade.")

        await add_wallet(uid, doc["offer_hc"])
        await trades_col.update_one({"trade_id": tid}, {"$set": {"status": "completed"}})

        await say(ctx, card(
            "✅  Trade Complete",
            (
                f"{ctx.author.mention} paid **{hc(doc['want_hc'])}** and received **{hc(doc['offer_hc'])}**.\n"
                f"<@{doc['from_id']}> paid **{hc(doc['offer_hc'])}** and received **{hc(doc['want_hc'])}**."
            ),
            GREEN,
            footer=f"Trade ID: {tid}",
        ))

    @trade.command(name="decline")
    async def trade_decline(self, ctx, trade_id: str = None):
        p = ctx.clean_prefix
        if not trade_id:
            return await fail(ctx, f"Use `{p}trade decline <trade id>`.")

        tid = trade_id.strip().lower()
        doc = await trades_col.find_one_and_update(
            {"trade_id": tid, "to_id": str(ctx.author.id), "status": "pending"},
            {"$set": {"status": "declined"}},
        )
        if not doc:
            return await fail(ctx, "Trade not found, or it was not sent to you.")

        await add_wallet(doc["from_id"], doc["offer_hc"])
        await say(ctx, card(
            "Trade Declined",
            f"Trade `{tid}` was declined.\n**{hc(doc['offer_hc'])}** went back to the sender.",
            RED,
        ))

    @trade.command(name="cancel")
    async def trade_cancel(self, ctx, trade_id: str = None):
        p = ctx.clean_prefix
        if not trade_id:
            return await fail(ctx, f"Use `{p}trade cancel <trade id>`.")

        tid = trade_id.strip().lower()
        doc = await trades_col.find_one_and_update(
            {"trade_id": tid, "from_id": str(ctx.author.id), "status": "pending"},
            {"$set": {"status": "cancelled"}},
        )
        if not doc:
            return await fail(ctx, "Trade not found, or it is not your trade.")

        await add_wallet(ctx.author.id, doc["offer_hc"])
        await say(ctx, card(
            "Trade Cancelled",
            f"Trade `{tid}` was cancelled.\n**{hc(doc['offer_hc'])}** is back in your wallet.",
            SLATE,
        ))

    @trade.command(name="list")
    async def trade_list(self, ctx):
        p = ctx.clean_prefix
        uid = str(ctx.author.id)
        docs = await trades_col.find({"status": "pending", "$or": [{"from_id": uid}, {"to_id": uid}]}).to_list(20)

        if not docs:
            return await say(ctx, card("🤝  Your Trades", "You have no open trades.", GOLD))

        embed = card("🤝  Your Trades", color=GOLD, footer=f"{p}trade accept / decline / cancel <id>")
        for doc in docs:
            if doc["from_id"] == uid:
                text = f"To <@{doc['to_id']}>\nYou give **{hc(doc['offer_hc'])}** · You want **{hc(doc['want_hc'])}**"
            else:
                text = f"From <@{doc['from_id']}>\nThey give **{hc(doc['offer_hc'])}** · They want **{hc(doc['want_hc'])}**"
            embed.add_field(name=f"ID  `{doc['trade_id']}`", value=text, inline=False)
        await say(ctx, embed)

    @commands.command(name="coinflip", aliases=["cf", "bet"])
    async def coinflip(self, ctx, amount: str = None, choice: str = None):
        p = ctx.clean_prefix
        if not amount:
            return await fail(ctx, f"Use `{p}cf <amount> [heads/tails]`.")

        uid = str(ctx.author.id)
        acc = await get_account(ctx.author.id)
        amt = parse_amount(amount, acc["wallet"])
        if amt is None or amt < MIN_BET:
            return await fail(ctx, f"The smallest bet is **{hc(MIN_BET)}**. Use a number or `all`.")

        pick = choice.lower() if choice and choice.lower() in ("heads", "tails", "h", "t") else rng.choice(["heads", "tails"])
        pick = "heads" if pick.startswith("h") else "tails"

        if not await take_wallet(uid, amt):
            return await fail(ctx, f"Your wallet only has **{hc(acc['wallet'])}**.")

        result = rng.choice(["heads", "tails"])
        won = pick == result

        if won:
            await add_wallet(uid, amt * 2, earned=amt)
            embed = card(
                f"🪙  {result.title()} — You Won!",
                f"You picked **{pick}** and won **{hc(amt)}**.",
                GREEN,
            )
        else:
            embed = card(
                f"🪙  {result.title()} — You Lost",
                f"You picked **{pick}** but it landed on **{result}**.\nYou lost **{hc(amt)}**.",
                RED,
            )

        acc = await get_account(ctx.author.id)
        embed.set_footer(text=f"Wallet: {hc(acc['wallet'])}")
        await say(ctx, embed)

    @commands.command(aliases=["slot"])
    async def slots(self, ctx, amount: str = None):
        p = ctx.clean_prefix
        if not amount:
            return await fail(ctx, f"Use `{p}slots <amount>`.")

        uid = str(ctx.author.id)
        acc = await get_account(ctx.author.id)
        amt = parse_amount(amount, acc["wallet"])
        if amt is None or amt < MIN_BET:
            return await fail(ctx, f"The smallest bet is **{hc(MIN_BET)}**. Use a number or `all`.")

        if not await take_wallet(uid, amt):
            return await fail(ctx, f"Your wallet only has **{hc(acc['wallet'])}**.")

        reels  = [rng.choice(self.SLOTS) for _ in range(3)]
        result = f"[ {reels[0]} | {reels[1]} | {reels[2]} ]"

        if reels[0] == reels[1] == reels[2]:
            mult   = self.SLOT_MULT.get(reels[0], 1.2)
            payout = int(amt * mult)
            embed  = card("🎰  Jackpot!", f"**{result}**\nYou won **{hc(payout)}** (x{mult}).", GOLD)
        elif len(set(reels)) == 2:
            payout = int(amt * 0.5)
            embed  = card("🎰  Almost!", f"**{result}**\nTwo match. You got back **{hc(payout)}**.", AMBER)
        else:
            payout = 0
            embed  = card("🎰  No Match", f"**{result}**\nYou lost **{hc(amt)}**.", RED)

        if payout > 0:
            await add_wallet(uid, payout, earned=max(0, payout - amt))

        acc = await get_account(ctx.author.id)
        embed.set_footer(text=f"Wallet: {hc(acc['wallet'])}")
        await say(ctx, embed)

    @commands.command()
    async def rob(self, ctx, member: discord.Member = None):
        if not member or member.id == ctx.author.id or member.bot:
            return await fail(ctx, "Mention a real member to rob.")

        uid = str(ctx.author.id)
        vid = str(member.id)

        robber = await get_account(ctx.author.id)
        victim = await get_account(member.id)

        if victim["wallet"] < 100:
            return await fail(ctx, f"**{member.display_name}** has less than 100 HC. Not worth it.")
        if robber["wallet"] < 50:
            return await fail(ctx, "You need at least **50 HC** in your wallet to pay a fine if you get caught.")

        if not await claim_cooldown(uid, "last_rob", ROB_CD):
            fresh = await get_account(ctx.author.id)
            left = _cd_left(fresh.get("last_rob"), ROB_CD)
            return await fail(ctx, f"The police are watching you. Wait **{_fmt_cd(left)}**.")

        guard = await economy_col.find_one_and_update(
            {"user_id": vid, "shield": True},
            {"$set": {"shield": False}},
        )
        if guard:
            return await say(ctx, card(
                "🔒  Robbery Blocked",
                f"**{member.display_name}** had a Padlock.\nThe Padlock broke, and you got nothing.",
                AMBER,
            ))

        if rng.random() < 0.45:
            wallet = victim["wallet"]
            steal  = rng.randint(max(1, wallet // 10), max(1, wallet // 3))
            if not await move_wallet(member.id, ctx.author.id, steal):
                return await fail(ctx, f"**{member.display_name}** has no money left to take.")
            await economy_col.update_one({"user_id": uid}, {"$inc": {"total_earned": steal}})
            return await say(ctx, card(
                "🕶️  Robbery Worked",
                f"You got away with **{hc(steal)}** from **{member.display_name}**.",
                GREEN,
            ))

        cap  = min(200, robber["wallet"] // 2)
        fine = rng.randint(min(50, cap), cap)
        await take_wallet(uid, fine)
        await say(ctx, card(
            "🚨  You Got Caught",
            f"The police caught you. You paid **{hc(fine)}** as a fine.",
            RED,
        ))

    @commands.command(aliases=["rich", "top"])
    async def richlist(self, ctx):
        pipeline = [
            {"$match": {"user_id": {"$ne": OWNER_ID}}},
            {"$addFields": {"total": {"$add": ["$wallet", "$bank"]}}},
            {"$sort": {"total": -1}},
            {"$limit": 10},
        ]
        docs = await economy_col.aggregate(pipeline).to_list(10)

        if not docs:
            return await fail(ctx, "There is no money data yet.")

        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for i, doc in enumerate(docs, 1):
            uid  = int(doc["user_id"])
            user = self.bot.get_user(uid)
            if user is None:
                try:
                    user = await self.bot.fetch_user(uid)
                except discord.HTTPException:
                    user = None
            name = user.display_name if user else f"User {uid}"
            pos  = medals[i - 1] if i <= 3 else f"`{i}.`"
            lines.append(f"{pos}  **{name}** — {hc(doc['total'])}")

        await say(ctx, card("🏆  Global Rich List", "\n".join(lines), GOLD, footer="Wallet and bank together · All servers"))

    @commands.command()
    @ctx_owner()
    async def givecash(self, ctx, member: discord.Member = None, amount: int = None):
        p = ctx.clean_prefix
        if not member or amount is None:
            return await fail(ctx, f"Use `{p}givecash @user <amount>`.")
        if amount <= 0 or amount > MAX_MONEY:
            return await fail(ctx, f"The amount must be between 1 and {MAX_MONEY:,}.")
        await add_wallet(member.id, amount)
        await say(ctx, card(description=f"✅  Gave **{hc(amount)}** to {member.mention}.", color=GREEN))

    @commands.command()
    @ctx_owner()
    async def takecash(self, ctx, member: discord.Member = None, amount: int = None):
        p = ctx.clean_prefix
        if not member or amount is None:
            return await fail(ctx, f"Use `{p}takecash @user <amount>`.")
        if amount <= 0 or amount > MAX_MONEY:
            return await fail(ctx, f"The amount must be between 1 and {MAX_MONEY:,}.")
        acc  = await get_account(member.id)
        take = min(amount, acc["wallet"])
        if take <= 0 or not await take_wallet(member.id, take):
            return await fail(ctx, f"{member.mention} has nothing in their wallet.")
        await say(ctx, card(description=f"✅  Removed **{hc(take)}** from {member.mention}.", color=SLATE))

    @commands.command()
    @ctx_owner()
    async def resetcash(self, ctx, member: discord.Member = None):
        p = ctx.clean_prefix
        if not member:
            return await fail(ctx, f"Use `{p}resetcash @user`.")
        await economy_col.update_one(
            {"user_id": str(member.id)},
            {"$set": {"wallet": 0, "bank": 0, "total_earned": 0}},
            upsert=True,
        )
        await say(ctx, card(description=f"✅  Balance reset for **{member.display_name}**.", color=SLATE))

    @commands.group(invoke_without_command=True)
    async def invest(self, ctx):
        p = ctx.clean_prefix
        embed = card(
            "📊  Investments",
            "Lock your HC for a while and see it grow.\nYou can only have one investment at a time.",
            GOLD,
            footer="Your HC is locked while you invest",
        )
        for key, inv in INVESTMENTS.items():
            embed.add_field(
                name=f"{inv['emoji']}  {inv['name']}",
                value=f"{inv['desc']}\n`{p}invest in {key} <amount>`",
                inline=False,
            )
        embed.add_field(
            name="Commands",
            value=(
                f"`{p}invest in <type> <amount>` — start\n"
                f"`{p}invest check` — see your investment\n"
                f"`{p}invest collect` — take your money when ready\n"
                f"`{p}invest withdraw` — leave early (10% fee)"
            ),
            inline=False,
        )
        await say(ctx, embed)

    @invest.command(name="in")
    async def invest_in(self, ctx, inv_type: str = None, amount: str = None):
        p = ctx.clean_prefix
        if not inv_type or inv_type.lower() not in INVESTMENTS:
            return await fail(ctx, f"Choose one: `safe` `market` `crypto` `business`\nExample: `{p}invest in market 1000`")
        if not amount:
            return await fail(ctx, f"Please add an amount. Example: `{p}invest in market 1000`")

        uid = str(ctx.author.id)
        key = inv_type.lower()
        acc = await get_account(ctx.author.id)
        amt = parse_amount(amount, acc["wallet"])
        if amt is None:
            return await fail(ctx, "Please enter a proper amount, like `1000` or `all`.")
        if amt < 100:
            return await fail(ctx, "The smallest investment is **100 HC**.")
        if amt > acc["wallet"]:
            return await fail(ctx, f"Your wallet only has **{hc(acc['wallet'])}**.")

        existing = await inv_col.find_one({"user_id": uid, "status": "active"})
        if existing:
            return await self._show_busy(ctx, existing)

        if not await take_wallet(uid, amt):
            return await fail(ctx, "You do not have enough HC for this anymore.")

        inv      = INVESTMENTS[key]
        maturity = datetime.now(timezone.utc) + timedelta(hours=inv["hours"])
        try:
            await inv_col.update_one(
                {"user_id": uid, "status": {"$ne": "active"}},
                {"$set": {"type": key, "amount": amt, "maturity": maturity, "status": "active"}},
                upsert=True,
            )
        except DuplicateKeyError:
            await add_wallet(uid, amt)
            existing = await inv_col.find_one({"user_id": uid, "status": "active"})
            if existing:
                return await self._show_busy(ctx, existing)
            return await fail(ctx, "Something went wrong. Your HC was returned. Please try again.")

        embed = card(
            f"{inv['emoji']}  Investment Started",
            (
                f"Plan: **{inv['name']}**\n"
                f"Locked: **{hc(amt)}**\n"
                f"Possible gain: **{int(inv['min_r'] * 100)}% to {int(inv['max_r'] * 100)}%**\n"
                f"Chance of loss: **{int(inv['risk'] * 100)}%**\n"
                f"Ready: <t:{int(maturity.timestamp())}:R>"
            ),
            GOLD,
            footer=f"{p}invest collect when ready · {p}invest withdraw to leave early (10% fee)",
        )
        await say(ctx, embed)

    async def _show_busy(self, ctx, existing: dict):
        p = ctx.clean_prefix
        mat  = _aware(existing["maturity"])
        left = int((mat - datetime.now(timezone.utc)).total_seconds())
        info = INVESTMENTS.get(existing["type"], {})
        if left > 0:
            text = (
                f"You are already investing in **{info.get('name', existing['type'])}** ({hc(existing['amount'])}).\n"
                f"It is ready in **{_fmt_cd(left)}**."
            )
        else:
            text = f"Your investment is ready. Use `{p}invest collect` first."
        await say(ctx, card(description=text, color=AMBER))

    @invest.command(name="check")
    async def invest_check(self, ctx):
        p = ctx.clean_prefix
        doc = await inv_col.find_one({"user_id": str(ctx.author.id), "status": "active"})
        if not doc:
            return await fail(ctx, f"You have no investment. Use `{p}invest` to see the options.")

        mat  = _aware(doc["maturity"])
        left = int((mat - datetime.now(timezone.utc)).total_seconds())
        if left <= 0:
            return await say(ctx, card("✅  Ready to Collect", f"Your investment is ready.\nUse `{p}invest collect` to take your money.", GREEN))

        inv = INVESTMENTS.get(doc["type"], {})
        low  = int(doc["amount"] * inv.get("min_r", 0))
        high = int(doc["amount"] * inv.get("max_r", 0))
        embed = card(
            f"{inv.get('emoji', '')}  {inv.get('name', 'Investment')}",
            color=GOLD,
            footer=f"{p}invest collect when ready · {p}invest withdraw to leave early (10% fee)",
        )
        embed.add_field(name="Locked", value=f"**{hc(doc['amount'])}**", inline=True)
        embed.add_field(name="Possible Gain", value=f"**+{low:,} to +{high:,} HC**", inline=True)
        embed.add_field(name="Ready", value=f"<t:{int(mat.timestamp())}:R>", inline=True)
        await say(ctx, embed)

    @invest.command(name="collect")
    async def invest_collect(self, ctx):
        uid = str(ctx.author.id)
        now = datetime.now(timezone.utc)

        doc = await inv_col.find_one_and_update(
            {"user_id": uid, "status": "active", "maturity": {"$lte": now}},
            {"$set": {"status": "done"}},
        )
        if not doc:
            active = await inv_col.find_one({"user_id": uid, "status": "active"})
            if not active:
                return await fail(ctx, "You have no investment to collect.")
            left = int((_aware(active["maturity"]) - now).total_seconds())
            return await fail(ctx, f"It is not ready yet. Come back in **{_fmt_cd(max(left, 1))}**.")

        inv    = INVESTMENTS.get(doc["type"], {})
        amount = doc["amount"]
        emoji  = inv.get("emoji", "")

        if rng.random() < inv.get("risk", 0):
            saved = await economy_col.find_one_and_update(
                {"user_id": uid, "insurance": True},
                {"$set": {"insurance": False}},
            )
            if saved:
                await add_wallet(uid, amount)
                embed = card(
                    f"{emoji}  Safety Net Saved You",
                    f"The market went down, but your Safety Net gave all your money back.\nYou received **{hc(amount)}**.",
                    AMBER,
                )
            else:
                loss_pct = inv.get("loss", 0.2)
                loss_amt = int(amount * loss_pct)
                payout   = amount - loss_amt
                await add_wallet(uid, payout)
                embed = card(
                    f"{emoji}  Investment Loss",
                    f"The market went against you.\nLost: **{hc(loss_amt)}** ({int(loss_pct * 100)}%)\nYou got back: **{hc(payout)}**",
                    RED,
                )
        else:
            ret_pct = rng.uniform(inv.get("min_r", 0.05), inv.get("max_r", 0.12))
            profit  = int(amount * ret_pct)
            payout  = amount + profit
            await add_wallet(uid, payout, earned=profit)
            embed = card(
                f"{emoji}  Investment Profit!",
                f"Invested: **{hc(amount)}**\nProfit: **+{hc(profit)}** (+{int(ret_pct * 100)}%)\nTotal: **{hc(payout)}**",
                GREEN,
            )

        embed.set_footer(text=f"Use {ctx.clean_prefix}invest to start a new one")
        await say(ctx, embed)

    @invest.command(name="withdraw")
    async def invest_withdraw(self, ctx):
        p = ctx.clean_prefix
        uid = str(ctx.author.id)
        now = datetime.now(timezone.utc)

        doc = await inv_col.find_one_and_update(
            {"user_id": uid, "status": "active", "maturity": {"$gt": now}},
            {"$set": {"status": "withdrawn"}},
        )
        if not doc:
            ready = await inv_col.find_one({"user_id": uid, "status": "active"})
            if ready:
                return await fail(ctx, f"Your investment is ready. Use `{p}invest collect` instead.")
            return await fail(ctx, "You have no investment.")

        penalty = int(doc["amount"] * 0.10)
        payout  = doc["amount"] - penalty
        await add_wallet(uid, payout)
        await say(ctx, card(
            "Early Exit",
            f"Fee (10%): **-{hc(penalty)}**\nBack in your wallet: **{hc(payout)}**",
            AMBER,
        ))


async def setup(bot):
    await bot.add_cog(Economy(bot))