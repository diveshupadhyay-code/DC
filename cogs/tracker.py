"""
cogs/tracker.py — Invite Tracker + Message Counter for Happy Bot.

INVITE TRACKER
  - Logs every invite used when a member joins (who invited them)
  - Stores invite usage in DB, detects which invite was used by diffing counts
  - ,invites [@user]      — see how many people a user has invited
  - ,inviteleaderboard    — top inviters in the server
  - ,inviteinfo <code>    — detailed info about a specific invite
  - ,invitelog #channel   — set the channel where invite logs are sent (Admin)
  - ,invitereset [@user]  — reset invite count (Admin)

MESSAGE COUNTER
  - Counts every message sent per user (global + per-server)
  - ,messages [@user]     — see total messages for a user
  - ,msgleaderboard       — top chatters in the server
  - ,msgreset [@user]     — reset message count (Admin)
  - ,msgstats             — server-wide message stats
"""

import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone

from utils.db import db, settings_col
from utils.helpers import BOT_OWNER_ID, ctx_admin, ctx_mod

# ── Collections ───────────────────────────────────────────────────────────────
invites_col     = db["invite_tracker"]     # {guild_id, inviter_id, code, uses, created_at}
invite_log_col  = db["invite_log_config"]  # {guild_id, channel_id}
msg_count_col   = db["message_counts"]     # {guild_id, user_id, count}

# ── In-memory invite cache ─────────────────────────────────────────────────────
# {guild_id: {code: uses}}
_invite_cache: dict[int, dict[str, int]] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _build_cache(guild: discord.Guild) -> dict[str, int]:
    """Fetch all invites for a guild and return {code: uses}."""
    try:
        invites = await guild.invites()
        return {inv.code: inv.uses for inv in invites}
    except discord.Forbidden:
        return {}


async def _get_inviter(guild_id: int, code: str) -> str | None:
    """Look up who created an invite by code from DB."""
    doc = await invites_col.find_one({"guild_id": str(guild_id), "code": code})
    return doc.get("inviter_id") if doc else None


class Tracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ══════════════════════════════════════════════════════════════════════════
    #  STARTUP — cache all guild invites
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            _invite_cache[guild.id] = await _build_cache(guild)

    # ══════════════════════════════════════════════════════════════════════════
    #  INVITE CACHE MAINTENANCE
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        """Add new invite to cache and DB."""
        if not invite.guild:
            return
        gid = invite.guild.id

        # Update memory cache
        if gid not in _invite_cache:
            _invite_cache[gid] = {}
        _invite_cache[gid][invite.code] = invite.uses or 0

        # Save to DB so we can look up the inviter later
        inviter_id = str(invite.inviter.id) if invite.inviter else None
        await invites_col.update_one(
            {"guild_id": str(gid), "code": invite.code},
            {"$set": {
                "guild_id":   str(gid),
                "code":       invite.code,
                "inviter_id": inviter_id,
                "uses":       invite.uses or 0,
                "max_uses":   invite.max_uses,
                "created_at": invite.created_at or datetime.now(timezone.utc),
                "url":        invite.url,
            }},
            upsert=True
        )

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        """Remove deleted invite from cache."""
        if invite.guild:
            _invite_cache.get(invite.guild.id, {}).pop(invite.code, None)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """Cache invites when bot joins a new server."""
        _invite_cache[guild.id] = await _build_cache(guild)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        """Clean up cache when bot leaves a server."""
        _invite_cache.pop(guild.id, None)

    # ══════════════════════════════════════════════════════════════════════════
    #  MEMBER JOIN — detect which invite was used
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild

        # Fetch current invite counts
        new_counts = await _build_cache(guild)
        old_counts = _invite_cache.get(guild.id, {})

        used_code    = None
        inviter_id   = None
        inviter_name = "Unknown"

        # Diff old vs new to find which invite gained a use
        for code, uses in new_counts.items():
            old_uses = old_counts.get(code, 0)
            if uses > old_uses:
                used_code = code
                break

        # Update cache immediately
        _invite_cache[guild.id] = new_counts

        if used_code:
            # Look up who owns this invite
            inviter_id = await _get_inviter(guild.id, used_code)
            if inviter_id:
                # Increment their invite count in DB
                await invites_col.update_one(
                    {"guild_id": str(guild.id), "code": used_code},
                    {"$inc": {"uses": 1}},
                    upsert=False
                )
                # Try to resolve name
                inviter = guild.get_member(int(inviter_id))
                inviter_name = inviter.mention if inviter else f"<@{inviter_id}>"

        # ── Send to invite log channel if configured ───────────────────────
        cfg = await invite_log_col.find_one({"guild_id": str(guild.id)})
        if cfg and cfg.get("channel_id"):
            ch = guild.get_channel(int(cfg["channel_id"]))
            if ch:
                embed = discord.Embed(
                    title="Member Joined",
                    color=0x57F287,
                    timestamp=datetime.now(timezone.utc)
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.add_field(name="Member",    value=f"{member.mention} (`{member.id}`)", inline=True)
                embed.add_field(name="Account",   value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
                embed.add_field(name="Invited By", value=inviter_name, inline=True)
                if used_code:
                    embed.add_field(name="Invite Code", value=f"`{used_code}`", inline=True)
                    embed.add_field(
                        name="Invite URL",
                        value=f"discord.gg/{used_code}",
                        inline=True
                    )
                else:
                    embed.add_field(name="Invite Code", value="Could not detect", inline=True)
                embed.set_footer(text=f"Member #{guild.member_count}")
                await ch.send(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  MEMBER LEAVE — log it too
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        cfg = await invite_log_col.find_one({"guild_id": str(member.guild.id)})
        if not cfg or not cfg.get("channel_id"):
            return
        ch = member.guild.get_channel(int(cfg["channel_id"]))
        if not ch:
            return
        embed = discord.Embed(
            title="Member Left",
            color=0xED4245,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Member",   value=f"{member} (`{member.id}`)", inline=True)
        embed.add_field(name="Joined",   value=f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "Unknown", inline=True)
        embed.set_footer(text=f"Members remaining: {member.guild.member_count}")
        await ch.send(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  MESSAGE COUNTER
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        await msg_count_col.update_one(
            {"guild_id": str(message.guild.id), "user_id": str(message.author.id)},
            {"$inc": {"count": 1}},
            upsert=True
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  INVITE COMMANDS
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["inv", "invcount"])
    async def invites(self, ctx, member: discord.Member = None):
        """Check how many people a member has invited to this server."""
        member = member or ctx.author
        cursor = invites_col.find({"guild_id": str(ctx.guild.id), "inviter_id": str(member.id)})
        docs   = await cursor.to_list(100)

        total_uses = sum(doc.get("uses", 0) for doc in docs)
        active     = len(docs)

        embed = discord.Embed(color=0x5865F2)
        embed.set_author(
            name=f"{member.display_name}'s Invites",
            icon_url=member.display_avatar.url
        )
        embed.add_field(name="👥 Total Invites", value=f"**{total_uses}**",  inline=True)
        embed.add_field(name="🔗 Active Links",  value=f"**{active}**",      inline=True)
        embed.set_footer(text=f"ID: {member.id}")
        await ctx.reply(embed=embed)

    @commands.command(aliases=["invlb", "topinviters"])
    async def inviteleaderboard(self, ctx):
        """See the top 10 inviters in this server."""
        cursor = invites_col.find({"guild_id": str(ctx.guild.id), "inviter_id": {"$ne": None}})
        docs   = await cursor.to_list(500)

        # Aggregate by inviter
        counts: dict[str, int] = {}
        for doc in docs:
            uid = doc["inviter_id"]
            counts[uid] = counts.get(uid, 0) + doc.get("uses", 0)

        if not counts:
            return await ctx.reply(embed=discord.Embed(
                description="No invite data yet.", color=0x2B2D31
            ))

        sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10]
        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for i, (uid, total) in enumerate(sorted_counts, 1):
            member = ctx.guild.get_member(int(uid))
            name   = member.display_name if member else f"User {uid}"
            pos    = medals[i-1] if i <= 3 else f"`{i}.`"
            lines.append(f"{pos} **{name}** — {total} invite(s)")

        embed = discord.Embed(
            title=f"Invite Leaderboard — {ctx.guild.name}",
            description="\n".join(lines),
            color=0x5865F2
        )
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        await ctx.reply(embed=embed)

    @commands.command(aliases=["infolink"])
    async def inviteinfo(self, ctx, code: str = None):
        """View detailed info about a specific invite code."""
        if not code:
            return await ctx.reply("Usage: `,inviteinfo <code>`  e.g. `,inviteinfo abc123`")

        code = code.replace("discord.gg/", "").replace("https://", "").strip()
        doc  = await invites_col.find_one({"guild_id": str(ctx.guild.id), "code": code})

        if not doc:
            return await ctx.reply(f"No data found for invite code `{code}`.")

        inviter = ctx.guild.get_member(int(doc["inviter_id"])) if doc.get("inviter_id") else None
        embed   = discord.Embed(title=f"Invite — {code}", color=0x5865F2)
        embed.add_field(name="Code",      value=f"`{code}`",                                    inline=True)
        embed.add_field(name="URL",       value=f"discord.gg/{code}",                           inline=True)
        embed.add_field(name="Created By", value=inviter.mention if inviter else f"`{doc.get('inviter_id','?')}`", inline=True)
        embed.add_field(name="Uses",      value=str(doc.get("uses", 0)),                        inline=True)
        embed.add_field(name="Max Uses",  value=str(doc.get("max_uses") or "Unlimited"),        inline=True)
        if doc.get("created_at"):
            ts = doc["created_at"]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            embed.add_field(name="Created", value=f"<t:{int(ts.timestamp())}:R>", inline=True)
        await ctx.reply(embed=embed)

    @commands.command(aliases=["invlog", "setinvitelog"])
    @ctx_admin()
    async def invitelog(self, ctx, channel: discord.TextChannel = None):
        """Set the channel where invite join/leave logs are sent. (Admin)"""
        if not channel:
            cfg = await invite_log_col.find_one({"guild_id": str(ctx.guild.id)})
            current = f"<#{cfg['channel_id']}>" if cfg and cfg.get("channel_id") else "Not set"
            embed   = discord.Embed(title="Invite Log Config", color=0x2B2D31)
            embed.add_field(name="Log Channel", value=current, inline=True)
            embed.add_field(
                name="Commands",
                value=(
                    "`,invitelog #channel` — set log channel\n"
                    "`,invitelog off` — disable logging"
                ),
                inline=False
            )
            return await ctx.reply(embed=embed)

        if isinstance(channel, str) and channel.lower() == "off":
            await invite_log_col.delete_one({"guild_id": str(ctx.guild.id)})
            return await ctx.reply("Invite logging disabled.")

        await invite_log_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"channel_id": str(channel.id)}},
            upsert=True
        )
        embed = discord.Embed(
            description=(
                f"Invite logs will be sent to {channel.mention}.\n\n"
                "**Logged events:**\n"
                "— Member joins (with inviter + invite code)\n"
                "— Member leaves"
            ),
            color=0x57F287
        )
        await ctx.reply(embed=embed)

    @commands.command(aliases=["invitelogoff"])
    @ctx_admin()
    async def invitelogdisable(self, ctx):
        """Disable invite logging for this server."""
        await invite_log_col.delete_one({"guild_id": str(ctx.guild.id)})
        await ctx.reply("Invite logging disabled.")

    @commands.command(aliases=["resetinvite"])
    @ctx_admin()
    async def invitereset(self, ctx, member: discord.Member = None):
        """Reset invite count for a member or clear all server invite data. (Admin)"""
        if member:
            await invites_col.update_many(
                {"guild_id": str(ctx.guild.id), "inviter_id": str(member.id)},
                {"$set": {"uses": 0}}
            )
            await ctx.reply(f"Invite count reset for **{member.display_name}**.")
        else:
            await invites_col.delete_many({"guild_id": str(ctx.guild.id)})
            await ctx.reply("All server invite data has been reset.")

    # ══════════════════════════════════════════════════════════════════════════
    #  MESSAGE COUNT COMMANDS
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["msgs", "msgcount", "mc2"])
    async def messages(self, ctx, member: discord.Member = None):
        """Check total messages sent by a user in this server."""
        member = member or ctx.author
        doc    = await msg_count_col.find_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(member.id)}
        )
        count = doc.get("count", 0) if doc else 0

        # Rank in server
        cursor = msg_count_col.find({"guild_id": str(ctx.guild.id)}).sort("count", -1)
        all_docs = await cursor.to_list(500)
        rank = next(
            (i+1 for i, d in enumerate(all_docs) if d["user_id"] == str(member.id)),
            None
        )

        embed = discord.Embed(color=0x5865F2)
        embed.set_author(
            name=f"{member.display_name}'s Messages",
            icon_url=member.display_avatar.url
        )
        embed.add_field(name="💬 Total Messages", value=f"**{count:,}**",                    inline=True)
        embed.add_field(name="🏆 Server Rank",    value=f"**#{rank}**" if rank else "Unranked", inline=True)
        embed.set_footer(text=f"Tracked since Happy joined · ID: {member.id}")
        await ctx.reply(embed=embed)

    @commands.command(aliases=["msglb", "topchatters", "chatleaderboard"])
    async def msgleaderboard(self, ctx):
        """See the top 10 most active chatters in this server."""
        cursor = msg_count_col.find({"guild_id": str(ctx.guild.id)}).sort("count", -1).limit(10)
        docs   = await cursor.to_list(10)

        if not docs:
            return await ctx.reply(embed=discord.Embed(
                description="No message data yet.", color=0x2B2D31
            ))

        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for i, doc in enumerate(docs, 1):
            member = ctx.guild.get_member(int(doc["user_id"]))
            name   = member.display_name if member else f"User {doc['user_id']}"
            pos    = medals[i-1] if i <= 3 else f"`{i}.`"
            lines.append(f"{pos} **{name}** — {doc['count']:,} messages")

        embed = discord.Embed(
            title=f"Message Leaderboard — {ctx.guild.name}",
            description="\n".join(lines),
            color=0x5865F2
        )
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        await ctx.reply(embed=embed)

    @commands.command(aliases=["servermsgstats"])
    async def msgstats(self, ctx):
        """View server-wide message statistics."""
        cursor   = msg_count_col.find({"guild_id": str(ctx.guild.id)})
        docs     = await cursor.to_list(None)

        if not docs:
            return await ctx.reply(embed=discord.Embed(
                description="No message data recorded yet.", color=0x2B2D31
            ))

        total   = sum(d["count"] for d in docs)
        members = len(docs)
        avg     = total // members if members else 0
        top_doc = max(docs, key=lambda d: d["count"])
        top_mem = ctx.guild.get_member(int(top_doc["user_id"]))
        top_name = top_mem.display_name if top_mem else f"User {top_doc['user_id']}"

        embed = discord.Embed(title=f"Message Stats — {ctx.guild.name}", color=0x5865F2)
        embed.add_field(name="💬 Total Messages",   value=f"**{total:,}**",           inline=True)
        embed.add_field(name="👥 Active Members",   value=f"**{members}**",            inline=True)
        embed.add_field(name="📊 Avg per Member",   value=f"**{avg:,}**",              inline=True)
        embed.add_field(name="🏆 Top Chatter",      value=f"**{top_name}** ({top_doc['count']:,} msgs)", inline=True)
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        embed.set_footer(text="Tracked since Happy joined this server")
        await ctx.reply(embed=embed)

    @commands.command(aliases=["resetmsg", "resetmessages"])
    @ctx_admin()
    async def msgreset(self, ctx, member: discord.Member = None):
        """Reset message count for a member or clear all server data. (Admin)"""
        if member:
            await msg_count_col.delete_one(
                {"guild_id": str(ctx.guild.id), "user_id": str(member.id)}
            )
            await ctx.reply(f"Message count reset for **{member.display_name}**.")
        else:
            await msg_count_col.delete_many({"guild_id": str(ctx.guild.id)})
            await ctx.reply("All message count data has been reset for this server.")

    # ── Slash versions ────────────────────────────────────────────────────────

    @app_commands.command(name="invites", description="Check how many people a user has invited")
    async def slash_invites(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        cursor = invites_col.find({"guild_id": str(interaction.guild.id), "inviter_id": str(member.id)})
        docs   = await cursor.to_list(100)
        total  = sum(doc.get("uses", 0) for doc in docs)
        embed  = discord.Embed(color=0x5865F2)
        embed.set_author(name=f"{member.display_name}'s Invites", icon_url=member.display_avatar.url)
        embed.add_field(name="Total Invites", value=f"**{total}**", inline=True)
        embed.add_field(name="Active Links",  value=f"**{len(docs)}**", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="messages", description="Check total messages sent by a user")
    async def slash_messages(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        doc    = await msg_count_col.find_one(
            {"guild_id": str(interaction.guild.id), "user_id": str(member.id)}
        )
        count = doc.get("count", 0) if doc else 0
        embed = discord.Embed(color=0x5865F2)
        embed.set_author(name=f"{member.display_name}'s Messages", icon_url=member.display_avatar.url)
        embed.add_field(name="Total Messages", value=f"**{count:,}**", inline=True)
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Tracker(bot))
