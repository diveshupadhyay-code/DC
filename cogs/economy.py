"""
cogs/economy.py — Happy Cash: Global Economy System.
  - Balance is GLOBAL across all servers
  - Only bot owner can give/take cash
  - Users can transfer to each other
  - Trade system (offer HC, want HC)
  - Server activity boosts work earnings
"""

import discord
from discord.ext import commands
import asyncio, random
from datetime import datetime, timezone, timedelta

from utils.db import db
from utils.helpers import BOT_OWNER_ID, ctx_owner

# ── Collections ───────────────────────────────────────────────────────────────
economy_col  = db["economy_global"]  # {user_id, wallet, bank, ...}  ← no guild_id
trades_col   = db["trades"]          # pending trades
activity_col = db["server_activity"] # {guild_id, msg_count, window_start}

CURRENCY       = "HC"
CURRENCY_EMOJI = "💰"

DAILY_MIN      = 150
DAILY_MAX      = 350
WORK_BASE_MIN  = 50
WORK_BASE_MAX  = 150
WORK_CD        = 3600    # 1h
DAILY_CD       = 86400   # 24h

# Activity thresholds for work boost
ACTIVITY_BOOST_THRESHOLD = 30   # msgs in last hour for active boost
ACTIVITY_PENALTY_THRESHOLD = 5  # msgs in last hour = dead server penalty

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


# ══════════════════════════════════════════════════════════════════════════════
#  DB HELPERS  (global — no guild_id)
# ══════════════════════════════════════════════════════════════════════════════

async def get_account(user_id: int) -> dict:
    doc = await economy_col.find_one({"user_id": str(user_id)})
    if not doc:
        doc = {
            "user_id":      str(user_id),
            "wallet":       0,
            "bank":         0,
            "last_daily":   None,
            "last_work":    None,
            "total_earned": 0,
        }
        await economy_col.insert_one(doc)
    return doc

async def add_wallet(user_id: int, amount: int):
    await economy_col.update_one(
        {"user_id": str(user_id)},
        {"$inc": {"wallet": amount,
                  "total_earned": max(0, amount)}},
        upsert=True
    )

async def add_bank(user_id: int, amount: int):
    await economy_col.update_one(
        {"user_id": str(user_id)},
        {"$inc": {"bank": amount}},
        upsert=True
    )

def _cd_left(ts, cooldown: int) -> int:
    if not ts:
        return 0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0, int(cooldown - (datetime.now(timezone.utc) - ts).total_seconds()))

def _fmt_cd(s: int) -> str:
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return f"{h}h {m}m" if h else (f"{m}m {sec}s" if m else f"{sec}s")


# ══════════════════════════════════════════════════════════════════════════════
#  ACTIVITY TRACKING
# ══════════════════════════════════════════════════════════════════════════════

async def _track_message(guild_id: int):
    """Increment hourly message count for a guild."""
    now = datetime.now(timezone.utc)
    doc = await activity_col.find_one({"guild_id": str(guild_id)})

    if not doc or (now - doc["window_start"].replace(tzinfo=timezone.utc)).total_seconds() > 3600:
        # Reset window
        await activity_col.update_one(
            {"guild_id": str(guild_id)},
            {"$set": {"msg_count": 1, "window_start": now}},
            upsert=True
        )
    else:
        await activity_col.update_one(
            {"guild_id": str(guild_id)},
            {"$inc": {"msg_count": 1}}
        )

async def _get_activity_multiplier(guild_id: int) -> tuple[float, str]:
    """Return (multiplier, label) based on server activity in last hour."""
    doc = await activity_col.find_one({"guild_id": str(guild_id)})
    if not doc:
        return 1.0, ""

    now     = datetime.now(timezone.utc)
    win_start = doc.get("window_start")
    if win_start:
        if win_start.tzinfo is None:
            win_start = win_start.replace(tzinfo=timezone.utc)
        if (now - win_start).total_seconds() > 3600:
            return 0.7, "Server inactive — 0.7x earnings"

    count = doc.get("msg_count", 0)
    if count >= ACTIVITY_BOOST_THRESHOLD:
        return 1.5, f"Server active ({count} msgs/hr) — 1.5x boost!"
    elif count >= 15:
        return 1.2, f"Server warming up ({count} msgs/hr) — 1.2x boost"
    elif count <= ACTIVITY_PENALTY_THRESHOLD:
        return 0.7, f"Server quiet ({count} msgs/hr) — 0.7x earnings"
    return 1.0, f"{count} msgs/hr"


# ══════════════════════════════════════════════════════════════════════════════
#  COG
# ══════════════════════════════════════════════════════════════════════════════

class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Track activity ────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        await _track_message(message.guild.id)

    # ── Balance ────────────────────────────────────────────────────────────────
    @commands.command(aliases=["bal", "wallet", "cash"])
    async def balance(self, ctx, member: discord.Member = None):
        """Check your global balance (same across all servers)."""
        member = member or ctx.author
        acc    = await get_account(member.id)
        total  = acc["wallet"] + acc["bank"]

        embed = discord.Embed(color=0xF0C040)
        embed.set_author(
            name=f"{member.display_name}'s Balance",
            icon_url=member.display_avatar.url
        )
        embed.add_field(name=f"{CURRENCY_EMOJI} Wallet", value=f"**{acc['wallet']:,} HC**", inline=True)
        embed.add_field(name="🏦 Bank",                  value=f"**{acc['bank']:,} HC**",   inline=True)
        embed.add_field(name="📊 Net Worth",             value=f"**{total:,} HC**",         inline=True)
        embed.set_footer(text=f"Global balance · All time earned: {acc.get('total_earned',0):,} HC")
        await ctx.reply(embed=embed)

    # ── Daily ──────────────────────────────────────────────────────────────────
    @commands.command()
    async def daily(self, ctx):
        """Claim your daily reward. Resets every 24 hours."""
        acc = await get_account(ctx.author.id)
        cd  = _cd_left(acc.get("last_daily"), DAILY_CD)
        if cd:
            return await ctx.reply(embed=discord.Embed(
                description=f"Already claimed. Come back in **{_fmt_cd(cd)}**.",
                color=0xED4245
            ))

        amount = random.randint(DAILY_MIN, DAILY_MAX)
        await economy_col.update_one(
            {"user_id": str(ctx.author.id)},
            {"$inc": {"wallet": amount, "total_earned": amount},
             "$set":  {"last_daily": datetime.now(timezone.utc)}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(
            title="Daily Reward",
            description=f"You claimed **{amount:,} HC**!",
            color=0xF0C040
        ).set_footer(text="Come back tomorrow · Balance is global across all servers"))

    # ── Work ───────────────────────────────────────────────────────────────────
    @commands.command()
    async def work(self, ctx):
        """Work for HC. Earnings depend on how active this server is."""
        acc = await get_account(ctx.author.id)
        cd  = _cd_left(acc.get("last_work"), WORK_CD)
        if cd:
            return await ctx.reply(embed=discord.Embed(
                description=f"You're tired. Rest for **{_fmt_cd(cd)}**.",
                color=0xED4245
            ))

        mult, activity_label = await _get_activity_multiplier(ctx.guild.id)
        base   = random.randint(WORK_BASE_MIN, WORK_BASE_MAX)
        amount = max(1, int(base * mult))
        story  = random.choice(WORK_RESPONSES).format(amount=f"{amount:,}")

        await economy_col.update_one(
            {"user_id": str(ctx.author.id)},
            {"$inc": {"wallet": amount, "total_earned": amount},
             "$set":  {"last_work": datetime.now(timezone.utc)}},
            upsert=True
        )
        embed = discord.Embed(description=f"{CURRENCY_EMOJI} {story}", color=0x57F287)
        if activity_label:
            embed.add_field(name="Server Activity", value=activity_label, inline=False)
        embed.set_footer(text=f"Next work in 1h · Wallet: {acc['wallet'] + amount:,} HC")
        await ctx.reply(embed=embed)

    # ── Activity status ────────────────────────────────────────────────────────
    @commands.command(aliases=["activity"])
    async def serverstatus(self, ctx):
        """Check this server's activity level and work earnings multiplier."""
        mult, label = await _get_activity_multiplier(ctx.guild.id)
        doc  = await activity_col.find_one({"guild_id": str(ctx.guild.id)})
        count = doc.get("msg_count", 0) if doc else 0

        if mult >= 1.5:
            color, status = 0x57F287, "Active"
        elif mult >= 1.0:
            color, status = 0xFEE75C, "Normal"
        else:
            color, status = 0xED4245, "Inactive"

        embed = discord.Embed(title=f"Server Activity — {ctx.guild.name}", color=color)
        embed.add_field(name="Status",          value=status,           inline=True)
        embed.add_field(name="Messages (1h)",   value=f"{count}",       inline=True)
        embed.add_field(name="Work Multiplier", value=f"**{mult}x**",   inline=True)
        embed.add_field(
            name="How it works",
            value=(
                "30+ msgs/hr → **1.5x** work earnings\n"
                "15–29 msgs/hr → **1.2x** work earnings\n"
                "6–14 msgs/hr → **1.0x** (normal)\n"
                "0–5 msgs/hr → **0.7x** work earnings\n"
                "Inactive >1h → **0.7x** penalty"
            ),
            inline=False
        )
        embed.set_footer(text="Keep the server active to boost everyone's earnings!")
        await ctx.reply(embed=embed)

    # ── Deposit / Withdraw ────────────────────────────────────────────────────
    @commands.command(aliases=["dep"])
    async def deposit(self, ctx, amount: str = None):
        """Deposit cash into your bank."""
        if not amount:
            return await ctx.reply("Usage: `,deposit <amount>` or `,deposit all`")
        acc = await get_account(ctx.author.id)
        amt = acc["wallet"] if amount.lower() == "all" else int(amount.replace(",", "")) if amount.replace(",","").isdigit() else None
        if amt is None:
            return await ctx.reply("Invalid amount.")
        if amt <= 0 or amt > acc["wallet"]:
            return await ctx.reply(f"You only have **{acc['wallet']:,} HC** in wallet.")
        await economy_col.update_one(
            {"user_id": str(ctx.author.id)},
            {"$inc": {"wallet": -amt, "bank": amt}}
        )
        await ctx.reply(embed=discord.Embed(
            description=f"Deposited **{amt:,} HC** → Bank.",
            color=0x57F287
        ).set_footer(text=f"Wallet: {acc['wallet']-amt:,} HC · Bank: {acc['bank']+amt:,} HC"))

    @commands.command(aliases=["with"])
    async def withdraw(self, ctx, amount: str = None):
        """Withdraw cash from your bank."""
        if not amount:
            return await ctx.reply("Usage: `,withdraw <amount>` or `,withdraw all`")
        acc = await get_account(ctx.author.id)
        amt = acc["bank"] if amount.lower() == "all" else int(amount.replace(",","")) if amount.replace(",","").isdigit() else None
        if amt is None:
            return await ctx.reply("Invalid amount.")
        if amt <= 0 or amt > acc["bank"]:
            return await ctx.reply(f"You only have **{acc['bank']:,} HC** in bank.")
        await economy_col.update_one(
            {"user_id": str(ctx.author.id)},
            {"$inc": {"bank": -amt, "wallet": amt}}
        )
        await ctx.reply(embed=discord.Embed(
            description=f"Withdrew **{amt:,} HC** → Wallet.",
            color=0x57F287
        ).set_footer(text=f"Wallet: {acc['wallet']+amt:,} HC · Bank: {acc['bank']-amt:,} HC"))

    # ── Transfer ───────────────────────────────────────────────────────────────
    @commands.command(aliases=["pay", "give", "send"])
    async def transfer(self, ctx, member: discord.Member = None, amount: str = None):
        """Send HC to another user (works globally)."""
        if not member or not amount:
            return await ctx.reply("Usage: `,transfer @user <amount>`")
        if member.id == ctx.author.id:
            return await ctx.reply("Can't send HC to yourself.")
        if member.bot:
            return await ctx.reply("Can't send HC to bots.")
        if not amount.replace(",","").isdigit():
            return await ctx.reply("Invalid amount.")

        amt = int(amount.replace(",",""))
        if amt <= 0:
            return await ctx.reply("Amount must be greater than 0.")

        acc = await get_account(ctx.author.id)
        if amt > acc["wallet"]:
            return await ctx.reply(f"Not enough in wallet. You have **{acc['wallet']:,} HC**.")

        await add_wallet(ctx.author.id, -amt)
        await add_wallet(member.id, amt)

        embed = discord.Embed(
            description=f"{CURRENCY_EMOJI} **{ctx.author.mention}** sent **{amt:,} HC** to **{member.mention}**.",
            color=0xF0C040
        )
        embed.set_footer(text="Global transfer — works across all servers")
        await ctx.reply(embed=embed)

    # ── TRADE SYSTEM ───────────────────────────────────────────────────────────
    @commands.group(invoke_without_command=True)
    async def trade(self, ctx):
        """Trade HC with another user. Sub-commands: offer, accept, decline, cancel, list"""
        embed = discord.Embed(title="Trade System", color=0xF0C040)
        embed.add_field(
            name="How to Trade",
            value=(
                "`,trade offer @user <offer_HC> <want_HC>` — send a trade offer\n"
                "Example: `,trade offer @Rohan 500 300`\n"
                "*(You offer 500 HC, you want 300 HC back)*\n\n"
                "`,trade list` — see your pending trades\n"
                "`,trade accept <trade_id>` — accept a trade\n"
                "`,trade decline <trade_id>` — decline a trade\n"
                "`,trade cancel <trade_id>` — cancel your own offer"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @trade.command(name="offer")
    async def trade_offer(self, ctx, member: discord.Member = None,
                          offer_hc: int = None, want_hc: int = None):
        """
        Send a trade offer.
        ,trade offer @user <you offer HC> <you want HC>
        """
        if not member or offer_hc is None or want_hc is None:
            return await ctx.reply("Usage: `,trade offer @user <offer> <want>`\nExample: `,trade offer @Rohan 500 300`")
        if member.id == ctx.author.id:
            return await ctx.reply("Can't trade with yourself.")
        if member.bot:
            return await ctx.reply("Can't trade with bots.")
        if offer_hc <= 0 or want_hc <= 0:
            return await ctx.reply("Both amounts must be greater than 0.")

        acc = await get_account(ctx.author.id)
        if offer_hc > acc["wallet"]:
            return await ctx.reply(f"Not enough in wallet. You have **{acc['wallet']:,} HC**.")

        # Lock the offered HC immediately
        await add_wallet(ctx.author.id, -offer_hc)

        trade_id = f"{ctx.author.id}{member.id}{int(datetime.now(timezone.utc).timestamp())}"[-8:]
        await trades_col.insert_one({
            "trade_id":  trade_id,
            "from_id":   str(ctx.author.id),
            "to_id":     str(member.id),
            "offer_hc":  offer_hc,
            "want_hc":   want_hc,
            "status":    "pending",
            "ts":        datetime.now(timezone.utc),
            "guild_id":  str(ctx.guild.id),
        })

        embed = discord.Embed(
            title="Trade Offer Sent",
            description=(
                f"{ctx.author.mention} → {member.mention}\n\n"
                f"**Offering:** {offer_hc:,} HC\n"
                f"**Wanting:** {want_hc:,} HC\n\n"
                f"Trade ID: `{trade_id}`"
            ),
            color=0xF0C040
        )
        embed.set_footer(text=f"{member.display_name}: use ,trade accept {trade_id} or ,trade decline {trade_id}")
        await ctx.reply(embed=embed)

        # Notify the recipient
        try:
            notify = discord.Embed(
                description=(
                    f"**{ctx.author.display_name}** sent you a trade offer!\n\n"
                    f"They offer **{offer_hc:,} HC**\n"
                    f"They want **{want_hc:,} HC** from you\n\n"
                    f"Trade ID: `{trade_id}`"
                ),
                color=0xF0C040
            )
            notify.set_footer(text=f"Use ,trade accept {trade_id} or ,trade decline {trade_id}")
            await member.send(embed=notify)
        except:
            pass

    @trade.command(name="accept")
    async def trade_accept(self, ctx, trade_id: str = None):
        """Accept a trade offer sent to you."""
        if not trade_id:
            return await ctx.reply("Usage: `,trade accept <trade_id>`")

        doc = await trades_col.find_one({"trade_id": trade_id, "status": "pending"})
        if not doc:
            return await ctx.reply("Trade not found or already completed.")
        if str(ctx.author.id) != doc["to_id"]:
            return await ctx.reply("This trade was not sent to you.")

        acc = await get_account(ctx.author.id)
        if doc["want_hc"] > acc["wallet"]:
            return await ctx.reply(
                f"Not enough HC. This trade requires **{doc['want_hc']:,} HC** from your wallet."
            )

        # Execute trade
        await add_wallet(ctx.author.id, -doc["want_hc"])     # recipient pays
        await add_wallet(int(doc["from_id"]), doc["want_hc"]) # sender receives
        await add_wallet(ctx.author.id, doc["offer_hc"])      # recipient receives locked HC

        await trades_col.update_one(
            {"trade_id": trade_id},
            {"$set": {"status": "completed"}}
        )

        sender = ctx.guild.get_member(int(doc["from_id"]))
        embed  = discord.Embed(
            title="Trade Completed",
            description=(
                f"**{ctx.author.mention}** accepted the trade!\n\n"
                f"{ctx.author.mention} paid **{doc['want_hc']:,} HC** → received **{doc['offer_hc']:,} HC**\n"
                f"{sender.mention if sender else 'Sender'} paid **{doc['offer_hc']:,} HC** → received **{doc['want_hc']:,} HC**"
            ),
            color=0x57F287
        )
        await ctx.reply(embed=embed)

    @trade.command(name="decline")
    async def trade_decline(self, ctx, trade_id: str = None):
        """Decline a trade offer."""
        if not trade_id:
            return await ctx.reply("Usage: `,trade decline <trade_id>`")

        doc = await trades_col.find_one({"trade_id": trade_id, "status": "pending"})
        if not doc:
            return await ctx.reply("Trade not found or already completed.")
        if str(ctx.author.id) != doc["to_id"]:
            return await ctx.reply("This trade was not sent to you.")

        # Refund the sender
        await add_wallet(int(doc["from_id"]), doc["offer_hc"])
        await trades_col.update_one({"trade_id": trade_id}, {"$set": {"status": "declined"}})

        await ctx.reply(embed=discord.Embed(
            description=f"Trade `{trade_id}` declined. **{doc['offer_hc']:,} HC** refunded to sender.",
            color=0xED4245
        ))

    @trade.command(name="cancel")
    async def trade_cancel(self, ctx, trade_id: str = None):
        """Cancel your own pending trade offer."""
        if not trade_id:
            return await ctx.reply("Usage: `,trade cancel <trade_id>`")

        doc = await trades_col.find_one({"trade_id": trade_id, "status": "pending"})
        if not doc:
            return await ctx.reply("Trade not found or already completed.")
        if str(ctx.author.id) != doc["from_id"]:
            return await ctx.reply("You can only cancel your own trades.")

        await add_wallet(ctx.author.id, doc["offer_hc"])
        await trades_col.update_one({"trade_id": trade_id}, {"$set": {"status": "cancelled"}})

        await ctx.reply(embed=discord.Embed(
            description=f"Trade `{trade_id}` cancelled. **{doc['offer_hc']:,} HC** refunded.",
            color=0x2B2D31
        ))

    @trade.command(name="list")
    async def trade_list(self, ctx):
        """View your pending trades (sent and received)."""
        uid    = str(ctx.author.id)
        cursor = trades_col.find({"status": "pending", "$or": [{"from_id": uid}, {"to_id": uid}]})
        docs   = await cursor.to_list(20)

        if not docs:
            return await ctx.reply("No pending trades.")

        embed = discord.Embed(title="Your Pending Trades", color=0xF0C040)
        for doc in docs:
            is_sender = doc["from_id"] == uid
            other_id  = doc["to_id"] if is_sender else doc["from_id"]
            other     = ctx.guild.get_member(int(other_id))
            other_name = other.display_name if other else f"User {other_id}"
            if is_sender:
                desc = f"→ **{other_name}** | You offer {doc['offer_hc']:,} HC, want {doc['want_hc']:,} HC"
            else:
                desc = f"← **{other_name}** | They offer {doc['offer_hc']:,} HC, want {doc['want_hc']:,} HC"
            embed.add_field(name=f"ID: `{doc['trade_id']}`", value=desc, inline=False)

        embed.set_footer(text=",trade accept/decline/cancel <id>")
        await ctx.reply(embed=embed)

    # ── GAMBLING ───────────────────────────────────────────────────────────────
    @commands.command(name="coinflip", aliases=["cf", "bet"])
    async def coinflip(self, ctx, amount: str = None, choice: str = None):
        """Gamble with a coinflip. ,cf <amount> [heads/tails]"""
        if not amount:
            return await ctx.reply("Usage: `,cf <amount> [heads/tails]`")

        acc = await get_account(ctx.author.id)
        amt = acc["wallet"] if amount.lower() == "all" else (int(amount.replace(",","")) if amount.replace(",","").isdigit() else None)
        if amt is None:
            return await ctx.reply("Invalid amount.")
        if amt <= 0 or amt > acc["wallet"]:
            return await ctx.reply(f"Not enough. Wallet: **{acc['wallet']:,} HC**.")

        pick   = choice.lower() if choice and choice.lower() in ("heads","tails","h","t") else random.choice(["heads","tails"])
        pick   = "heads" if pick.startswith("h") else "tails"
        result = random.choice(["heads","tails"])
        won    = pick == result

        if won:
            await add_wallet(ctx.author.id, amt)
            embed = discord.Embed(
                title=f"🪙 {result.title()} — Won!",
                description=f"You picked **{pick}** and won **{amt:,} HC**!",
                color=0x57F287
            )
            new_w = acc["wallet"] + amt
        else:
            await add_wallet(ctx.author.id, -amt)
            embed = discord.Embed(
                title=f"🪙 {result.title()} — Lost",
                description=f"You picked **{pick}** but it was **{result}**. Lost **{amt:,} HC**.",
                color=0xED4245
            )
            new_w = acc["wallet"] - amt

        embed.set_footer(text=f"Wallet: {new_w:,} HC")
        await ctx.reply(embed=embed)

    SLOTS     = ["🍒","🍋","🍊","🍇","💎","7️⃣","⭐"]
    SLOT_MULT = {"💎":5,"7️⃣":4,"⭐":3,"🍇":2.5,"🍊":2,"🍋":1.5,"🍒":1.2}

    @commands.command(aliases=["slot"])
    async def slots(self, ctx, amount: str = None):
        """Spin the slot machine. Match 3 for jackpot!"""
        if not amount:
            return await ctx.reply("Usage: `,slots <amount>`")

        acc = await get_account(ctx.author.id)
        amt = acc["wallet"] if amount.lower() == "all" else (int(amount.replace(",","")) if amount.replace(",","").isdigit() else None)
        if amt is None:
            return await ctx.reply("Invalid amount.")
        if amt <= 0 or amt > acc["wallet"]:
            return await ctx.reply(f"Not enough. Wallet: **{acc['wallet']:,} HC**.")

        reels  = [random.choice(self.SLOTS) for _ in range(3)]
        result = f"[ {reels[0]} | {reels[1]} | {reels[2]} ]"

        if reels[0] == reels[1] == reels[2]:
            mult     = self.SLOT_MULT.get(reels[0], 1.2)
            winnings = int(amt * mult)
            await add_wallet(ctx.author.id, winnings - amt)
            embed = discord.Embed(title="Jackpot!", description=f"**{result}**\nWon **{winnings:,} HC**! (x{mult})", color=0xffd700)
        elif len(set(reels)) == 2:
            winnings = int(amt * 0.5)
            await add_wallet(ctx.author.id, winnings - amt)
            embed = discord.Embed(title="Almost!", description=f"**{result}**\n2 match — got back **{winnings:,} HC**.", color=0xFEE75C)
        else:
            await add_wallet(ctx.author.id, -amt)
            winnings = 0
            embed = discord.Embed(title="No Match", description=f"**{result}**\nLost **{amt:,} HC**.", color=0xED4245)

        embed.set_footer(text=f"Wallet: {max(0, acc['wallet'] + (winnings - amt)):,} HC")
        await ctx.reply(embed=embed)

    @commands.command()
    async def rob(self, ctx, member: discord.Member = None):
        """Try to rob someone's wallet. 45% success rate."""
        if not member or member.id == ctx.author.id or member.bot:
            return await ctx.reply("Mention a valid member to rob.")

        robber = await get_account(ctx.author.id)
        victim = await get_account(member.id)

        if victim["wallet"] < 100:
            return await ctx.reply(f"**{member.display_name}** has less than 100 HC. Not worth it.")
        if robber["wallet"] < 50:
            return await ctx.reply("You need at least **50 HC** in your wallet (fine insurance).")

        if random.random() < 0.45:
            steal = random.randint(victim["wallet"] // 10, victim["wallet"] // 3)
            await add_wallet(ctx.author.id, steal)
            await add_wallet(member.id, -steal)
            embed = discord.Embed(
                title="Robbery Successful",
                description=f"You got away with **{steal:,} HC** from **{member.display_name}**!",
                color=0x57F287
            )
        else:
            fine = random.randint(50, min(200, robber["wallet"] // 2))
            await add_wallet(ctx.author.id, -fine)
            embed = discord.Embed(
                title="Caught!",
                description=f"Police caught you! Paid **{fine:,} HC** as fine.",
                color=0xED4245
            )
        await ctx.reply(embed=embed)

    # ── Leaderboard ────────────────────────────────────────────────────────────
    @commands.command(aliases=["rich", "top"])
    async def richlist(self, ctx):
        """Top 10 richest Happy Cash holders globally."""
        cursor = economy_col.find().sort("wallet", -1).limit(10)
        docs   = await cursor.to_list(10)

        if not docs:
            return await ctx.reply("No economy data yet.")

        medals = ["🥇","🥈","🥉"]
        lines  = []
        for i, doc in enumerate(docs, 1):
            try:
                user  = await self.bot.fetch_user(int(doc["user_id"]))
                name  = user.display_name
            except:
                name  = f"User {doc['user_id']}"
            total = doc["wallet"] + doc["bank"]
            pos   = medals[i-1] if i <= 3 else f"`{i}.`"
            lines.append(f"{pos} **{name}** — {total:,} HC")

        embed = discord.Embed(
            title="Global Rich List",
            description="\n".join(lines),
            color=0xF0C040
        )
        embed.set_footer(text="Global leaderboard · Wallet + Bank")
        await ctx.reply(embed=embed)

    # ── Owner only: give/take/reset ────────────────────────────────────────────
    @commands.command()
    @ctx_owner()
    async def givecash(self, ctx, member: discord.Member = None, amount: int = None):
        """Give HC to a user — Bot Owner only."""
        if not member or not amount:
            return await ctx.reply("Usage: `,givecash @user <amount>`")
        await add_wallet(member.id, amount)
        await ctx.reply(embed=discord.Embed(
            description=f"Gave **{amount:,} HC** to {member.mention}.",
            color=0x57F287
        ))

    @commands.command()
    @ctx_owner()
    async def takecash(self, ctx, member: discord.Member = None, amount: int = None):
        """Remove HC from a user — Bot Owner only."""
        if not member or not amount:
            return await ctx.reply("Usage: `,takecash @user <amount>`")
        await add_wallet(member.id, -amount)
        await ctx.reply(embed=discord.Embed(
            description=f"Removed **{amount:,} HC** from {member.mention}.",
            color=0x2B2D31
        ))

    @commands.command()
    @ctx_owner()
    async def resetcash(self, ctx, member: discord.Member = None):
        """Reset a user's balance — Bot Owner only."""
        if not member:
            return await ctx.reply("Usage: `,resetcash @user`")
        await economy_col.update_one(
            {"user_id": str(member.id)},
            {"$set": {"wallet": 0, "bank": 0, "total_earned": 0}},
            upsert=True
        )
        await ctx.reply(f"Balance reset for **{member.display_name}**.")


async def setup(bot):
    await bot.add_cog(Economy(bot))
