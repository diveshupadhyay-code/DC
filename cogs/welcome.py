"""
cogs/welcome.py — Welcome/bye messages, logging config, automod, counters.
"""

import discord
from discord.ext import commands
from utils.db import settings_col, logs_col, counters_col
from utils.helpers import ctx_admin, ctx_mod, log_event, update_server_data


class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Welcome config ────────────────────────────────────────────────────────
    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def welcome(self, ctx):
        """Configure welcome messages. Sub-commands: set, enable, disable, test"""
        gs  = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        cid = gs.get("welcome_channel")
        ch  = f"<#{cid}>" if cid else "Not set"
        en  = "Enabled" if gs.get("welcome_enabled") else "Disabled"
        embed = discord.Embed(title="Welcome Settings", color=0x2B2D31)
        embed.add_field(name="Status",  value=en, inline=True)
        embed.add_field(name="Channel", value=ch, inline=True)
        embed.add_field(
            name="Sub-commands",
            value=(
                "`,welcome set #channel` — set channel\n"
                "`,welcome enable` — turn on\n"
                "`,welcome disable` — turn off\n"
                "`,welcome test` — preview message"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @welcome.command(name="set")
    @commands.has_permissions(administrator=True)
    async def welcome_set(self, ctx, channel: discord.TextChannel = None):
        """Set the welcome channel."""
        if not channel:
            return await ctx.reply("Mention a channel: `,welcome set #channel`")
        await update_server_data(ctx.guild.id, "welcome_channel", channel.id)
        await ctx.reply(f"Welcome channel set to {channel.mention}.")

    @welcome.command(name="enable")
    @commands.has_permissions(administrator=True)
    async def welcome_enable(self, ctx):
        """Enable welcome messages."""
        await update_server_data(ctx.guild.id, "welcome_enabled", True)
        await ctx.reply("Welcome messages enabled.")

    @welcome.command(name="disable")
    @commands.has_permissions(administrator=True)
    async def welcome_disable(self, ctx):
        """Disable welcome messages."""
        await update_server_data(ctx.guild.id, "welcome_enabled", False)
        await ctx.reply("Welcome messages disabled.")

    @welcome.command(name="test")
    @commands.has_permissions(administrator=True)
    async def welcome_test(self, ctx):
        """Preview what the welcome message looks like."""
        embed = self._build_welcome_embed(ctx.author, ctx.guild)
        await ctx.reply("Preview:", embed=embed)

    def _build_welcome_embed(self, member: discord.Member, guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(color=0x2B2D31)
        embed.set_author(
            name=f"Welcome to {guild.name}!",
            icon_url=guild.icon.url if guild.icon else None
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.description = (
            f"Hey {member.mention}, glad you're here!\n"
            f"You are member **#{guild.member_count}**."
        )
        embed.set_footer(
            text=f"Account created {member.created_at.strftime('%d %b %Y')}"
        )
        return embed

    # ── Bye config ────────────────────────────────────────────────────────────
    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def bye(self, ctx):
        """Configure bye messages. Sub-commands: set, enable, disable, test"""
        gs  = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        cid = gs.get("bye_channel")
        ch  = f"<#{cid}>" if cid else "Not set"
        en  = "Enabled" if gs.get("bye_enabled") else "Disabled"
        embed = discord.Embed(title="Bye Settings", color=0x2B2D31)
        embed.add_field(name="Status",  value=en, inline=True)
        embed.add_field(name="Channel", value=ch, inline=True)
        embed.add_field(
            name="Sub-commands",
            value=(
                "`,bye set #channel` — set channel\n"
                "`,bye enable` — turn on\n"
                "`,bye disable` — turn off\n"
                "`,bye test` — preview message"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @bye.command(name="set")
    @commands.has_permissions(administrator=True)
    async def bye_set(self, ctx, channel: discord.TextChannel = None):
        """Set the bye channel."""
        if not channel:
            return await ctx.reply("Mention a channel: `,bye set #channel`")
        await update_server_data(ctx.guild.id, "bye_channel",   channel.id)
        await update_server_data(ctx.guild.id, "bye_enabled",   True)
        await ctx.reply(f"Bye channel set to {channel.mention} and enabled.")

    @bye.command(name="enable")
    @commands.has_permissions(administrator=True)
    async def bye_enable(self, ctx):
        """Enable bye messages."""
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        if not gs.get("bye_channel"):
            return await ctx.reply("Set a bye channel first: `,bye set #channel`")
        await update_server_data(ctx.guild.id, "bye_enabled", True)
        await ctx.reply("Bye messages enabled.")

    @bye.command(name="disable")
    @commands.has_permissions(administrator=True)
    async def bye_disable(self, ctx):
        """Disable bye messages."""
        await update_server_data(ctx.guild.id, "bye_enabled", False)
        await ctx.reply("Bye messages disabled.")

    @bye.command(name="test")
    @commands.has_permissions(administrator=True)
    async def bye_test(self, ctx):
        """Preview the bye message."""
        embed = self._build_bye_embed(ctx.author, ctx.guild)
        await ctx.reply("Preview:", embed=embed)

    def _build_bye_embed(self, member: discord.Member, guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(color=0x2B2D31)
        embed.set_author(
            name=f"{member.display_name} left the server",
            icon_url=member.display_avatar.url
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.description = (
            f"**{member.mention}** has left.\n"
            f"Members remaining: **{guild.member_count}**"
        )
        embed.set_footer(
            text=f"Joined {member.joined_at.strftime('%d %b %Y') if member.joined_at else 'Unknown'}"
        )
        return embed

    # ── Logging ───────────────────────────────────────────────────────────────
    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def logs(self, ctx):
        """Configure server logging. Sub-commands: set, disable, status"""
        cfg = await logs_col.find_one({"guild_id": str(ctx.guild.id)})
        ch  = f"<#{cfg['channel_id']}>" if cfg and cfg.get("channel_id") else "Not set"
        embed = discord.Embed(title="Logging Config", color=0x2B2D31)
        embed.add_field(name="Log Channel", value=ch, inline=True)
        embed.add_field(
            name="Logged Events",
            value=(
                "Member join/leave, bans, kicks,\n"
                "message edit/delete, channel lock,\n"
                "warnings, jail events"
            ),
            inline=False
        )
        embed.add_field(
            name="Commands",
            value="`,logs set #channel` | `,logs disable`",
            inline=False
        )
        await ctx.reply(embed=embed)

    @logs.command(name="set")
    @commands.has_permissions(administrator=True)
    async def logs_set(self, ctx, channel: discord.TextChannel = None):
        """Set the log channel."""
        if not channel:
            return await ctx.reply("Mention a channel: `,logs set #channel`")
        await logs_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"channel_id": str(channel.id)}},
            upsert=True
        )
        await ctx.reply(f"Log channel set to {channel.mention}.")

    @logs.command(name="disable")
    @commands.has_permissions(administrator=True)
    async def logs_disable(self, ctx):
        """Disable logging."""
        await logs_col.delete_one({"guild_id": str(ctx.guild.id)})
        await ctx.reply("Logging disabled.")

    # ── AutoMod ───────────────────────────────────────────────────────────────
    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def automod(self, ctx):
        """AutoMod configuration. Sub-command: invite"""
        gs  = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        inv = "Enabled" if gs.get("invite_block") else "Disabled"
        embed = discord.Embed(title="AutoMod Settings", color=0x2B2D31)
        embed.add_field(name="Anti-Invite", value=inv, inline=True)
        embed.add_field(name="Commands", value="`,automod invite on/off`", inline=False)
        await ctx.reply(embed=embed)

    @automod.command(name="invite")
    @commands.has_permissions(administrator=True)
    async def automod_invite(self, ctx, status: str = None):
        """Block Discord invite links in this server."""
        if not status or status.lower() not in ("on", "off"):
            return await ctx.reply("Usage: `,automod invite on` or `,automod invite off`")
        state = status.lower() == "on"
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"invite_block": state}},
            upsert=True
        )
        await ctx.reply(f"Anti-invite blocker: **{status.upper()}**.")

    # ── Counters ──────────────────────────────────────────────────────────────
    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def counter(self, ctx):
        """Live counters in voice channel names. Sub-command: create"""
        embed = discord.Embed(
            title="Counters",
            description=(
                "Create live-updating counters in voice channel names.\n\n"
                "**Usage:**\n"
                "`,counter create members #vc-channel`\n"
                "`,counter create bots #vc-channel`\n"
                "`,counter create channels #vc-channel`"
            ),
            color=0x2B2D31
        )
        await ctx.reply(embed=embed)

    @counter.command(name="create")
    @commands.has_permissions(administrator=True)
    async def counter_create(self, ctx, ctype: str = None, channel: discord.VoiceChannel = None):
        """Create a counter: members, bots, or channels."""
        if not ctype or ctype.lower() not in ("members","bots","channels") or not channel:
            return await ctx.reply("Usage: `,counter create members/bots/channels #vc`")
        await counters_col.update_one(
            {"guild_id": str(ctx.guild.id), "type": ctype.lower()},
            {"$set": {"channel_id": str(channel.id)}},
            upsert=True
        )
        await self._update_counters(ctx.guild)
        await ctx.reply(f"Counter `{ctype}` linked to {channel.mention}.")

    async def _update_counters(self, guild: discord.Guild):
        async for doc in counters_col.find({"guild_id": str(guild.id)}):
            ch = guild.get_channel(int(doc["channel_id"]))
            if not ch:
                continue
            ctype = doc["type"]
            try:
                if ctype == "members":
                    await ch.edit(name=f"Members: {guild.member_count}")
                elif ctype == "bots":
                    bots = sum(1 for m in guild.members if m.bot)
                    await ch.edit(name=f"Bots: {bots}")
                elif ctype == "channels":
                    await ch.edit(name=f"Channels: {len(guild.channels)}")
            except:
                pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._update_counters(member.guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._update_counters(member.guild)


async def setup(bot):
    await bot.add_cog(Welcome(bot))
