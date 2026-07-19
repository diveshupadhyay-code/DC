import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import re
from datetime import timedelta, datetime, timezone

from utils.db import (
    warns_col, settings_col, jail_col,
    notes_col, cases_col, antispam_col
)
from utils.helpers import (
    BOT_OWNER_ID, ctx_mod, ctx_admin, ctx_owner,
    log_event, is_mod_or_owner, is_admin_or_owner
)

_msg_timestamps: dict = {}


async def _toggle_lock(channel, lock: bool, reason: str):
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
    last = await cases_col.find_one(
        {"guild_id": guild_id}, sort=[("case_num", -1)]
    )
    return (last["case_num"] + 1) if last else 1


async def _log_case(guild_id: str, case_type: str, user: discord.User,
                    mod: discord.User, reason: str) -> int:
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
    embed.add_field(name="User", value=f"{member.mention}\n`{member.id}`", inline=True)
    embed.add_field(name="Moderator", value=f"{mod.mention}\n`{mod.id}`", inline=True)
    embed.add_field(name="Case", value=f"#{case}", inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    if extra:
        for k, v in extra.items():
            embed.add_field(name=k, value=v, inline=True)
    return embed


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if message.author.id == BOT_OWNER_ID:
            return

        cfg = await antispam_col.find_one({"guild_id": str(message.guild.id)})
        if not cfg or not cfg.get("enabled"):
            return

        threshold = cfg.get("threshold", 5)
        action    = cfg.get("action", "mute")
        key       = (message.guild.id, message.author.id)
        now       = datetime.now(timezone.utc).timestamp()

        stamps = _msg_timestamps.get(key, [])
        stamps = [t for t in stamps if now - t < 5]
        stamps.append(now)
        _msg_timestamps[key] = stamps

        if len(stamps) >= threshold:
            _msg_timestamps[key] = []
            member = message.author
            try:
                if action == "mute":
                    await member.timeout(timedelta(minutes=10),
                                         reason="Spamming messages too fast")
                    await message.channel.send(
                        embed=discord.Embed(
                            description=f"{member.mention} has been muted for 10 minutes for spamming.",
                            color=0x2B2D31
                        ), delete_after=8
                    )
                elif action == "kick":
                    await member.kick(reason="Spamming messages too fast")
                elif action == "ban":
                    await member.ban(reason="Spamming messages too fast")
                await log_event(self.bot, message.guild, "antispam",
                                f"{member} triggered anti-spam ({action}).")
            except discord.Forbidden:
                pass

    @commands.command()
    @ctx_mod()
    async def kick(self, ctx, member: discord.Member = None, *, reason="No reason provided"):
        if not member:
            return await ctx.reply("Usage: `,kick <@user/id> [reason]`")
        if not _hierarchy_ok(ctx.author, member, BOT_OWNER_ID):
            return await ctx.reply("You cannot kick this user because their role is higher than or equal to yours.")
        if ctx.guild.me.top_role <= member.top_role:
            return await ctx.reply("I cannot kick this user because their role is higher than or equal to mine.")

        case = await _log_case(str(ctx.guild.id), "kick", member, ctx.author, reason)
        try:
            await member.send(embed=discord.Embed(
                description=f"You have been kicked from **{ctx.guild.name}**.\nReason: {reason}",
                color=0x2B2D31
            ))
        except discord.HTTPException:
            pass
        await member.kick(reason=reason)
        await ctx.reply(embed=_build_action_embed("User Kicked", member, ctx.author, reason, case))
        await log_event(self.bot, ctx.guild, "kick",
                        f"Case #{case} — {member} kicked by {ctx.author}. {reason}")

    @app_commands.command(name="kick", description="Kick a user from the server")
    @is_mod_or_owner()
    async def slash_kick(self, interaction: discord.Interaction,
                          member: discord.Member, reason: str = "No reason provided"):
        if not _hierarchy_ok(interaction.user, member, BOT_OWNER_ID):
            return await interaction.response.send_message(
                "You cannot kick this user because their role is higher than or equal to yours.", ephemeral=True
            )
        if interaction.guild.me.top_role <= member.top_role:
            return await interaction.response.send_message(
                "I cannot kick this user because their role is higher than or equal to mine.", ephemeral=True
            )
            
        case = await _log_case(str(interaction.guild.id), "kick", member, interaction.user, reason)
        try:
            await member.send(embed=discord.Embed(
                description=f"You have been kicked from **{interaction.guild.name}**.\nReason: {reason}",
                color=0x2B2D31
            ))
        except discord.HTTPException:
            pass
            
        await member.kick(reason=reason)
        await interaction.response.send_message(
            embed=discord.Embed(description=f"Kicked **{member}**. Case #{case}.", color=0x2B2D31),
            ephemeral=True
        )

    @commands.command()
    @ctx_mod()
    async def ban(self, ctx, member: discord.Member = None, *, reason="No reason provided"):
        if not member:
            return await ctx.reply("Usage: `,ban <@user/id> [reason]`")
        if not _hierarchy_ok(ctx.author, member, BOT_OWNER_ID):
            return await ctx.reply("You cannot ban this user because their role is higher than or equal to yours.")
        if ctx.guild.me.top_role <= member.top_role:
            return await ctx.reply("I cannot ban this user because their role is higher than or equal to mine.")

        case = await _log_case(str(ctx.guild.id), "ban", member, ctx.author, reason)
        try:
            await member.send(embed=discord.Embed(
                description=f"You have been permanently banned from **{ctx.guild.name}**.\nReason: {reason}",
                color=0x2B2D31
            ))
        except discord.HTTPException:
            pass
        await member.ban(reason=reason, delete_message_days=1)
        await ctx.reply(embed=_build_action_embed(
            "User Banned", member, ctx.author, reason, case, color=0x2B2D31
        ))
        await log_event(self.bot, ctx.guild, "ban",
                        f"Case #{case} — {member} banned by {ctx.author}. {reason}")

    @commands.command()
    @ctx_mod()
    async def tempban(self, ctx, member: discord.Member = None,
                      duration: str = None, *, reason="No reason provided"):
        if not member or not duration:
            return await ctx.reply("Usage: `,tempban <@user/id> <duration> [reason]`\nExample: `,tempban @user 7d Rules violation`")
        if not _hierarchy_ok(ctx.author, member, BOT_OWNER_ID):
            return await ctx.reply("You cannot ban this user because their role is higher than or equal to yours.")
        if ctx.guild.me.top_role <= member.top_role:
            return await ctx.reply("I cannot ban this user because their role is higher than or equal to mine.")

        minutes = self._parse_duration(duration)
        if minutes is None:
            return await ctx.reply("Invalid time format. Use something like `10m`, `2h`, or `7d`.")

        case = await _log_case(str(ctx.guild.id), "tempban", member, ctx.author, reason)
        try:
            await member.send(embed=discord.Embed(
                description=(
                    f"You have been temporarily banned from **{ctx.guild.name}**.\n"
                    f"Time: **{duration}**\nReason: {reason}"
                ),
                color=0x2B2D31
            ))
        except discord.HTTPException:
            pass
        await member.ban(reason=f"[Tempban {duration}] {reason}", delete_message_days=1)
        await ctx.reply(embed=_build_action_embed(
            "User Temporarily Banned", member, ctx.author, reason, case,
            color=0x2B2D31, extra={"Duration": duration}
        ))
        await log_event(self.bot, ctx.guild, "tempban",
                        f"Case #{case} — {member} tempbanned {duration} by {ctx.author}.")

        await asyncio.sleep(minutes * 60)
        try:
            await ctx.guild.unban(member, reason=f"Tempban expired ({duration})")
            await log_event(self.bot, ctx.guild, "tempban_expired",
                            f"{member} ban has expired automatically after {duration}.")
        except discord.HTTPException:
            pass

    @commands.command()
    @ctx_mod()
    async def unban(self, ctx, user_id: int = None, *, reason="No reason provided"):
        if not user_id:
            return await ctx.reply("Usage: `,unban <user_id> [reason]`")
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=reason)
            case = await _log_case(str(ctx.guild.id), "unban", user, ctx.author, reason)
            await ctx.reply(embed=discord.Embed(
                description=f"Unbanned **{user}**. Case #{case}.", color=0x2B2D31
            ))
            await log_event(self.bot, ctx.guild, "unban",
                            f"Case #{case} — {user} unbanned by {ctx.author}.")
        except discord.NotFound:
            await ctx.reply("This user could not be found in the ban list.")

    @app_commands.command(name="ban", description="Ban a user from the server")
    @is_mod_or_owner()
    async def slash_ban(self, interaction: discord.Interaction,
                         member: discord.Member, reason: str = "No reason provided"):
        if not _hierarchy_ok(interaction.user, member, BOT_OWNER_ID):
            return await interaction.response.send_message(
                "You cannot ban this user because their role is higher than or equal to yours.", ephemeral=True
            )
        if interaction.guild.me.top_role <= member.top_role:
            return await interaction.response.send_message(
                "I cannot ban this user because their role is higher than or equal to mine.", ephemeral=True
            )
            
        case = await _log_case(str(interaction.guild.id), "ban", member, interaction.user, reason)
        try:
            await member.send(embed=discord.Embed(
                description=f"You have been permanently banned from **{interaction.guild.name}**.\nReason: {reason}",
                color=0x2B2D31
            ))
        except discord.HTTPException:
            pass
            
        await member.ban(reason=reason, delete_message_days=1)
        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"Banned **{member}**. Case #{case}.", color=0x2B2D31
            ), ephemeral=True
        )

    @commands.command()
    @ctx_mod()
    async def mute(self, ctx, member: discord.Member = None,
                   duration: str = "10m", *, reason="No reason provided"):
        if not member:
            return await ctx.reply("Usage: `,mute <@user/id> [duration] [reason]`\nExample: `,mute @user 30m Chat spam`")
        if not _hierarchy_ok(ctx.author, member, BOT_OWNER_ID):
            return await ctx.reply("You cannot mute this user because their role is higher than or equal to yours.")
        if ctx.guild.me.top_role <= member.top_role:
            return await ctx.reply("I cannot mute this user because their role is higher than or equal to mine.")

        minutes = self._parse_duration(duration) or 10
        if minutes > 40320:
            return await ctx.reply("You cannot mute someone for longer than 28 days.")

        case = await _log_case(str(ctx.guild.id), "mute", member, ctx.author, reason)
        await member.timeout(timedelta(minutes=minutes), reason=reason)
        await ctx.reply(embed=_build_action_embed(
            "User Muted", member, ctx.author, reason, case,
            extra={"Time": duration}
        ))
        try:
            await member.send(embed=discord.Embed(
                description=f"You have been muted in **{ctx.guild.name}** for **{duration}**.\nReason: {reason}",
                color=0x2B2D31
            ))
        except discord.HTTPException:
            pass
        await log_event(self.bot, ctx.guild, "mute",
                        f"Case #{case} — {member} muted {duration} by {ctx.author}.")

    @commands.command()
    @ctx_mod()
    async def unmute(self, ctx, member: discord.Member = None, *, reason="Unmuted by moderator"):
        if not member:
            return await ctx.reply("Usage: `,unmute <@user/id>`")
        if not _hierarchy_ok(ctx.author, member, BOT_OWNER_ID):
            return await ctx.reply("You cannot unmute this user because their role is higher than or equal to yours.")
            
        await member.timeout(None, reason=reason)
        case = await _log_case(str(ctx.guild.id), "unmute", member, ctx.author, reason)
        await ctx.reply(embed=discord.Embed(
            description=f"Mute removed for **{member}**. Case #{case}.", color=0x2B2D31
        ))

    @app_commands.command(name="mute", description="Mute a user")
    @is_mod_or_owner()
    @app_commands.describe(member="User to mute", minutes="Time in minutes", reason="Reason")
    async def slash_mute(self, interaction: discord.Interaction,
                          member: discord.Member, minutes: int = 10, reason: str = "No reason"):
        if not _hierarchy_ok(interaction.user, member, BOT_OWNER_ID):
            return await interaction.response.send_message(
                "You cannot mute this user because their role is higher than or equal to yours.", ephemeral=True
            )
        if interaction.guild.me.top_role <= member.top_role:
            return await interaction.response.send_message(
                "I cannot mute this user because their role is higher than or equal to mine.", ephemeral=True
            )
            
        await member.timeout(timedelta(minutes=minutes), reason=reason)
        case = await _log_case(str(interaction.guild.id), "mute", member, interaction.user, reason)
        await interaction.response.send_message(
            f"Muted **{member}** for {minutes} minutes. Case #{case}.", ephemeral=True
        )

    @commands.command()
    @ctx_mod()
    async def warn(self, ctx, member: discord.Member = None, *, reason="No reason provided"):
        if not member:
            return await ctx.reply("Usage: `,warn <@user/id> [reason]`")
        if not _hierarchy_ok(ctx.author, member, BOT_OWNER_ID):
            return await ctx.reply("You cannot warn this user because their role is higher than or equal to yours.")

        sid, uid = str(ctx.guild.id), str(member.id)
        doc      = await warns_col.find_one({"server_id": sid, "user_id": uid})
        count    = (doc["count"] + 1) if doc else 1
        await warns_col.update_one(
            {"server_id": sid, "user_id": uid},
            {"$set": {"count": count}}, upsert=True
        )
        case = await _log_case(sid, "warn", member, ctx.author, reason)

        embed = discord.Embed(title="Warning Issued", color=0x2B2D31,
                               timestamp=datetime.now(timezone.utc))
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User", value=f"{member.mention}\n`{member.id}`", inline=True)
        embed.add_field(name="Moderator", value=f"{ctx.author.mention}\n`{ctx.author.id}`", inline=True)
        embed.add_field(name="Details", value=f"Case: #{case}\nTotal Warns: **{count}**", inline=True)
        embed.add_field(name="Reason",   value=reason,                              inline=False)
        await ctx.reply(embed=embed)

        try:
            await member.send(embed=discord.Embed(
                description=(
                    f"You received warning **#{count}** in **{ctx.guild.name}**.\n"
                    f"Reason: {reason}"
                ),
                color=0x2B2D31
            ))
        except discord.HTTPException:
            pass

        gs = await settings_col.find_one({"_id": sid}) or {}
        warn_mute_at = gs.get("warn_mute_at", 0)
        warn_ban_at  = gs.get("warn_ban_at",  0)
        
        if warn_ban_at and count >= warn_ban_at:
            try:
                await member.ban(reason=f"Auto-ban: reached warning limit ({count}/{warn_ban_at})")
                await ctx.send(embed=discord.Embed(
                    description=f"{member.mention} has been auto-banned after reaching the maximum warning limit.",
                    color=0x2B2D31
                ))
            except discord.HTTPException:
                pass
        elif warn_mute_at and count >= warn_mute_at:
            try:
                await member.timeout(timedelta(hours=1),
                                     reason=f"Auto-mute: reached warning limit ({count}/{warn_mute_at})")
                await ctx.send(embed=discord.Embed(
                    description=f"{member.mention} has been auto-muted for 1 hour after reaching the warning limit.",
                    color=0x2B2D31
                ))
            except discord.HTTPException:
                pass

        await log_event(self.bot, ctx.guild, "warn",
                        f"Case #{case} — {member} warned by {ctx.author}. #{count} {reason}")

    @app_commands.command(name="warn", description="Warn a user")
    @is_mod_or_owner()
    async def slash_warn(self, interaction: discord.Interaction,
                          member: discord.Member, reason: str = "No reason provided"):
        if not _hierarchy_ok(interaction.user, member, BOT_OWNER_ID):
            return await interaction.response.send_message(
                "You cannot warn this user because their role is higher than or equal to yours.", ephemeral=True
            )
            
        await interaction.response.defer()
        sid, uid = str(interaction.guild.id), str(member.id)
        doc      = await warns_col.find_one({"server_id": sid, "user_id": uid})
        count    = (doc["count"] + 1) if doc else 1
        await warns_col.update_one(
            {"server_id": sid, "user_id": uid},
            {"$set": {"count": count}}, upsert=True
        )
        case  = await _log_case(sid, "warn", member, interaction.user, reason)
        embed = discord.Embed(title="Warning Issued", color=0x2B2D31)
        embed.add_field(name="User",   value=member.mention, inline=True)
        embed.add_field(name="Warnings", value=f"**{count}** Total", inline=True)
        embed.add_field(name="Case",     value=f"#{case}",     inline=True)
        embed.add_field(name="Reason",   value=reason,         inline=False)
        await interaction.followup.send(embed=embed)

    @commands.command(aliases=["warnlist", "wl"])
    @ctx_mod()
    async def warnings(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        doc    = await warns_col.find_one(
            {"server_id": str(ctx.guild.id), "user_id": str(member.id)}
        )
        count  = doc["count"] if doc else 0
        embed  = discord.Embed(color=0x2B2D31)
        embed.set_author(name=f"Warnings — {member}", icon_url=member.display_avatar.url)
        embed.add_field(name="Total Warnings", value=f"**{count}** warnings on file", inline=True)
        embed.set_footer(text=f"User ID: {member.id}")
        await ctx.reply(embed=embed)

    @commands.command(aliases=["cw"])
    @ctx_mod()
    async def clearwarns(self, ctx, member: discord.Member = None):
        if not member:
            return await ctx.reply("Usage: `,clearwarns <@user/id>`")
        if not _hierarchy_ok(ctx.author, member, BOT_OWNER_ID):
            return await ctx.reply("You cannot clear warnings for this user because their role is higher than or equal to yours.")
            
        result = await warns_col.delete_one(
            {"server_id": str(ctx.guild.id), "user_id": str(member.id)}
        )
        if result.deleted_count:
            await ctx.reply(f"All warnings cleared for **{member}**.")
        else:
            await ctx.reply(f"**{member}** has no warnings to clear.")

    @commands.command(aliases=["sb"])
    @ctx_mod()
    async def softban(self, ctx, member: discord.Member = None, *, reason="No reason provided"):
        if not member:
            return await ctx.reply("Usage: `,softban <@user/id> [reason]`")
        if not _hierarchy_ok(ctx.author, member, BOT_OWNER_ID):
            return await ctx.reply("You cannot softban this user because their role is higher than or equal to yours.")
        if ctx.guild.me.top_role <= member.top_role:
            return await ctx.reply("I cannot softban this user because their role is higher than or equal to mine.")

        case = await _log_case(str(ctx.guild.id), "softban", member, ctx.author, reason)
        try:
            await member.send(embed=discord.Embed(
                description=f"You were softbanned from **{ctx.guild.name}**.\nReason: {reason}",
                color=0x2B2D31
            ))
        except discord.HTTPException:
            pass
        await ctx.guild.ban(member, reason=f"Softban: {reason}", delete_message_days=7)
        await ctx.guild.unban(member, reason="Softban complete")
        await ctx.reply(embed=_build_action_embed(
            "User Softbanned", member, ctx.author, reason, case
        ))
        await log_event(self.bot, ctx.guild, "softban",
                        f"Case #{case} — {member} softbanned by {ctx.author}.")

    @commands.command(aliases=["nick"])
    @ctx_mod()
    async def nickname(self, ctx, member: discord.Member = None, *, new_name: str = None):
        if not member:
            return await ctx.reply("Usage: `,nickname <@user/id> [new name]`")
        if not _hierarchy_ok(ctx.author, member, BOT_OWNER_ID):
            return await ctx.reply("You cannot change the nickname of this user because their role is higher than or equal to yours.")
        if member.id == ctx.guild.owner_id:
            return await ctx.reply("You cannot change the server owner's nickname.")
        if new_name and len(new_name) > 32:
            return await ctx.reply("The nickname must be 32 characters or less.")
            
        await member.edit(nick=new_name or None)
        await ctx.reply(
            f"Nickname of {member.mention} has been " +
            (f"set to `{new_name}`." if new_name else "reset to default.")
        )

    @commands.command()
    @ctx_mod()
    async def lock(self, ctx, channel: discord.abc.GuildChannel = None, *, reason="No reason provided"):
        channel = channel or ctx.channel
        await _toggle_lock(channel, True, f"Locked by {ctx.author}: {reason}")
        await ctx.reply(embed=discord.Embed(
            description=f"Locked **{channel.mention}**.\nReason: {reason}", color=0x2B2D31
        ))
        await log_event(self.bot, ctx.guild, "channel_lock",
                        f"{channel} locked by {ctx.author}. {reason}")

    @app_commands.command(name="lock", description="Lock a channel to stop messages")
    @is_mod_or_owner()
    async def slash_lock(self, interaction: discord.Interaction,
                          channel: discord.TextChannel = None, reason: str = "No reason"):
        ch = channel or interaction.channel
        await _toggle_lock(ch, True, reason)
        await interaction.response.send_message(f"Locked {ch.mention}.", ephemeral=True)

    @commands.command()
    @ctx_mod()
    async def unlock(self, ctx, channel: discord.abc.GuildChannel = None, *, reason="No reason provided"):
        channel = channel or ctx.channel
        await _toggle_lock(channel, False, f"Unlocked by {ctx.author}: {reason}")
        await ctx.reply(embed=discord.Embed(
            description=f"Unlocked **{channel.mention}**.", color=0x2B2D31
        ))

    @app_commands.command(name="unlock", description="Unlock a channel to allow messages")
    @is_mod_or_owner()
    async def slash_unlock(self, interaction: discord.Interaction,
                            channel: discord.TextChannel = None):
        ch = channel or interaction.channel
        await _toggle_lock(ch, False, "Unlocked")
        await interaction.response.send_message(f"Unlocked {ch.mention}.", ephemeral=True)

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def lockdown(self, ctx, *, reason="Emergency lockdown"):
        msg   = await ctx.reply("Locking down the server...")
        count = 0
        for ch in ctx.guild.channels:
            if isinstance(ch, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel)):
                try:
                    await _toggle_lock(ch, True, reason)
                    count += 1
                except discord.HTTPException:
                    pass
        await msg.edit(content=None, embed=discord.Embed(
            title="Server Lockdown Active",
            description=f"Locked **{count}** channels.\nReason: {reason}",
            color=0x2B2D31
        ))
        await log_event(self.bot, ctx.guild, "lockdown",
                        f"Server locked by {ctx.author}. {reason}")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def unlockdown(self, ctx):
        msg   = await ctx.reply("Lifting server lockdown...")
        count = 0
        for ch in ctx.guild.channels:
            if isinstance(ch, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel)):
                try:
                    await _toggle_lock(ch, False, "Lockdown lifted")
                    count += 1
                except discord.HTTPException:
                    pass
        await msg.edit(content=None, embed=discord.Embed(
            description=f"Lockdown lifted. Unlocked **{count}** channels.", color=0x2B2D31
        ))

    @commands.command(aliases=["vlock"])
    @ctx_mod()
    async def vclock(self, ctx, channel: discord.VoiceChannel = None):
        if not channel:
            channel = ctx.author.voice.channel if ctx.author.voice else None
        if not channel:
            return await ctx.reply("You must be in a voice channel or mention one to use this command.")
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.connect = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.reply(f"Locked voice channel: **{channel.name}**.")

    @commands.command(aliases=["vunlock"])
    @ctx_mod()
    async def vcunlock(self, ctx, channel: discord.VoiceChannel = None):
        if not channel:
            channel = ctx.author.voice.channel if ctx.author.voice else None
        if not channel:
            return await ctx.reply("You must be in a voice channel or mention one to use this command.")
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.connect = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.reply(f"Unlocked voice channel: **{channel.name}**.")

    @commands.command(aliases=["clear", "c"])
    @ctx_mod()
    async def purge(self, ctx, target: str = None, limit: int = None):
        if not target:
            embed = discord.Embed(title="Delete Messages", color=0x2B2D31)
            embed.add_field(
                name="Options",
                value=(
                    "`,purge <amount>` — Delete recent messages\n"
                    "`,purge bots <amount>` — Delete bot messages only\n"
                    "`,purge @user <amount>` — Delete messages from a specific user\n"
                    "`,purge links <amount>` — Delete messages containing links\n"
                    "`,purge images <amount>` — Delete messages with attachments\n"
                    "`,purge embeds <amount>` — Delete messages containing embeds"
                ),
                inline=False
            )
            return await ctx.reply(embed=embed)

        try:
            await ctx.message.delete()
        except discord.HTTPException:
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
            msgs = await ctx.channel.purge(limit=lim, check=lambda m: m.author.id == uid)
        else:
            try:
                msgs = await ctx.channel.purge(limit=int(target))
            except ValueError:
                return await ctx.send("Invalid usage. Use `,purge` to see the available options.", delete_after=5)

        await ctx.send(embed=discord.Embed(
            description=f"Deleted **{len(msgs)}** message(s).", color=0x2B2D31
        ), delete_after=4)

    @app_commands.command(name="clear", description="Delete messages in bulk")
    @is_mod_or_owner()
    async def slash_clear(self, interaction: discord.Interaction, amount: int):
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(
            f"Deleted **{len(deleted)}** messages.", ephemeral=True
        )

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def massrole(self, ctx, action: str = None, target: str = None, role: discord.Role = None):
        if not action or action.lower() not in ("add", "remove") or not role:
            return await ctx.reply(
                "Usage:\n"
                "`,massrole add everyone @Role` — Give a role to all members\n"
                "`,massrole add bots @Role` — Give a role to all bots\n"
                "`,massrole remove everyone @Role` — Remove a role from everyone"
            )
        if ctx.guild.me.top_role <= role:
            return await ctx.reply("I cannot manage this role because it is higher than or equal to my highest role.")

        tl = (target or "").lower().strip().lstrip("<@&").rstrip(">")
        if tl in ("bots", "bot"):
            members, label = [m for m in ctx.guild.members if m.bot],     "bots"
        else:
            members, label = [m for m in ctx.guild.members if not m.bot], "members"

        if not members:
            return await ctx.reply(f"No {label} found.")

        msg   = await ctx.reply(
            embed=discord.Embed(
                description=f"Updating **{len(members)}** {label}...",
                color=0x2B2D31
            )
        )
        count  = 0
        failed = 0

        for i, member in enumerate(members):
            try:
                if action.lower() == "add" and role not in member.roles:
                    await member.add_roles(role, reason=f"Massrole add by {ctx.author}")
                    count += 1
                elif action.lower() == "remove" and role in member.roles:
                    await member.remove_roles(role, reason=f"Massrole remove by {ctx.author}")
                    count += 1
            except discord.Forbidden:
                failed += 1
            except Exception:
                failed += 1
                
            if (i + 1) % 25 == 0:
                await msg.edit(embed=discord.Embed(
                    description=f"Updating... {i+1}/{len(members)} done.", color=0x2B2D31
                ))
            await asyncio.sleep(0.35)

        result_embed = discord.Embed(
            description=(
                f"**{action.capitalize()}d** {role.mention} for **{count}** {label}."
                + (f"\nSkipped {failed} user(s) due to missing permissions." if failed else "")
            ),
            color=0x2B2D31
        )
        await msg.edit(embed=result_embed)
        await log_event(self.bot, ctx.guild, "massrole",
                        f"{action} {role} for {count} targets by {ctx.author}.")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def jailsetup(self, ctx):
        jailed = discord.utils.get(ctx.guild.roles, name="Jailed")
        if not jailed:
            jailed = await ctx.guild.create_role(
                name="Jailed", color=discord.Color.dark_gray(),
                reason="Setting up jail system"
            )

        failed = 0
        for ch in ctx.guild.channels:
            try:
                await ch.set_permissions(jailed, view_channel=False, send_messages=False)
            except discord.HTTPException:
                failed += 1

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
                f"Channel: {jail_ch.mention}\n\n"
                f"{'⚠️ Could not configure access for some channels.' if failed else 'All channels set up successfully.'}"
            ),
            color=0x2B2D31
        ))

    @commands.command()
    @ctx_mod()
    async def jail(self, ctx, member: discord.Member = None, *, reason="No reason provided"):
        if not member:
            return await ctx.reply("Usage: `,jail <@user/id> [reason]`")
        if not _hierarchy_ok(ctx.author, member, BOT_OWNER_ID):
            return await ctx.reply("You cannot jail this user because their role is higher than or equal to yours.")
            
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)})
        if not gs or not gs.get("jail_role_id"):
            return await ctx.reply("The jail system is not set up yet. Run `,jailsetup` first.")
        jailed_role = ctx.guild.get_role(gs["jail_role_id"])
        if not jailed_role:
            return await ctx.reply("The Jailed role is missing. Run `,jailsetup` again.")
        if jailed_role in member.roles:
            return await ctx.reply(f"**{member}** is already jailed.")

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

        jail_ch_id = gs.get("jail_channel_id")
        if jail_ch_id:
            jail_ch = ctx.guild.get_channel(jail_ch_id)
            if jail_ch:
                await jail_ch.send(embed=discord.Embed(
                    description=(
                        f"{member.mention}, you were jailed by {ctx.author.mention}.\n"
                        f"Reason: {reason}\n\nPlease wait here for a moderator."
                    ),
                    color=0x2B2D31
                ))

        await ctx.reply(embed=_build_action_embed(
            "User Jailed", member, ctx.author, reason, case
        ))
        await log_event(self.bot, ctx.guild, "jail",
                        f"Case #{case} — {member} jailed by {ctx.author}. {reason}")

    @commands.command()
    @ctx_mod()
    async def unjail(self, ctx, member: discord.Member = None, *, reason="Released"):
        if not member:
            return await ctx.reply("Usage: `,unjail <@user/id>`")
        doc = await jail_col.find_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(member.id)}
        )
        if not doc:
            return await ctx.reply(f"**{member}** is not currently jailed.")

        old_roles = [
            ctx.guild.get_role(rid) for rid in doc.get("old_roles", [])
            if ctx.guild.get_role(rid)
        ]
        await member.edit(roles=old_roles, reason=f"Unjailed by {ctx.author}")
        await jail_col.delete_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})

        case = await _log_case(str(ctx.guild.id), "unjail", member, ctx.author, reason)
        await ctx.reply(embed=discord.Embed(
            description=f"Released **{member}** from jail. Case #{case}.", color=0x2B2D31
        ))
        await log_event(self.bot, ctx.guild, "unjail",
                        f"Case #{case} — {member} released by {ctx.author}.")

    @commands.group(invoke_without_command=True)
    @ctx_mod()
    async def note(self, ctx, member: discord.Member = None):
        if not member:
            return await ctx.reply("Usage:\n`,note <@user>`\n`,note add <@user> <text>`\n`,note clear <@user>`")
        doc   = await notes_col.find_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
        notes = doc.get("notes", []) if doc else []
        embed = discord.Embed(title=f"Notes for {member}", color=0x2B2D31)
        if not notes:
            embed.description = "No notes found for this user."
        else:
            for i, n in enumerate(notes, 1):
                ts  = n.get("ts", "?")
                by  = n.get("by", "Unknown")
                embed.add_field(
                    name=f"Note #{i} — Added by {by}",
                    value=f"{n['text']}\n*{ts}*",
                    inline=False
                )
        embed.set_footer(text=f"User ID: {member.id}")
        await ctx.reply(embed=embed)

    @note.command(name="add")
    @ctx_mod()
    async def note_add(self, ctx, member: discord.Member = None, *, text: str = None):
        if not member or not text:
            return await ctx.reply("Usage: `,note add <@user/id> <text>`")
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
        if not member:
            return await ctx.reply("Usage: `,note clear <@user/id>`")
        result = await notes_col.delete_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(member.id)}
        )
        await ctx.reply(
            f"Notes cleared for **{member}**." if result.deleted_count
            else f"No notes found for **{member}**."
        )

    @commands.command(aliases=["history", "modlog"])
    @ctx_mod()
    async def cases(self, ctx, member: discord.Member = None, limit: int = 5):
        if not member:
            return await ctx.reply("Usage: `,cases <@user/id> [limit]`")
        limit = min(max(limit, 1), 20)
        cursor = cases_col.find(
            {"guild_id": str(ctx.guild.id), "user_id": str(member.id)}
        ).sort("case_num", -1).limit(limit)
        docs = await cursor.to_list(limit)

        if not docs:
            return await ctx.reply(f"No history found for **{member}**.")

        embed = discord.Embed(
            title=f"History — {member}",
            color=0x2B2D31
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        for doc in docs:
            ts = doc.get("ts")
            ts_str = ts.strftime("%d %b %Y") if ts else "?"
            embed.add_field(
                name=f"#{doc['case_num']} — {doc['type'].upper()}",
                value=f"**Moderator:** {doc.get('mod_tag','?')}\n**Reason:** {doc.get('reason','?')}\n*Date: {ts_str}*",
                inline=False
            )
        embed.set_footer(text=f"Showing {len(docs)} recent case(s) | User ID: {member.id}")
        await ctx.reply(embed=embed)

    @commands.command()
    @ctx_mod()
    async def case(self, ctx, case_num: int = None):
        if case_num is None:
            return await ctx.reply("Usage: `,case <number>`")
        doc = await cases_col.find_one(
            {"guild_id": str(ctx.guild.id), "case_num": case_num}
        )
        if not doc:
            return await ctx.reply(f"Case **#{case_num}** does not exist.")
        ts     = doc.get("ts")
        ts_str = ts.strftime("%d %b %Y %H:%M UTC") if ts else "?"
        embed  = discord.Embed(
            title=f"Case #{case_num} — {doc['type'].upper()}",
            color=0x2B2D31,
            timestamp=ts
        )
        embed.add_field(name="User", value=f"{doc.get('user_tag','?')}\n`{doc.get('user_id','?')}`", inline=True)
        embed.add_field(name="Moderator", value=f"{doc.get('mod_tag','?')}\n`{doc.get('mod_id','?')}`", inline=True)
        embed.add_field(name="Date",   value=ts_str,                                                   inline=True)
        embed.add_field(name="Reason", value=doc.get("reason", "?"),                                   inline=False)
        await ctx.reply(embed=embed)

    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def antispam(self, ctx):
        cfg = await antispam_col.find_one({"guild_id": str(ctx.guild.id)}) or {}
        embed = discord.Embed(title="Anti-Spam Settings", color=0x2B2D31)
        embed.add_field(name="Status",    value="Enabled" if cfg.get("enabled") else "Disabled", inline=True)
        embed.add_field(name="Limit", value=f"{cfg.get('threshold', 5)} messages / 5s",            inline=True)
        embed.add_field(name="Punishment",    value=str(cfg.get('action', 'mute')).upper(),                        inline=True)
        embed.add_field(
            name="Commands",
            value=(
                "`,antispam enable` — Turn on anti-spam\n"
                "`,antispam disable` — Turn off anti-spam\n"
                "`,antispam set <limit> <action>` — Change limits\n"
                "Actions: `mute` | `kick` | `ban`"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @antispam.command(name="enable")
    @commands.has_permissions(administrator=True)
    async def antispam_enable(self, ctx):
        await antispam_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"enabled": True}}, upsert=True
        )
        await ctx.reply("Anti-spam has been turned on.")

    @antispam.command(name="disable")
    @commands.has_permissions(administrator=True)
    async def antispam_disable(self, ctx):
        await antispam_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"enabled": False}}, upsert=True
        )
        await ctx.reply("Anti-spam has been turned off.")

    @antispam.command(name="set")
    @commands.has_permissions(administrator=True)
    async def antispam_set(self, ctx, threshold: int = 5, action: str = "mute"):
        if action.lower() not in ("mute", "kick", "ban"):
            return await ctx.reply("Action must be either `mute`, `kick`, or `ban`.")
        if not 2 <= threshold <= 30:
            return await ctx.reply("The limit must be a number between 2 and 30.")
        await antispam_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"threshold": threshold, "action": action.lower()}},
            upsert=True
        )
        await ctx.reply(f"Anti-spam updated: Reaching **{threshold}** messages in 5 seconds will result in a **{action}**.")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setupmute(self, ctx):
        msg   = await ctx.reply("Setting up mute roles and updating permissions...")
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
            except discord.HTTPException:
                pass

        embed = discord.Embed(title="Mute Roles Configured", color=0x2B2D31)
        embed.add_field(name="Roles", value="\n".join(
            [f"{'Created new' if n in created else 'Found existing'}: `{n}`" for n in role_defs]
        ), inline=False)
        embed.add_field(name="Text Channels Set",  value=f"{text_count} channels locked",  inline=True)
        embed.add_field(name="Voice Channels Set", value=f"{voice_count} channels locked", inline=True)
        embed.set_footer(text="Make sure Happy's role is higher than these roles in Server Settings!")
        await msg.edit(content=None, embed=embed)

    @staticmethod
    def _parse_duration(s: str):
        m = re.fullmatch(r"(\d+)(m|h|d)?", s.strip().lower())
        if not m:
            return None
        val, unit = int(m.group(1)), m.group(2) or "m"
        return val * {"m": 1, "h": 60, "d": 1440}[unit]


async def setup(bot):
    await bot.add_cog(Moderation(bot))