import asyncio
import re
from datetime import datetime, timezone
from typing import Union

import discord
from discord import app_commands
from discord.ext import commands
from pymongo import ReturnDocument, UpdateOne

from utils.db import invite_log_col, invites_col, msg_count_col
from utils.helpers import ctx_admin

NEUTRAL = 0x2B2D31
BRAND = 0x5865F2
SUCCESS = 0x57F287
DANGER = 0xED4245
MEDALS = ["🥇", "🥈", "🥉"]
CODE_PATTERN = re.compile(r"^[A-Za-z0-9-]{2,32}$")

invite_cache: dict[int, dict[str, int]] = {}
join_locks: dict[int, asyncio.Lock] = {}


def make_embed(title=None, description=None, color=BRAND, guild=None):
    embed = discord.Embed(title=title, description=description, color=color)
    if guild:
        embed.set_footer(text=guild.name, icon_url=guild.icon.url if guild.icon else None)
    return embed


def error_embed(text, guild=None):
    return make_embed("❌ Oops", text, DANGER, guild)


def success_embed(text, guild=None):
    return make_embed("✅ Done", text, SUCCESS, guild)


def count_text(number, word):
    return f"{number:,} {word}" + ("" if number == 1 else "s")


def place(position):
    return MEDALS[position - 1] if position <= 3 else f"`{position}.`"


async def read_invites(guild):
    try:
        return await guild.invites()
    except discord.HTTPException:
        return None


def invite_operation(guild_id, invite):
    return UpdateOne(
        {"guild_id": str(guild_id), "code": invite.code},
        {
            "$set": {
                "inviter_id": str(invite.inviter.id) if invite.inviter else None,
                "max_uses": invite.max_uses,
                "url": invite.url,
                "deleted": False,
            },
            "$setOnInsert": {
                "uses": invite.uses or 0,
                "created_at": invite.created_at or datetime.now(timezone.utc),
            },
        },
        upsert=True,
    )


async def sync_invites(guild):
    invites = await read_invites(guild)
    if invites is None:
        invite_cache[guild.id] = {}
        return
    invite_cache[guild.id] = {inv.code: inv.uses or 0 for inv in invites}
    operations = [invite_operation(guild.id, inv) for inv in invites]
    if operations:
        await invites_col.bulk_write(operations, ordered=False)
    await invites_col.update_many(
        {"guild_id": str(guild.id), "code": {"$nin": list(invite_cache[guild.id])}},
        {"$set": {"deleted": True}},
    )


async def find_used_invite(guild):
    fresh = await read_invites(guild)
    if fresh is None:
        return None, None

    fresh_by_code = {inv.code: inv for inv in fresh}
    new_counts = {code: inv.uses or 0 for code, inv in fresh_by_code.items()}
    old_counts = invite_cache.get(guild.id, {})

    used = next(
        (code for code, uses in new_counts.items() if uses > old_counts.get(code, 0)), None
    )

    if used is None:
        gone = [code for code in old_counts if code not in new_counts]
        if gone:
            docs = await invites_col.find(
                {"guild_id": str(guild.id), "code": {"$in": gone}}
            ).to_list(len(gone))
            limits = {doc["code"]: doc.get("max_uses") for doc in docs}
            maybe = [
                code for code in gone if limits.get(code) and old_counts[code] + 1 >= limits[code]
            ]
            if len(maybe) == 1:
                used = maybe[0]

    invite_cache[guild.id] = new_counts

    if used is None:
        return None, None

    doc = await invites_col.find_one_and_update(
        {"guild_id": str(guild.id), "code": used},
        {"$inc": {"uses": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None and used in fresh_by_code:
        await invites_col.bulk_write([invite_operation(guild.id, fresh_by_code[used])])
        doc = await invites_col.find_one({"guild_id": str(guild.id), "code": used})

    return used, (doc.get("inviter_id") if doc else None)


async def get_log_channel(guild):
    config = await invite_log_col.find_one({"guild_id": str(guild.id)})
    if not config or not config.get("channel_id"):
        return None
    channel = guild.get_channel(int(config["channel_id"]))
    if channel is None:
        return None
    perms = channel.permissions_for(guild.me)
    if not (perms.send_messages and perms.embed_links):
        return None
    return channel


async def invite_totals(guild_id, user_id):
    rows = await invites_col.aggregate(
        [
            {"$match": {"guild_id": str(guild_id), "inviter_id": str(user_id)}},
            {
                "$group": {
                    "_id": None,
                    "total": {"$sum": "$uses"},
                    "active": {"$sum": {"$cond": [{"$eq": ["$deleted", True]}, 0, 1]}},
                }
            },
        ]
    ).to_list(1)
    if not rows:
        return 0, 0
    return rows[0]["total"], rows[0]["active"]


async def build_invites_embed(guild, member):
    total, active = await invite_totals(guild.id, member.id)
    embed = make_embed(color=BRAND, guild=guild)
    embed.set_author(name=f"{member.display_name} • Invites", icon_url=member.display_avatar.url)
    embed.add_field(name="👥 People invited", value=f"**{total:,}**", inline=True)
    embed.add_field(name="🔗 Active links", value=f"**{active:,}**", inline=True)
    return embed


async def build_messages_embed(guild, member):
    doc = await msg_count_col.find_one({"guild_id": str(guild.id), "user_id": str(member.id)})
    count = doc.get("count", 0) if doc else 0
    if count > 0:
        ahead = await msg_count_col.count_documents(
            {"guild_id": str(guild.id), "count": {"$gt": count}}
        )
        rank = f"**#{ahead + 1:,}**"
    else:
        rank = "Not ranked yet"
    embed = make_embed(color=BRAND, guild=guild)
    embed.set_author(name=f"{member.display_name} • Messages", icon_url=member.display_avatar.url)
    embed.add_field(name="💬 Messages sent", value=f"**{count:,}**", inline=True)
    embed.add_field(name="🏆 Server rank", value=rank, inline=True)
    return embed


class ConfirmView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who used this command can press these buttons.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Yes, reset it", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()


async def ask_to_confirm(ctx, text):
    view = ConfirmView(ctx.author.id)
    message = await ctx.reply(
        embed=make_embed("⚠️ Are you sure?", f"{text}\n\nThis cannot be undone.", DANGER, ctx.guild),
        view=view,
        mention_author=False,
    )
    await view.wait()
    return view.confirmed, message


class Tracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        return True

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await sync_invites(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        await sync_invites(guild)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        invite_cache.pop(guild.id, None)
        join_locks.pop(guild.id, None)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        if not invite.guild:
            return
        invite_cache.setdefault(invite.guild.id, {})[invite.code] = invite.uses or 0
        await invites_col.bulk_write([invite_operation(invite.guild.id, invite)])

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        if not invite.guild:
            return
        await invites_col.update_one(
            {"guild_id": str(invite.guild.id), "code": invite.code},
            {"$set": {"deleted": True}},
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        lock = join_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            used_code, inviter_id = await find_used_invite(guild)

        channel = await get_log_channel(guild)
        if not channel:
            return

        embed = make_embed("📥 Member Joined", f"{member.mention} joined the server.", SUCCESS, guild)
        embed.timestamp = datetime.now(timezone.utc)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="Account created", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True
        )
        embed.add_field(
            name="Invited by", value=f"<@{inviter_id}>" if inviter_id else "Unknown", inline=True
        )
        embed.add_field(
            name="Invite", value=f"`{used_code}`" if used_code else "Could not find it", inline=True
        )
        embed.set_footer(
            text=f"Member #{guild.member_count:,} • ID {member.id}",
            icon_url=guild.icon.url if guild.icon else None,
        )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        channel = await get_log_channel(member.guild)
        if not channel:
            return
        safe_name = discord.utils.escape_markdown(str(member))
        embed = make_embed("📤 Member Left", f"**{safe_name}** left the server.", DANGER, member.guild)
        embed.timestamp = datetime.now(timezone.utc)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="Joined",
            value=f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "Unknown",
            inline=True,
        )
        embed.set_footer(
            text=f"{member.guild.member_count:,} members left • ID {member.id}",
            icon_url=member.guild.icon.url if member.guild.icon else None,
        )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.webhook_id or not message.guild:
            return
        await msg_count_col.update_one(
            {"guild_id": str(message.guild.id), "user_id": str(message.author.id)},
            {"$inc": {"count": 1}},
            upsert=True,
        )

    @commands.command(
        name="invites",
        aliases=["inv", "invcount"],
        help="See how many people someone has invited.",
    )
    async def invites(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        await ctx.reply(embed=await build_invites_embed(ctx.guild, member), mention_author=False)

    @commands.command(
        name="inviteleaderboard",
        aliases=["invlb", "topinviters"],
        help="See who has invited the most people.",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def inviteleaderboard(self, ctx):
        rows = await invites_col.aggregate(
            [
                {"$match": {"guild_id": str(ctx.guild.id), "inviter_id": {"$ne": None}}},
                {"$group": {"_id": "$inviter_id", "total": {"$sum": "$uses"}}},
                {"$match": {"total": {"$gt": 0}}},
                {"$sort": {"total": -1}},
                {"$limit": 10},
            ]
        ).to_list(10)

        if not rows:
            return await ctx.reply(
                embed=make_embed("🏆 Invite Leaderboard", "No invites yet. Share a link to get started!", NEUTRAL, ctx.guild),
                mention_author=False,
            )

        lines = [
            f"{place(i)} <@{row['_id']}> — **{count_text(row['total'], 'invite')}**"
            for i, row in enumerate(rows, 1)
        ]
        embed = make_embed("🏆 Invite Leaderboard", "\n".join(lines), BRAND, ctx.guild)
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(
        name="inviteinfo",
        aliases=["infolink"],
        help="See details about an invite code.",
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def inviteinfo(self, ctx, code: str = None):
        if not code:
            return await ctx.reply(
                embed=error_embed(
                    f"Please give me an invite code. Example: `{ctx.clean_prefix}inviteinfo abc123`", ctx.guild
                ),
                mention_author=False,
            )

        clean = code.strip().rstrip("/").split("/")[-1]
        if not CODE_PATTERN.match(clean):
            return await ctx.reply(
                embed=error_embed("That does not look like an invite code.", ctx.guild),
                mention_author=False,
            )

        doc = await invites_col.find_one({"guild_id": str(ctx.guild.id), "code": clean})
        if not doc:
            return await ctx.reply(
                embed=error_embed(f"I have no record of the invite `{clean}`.", ctx.guild),
                mention_author=False,
            )

        embed = make_embed(f"🔗 Invite {clean}", None, BRAND, ctx.guild)
        embed.add_field(name="Link", value=f"discord.gg/{clean}", inline=True)
        embed.add_field(
            name="Created by",
            value=f"<@{doc['inviter_id']}>" if doc.get("inviter_id") else "Unknown",
            inline=True,
        )
        embed.add_field(name="Status", value="Deleted" if doc.get("deleted") else "Active", inline=True)
        embed.add_field(name="Uses", value=f"{doc.get('uses', 0):,}", inline=True)
        embed.add_field(name="Use limit", value=str(doc.get("max_uses") or "No limit"), inline=True)
        created = doc.get("created_at")
        if created:
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            embed.add_field(name="Created", value=f"<t:{int(created.timestamp())}:R>", inline=True)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(
        name="invitelog",
        aliases=["invlog", "setinvitelog"],
        help="Choose where join and leave logs are posted.",
    )
    @ctx_admin()
    async def invitelog(self, ctx, setting: Union[discord.TextChannel, str] = None):
        prefix = ctx.clean_prefix

        if setting is None:
            config = await invite_log_col.find_one({"guild_id": str(ctx.guild.id)})
            current = f"<#{config['channel_id']}>" if config and config.get("channel_id") else "Not set"
            embed = make_embed("📋 Invite Logs", None, NEUTRAL, ctx.guild)
            embed.add_field(name="Log channel", value=current, inline=False)
            embed.add_field(
                name="How to change it",
                value=f"`{prefix}invitelog #channel` — Turn on\n`{prefix}invitelog off` — Turn off",
                inline=False,
            )
            return await ctx.reply(embed=embed, mention_author=False)

        if isinstance(setting, str):
            if setting.lower() in ("off", "disable", "none"):
                await invite_log_col.delete_one({"guild_id": str(ctx.guild.id)})
                return await ctx.reply(
                    embed=success_embed("Invite logs are now turned off.", ctx.guild), mention_author=False
                )
            return await ctx.reply(
                embed=error_embed("Please mention a channel, or type `off` to turn logs off.", ctx.guild),
                mention_author=False,
            )

        perms = setting.permissions_for(ctx.guild.me)
        if not (perms.send_messages and perms.embed_links):
            return await ctx.reply(
                embed=error_embed(
                    f"I cannot post embeds in {setting.mention}. Please give me permission and try again.",
                    ctx.guild,
                ),
                mention_author=False,
            )

        await invite_log_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"channel_id": str(setting.id)}},
            upsert=True,
        )
        await ctx.reply(
            embed=success_embed(
                f"Logs will be posted in {setting.mention}.\n\n"
                "**What you will see**\n"
                "• Who joined, and who invited them\n"
                "• Who left the server",
                ctx.guild,
            ),
            mention_author=False,
        )

    @commands.command(
        name="invitelogdisable",
        aliases=["invitelogoff"],
        help="Turn off invite logs.",
    )
    @ctx_admin()
    async def invitelogdisable(self, ctx):
        await invite_log_col.delete_one({"guild_id": str(ctx.guild.id)})
        await ctx.reply(embed=success_embed("Invite logs are now turned off.", ctx.guild), mention_author=False)

    @commands.command(
        name="invitereset",
        aliases=["resetinvite"],
        help="Reset invite counts for one person or the whole server.",
    )
    @ctx_admin()
    async def invitereset(self, ctx, member: discord.Member = None):
        if member:
            await invites_col.update_many(
                {"guild_id": str(ctx.guild.id), "inviter_id": str(member.id)},
                {"$set": {"uses": 0}},
            )
            return await ctx.reply(
                embed=success_embed(f"Invite count reset for {member.mention}.", ctx.guild),
                mention_author=False,
            )

        confirmed, message = await ask_to_confirm(
            ctx, "This will erase the invite counts for everyone in this server."
        )
        if not confirmed:
            return await message.edit(
                embed=make_embed("Cancelled", "Nothing was changed.", NEUTRAL, ctx.guild), view=None
            )
        await invites_col.delete_many({"guild_id": str(ctx.guild.id)})
        await sync_invites(ctx.guild)
        await message.edit(
            embed=success_embed("All invite counts for this server were reset.", ctx.guild), view=None
        )

    @commands.command(
        name="messages",
        aliases=["msgs", "msgcount", "mc2"],
        help="See how many messages someone has sent.",
    )
    async def messages(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        await ctx.reply(embed=await build_messages_embed(ctx.guild, member), mention_author=False)

    @commands.command(
        name="msgleaderboard",
        aliases=["msglb", "topchatters", "chatleaderboard"],
        help="See the most active chatters.",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def msgleaderboard(self, ctx):
        docs = (
            await msg_count_col.find({"guild_id": str(ctx.guild.id), "count": {"$gt": 0}})
            .sort("count", -1)
            .limit(10)
            .to_list(10)
        )

        if not docs:
            return await ctx.reply(
                embed=make_embed("🏆 Message Leaderboard", "No messages counted yet.", NEUTRAL, ctx.guild),
                mention_author=False,
            )

        lines = [
            f"{place(i)} <@{doc['user_id']}> — **{count_text(doc['count'], 'message')}**"
            for i, doc in enumerate(docs, 1)
        ]
        embed = make_embed("🏆 Message Leaderboard", "\n".join(lines), BRAND, ctx.guild)
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(
        name="msgstats",
        aliases=["servermsgstats"],
        help="See message numbers for the whole server.",
    )
    @commands.cooldown(1, 10, commands.BucketType.guild)
    async def msgstats(self, ctx):
        rows = await msg_count_col.aggregate(
            [
                {"$match": {"guild_id": str(ctx.guild.id)}},
                {"$group": {"_id": None, "total": {"$sum": "$count"}, "members": {"$sum": 1}}},
            ]
        ).to_list(1)

        if not rows:
            return await ctx.reply(
                embed=make_embed("📊 Message Stats", "No messages counted yet.", NEUTRAL, ctx.guild),
                mention_author=False,
            )

        total = rows[0]["total"]
        members = rows[0]["members"]
        top = await msg_count_col.find_one({"guild_id": str(ctx.guild.id)}, sort=[("count", -1)])

        embed = make_embed("📊 Message Stats", None, BRAND, ctx.guild)
        embed.add_field(name="💬 Total messages", value=f"**{total:,}**", inline=True)
        embed.add_field(name="👥 Active members", value=f"**{members:,}**", inline=True)
        embed.add_field(name="📈 Average each", value=f"**{total // members:,}**", inline=True)
        embed.add_field(
            name="🏆 Top chatter",
            value=f"<@{top['user_id']}> — **{count_text(top['count'], 'message')}**",
            inline=False,
        )
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(
        name="msgreset",
        aliases=["resetmsg", "resetmessages"],
        help="Reset message counts for one person or the whole server.",
    )
    @ctx_admin()
    async def msgreset(self, ctx, member: discord.Member = None):
        if member:
            await msg_count_col.delete_one(
                {"guild_id": str(ctx.guild.id), "user_id": str(member.id)}
            )
            return await ctx.reply(
                embed=success_embed(f"Message count reset for {member.mention}.", ctx.guild),
                mention_author=False,
            )

        confirmed, message = await ask_to_confirm(
            ctx, "This will erase the message counts for everyone in this server."
        )
        if not confirmed:
            return await message.edit(
                embed=make_embed("Cancelled", "Nothing was changed.", NEUTRAL, ctx.guild), view=None
            )
        await msg_count_col.delete_many({"guild_id": str(ctx.guild.id)})
        await message.edit(
            embed=success_embed("All message counts for this server were reset.", ctx.guild), view=None
        )

    @app_commands.command(name="invites", description="See how many people someone has invited")
    @app_commands.describe(member="The person to look up")
    @app_commands.guild_only()
    async def slash_invites(self, interaction: discord.Interaction, member: discord.Member = None):
        await interaction.response.defer()
        member = member or interaction.user
        await interaction.followup.send(embed=await build_invites_embed(interaction.guild, member))

    @app_commands.command(name="messages", description="See how many messages someone has sent")
    @app_commands.describe(member="The person to look up")
    @app_commands.guild_only()
    async def slash_messages(self, interaction: discord.Interaction, member: discord.Member = None):
        await interaction.response.defer()
        member = member or interaction.user
        await interaction.followup.send(embed=await build_messages_embed(interaction.guild, member))


async def setup(bot):
    await bot.add_cog(Tracker(bot))