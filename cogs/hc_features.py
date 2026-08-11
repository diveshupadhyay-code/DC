import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta

from utils.db import db, premium_col
from utils.helpers import BOT_OWNER_ID, ctx_owner
from cogs.economy import get_account, add_wallet

hc_costs_col = db["hc_costs"]
override_col = db["owner_override"]

DEFAULT_COSTS = {
    "ai_msg_cost": 10,
    "emoji_cost": 100,
    "summon_owner_cost": 5000,
    "premium_hour_cost": 50,
    "override_cost": 1000000,
}


async def get_costs() -> dict:
    doc = await hc_costs_col.find_one({"_id": "global"})
    costs = dict(DEFAULT_COSTS)
    if doc:
        costs.update({k: v for k, v in doc.items() if k in DEFAULT_COSTS})
    return costs


async def try_charge(user_id: int, amount: int) -> bool:
    if amount <= 0:
        return True
    acc = await get_account(user_id)
    if acc["wallet"] < amount:
        return False
    await add_wallet(user_id, -amount)
    return True


async def share_owner_bonus(ctx, spent_amount: int):
    if ctx.author.id != BOT_OWNER_ID or spent_amount <= 0:
        return

    bonus_pool = int(spent_amount * 0.10)
    if bonus_pool <= 0:
        return

    members = [m for m in ctx.guild.members if not m.bot]
    if not members:
        return

    share = max(1, bonus_pool // len(members))
    for member in members:
        await add_wallet(member.id, share)

    embed = discord.Embed(
        title="🎁 Owner Bonus Distributed!",
        description=f"The server owner just spent big! **{bonus_pool:,} HC** (10% bonus) has been distributed among **{len(members)}** server members.\n\nEveryone received: **+{share:,} HC**",
        color=0xFEE75C
    )
    await ctx.send(embed=embed)


async def is_override_active() -> bool:
    doc = await override_col.find_one({"_id": "owner"})
    return bool(doc and doc.get("active"))


def has_permissions_or_override(**perms):
    async def predicate(ctx):
        if ctx.author.id == BOT_OWNER_ID and await is_override_active():
            return True
        return await commands.has_permissions(**perms).predicate(ctx)
    return commands.check(predicate)


class HCFeatures(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="hccost", aliases=["hccosts"], invoke_without_command=True)
    @ctx_owner()
    async def hccost(self, ctx):
        costs = await get_costs()
        embed = discord.Embed(
            title="💰 Price List",
            description="Here are the official rates for feature usage:",
            color=0x2B2D31
        )
        for key, val in costs.items():
            embed.add_field(
                name=f"• {key.replace('_', ' ').title()}",
                value=f"`{val:,} HC`",
                inline=True
            )
        embed.set_footer(
            text=f"Requested by {ctx.author.display_name} | Use ,hccost set <key> <amount> to change rates",
            icon_url=ctx.author.display_avatar.url
        )
        await ctx.reply(embed=embed)

    @hccost.command(name="set")
    @ctx_owner()
    async def hccost_set(self, ctx, key: str = None, amount: int = None):
        if not key or amount is None or key not in DEFAULT_COSTS or amount < 0:
            embed = discord.Embed(
                title="❌ Invalid Syntax",
                description="Please use the correct format:\n`,hccost set <key> <amount>`",
                color=0xED4245
            )
            embed.add_field(
                name="Valid Keys",
                value="\n".join(f"• `{k}`" for k in DEFAULT_COSTS),
                inline=False
            )
            return await ctx.reply(embed=embed)

        await hc_costs_col.update_one(
            {"_id": "global"}, {"$set": {key: amount}}, upsert=True
        )
        embed = discord.Embed(
            title="✅ Rate Updated Successfully",
            description=f"The price for `{key}` is now set to **{amount:,} HC**.",
            color=0x57F287
        )
        await ctx.reply(embed=embed)

    @commands.command(name="visitserver", aliases=["ownervisit", "summonme"])
    @ctx_owner()
    async def visitserver(self, ctx, guild_id: int = None):
        costs = await get_costs()
        cost = costs["summon_owner_cost"]

        if not guild_id:
            embed = discord.Embed(
                title="🚪 External Server Access",
                description="Provide a server ID to receive a VIP invite directly in your DM.",
                color=0x5865F2
            )
            embed.add_field(name="Usage", value="`,visitserver <guild_id>`", inline=True)
            embed.add_field(name="Required Cost", value=f"**{cost:,} HC**", inline=True)
            return await ctx.reply(embed=embed)

        guild = self.bot.get_guild(guild_id)
        if not guild:
            embed = discord.Embed(
                title="❌ Server Not Found",
                description=f"Could not locate any server matching the ID `{guild_id}`.",
                color=0xED4245
            )
            return await ctx.reply(embed=embed)

        if not await try_charge(ctx.author.id, cost):
            embed = discord.Embed(
                title="❌ Insufficient Balance",
                description=f"You need at least **{cost:,} HC** to generate an invite for **{guild.name}**.",
                color=0xED4245
            )
            return await ctx.reply(embed=embed)

        invite = None
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).create_instant_invite:
                try:
                    invite = await ch.create_invite(
                        max_age=3600, max_uses=1, reason=f"Owner visit paid ({cost} HC)"
                    )
                    break
                except discord.HTTPException:
                    continue

        if not invite:
            await add_wallet(ctx.author.id, cost)
            embed = discord.Embed(
                title="❌ Permission Denied",
                description="Unable to generate an invite due to server permission settings. Your HC balance has been refunded.",
                color=0xED4245
            )
            return await ctx.reply(embed=embed)

        dm_embed = discord.Embed(
            title="🎟️ Your VIP Pass is Ready",
            color=0x57F287
        )
        dm_embed.add_field(name="Target Server", value=f"**{guild.name}**", inline=False)
        dm_embed.add_field(name="Invite Link", value=f"[Click Here to Join]({invite.url})", inline=False)
        dm_embed.add_field(name="Validity", value="Valid for 1 hour or 1 single use.", inline=False)

        try:
            await ctx.author.send(embed=dm_embed)
            reply_embed = discord.Embed(
                title="✅ Pass Delivered",
                description="The server invite link has been delivered directly to your Direct Messages.",
                color=0x57F287
            )
            await ctx.reply(embed=reply_embed)
        except discord.Forbidden:
            await ctx.reply(embed=dm_embed)

        await share_owner_bonus(ctx, cost)

    @commands.command(name="hcpremium", aliases=["buypremium"])
    async def hcpremium(self, ctx, hours: int = None):
        costs = await get_costs()
        unit_cost = costs["premium_hour_cost"]

        if not hours or hours <= 0:
            embed = discord.Embed(
                title="✨ Purchase Premium Pass",
                description="Upgrade your account status to enjoy exclusive premium privileges.",
                color=0xFEE75C
            )
            embed.add_field(name="Usage", value="`,hcpremium <hours>`", inline=True)
            embed.add_field(name="Rate", value=f"**{unit_cost:,} HC** per hour", inline=True)
            return await ctx.reply(embed=embed)

        cost = hours * unit_cost
        if not await try_charge(ctx.author.id, cost):
            embed = discord.Embed(
                title="❌ Insufficient Funds",
                description=f"Purchasing **{hours} hour(s)** of premium costs **{cost:,} HC**. Your current wallet balance is too low.",
                color=0xED4245
            )
            return await ctx.reply(embed=embed)

        now = datetime.now(timezone.utc)
        existing = await premium_col.find_one({"type": "user", "id": str(ctx.author.id)})
        base = now

        if existing and existing.get("expires_at"):
            exp = existing["expires_at"]
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp > now:
                base = exp

        new_expiry = base + timedelta(hours=hours)
        await premium_col.update_one(
            {"type": "user", "id": str(ctx.author.id)},
            {"$set": {
                "type": "user", "id": str(ctx.author.id),
                "name": str(ctx.author), "expires_at": new_expiry, "via_hc": True,
            }},
            upsert=True
        )

        embed = discord.Embed(
            title="✨ Premium Activated Successfully",
            description="Your account has been successfully upgraded to VIP status.",
            color=0xFEE75C
        )
        embed.add_field(name="Valid Until", value=f"<t:{int(new_expiry.timestamp())}:F>", inline=False)
        embed.add_field(name="Time Remaining", value=f"<t:{int(new_expiry.timestamp())}:R>", inline=False)
        embed.add_field(name="Total Deducted", value=f"**{cost:,} HC**", inline=False)
        await ctx.reply(embed=embed)

        await share_owner_bonus(ctx, cost)

    @commands.group(name="override", invoke_without_command=True)
    @ctx_owner()
    async def override_group(self, ctx):
        active = await is_override_active()
        costs = await get_costs()
        
        embed = discord.Embed(
            title="🛡️ Owner Privilege Override",
            description="Toggle administrative permissions override across all servers.",
            color=0x57F287 if active else 0xED4245
        )
        embed.add_field(name="Current Status", value=f"**{'🟢 ACTIVE' if active else '🔴 INACTIVE'}**", inline=False)
        embed.add_field(name="Activation Cost", value=f"`,override on` — **{costs['override_cost']:,} HC**", inline=True)
        embed.add_field(name="Deactivation Cost", value="`,override off` — **Free**", inline=True)
        await ctx.reply(embed=embed)

    @override_group.command(name="on")
    @ctx_owner()
    async def override_on(self, ctx):
        if await is_override_active():
            embed = discord.Embed(
                title="⚡ Already Enabled",
                description="The privilege override mode is already running.",
                color=0xFEE75C
            )
            return await ctx.reply(embed=embed)

        costs = await get_costs()
        cost = costs["override_cost"]
        if not await try_charge(ctx.author.id, cost):
            embed = discord.Embed(
                title="❌ Insufficient Balance",
                description=f"Activating privilege override requires **{cost:,} HC**.",
                color=0xED4245
            )
            return await ctx.reply(embed=embed)

        await override_col.update_one({"_id": "owner"}, {"$set": {"active": True}}, upsert=True)
        embed = discord.Embed(
            title="🛡️ Override Mode Activated",
            description=f"Owner override is now active. **{cost:,} HC** has been deducted from your account.",
            color=0x57F287
        )
        await ctx.reply(embed=embed)

        await share_owner_bonus(ctx, cost)

    @override_group.command(name="off")
    @ctx_owner()
    async def override_off(self, ctx):
        await override_col.update_one({"_id": "owner"}, {"$set": {"active": False}}, upsert=True)
        embed = discord.Embed(
            title="🛡️ Override Mode Deactivated",
            description="Privilege override mode has been turned off. Standard permissions apply.",
            color=0x2B2D31
        )
        await ctx.reply(embed=embed)


async def setup(bot):
    await bot.add_cog(HCFeatures(bot))