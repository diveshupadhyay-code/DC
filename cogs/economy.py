"""
cogs/economy.py — Happy Cash economy system.
Currency: HappyCash (HC)
"""

import discord
from discord.ext import commands
import asyncio, random
from datetime import datetime, timezone, timedelta

from utils.db import db
from utils.helpers import BOT_OWNER_ID

economy_col = db["economy"]  # {user_id, guild_id, wallet, bank, last_daily, last_work, total_earned}

CURRENCY    = "HC"
CURRENCY_EMOJI = "💰"

DAILY_MIN   = 150
DAILY_MAX   = 350
WORK_MIN    = 50
WORK_MAX    = 150
WORK_CD     = 3600   # 1 hour cooldown
DAILY_CD    = 86400  # 24 hour cooldown

WORK_RESPONSES = [
    "You delivered packages and earned {amount} HC.",
    "You fixed bugs in code and earned {amount} HC.",
    "You walked dogs around the block for {amount} HC.",
    "You made chai for the whole office and got {amount} HC.",
    "You sold memes online and made {amount} HC.",
    "You drove an auto-rickshaw and collected {amount} HC.",
    "You tutored a student and earned {amount} HC.",
    "You won a gaming tournament and took home {amount} HC.",
    "You streamed on Discord and got {amount} HC in donations.",
    "You helped someone with their resume for {amount} HC.",
]


# ── DB helpers ────────────────────────────────────────────────────────────────
async def get_account(user_id: int, guild_id: int) -> dict:
    doc = await economy_col.find_one({"user_id": str(user_id), "guild_id": str(guild_id)})
    if not doc:
        doc = {
            "user_id":      str(user_id),
            "guild_id":     str(guild_id),
            "wallet":       0,
            "bank":         0,
            "last_daily":   None,
            "last_work":    None,
            "total_earned": 0,
        }
        await economy_col.insert_one(doc)
    return doc

async def update_wallet(user_id: int, guild_id: int, amount: int):
    await economy_col.update_one(
        {"user_id": str(user_id), "guild_id": str(guild_id)},
        {"$inc": {"wallet": amount, "total_earned": max(0, amount)}},
        upsert=True
    )

async def update_bank(user_id: int, guild_id: int, amount: int):
    await economy_col.update_one(
        {"user_id": str(user_id), "guild_id": str(guild_id)},
        {"$inc": {"bank": amount}},
        upsert=True
    )

def _cd_remaining(last_time, cooldown: int) -> int:
    """Returns seconds remaining on cooldown, 0 if ready."""
    if not last_time:
        return 0
    if last_time.tzinfo is None:
        last_time = last_time.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - last_time).total_seconds()
    return max(0, int(cooldown - elapsed))

def _fmt_cd(seconds: int) -> str:
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Balance ────────────────────────────────────────────────────────────────
    @commands.command(aliases=["bal", "wallet", "cash"])
    async def balance(self, ctx, member: discord.Member = None):
        """Check your or someone's balance."""
        member = member or ctx.author
        acc    = await get_account(member.id, ctx.guild.id)
        total  = acc["wallet"] + acc["bank"]

        embed = discord.Embed(color=0xF0C040)
        embed.set_author(
            name=f"{member.display_name}'s Balance",
            icon_url=member.display_avatar.url
        )
        embed.add_field(name=f"{CURRENCY_EMOJI} Wallet", value=f"**{acc['wallet']:,} {CURRENCY}**", inline=True)
        embed.add_field(name="🏦 Bank",                  value=f"**{acc['bank']:,} {CURRENCY}**",   inline=True)
        embed.add_field(name="📊 Total",                 value=f"**{total:,} {CURRENCY}**",         inline=True)
        embed.set_footer(text=f"All time earned: {acc.get('total_earned', 0):,} HC")
        await ctx.reply(embed=embed)

    # ── Daily ──────────────────────────────────────────────────────────────────
    @commands.command()
    async def daily(self, ctx):
        """Claim your daily reward (resets every 24 hours)."""
        acc = await get_account(ctx.author.id, ctx.guild.id)
        cd  = _cd_remaining(acc.get("last_daily"), DAILY_CD)

        if cd > 0:
            embed = discord.Embed(
                description=f"Daily already claimed. Come back in **{_fmt_cd(cd)}**.",
                color=0xED4245
            )
            return await ctx.reply(embed=embed)

        amount = random.randint(DAILY_MIN, DAILY_MAX)
        await economy_col.update_one(
            {"user_id": str(ctx.author.id), "guild_id": str(ctx.guild.id)},
            {"$inc": {"wallet": amount, "total_earned": amount},
             "$set": {"last_daily": datetime.now(timezone.utc)}},
            upsert=True
        )
        embed = discord.Embed(
            title="Daily Reward",
            description=f"You claimed **{amount:,} {CURRENCY}**!",
            color=0xF0C040
        )
        embed.set_footer(text="Come back tomorrow for another reward.")
        await ctx.reply(embed=embed)

    # ── Work ───────────────────────────────────────────────────────────────────
    @commands.command()
    async def work(self, ctx):
        """Work for cash (1 hour cooldown)."""
        acc = await get_account(ctx.author.id, ctx.guild.id)
        cd  = _cd_remaining(acc.get("last_work"), WORK_CD)

        if cd > 0:
            embed = discord.Embed(
                description=f"You're tired. Rest for **{_fmt_cd(cd)}** before working again.",
                color=0xED4245
            )
            return await ctx.reply(embed=embed)

        amount   = random.randint(WORK_MIN, WORK_MAX)
        response = random.choice(WORK_RESPONSES).format(amount=f"{amount:,}")

        await economy_col.update_one(
            {"user_id": str(ctx.author.id), "guild_id": str(ctx.guild.id)},
            {"$inc": {"wallet": amount, "total_earned": amount},
             "$set": {"last_work": datetime.now(timezone.utc)}},
            upsert=True
        )
        embed = discord.Embed(description=f"{CURRENCY_EMOJI} {response}", color=0x57F287)
        embed.set_footer(text=f"Next work available in 1 hour · Wallet: {acc['wallet'] + amount:,} HC")
        await ctx.reply(embed=embed)

    # ── Deposit ────────────────────────────────────────────────────────────────
    @commands.command(aliases=["dep"])
    async def deposit(self, ctx, amount: str = None):
        """Deposit cash into your bank. Use 'all' to deposit everything."""
        acc = await get_account(ctx.author.id, ctx.guild.id)

        if not amount:
            return await ctx.reply("Usage: `,deposit <amount>` or `,deposit all`")

        if amount.lower() == "all":
            amt = acc["wallet"]
        else:
            try:
                amt = int(amount.replace(",", ""))
            except:
                return await ctx.reply("Enter a valid amount or `all`.")

        if amt <= 0:
            return await ctx.reply("Amount must be greater than 0.")
        if amt > acc["wallet"]:
            return await ctx.reply(f"You only have **{acc['wallet']:,} {CURRENCY}** in your wallet.")

        await economy_col.update_one(
            {"user_id": str(ctx.author.id), "guild_id": str(ctx.guild.id)},
            {"$inc": {"wallet": -amt, "bank": amt}}
        )
        embed = discord.Embed(
            description=f"Deposited **{amt:,} {CURRENCY}** into your bank.",
            color=0x57F287
        )
        embed.set_footer(text=f"Bank: {acc['bank'] + amt:,} HC · Wallet: {acc['wallet'] - amt:,} HC")
        await ctx.reply(embed=embed)

    # ── Withdraw ───────────────────────────────────────────────────────────────
    @commands.command(aliases=["with"])
    async def withdraw(self, ctx, amount: str = None):
        """Withdraw cash from your bank."""
        acc = await get_account(ctx.author.id, ctx.guild.id)

        if not amount:
            return await ctx.reply("Usage: `,withdraw <amount>` or `,withdraw all`")

        if amount.lower() == "all":
            amt = acc["bank"]
        else:
            try:
                amt = int(amount.replace(",", ""))
            except:
                return await ctx.reply("Enter a valid amount or `all`.")

        if amt <= 0:
            return await ctx.reply("Amount must be greater than 0.")
        if amt > acc["bank"]:
            return await ctx.reply(f"You only have **{acc['bank']:,} {CURRENCY}** in your bank.")

        await economy_col.update_one(
            {"user_id": str(ctx.author.id), "guild_id": str(ctx.guild.id)},
            {"$inc": {"bank": -amt, "wallet": amt}}
        )
        embed = discord.Embed(
            description=f"Withdrew **{amt:,} {CURRENCY}** from your bank.",
            color=0x57F287
        )
        embed.set_footer(text=f"Wallet: {acc['wallet'] + amt:,} HC · Bank: {acc['bank'] - amt:,} HC")
        await ctx.reply(embed=embed)

    # ── Give / Pay ─────────────────────────────────────────────────────────────
    @commands.command(aliases=["pay", "give"])
    async def transfer(self, ctx, member: discord.Member = None, amount: str = None):
        """Send cash to another member."""
        if not member or not amount:
            return await ctx.reply("Usage: `,transfer @user <amount>`")
        if member.id == ctx.author.id:
            return await ctx.reply("You can't send money to yourself.")
        if member.bot:
            return await ctx.reply("You can't send money to bots.")

        try:
            amt = int(amount.replace(",", ""))
        except:
            return await ctx.reply("Enter a valid amount.")

        if amt <= 0:
            return await ctx.reply("Amount must be greater than 0.")

        acc = await get_account(ctx.author.id, ctx.guild.id)
        if amt > acc["wallet"]:
            return await ctx.reply(f"Not enough in wallet. You have **{acc['wallet']:,} {CURRENCY}**.")

        await update_wallet(ctx.author.id, ctx.guild.id, -amt)
        await update_wallet(member.id, ctx.guild.id, amt)

        embed = discord.Embed(
            description=f"{CURRENCY_EMOJI} **{ctx.author.mention}** sent **{amt:,} {CURRENCY}** to **{member.mention}**.",
            color=0xF0C040
        )
        await ctx.reply(embed=embed)

    # ── Coinflip ───────────────────────────────────────────────────────────────
    @commands.command(aliases=["cf"])
    async def coinflip(self, ctx, amount: str = None, choice: str = None):
        """
        Gamble with a coinflip.
        Usage: ,cf <amount> [heads/tails]
        Example: ,cf 500 heads  or  ,cf all
        """
        if not amount:
            return await ctx.reply("Usage: `,cf <amount> [heads/tails]`\nExample: `,cf 500 heads`")

        acc = await get_account(ctx.author.id, ctx.guild.id)

        if amount.lower() == "all":
            amt = acc["wallet"]
        else:
            try:
                amt = int(amount.replace(",", ""))
            except:
                return await ctx.reply("Enter a valid amount or `all`.")

        if amt <= 0:
            return await ctx.reply("Amount must be greater than 0.")
        if amt > acc["wallet"]:
            return await ctx.reply(f"Not enough in wallet. You have **{acc['wallet']:,} {CURRENCY}**.")

        # Determine user's pick
        if choice and choice.lower() in ("heads", "h"):
            pick = "heads"
        elif choice and choice.lower() in ("tails", "t"):
            pick = "tails"
        else:
            pick = random.choice(["heads", "tails"])

        result = random.choice(["heads", "tails"])
        won    = pick == result
        emoji  = "🪙"

        if won:
            await update_wallet(ctx.author.id, ctx.guild.id, amt)
            embed = discord.Embed(
                title=f"{emoji} {result.title()} — You Won!",
                description=f"You picked **{pick}** and won **{amt:,} {CURRENCY}**!",
                color=0x57F287
            )
            new_wallet = acc["wallet"] + amt
        else:
            await update_wallet(ctx.author.id, ctx.guild.id, -amt)
            embed = discord.Embed(
                title=f"{emoji} {result.title()} — You Lost",
                description=f"You picked **{pick}** but it was **{result}**. Lost **{amt:,} {CURRENCY}**.",
                color=0xED4245
            )
            new_wallet = acc["wallet"] - amt

        embed.set_footer(text=f"Wallet: {new_wallet:,} HC")
        await ctx.reply(embed=embed)

    # ── Slots ──────────────────────────────────────────────────────────────────
    SLOTS = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣", "⭐"]
    SLOT_MULT = {"💎": 5, "7️⃣": 4, "⭐": 3, "🍇": 2.5, "🍊": 2, "🍋": 1.5, "🍒": 1.2}

    @commands.command(aliases=["slot"])
    async def slots(self, ctx, amount: str = None):
        """Spin the slot machine. Match 3 to win big!"""
        if not amount:
            return await ctx.reply("Usage: `,slots <amount>`")

        acc = await get_account(ctx.author.id, ctx.guild.id)

        if amount.lower() == "all":
            amt = acc["wallet"]
        else:
            try:
                amt = int(amount.replace(",", ""))
            except:
                return await ctx.reply("Enter a valid amount or `all`.")

        if amt <= 0:
            return await ctx.reply("Amount must be greater than 0.")
        if amt > acc["wallet"]:
            return await ctx.reply(f"Not enough in wallet. You have **{acc['wallet']:,} {CURRENCY}**.")

        # Spin
        reels  = [random.choice(self.SLOTS) for _ in range(3)]
        result = f"[ {reels[0]} | {reels[1]} | {reels[2]} ]"

        if reels[0] == reels[1] == reels[2]:
            # Jackpot — all 3 match
            mult     = self.SLOT_MULT.get(reels[0], 1.2)
            winnings = int(amt * mult)
            net      = winnings - amt
            await update_wallet(ctx.author.id, ctx.guild.id, net)
            embed = discord.Embed(
                title="Jackpot!",
                description=(
                    f"**{result}**\n\n"
                    f"All 3 match! You won **{winnings:,} {CURRENCY}**! (x{mult})"
                ),
                color=0xffd700
            )
        elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
            # 2 match — small win
            winnings = int(amt * 0.5)
            net      = winnings - amt
            await update_wallet(ctx.author.id, ctx.guild.id, net)
            embed = discord.Embed(
                title="Almost!",
                description=(
                    f"**{result}**\n\n"
                    f"2 match! You got back **{winnings:,} {CURRENCY}**."
                ),
                color=0xFEE75C
            )
        else:
            # No match — lose
            await update_wallet(ctx.author.id, ctx.guild.id, -amt)
            winnings = 0
            embed = discord.Embed(
                title="No Match",
                description=(
                    f"**{result}**\n\n"
                    f"Better luck next time. Lost **{amt:,} {CURRENCY}**."
                ),
                color=0xED4245
            )

        new_wallet = acc["wallet"] + (winnings - amt if winnings else -amt)
        embed.set_footer(text=f"Wallet: {max(0, new_wallet):,} HC")
        await ctx.reply(embed=embed)

    # ── Rob ────────────────────────────────────────────────────────────────────
    @commands.command()
    async def rob(self, ctx, member: discord.Member = None):
        """
        Try to rob someone's wallet. High risk, high reward.
        50% chance — fail and pay a fine.
        """
        if not member:
            return await ctx.reply("Usage: `,rob @user`")
        if member.id == ctx.author.id:
            return await ctx.reply("Can't rob yourself.")
        if member.bot:
            return await ctx.reply("Can't rob a bot.")

        robber_acc = await get_account(ctx.author.id, ctx.guild.id)
        victim_acc = await get_account(member.id, ctx.guild.id)

        if victim_acc["wallet"] < 100:
            return await ctx.reply(f"**{member.display_name}** is broke. Not worth robbing.")
        if robber_acc["wallet"] < 50:
            return await ctx.reply("You need at least **50 HC** in your wallet to attempt a rob (fine insurance).")

        success = random.random() < 0.45   # 45% success rate

        if success:
            steal = random.randint(
                max(1, victim_acc["wallet"] // 10),
                max(1, victim_acc["wallet"] // 3)
            )
            await update_wallet(ctx.author.id, ctx.guild.id, steal)
            await update_wallet(member.id, ctx.guild.id, -steal)
            embed = discord.Embed(
                title="Robbery Successful",
                description=(
                    f"You robbed **{member.display_name}** and got away with "
                    f"**{steal:,} {CURRENCY}**!"
                ),
                color=0x57F287
            )
        else:
            fine = random.randint(50, min(200, robber_acc["wallet"] // 2))
            await update_wallet(ctx.author.id, ctx.guild.id, -fine)
            embed = discord.Embed(
                title="Caught!",
                description=(
                    f"You were caught trying to rob **{member.display_name}**!\n"
                    f"You paid a fine of **{fine:,} {CURRENCY}**."
                ),
                color=0xED4245
            )

        await ctx.reply(embed=embed)

    # ── Leaderboard ────────────────────────────────────────────────────────────
    @commands.command(aliases=["rich", "eco"])
    async def richlist(self, ctx):
        """See the richest members in this server."""
        cursor = economy_col.find(
            {"guild_id": str(ctx.guild.id)}
        ).sort("wallet", -1).limit(10)
        docs = await cursor.to_list(10)

        if not docs:
            return await ctx.reply("No economy data yet. Use `,daily` to get started!")

        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for i, doc in enumerate(docs, 1):
            m     = ctx.guild.get_member(int(doc["user_id"]))
            name  = m.display_name if m else f"Unknown"
            total = doc["wallet"] + doc["bank"]
            pos   = medals[i-1] if i <= 3 else f"`{i}.`"
            lines.append(f"{pos} **{name}** — {total:,} HC (💰{doc['wallet']:,} | 🏦{doc['bank']:,})")

        embed = discord.Embed(
            title=f"Richest Members — {ctx.guild.name}",
            description="\n".join(lines),
            color=0xF0C040
        )
        embed.set_footer(text="Wallet + Bank = Total")
        await ctx.reply(embed=embed)

    # ── Admin: give/take cash ──────────────────────────────────────────────────
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def givecash(self, ctx, member: discord.Member = None, amount: int = None):
        """Give cash to a member (Admin only)."""
        if not member or not amount:
            return await ctx.reply("Usage: `,givecash @user <amount>`")
        await update_wallet(member.id, ctx.guild.id, amount)
        await ctx.reply(embed=discord.Embed(
            description=f"Gave **{amount:,} {CURRENCY}** to {member.mention}.",
            color=0x57F287
        ))

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def takecash(self, ctx, member: discord.Member = None, amount: int = None):
        """Remove cash from a member (Admin only)."""
        if not member or not amount:
            return await ctx.reply("Usage: `,takecash @user <amount>`")
        await update_wallet(member.id, ctx.guild.id, -amount)
        await ctx.reply(embed=discord.Embed(
            description=f"Removed **{amount:,} {CURRENCY}** from {member.mention}.",
            color=0x2B2D31
        ))

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def resetcash(self, ctx, member: discord.Member = None):
        """Reset a member's entire balance (Admin only)."""
        if not member:
            return await ctx.reply("Usage: `,resetcash @user`")
        await economy_col.update_one(
            {"user_id": str(member.id), "guild_id": str(ctx.guild.id)},
            {"$set": {"wallet": 0, "bank": 0, "total_earned": 0}},
            upsert=True
        )
        await ctx.reply(f"Balance reset for **{member.display_name}**.")


async def setup(bot):
    await bot.add_cog(Economy(bot))
