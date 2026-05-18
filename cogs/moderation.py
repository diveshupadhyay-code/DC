"""
cogs/moderation.py — Full moderation suite:
  kick, ban, unban, tempban, mute, unmute, warn, warnings, clearwarns,
  softban, nickname, lock, unlock, lockdown, unlockdown, vclock, vcunlock,
  purge (6 modes), massrole, jail, unjail, jailsetup, setupmute,
  antispam, note, notes, clearnotes, case history.
"""

import discord
from discord.ext import commands
from discord import app_commands
import asyncio, re
from datetime import timedelta, datetime, timezone

from utils.db import (
    warns_col, settings_col, jail_col,
    notes_col, cases_col, antispam_col
)
from utils.helpers import (
    BOT_OWNER_ID, ctx_mod, ctx_admin, ctx_owner,
    log_event, is_mod_or_owner, is_admin_or_owner
)

# ── Shared antispam state (in-memory) ────────────────────────────────────────
# {(guild_id, user_id): [timestamps]}
_msg_timestamps: dict = {}


# ═════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═════════════════════════════════════════════════════════════════════════════

async def _toggle_lock(channel, lock: bool, reason: str):
    """Lock or unlock any text / voice / thread / forum channel."""
    ow = channel.overwrites_for(channel.guild.default_role)
    if isinstance(channel, discord.VoiceChannel):
        ow.connect = False if lock else None
        ow.speak   = False if lock else None
    else:
        ow.send_messages            = False if lock else None
        ow.send_messages_in_threads = False if lock else None
        ow.create_public_threads    = False if lock else None
        ow.add_reactions            = False if lock else None
    await channel.set_permissions(channel.guild.default_role, overwrite=ow, reason=reason)


async def _next_case(guild_id: str) -> int:
    """Return the next case number for a guild."""
    last = await cases_col.find_one(
        {"guild_id": guild_id}, sort=[("case_num", -1)]
    )
    return (last["case_num"] + 1) if last else 1


async def _log_case(guild_id: str, case_type: str, user: discord.User,
                    mod: discord.User, reason: str) -> int:
    """Create a moderation case and return the case number."""
    num = await _next_case(guild_id)
    await cases_col.insert_one({
        "guild_id": guild_id,
        "case_num": num,
        "type":     case_type,
        "user_id":  str(user.id),
        "user_tag": str(user),
        "mod_id":   str(mod.id),
        "mod_tag":  str(mod),
        "reason":   reason,
        "ts":       datetime.now(timezone.utc),
    })
    return num


def _hierarchy_ok(ctx_author: discord.Member, target: discord.Member,
                  bot_owner_id: int) -> bool:
    """Return True if ctx_author can moderate target."""
    if ctx_author.id == bot_owner_id:
        return True
    return ctx_author.top_role > target.top_role


def _build_action_embed(title: str, member: discord.Member,
                        mod: discord.Member, reason: str,
                        case: int, color: int = 0x2B2D31,
                        extra: dict = None) -> discord.Embed:
    embed = discord.Embed(title=title, color=color,
                          timestamp=datetime.now(timezone.utc))
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Member",  value=f"{member.mention} (`{member.id}`)", inline=True)
    embed.add_field(name="Mod",     value=mod.mention,                          inline=True)
    embed.add_field(name="Case",    value=f"#{case}",                           inline=True)
    embed.add_field(name="Reason",  value=reason,                               inline=False)
    if extra:
        for k, v in extra.items():
            embed.add_field(name=k, value=v, inline=True)
    return embed


# ═════════════════════════════════════════════════════════════════════════════
#  COG
# ═════════════════════════════════════════════════════════════════════════════

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Anti-spam listener ────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if message.author.id == BOT_OWNER_ID:
            return

        cfg = await antispam_col.find_one({"guild_id": str(message.guild.id)})
        if not cfg or not cfg.get("enabled"):
            return

        threshold = cfg.get("threshold", 5)   # msgs per 5 seconds
        action    = cfg.get("action", "mute")  # mute | kick | ban
        key       = (message.guild.id, message.author.id)
        now       = datetime.now(timezone.utc).timestamp()

        stamps = _msg_timestamps.get(key, [])
        stamps = [t for t in stamps if now - t < 5]   # keep last 5s
        stamps.append(now)
        _msg_timestamps[key] = stamps

        if len(stamps) >= threshold:
            _msg_timestamps[key] = []    # reset after triggering
            member = message.author
            try:
                if action == "mute":
                    await member.timeout(timedelta(minutes=10),
                                         reason="Anti-spam: message flood")
                    await message.channel.send(
                        embed=discord.Embed(
                            description=f"{member.mention} muted (10m) for spamming.",
                            color=0xff4444
                        ), delete_after=8
                    )
                elif action == "kick":
                    await member.kick(reason="Anti-spam: message flood")
                elif action == "ban":
                    await member.ban(reason="Anti-spam: message flood")
                await log_event(self.bot, message.guild, "antispam",
                                f"{member} triggered anti-spam ({action}).")
            except discord.Forbidden:
                pass

    # ══════════════════════════════════════════════════════════════════════════
    #  KICK
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @ctx_mod()
    async def kick(self, ctx, member: discord.Member = None, *, reason="No reason provided"):
        """Kick a member from the server."""
        if not member:
            return await ctx.reply("Usage: `,kick @member [reason]`")
        if not _hierarchy_ok(ctx.author, member, BOT_OWNER_ID):
            return await ctx.reply("Cannot kick someone with an equal or higher role.")
        if ctx.guild.me.top_role <= member.top_role:
            return await ctx.reply("My role is too low to kick that member.")

        case = await _log_case(str(ctx.guild.id), "kick", member, ctx.author, reason)
        try:
            await member.send(embed=discord.Embed(
                description=f"You were kicked from **{ctx.guild.name}**.\nReason: {reason}",
                color=0xff4444
            ))
        except:
            pass
        await member.kick(reason=reason)
        await ctx.reply(embed=_build_action_embed("Member Kicked", member, ctx.author, reason, case))
        await log_event(self.bot, ctx.guild, "kick",
                        f"Case #{case} — {member} kicked by {ctx.author}. {reason}")

    @app_commands.command(name="kick", description="Kick a member")
    @is_mod_or_owner()
    async def slash_kick(self, interaction: discord.Interaction,
                          member: discord.Member, reason: str = "No reason provided"):
        if not _hierarchy_ok(interaction.user, member, BOT_OWNER_ID):
            return await interaction.response.send_message(
                "Cannot kick someone with equal or higher role.", ephemeral=True
            )
        case = await _log_case(str(interaction.guild.id), "kick", member, interaction.user, reason)
        await member.kick(reason=reason)
        await interaction.response.send_message(
            embed=discord.Embed(description=f"**{member}** kicked. Case #{case}.", color=0x2B2D31),
            ephemeral=True
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  BAN / TEMPBAN / UNBAN
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @ctx_mod()
    async def ban(self, ctx, member: discord.Member = None, *, reason="No reason provided"):
        """Permanently ban a member."""
        if not member:
            return await ctx.reply("Usage: `,ban @member [reason]`")
        if not _hierarchy_ok(ctx.author, member, BOT_OWNER_ID):
            return await ctx.reply("Cannot ban someone with an equal or higher role.")

        case = await _log_case(str(ctx.guild.id), "ban", member, ctx.author, reason)
        try:
            await member.send(embed=discord.Embed(
                description=f"You were banned from **{ctx.guild.name}**.\nReason: {reason}",
                color=0xff4444
            ))
        except:
            pass
        await member.ban(reason=reason, delete_message_days=1)
        await ctx.reply(embed=_build_action_embed(
            "Member Banned", member, ctx.author, reason, case, color=0xff4444
        ))
        await log_event(self.bot, ctx.guild, "ban",
                        f"Case #{case} — {member} banned by {ctx.author}. {reason}")

    @commands.command()
    @ctx_mod()
    async def tempban(self, ctx, member: discord.Member = None,
                      duration: str = None, *, reason="No reason provided"):
        """
        Temporarily ban a member.
        Duration: 10m  2h  7d  etc.
        Usage: `,tempban @member 7d Repeated violations`
        """
        if not member or not duration:
            return await ctx.reply("Usage: `,tempban @member <duration> [reason]`\nExample: `,tempban @user 7d Spamming`")
        if not _hierarchy_ok(ctx.author, member, BOT_OWNER_ID):
            return await ctx.reply("Cannot tempban someone with an equal or higher role.")

        minutes = self._parse_duration(duration)
        if minutes is None:
            return await ctx.reply("Invalid duration. Use formats like `10m`, `2h`, `7d`.")

        case = await _log_case(str(ctx.guild.id), "tempban", member, ctx.author, reason)
        try:
            await member.send(embed=discord.Embed(
                description=(
                    f"You were temporarily banned from **{ctx.guild.name}**.\n"
                    f"Duration: **{duration}**\nReason: {reason}"
                ),
                color=0xff4444
            ))
        except:
            pass
        await member.ban(reason=f"[Tempban {duration}] {reason}", delete_message_days=1)
        await ctx.reply(embed=_build_action_embed(
            "Member Temp-Banned", member, ctx.author, reason, case,
            color=0xff4444, extra={"Duration": duration}
        ))
        await log_event(self.bot, ctx.guild, "tempban",
                        f"Case #{case} — {member} tempbanned {duration} by {ctx.author}.")

        # Schedule unban
        await asyncio.sleep(minutes * 60)
        try:
            await ctx.guild.unban(member, reason=f"Tempban expired ({duration})")
            await log_event(self.bot, ctx.guild, "tempban_expired",
                            f"{member} tempban expired after {duration}.")
        except:
            pass

    @commands.command()
    @ctx_mod()
    async def unban(self, ctx, user_id: int = None, *, reason="No reason provided"):
        """Unban a user by their ID."""
        if not user_id:
            return await ctx.reply("Usage: `,unban <user_id> [reason]`")
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=reason)
            case = await _log_case(str(ctx.guild.id), "unban", user, ctx.author, reason)
            await ctx.reply(embed=discord.Embed(
                description=f"**{user}** unbanned. Case #{case}.", color=0x2B2D31
            ))
            await log_event(self.bot, ctx.guild, "unban",
                            f"Case #{case} — {user} unbanned by {ctx.author}.")
        except discord.NotFound:
            await ctx.reply("User not found or not currently banned.")

    @app_commands.command(name="ban", description="Ban a member")
    @is_mod_or_owner()
    async def slash_ban(self, interaction: discord.Interaction,
                         member: discord.Member, reason: str = "No reason provided"):
        if not _hierarchy_ok(interaction.user, member, BOT_OWNER_ID):
            return await interaction.response.send_message(
                "Cannot ban someone with equal or higher role.", ephemeral=True
            )
        case = await _log_case(str(interaction.guild.id), "ban", member, interaction.user, reason)
        await member.ban(reason=reason, delete_message_days=1)
        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"**{member}** banned. Case #{case}.", color=0xff4444
            ), ephemeral=True
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  MUTE / UNMUTE
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @ctx_mod()
    async def mute(self, ctx, member: discord.Member = None,
                   duration: str = "10m", *, reason="No reason provided"):
        """
        Timeout a member.
        Usage: `,mute @member [duration] [reason]`
        Duration: 10m 2h 7d (default 10m, max 28d)
        """
        if not member:
            return await ctx.reply("Usage: `,mute @member [duration] [reason]`\nExample: `,mute @user 30m Spamming`")
        if not _hierarchy_ok(ctx.author, member, BOT_OWNER_ID):
            return await ctx.reply("Cannot mute someone with an equal or higher role.")

        minutes = self._parse_duration(duration) or 10
        if minutes > 40320:   # 28 days Discord max
            return await ctx.reply("Maximum timeout duration is 28 days.")

        case = await _log_case(str(ctx.guild.id), "mute", member, ctx.author, reason)
        await member.timeout(timedelta(minutes=minutes), reason=reason)
        await ctx.reply(embed=_build_action_embed(
            "Member Muted", member, ctx.author, reason, case,
            extra={"Duration": duration}
        ))
        try:
            await member.send(embed=discord.Embed(
                description=f"You were muted in **{ctx.guild.name}** for **{duration}**.\nReason: {reason}",
                color=0xff4444
            ))
        except:
            pass
        await log_event(self.bot, ctx.guild, "mute",
                        f"Case #{case} — {member} muted {duration} by {ctx.author}.")

    @commands.command()
    @ctx_mod()
    async def unmute(self, ctx, member: discord.Member = None, *, reason="Unmuted by moderator"):
        """Remove a timeout from a member."""
        if not member:
            return await ctx.reply("Usage: `,unmute @member`")
        await member.timeout(None, reason=reason)
        case = await _log_case(str(ctx.guild.id), "unmute", member, ctx.author, reason)
        await ctx.reply(embed=discord.Embed(
            description=f"Timeout removed for **{member}**. Case #{case}.", color=0x2B2D31
        ))

    @app_commands.command(name="mute", description="Timeout a member")
    @is_mod_or_owner()
    @app_commands.describe(member="Member to mute", minutes="Duration in minutes", reason="Reason")
    async def slash_mute(self, interaction: discord.Interaction,
                          member: discord.Member, minutes: int = 10, reason: str = "No reason"):
        if not _hierarchy_ok(interaction.user, member, BOT_OWNER_ID):
            return await interaction.response.send_message(
                "Cannot mute someone with equal or higher role.", ephemeral=True
            )
        await member.timeout(timedelta(minutes=minutes), reason=reason)
        case = await _log_case(str(interaction.guild.id), "mute", member, interaction.user, reason)
        await interaction.response.send_message(
            f"**{member}** muted for {minutes} min. Case #{case}.", ephemeral=True
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  WARN / WARNINGS / CLEARWARNS
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @ctx_mod()
    async def warn(self, ctx, member: discord.Member = None, *, reason="No reason provided"):
        """Warn a member. Warnings are tracked in the database."""
        if not member:
            return await ctx.reply("Usage: `,warn @member [reason]`")

        sid, uid = str(ctx.guild.id), str(member.id)
        doc      = await warns_col.find_one({"server_id": sid, "user_id": uid})
        count    = (doc["count"] + 1) if doc else 1
        await warns_col.update_one(
            {"server_id": sid, "user_id": uid},
            {"$set": {"count": count}}, upsert=True
        )
        case = await _log_case(sid, "warn", member, ctx.author, reason)

        embed = discord.Embed(title="Warning Issued", color=0xff4444,
                               timestamp=datetime.now(timezone.utc))
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Member",   value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Mod",      value=ctx.author.mention,                  inline=True)
        embed.add_field(name="Case",     value=f"#{case}",                          inline=True)
        embed.add_field(name="Warnings", value=f"**{count}** total",                inline=True)
        embed.add_field(name="Reason",   value=reason,                              inline=False)
        await ctx.reply(embed=embed)

        # DM the member
        try:
            await member.send(embed=discord.Embed(
                description=(
                    f"You received warning **#{count}** in **{ctx.guild.name}**.\n"
                    f"Reason: {reason}\n\n"
                    f"Please review the server rules."
                ),
                color=0xff4444
            ))
        except:
            pass

        # Auto-action thresholds (configurable via antispam config for now)
        gs = await settings_col.find_one({"_id": sid}) or {}
        warn_mute_at = gs.get("warn_mute_at", 0)     # 0 = disabled
        warn_ban_at  = gs.get("warn_ban_at",  0)
        if warn_ban_at and count >= warn_ban_at:
            try:
                await member.ban(reason=f"Auto-ban: reached {count} warnings")
                await ctx.send(embed=discord.Embed(
                    description=f"{member.mention} auto-banned after reaching **{count}** warnings.",
                    color=0xff0000
                ))
            except:
                pass
        elif warn_mute_at and count >= warn_mute_at:
            try:
                await member.timeout(timedelta(hours=1),
                                     reason=f"Auto-mute: reached {count} warnings")
                await ctx.send(embed=discord.Embed(
                    description=f"{member.mention} auto-muted (1h) after reaching **{count}** warnings.",
                    color=0xff4444
                ))
            except:
                pass

        await log_event(self.bot, ctx.guild, "warn",
                        f"Case #{case} — {member} warned by {ctx.author}. #{count} {reason}")

    @app_commands.command(name="warn", description="Warn a member")
    @is_mod_or_owner()
    async def slash_warn(self, interaction: discord.Interaction,
                          member: discord.Member, reason: str = "No reason provided"):
        await interaction.response.defer()
        sid, uid = str(interaction.guild.id), str(member.id)
        doc      = await warns_col.find_one({"server_id": sid, "user_id": uid})
        count    = (doc["count"] + 1) if doc else 1
        await warns_col.update_one(
            {"server_id": sid, "user_id": uid},
            {"$set": {"count": count}}, upsert=True
        )
        case  = await _log_case(sid, "warn", member, interaction.user, reason)
        embed = discord.Embed(title="Warning Issued", color=0xff4444)
        embed.add_field(name="Member",   value=member.mention, inline=True)
        embed.add_field(name="Warnings", value=count,          inline=True)
        embed.add_field(name="Case",     value=f"#{case}",     inline=True)
        embed.add_field(name="Reason",   value=reason,         inline=False)
        await interaction.followup.send(embed=embed)

    @commands.command(aliases=["warnlist", "wl"])
    @ctx_mod()
    async def warnings(self, ctx, member: discord.Member = None):
        """View warning count for a member."""
        member = member or ctx.author
        doc    = await warns_col.find_one(
            {"server_id": str(ctx.guild.id), "user_id": str(member.id)}
        )
        count  = doc["count"] if doc else 0
        embed  = discord.Embed(color=0x2B2D31)
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.add_field(name="Total Warnings", value=f"**{count}**", inline=True)
        embed.set_footer(text=f"ID: {member.id}")
        await ctx.reply(embed=embed)

    @commands.command(aliases=["cw"])
    @ctx_mod()
    async def clearwarns(self, ctx, member: discord.Member = None):
        """Clear all warnings for a member."""
        if not member:
            return await ctx.reply("Usage: `,clearwarns @member`")
        result = await warns_col.delete_one(
            {"server_id": str(ctx.guild.id), "user_id": str(member.id)}
        )
        if result.deleted_count:
            await ctx.reply(f"All warnings cleared for **{member}**.")
        else:
            await ctx.reply(f"**{member}** has no warnings to clear.")

    # ══════════════════════════════════════════════════════════════════════════
    #  SOFTBAN
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["sb"])
    @ctx_mod()
    async def softban(self, ctx, member: discord.Member = None, *, reason="No reason provided"):
        """Ban + immediately unban a member (removes recent messages)."""
        if not member:
            return await ctx.reply("Usage: `,softban @member [reason]`")
        if not _hierarchy_ok(ctx.author, member, BOT_OWNER_ID):
            return await ctx.reply("Cannot softban someone with equal or higher role.")

        case = await _log_case(str(ctx.guild.id), "softban", member, ctx.author, reason)
        try:
            await member.send(embed=discord.Embed(
                description=f"You were softbanned from **{ctx.guild.name}**.\nReason: {reason}",
                color=0xff4444
            ))
        except:
            pass
        await ctx.guild.ban(member, reason=f"Softban: {reason}", delete_message_days=7)
        await ctx.guild.unban(member, reason="Softban complete")
        await ctx.reply(embed=_build_action_embed(
            "Member Softbanned", member, ctx.author, reason, case
        ))
        await log_event(self.bot, ctx.guild, "softban",
                        f"Case #{case} — {member} softbanned by {ctx.author}.")

    # ══════════════════════════════════════════════════════════════════════════
    #  NICKNAME
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["nick"])
    @ctx_mod()
    async def nickname(self, ctx, member: discord.Member = None, *, new_name: str = None):
        """Change or reset a member's nickname."""
        if not member:
            return await ctx.reply("Usage: `,nickname @member [new name]`")
        if not _hierarchy_ok(ctx.author, member, BOT_OWNER_ID):
            return await ctx.reply("Cannot change nickname of someone with equal or higher role.")
        if member.id == ctx.guild.owner_id:
            return await ctx.reply("Cannot change the server owner's nickname.")
        if new_name and len(new_name) > 32:
            return await ctx.reply("Nickname must be 32 characters or fewer.")
        await member.edit(nick=new_name or None)
        await ctx.reply(
            f"Nickname of {member.mention} " +
            (f"set to `{new_name}`." if new_name else "reset to default.")
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  LOCK / UNLOCK
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @ctx_mod()
    async def lock(self, ctx, channel: discord.abc.GuildChannel = None, *, reason="No reason provided"):
        """Lock a channel (text, voice, thread, or forum)."""
        channel = channel or ctx.channel
        await _toggle_lock(channel, True, f"Locked by {ctx.author}: {reason}")
        await ctx.reply(embed=discord.Embed(
            description=f"**{channel.mention}** locked.\nReason: {reason}", color=0x2B2D31
        ))
        await log_event(self.bot, ctx.guild, "channel_lock",
                        f"{channel} locked by {ctx.author}. {reason}")

    @app_commands.command(name="lock", description="Lock a channel")
    @is_mod_or_owner()
    async def slash_lock(self, interaction: discord.Interaction,
                          channel: discord.TextChannel = None, reason: str = "No reason"):
        ch = channel or interaction.channel
        await _toggle_lock(ch, True, reason)
        await interaction.response.send_message(f"{ch.mention} locked.", ephemeral=True)

    @commands.command()
    @ctx_mod()
    async def unlock(self, ctx, channel: discord.abc.GuildChannel = None, *, reason="No reason provided"):
        """Unlock a channel."""
        channel = channel or ctx.channel
        await _toggle_lock(channel, False, f"Unlocked by {ctx.author}: {reason}")
        await ctx.reply(embed=discord.Embed(
            description=f"**{channel.mention}** unlocked.", color=0x2B2D31
        ))

    @app_commands.command(name="unlock", description="Unlock a channel")
    @is_mod_or_owner()
    async def slash_unlock(self, interaction: discord.Interaction,
                            channel: discord.TextChannel = None):
        ch = channel or interaction.channel
        await _toggle_lock(ch, False, "Unlocked")
        await interaction.response.send_message(f"{ch.mention} unlocked.", ephemeral=True)

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def lockdown(self, ctx, *, reason="Emergency lockdown"):
        """Lock ALL server channels instantly."""
        msg   = await ctx.reply("Initiating server lockdown...")
        count = 0
        for ch in ctx.guild.channels:
            if isinstance(ch, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel)):
                try:
                    await _toggle_lock(ch, True, reason)
                    count += 1
                except:
                    pass
        await msg.edit(content=None, embed=discord.Embed(
            title="Server Lockdown Active",
            description=f"**{count}** channels locked.\nReason: {reason}\n\nUse `,unlockdown` to lift.",
            color=0xff0000
        ))
        await log_event(self.bot, ctx.guild, "lockdown",
                        f"Server locked by {ctx.author}. {reason}")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def unlockdown(self, ctx):
        """Lift the server lockdown."""
        msg   = await ctx.reply("Lifting lockdown...")
        count = 0
        for ch in ctx.guild.channels:
            if isinstance(ch, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel)):
                try:
                    await _toggle_lock(ch, False, "Lockdown lifted")
                    count += 1
                except:
                    pass
        await msg.edit(content=None, embed=discord.Embed(
            description=f"Lockdown lifted. **{count}** channels unlocked.", color=0x2B2D31
        ))

    @commands.command(aliases=["vlock"])
    @ctx_mod()
    async def vclock(self, ctx, channel: discord.VoiceChannel = None):
        """Lock a voice channel (prevents new joins)."""
        if not channel:
            channel = ctx.author.voice.channel if ctx.author.voice else None
        if not channel:
            return await ctx.reply("Join a voice channel or mention one.")
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.connect = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.reply(f"**{channel.name}** locked.")

    @commands.command(aliases=["vunlock"])
    @ctx_mod()
    async def vcunlock(self, ctx, channel: discord.VoiceChannel = None):
        """Unlock a voice channel."""
        if not channel:
            channel = ctx.author.voice.channel if ctx.author.voice else None
        if not channel:
            return await ctx.reply("Join a voice channel or mention one.")
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.connect = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.reply(f"**{channel.name}** unlocked.")

    # ══════════════════════════════════════════════════════════════════════════
    #  PURGE  (6 modes)
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["clear", "c"])
    @ctx_mod()
    async def purge(self, ctx, target: str = None, limit: int = None):
        """
        Delete messages in bulk.
        Modes: amount  |  bots  |  @user  |  links  |  images  |  embeds
        Usage: `,purge 50`  `,purge bots 20`  `,purge @user 30`
        """
        if not target:
            embed = discord.Embed(title="Purge", color=0x2B2D31)
            embed.add_field(
                name="Modes",
                value=(
                    "`,purge <amount>` — recent messages\n"
                    "`,purge bots <amount>` — bot messages only\n"
                    "`,purge @user <amount>` — specific user's messages\n"
                    "`,purge links <amount>` — messages containing links\n"
                    "`,purge images <amount>` — messages with attachments\n"
                    "`,purge embeds <amount>` — messages with embeds"
                ),
                inline=False
            )
            return await ctx.reply(embed=embed)

        try:
            await ctx.message.delete()
        except:
            pass

        tl = target.lower()
        lim = limit or 50

        checks = {
            "bots":   lambda m: m.author.bot,
            "links":  lambda m: "http" in m.content or "https" in m.content,
            "images": lambda m: bool(m.attachments),
            "embeds": lambda m: bool(m.embeds),
        }

        if tl in checks:
            msgs = await ctx.channel.purge(limit=lim, check=checks[tl])
        elif target.startswith("<@"):
            uid  = int(re.sub(r"[<@!>]", "", target))
            msgs = await ctx.channel.purge(limit=lim, check=lambda m, u=uid: m.author.id == u)
        else:
            try:
                msgs = await ctx.channel.purge(limit=int(target))
            except ValueError:
                return await ctx.send("Invalid format. Use `,purge` for help.", delete_after=5)

        await ctx.send(embed=discord.Embed(
            description=f"Purged **{len(msgs)}** message(s).", color=0x2B2D31
        ), delete_after=4)

    @app_commands.command(name="clear", description="Delete messages in bulk")
    @is_mod_or_owner()
    async def slash_clear(self, interaction: discord.Interaction, amount: int):
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(
            f"Deleted **{len(deleted)}** messages.", ephemeral=True
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  MASSROLE
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def massrole(self, ctx, action: str = None, target: str = None, role: discord.Role = None):
        """
        Add/remove a role for all members or all bots.
        Usage:
          `,massrole add everyone @Role`
          `,massrole add bots @Role`
          `,massrole remove everyone @Role`
        """
        if not action or action.lower() not in ("add", "remove") or not role:
            return await ctx.reply(
                "Usage:\n"
                "`,massrole add everyone @Role` — all humans\n"
                "`,massrole add bots @Role` — all bots\n"
                "`,massrole remove everyone @Role` — remove from all humans"
            )
        if ctx.guild.me.top_role <= role:
            return await ctx.reply("My role is below that role. Move my role higher first.")

        tl = (target or "").lower().strip().lstrip("<@&").rstrip(">")
        if tl in ("bots", "bot"):
            members, label = [m for m in ctx.guild.members if m.bot],     "bots"
        else:
            members, label = [m for m in ctx.guild.members if not m.bot], "members"

        if not members:
            return await ctx.reply(f"No {label} found.")

        msg   = await ctx.reply(
            embed=discord.Embed(
                description=f"Processing **{len(members)}** {label}...",
                color=0x2B2D31
            )
        )
        count  = 0
        failed = 0

        for i, member in enumerate(members):
            try:
                if action.lower() == "add" and role not in member.roles:
                    await member.add_roles(role, reason=f"Massrole by {ctx.author}")
                    count += 1
                elif action.lower() == "remove" and role in member.roles:
                    await member.remove_roles(role, reason=f"Massrole by {ctx.author}")
                    count += 1
            except discord.Forbidden:
                failed += 1
            except Exception:
                failed += 1
            # Update progress every 25 members
            if (i + 1) % 25 == 0:
                await msg.edit(embed=discord.Embed(
                    description=f"Processing... {i+1}/{len(members)} done.", color=0x2B2D31
                ))
            await asyncio.sleep(0.35)

        result_embed = discord.Embed(
            description=(
                f"**{action.capitalize()}d** {role.mention} for **{count}** {label}."
                + (f"\n{failed} member(s) failed (missing permissions or role too high)." if failed else "")
            ),
            color=0x2B2D31
        )
        await msg.edit(embed=result_embed)
        await log_event(self.bot, ctx.guild, "massrole",
                        f"{action} {role} for {count} {label} by {ctx.author}.")

    # ══════════════════════════════════════════════════════════════════════════
    #  JAIL
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def jailsetup(self, ctx):
        """Create the Jailed role and a private #jail channel."""
        jailed = discord.utils.get(ctx.guild.roles, name="Jailed")
        if not jailed:
            jailed = await ctx.guild.create_role(
                name="Jailed", color=discord.Color.dark_gray(),
                reason="Jail system setup"
            )

        # Deny Jailed role in all channels
        failed = 0
        for ch in ctx.guild.channels:
            try:
                await ch.set_permissions(jailed, view_channel=False, send_messages=False)
            except:
                failed += 1

        # Create jail channel
        jail_ch = discord.utils.get(ctx.guild.channels, name="jail")
        if not jail_ch:
            ow = {
                ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                jailed:                 discord.PermissionOverwrite(view_channel=True, send_messages=True),
                ctx.guild.me:           discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True),
            }
            jail_ch = await ctx.guild.create_text_channel("jail", overwrites=ow)

        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"jail_role_id": jailed.id, "jail_channel_id": jail_ch.id}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(
            title="Jail System Ready",
            description=(
                f"Role: {jailed.mention}\n"
                f"Channel: {jail_ch.mention}\n"
                f"{'⚠️ Some channels could not be configured.' if failed else 'All channels configured.'}"
            ),
            color=0x2B2D31
        ))

    @commands.command()
    @ctx_mod()
    async def jail(self, ctx, member: discord.Member = None, *, reason="No reason provided"):
        """Send a member to jail (strips all roles, applies Jailed role)."""
        if not member:
            return await ctx.reply("Usage: `,jail @member [reason]`")
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)})
        if not gs or not gs.get("jail_role_id"):
            return await ctx.reply("Jail not configured. Run `,jailsetup` first.")
        jailed_role = ctx.guild.get_role(gs["jail_role_id"])
        if not jailed_role:
            return await ctx.reply("Jailed role missing. Run `,jailsetup` again.")
        if jailed_role in member.roles:
            return await ctx.reply(f"**{member}** is already jailed.")

        # Save current roles (skip managed and @everyone)
        old_roles = [r.id for r in member.roles
                     if r != ctx.guild.default_role and not r.managed]
        await jail_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(member.id)},
            {"$set": {"old_roles": old_roles, "reason": reason, "mod_id": str(ctx.author.id)}},
            upsert=True
        )

        safe_roles = [r for r in [jailed_role] if not r.managed]
        await member.edit(roles=safe_roles, reason=f"Jailed by {ctx.author}: {reason}")

        case = await _log_case(str(ctx.guild.id), "jail", member, ctx.author, reason)

        # Notify in jail channel
        jail_ch_id = gs.get("jail_channel_id")
        if jail_ch_id:
            jail_ch = ctx.guild.get_channel(jail_ch_id)
            if jail_ch:
                await jail_ch.send(embed=discord.Embed(
                    description=(
                        f"{member.mention}, you were jailed by {ctx.author.mention}.\n"
                        f"Reason: {reason}\n\nWait for a moderator to release you."
                    ),
                    color=0xff4444
                ))

        await ctx.reply(embed=_build_action_embed(
            "Member Jailed", member, ctx.author, reason, case
        ))
        await log_event(self.bot, ctx.guild, "jail",
                        f"Case #{case} — {member} jailed by {ctx.author}. {reason}")

    @commands.command()
    @ctx_mod()
    async def unjail(self, ctx, member: discord.Member = None, *, reason="Released"):
        """Release a member from jail and restore their roles."""
        if not member:
            return await ctx.reply("Usage: `,unjail @member`")
        doc = await jail_col.find_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(member.id)}
        )
        if not doc:
            return await ctx.reply(f"**{member}** is not jailed.")

        old_roles = [
            ctx.guild.get_role(rid) for rid in doc.get("old_roles", [])
            if ctx.guild.get_role(rid)
        ]
        await member.edit(roles=old_roles, reason=f"Unjailed by {ctx.author}")
        await jail_col.delete_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})

        case = await _log_case(str(ctx.guild.id), "unjail", member, ctx.author, reason)
        await ctx.reply(embed=discord.Embed(
            description=f"**{member}** released from jail. Case #{case}.", color=0x2B2D31
        ))
        await log_event(self.bot, ctx.guild, "unjail",
                        f"Case #{case} — {member} released by {ctx.author}.")

    # ══════════════════════════════════════════════════════════════════════════
    #  MOD NOTES
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(invoke_without_command=True)
    @ctx_mod()
    async def note(self, ctx, member: discord.Member = None):
        """View mod notes for a member. Sub-commands: add, clear"""
        if not member:
            return await ctx.reply("Usage: `,note @member` | `,note add @member <text>` | `,note clear @member`")
        doc   = await notes_col.find_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
        notes = doc.get("notes", []) if doc else []
        embed = discord.Embed(title=f"Mod Notes — {member}", color=0x2B2D31)
        if not notes:
            embed.description = "No notes on file."
        else:
            for i, n in enumerate(notes, 1):
                ts  = n.get("ts", "?")
                by  = n.get("by", "Unknown")
                embed.add_field(
                    name=f"Note #{i} — by {by}",
                    value=f"{n['text']}\n*{ts}*",
                    inline=False
                )
        embed.set_footer(text=f"ID: {member.id}")
        await ctx.reply(embed=embed)

    @note.command(name="add")
    @ctx_mod()
    async def note_add(self, ctx, member: discord.Member = None, *, text: str = None):
        """Add a mod note to a member."""
        if not member or not text:
            return await ctx.reply("Usage: `,note add @member <text>`")
        await notes_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(member.id)},
            {"$push": {"notes": {
                "text": text,
                "by":   ctx.author.display_name,
                "ts":   datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
            }}},
            upsert=True
        )
        await ctx.reply(f"Note added for **{member}**.")

    @note.command(name="clear")
    @ctx_mod()
    async def note_clear(self, ctx, member: discord.Member = None):
        """Clear all mod notes for a member."""
        if not member:
            return await ctx.reply("Usage: `,note clear @member`")
        result = await notes_col.delete_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(member.id)}
        )
        await ctx.reply(
            f"Notes cleared for **{member}**." if result.deleted_count
            else f"No notes found for **{member}**."
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  CASE HISTORY
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["history", "modlog"])
    @ctx_mod()
    async def cases(self, ctx, member: discord.Member = None, limit: int = 5):
        """View moderation case history for a member."""
        if not member:
            return await ctx.reply("Usage: `,cases @member [limit]`")
        limit = min(max(limit, 1), 20)
        cursor = cases_col.find(
            {"guild_id": str(ctx.guild.id), "user_id": str(member.id)}
        ).sort("case_num", -1).limit(limit)
        docs = await cursor.to_list(limit)

        if not docs:
            return await ctx.reply(f"No cases found for **{member}**.")

        embed = discord.Embed(
            title=f"Cases — {member}",
            color=0x2B2D31
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        for doc in docs:
            ts = doc.get("ts")
            ts_str = ts.strftime("%d %b %Y") if ts else "?"
            embed.add_field(
                name=f"#{doc['case_num']} — {doc['type'].upper()}",
                value=f"**Mod:** {doc.get('mod_tag','?')}\n**Reason:** {doc.get('reason','?')}\n*{ts_str}*",
                inline=False
            )
        embed.set_footer(text=f"Showing {len(docs)} most recent case(s) | ID: {member.id}")
        await ctx.reply(embed=embed)

    @commands.command()
    @ctx_mod()
    async def case(self, ctx, case_num: int = None):
        """View a specific case by number."""
        if case_num is None:
            return await ctx.reply("Usage: `,case <number>`")
        doc = await cases_col.find_one(
            {"guild_id": str(ctx.guild.id), "case_num": case_num}
        )
        if not doc:
            return await ctx.reply(f"Case **#{case_num}** not found.")
        ts     = doc.get("ts")
        ts_str = ts.strftime("%d %b %Y %H:%M UTC") if ts else "?"
        embed  = discord.Embed(
            title=f"Case #{case_num} — {doc['type'].upper()}",
            color=0x2B2D31,
            timestamp=ts
        )
        embed.add_field(name="Member", value=f"{doc.get('user_tag','?')} (`{doc.get('user_id','?')}`)", inline=True)
        embed.add_field(name="Mod",    value=doc.get("mod_tag", "?"),                                  inline=True)
        embed.add_field(name="Date",   value=ts_str,                                                   inline=True)
        embed.add_field(name="Reason", value=doc.get("reason", "?"),                                   inline=False)
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  ANTISPAM CONFIG
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def antispam(self, ctx):
        """Configure the anti-spam system. Sub-commands: enable, disable, set"""
        cfg = await antispam_col.find_one({"guild_id": str(ctx.guild.id)}) or {}
        embed = discord.Embed(title="Anti-Spam Config", color=0x2B2D31)
        embed.add_field(name="Status",    value="Enabled" if cfg.get("enabled") else "Disabled", inline=True)
        embed.add_field(name="Threshold", value=f"{cfg.get('threshold', 5)} msgs/5s",            inline=True)
        embed.add_field(name="Action",    value=cfg.get("action", "mute"),                        inline=True)
        embed.add_field(
            name="Commands",
            value=(
                "`,antispam enable` — turn on\n"
                "`,antispam disable` — turn off\n"
                "`,antispam set <threshold> <action>` — configure\n"
                "Actions: `mute` | `kick` | `ban`"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @antispam.command(name="enable")
    @commands.has_permissions(administrator=True)
    async def antispam_enable(self, ctx):
        """Enable the anti-spam system."""
        await antispam_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"enabled": True}}, upsert=True
        )
        await ctx.reply("Anti-spam enabled.")

    @antispam.command(name="disable")
    @commands.has_permissions(administrator=True)
    async def antispam_disable(self, ctx):
        """Disable the anti-spam system."""
        await antispam_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"enabled": False}}, upsert=True
        )
        await ctx.reply("Anti-spam disabled.")

    @antispam.command(name="set")
    @commands.has_permissions(administrator=True)
    async def antispam_set(self, ctx, threshold: int = 5, action: str = "mute"):
        """
        Configure anti-spam threshold and action.
        Usage: `,antispam set 6 mute`   (6 msgs in 5s → mute)
        Actions: mute | kick | ban
        """
        if action.lower() not in ("mute", "kick", "ban"):
            return await ctx.reply("Action must be `mute`, `kick`, or `ban`.")
        if not 2 <= threshold <= 30:
            return await ctx.reply("Threshold must be between 2 and 30.")
        await antispam_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"threshold": threshold, "action": action.lower()}},
            upsert=True
        )
        await ctx.reply(f"Anti-spam: triggers at **{threshold}** msg/5s → **{action}**.")

    # ══════════════════════════════════════════════════════════════════════════
    #  SETUPMUTE
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setupmute(self, ctx):
        """Create Muted, Image Muted, and Reaction Muted roles and apply permissions."""
        msg   = await ctx.reply("Setting up mute roles...")
        guild = ctx.guild
        role_defs = {
            "Muted":          discord.Color.dark_grey(),
            "Image Muted":    discord.Color.blue(),
            "Reaction Muted": discord.Color.orange(),
        }
        role_objs  = {}
        created    = []
        for name, color in role_defs.items():
            existing = discord.utils.get(guild.roles, name=name)
            if existing:
                role_objs[name] = existing
            else:
                role_objs[name] = await guild.create_role(name=name, color=color)
                created.append(name)

        text_count  = 0
        voice_count = 0
        for ch in guild.channels:
            try:
                if isinstance(ch, discord.TextChannel):
                    await ch.set_permissions(role_objs["Muted"],
                                              send_messages=False, add_reactions=False)
                    await ch.set_permissions(role_objs["Image Muted"],
                                              attach_files=False, embed_links=False)
                    await ch.set_permissions(role_objs["Reaction Muted"],
                                              add_reactions=False)
                    text_count += 1
                elif isinstance(ch, discord.VoiceChannel):
                    await ch.set_permissions(role_objs["Muted"],
                                              speak=False, send_messages=False)
                    voice_count += 1
            except:
                pass

        embed = discord.Embed(title="Mute System Ready", color=0x2B2D31)
        embed.add_field(name="Roles", value="\n".join(
            [f"{'Created' if n in created else 'Found existing'}: `{n}`" for n in role_defs]
        ), inline=False)
        embed.add_field(name="Text Channels",  value=text_count,  inline=True)
        embed.add_field(name="Voice Channels", value=voice_count, inline=True)
        embed.set_footer(text="Keep Happy's role above these roles in Server Settings!")
        await msg.edit(content=None, embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  DURATION PARSER (shared with admin.py via staticmethod)
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _parse_duration(s: str):
        """Parse '10m', '2h', '7d' → minutes. Returns None on failure."""
        import re as _re
        m = _re.fullmatch(r"(\d+)(m|h|d)?", s.strip().lower())
        if not m:
            return None
        val, unit = int(m.group(1)), m.group(2) or "m"
        return val * {"m": 1, "h": 60, "d": 1440}[unit]


async def setup(bot):
    await bot.add_cog(Moderation(bot))