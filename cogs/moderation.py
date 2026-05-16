"""
cogs/moderation.py — kick, ban, mute, warn, jail, lock, purge, massrole, etc.
"""

import discord
from discord.ext import commands
from discord import app_commands
import asyncio, re
from datetime import timedelta

from utils.db import warns_col, settings_col, jail_col
from utils.helpers import (
    BOT_OWNER_ID, ctx_mod, ctx_admin, ctx_owner,
    log_event, is_mod_or_owner, is_admin_or_owner
)


# ── Channel lock helper (text / voice / thread / forum) ──────────────────────
async def _toggle_lock(channel, lock: bool, reason: str):
    ow = channel.overwrites_for(channel.guild.default_role)
    if isinstance(channel, discord.VoiceChannel):
        ow.connect = False if lock else None
        ow.speak   = False if lock else None
    else:
        ow.send_messages             = False if lock else None
        ow.send_messages_in_threads  = False if lock else None
        ow.create_public_threads     = False if lock else None
        ow.add_reactions             = False if lock else None
    await channel.set_permissions(
        channel.guild.default_role, overwrite=ow, reason=reason
    )


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Kick ──────────────────────────────────────────────────────────────────
    @commands.command()
    @ctx_mod()
    async def kick(self, ctx, member: discord.Member = None, *, reason="No reason provided"):
        if not member:
            return await ctx.reply("Mention a member to kick.")
        if ctx.author.id != BOT_OWNER_ID and ctx.author.top_role <= member.top_role:
            return await ctx.reply("Cannot kick someone with an equal or higher role.")
        await member.kick(reason=reason)
        await ctx.reply(embed=discord.Embed(
            description=f"**{member}** has been kicked.\nReason: {reason}", color=0x2B2D31
        ))
        await log_event(self.bot, ctx.guild, "kick", f"{member} kicked by {ctx.author}. {reason}")

    @app_commands.command(name="kick", description="Kick a member from the server")
    @is_mod_or_owner()
    async def slash_kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        if interaction.user.id != BOT_OWNER_ID and interaction.user.top_role <= member.top_role:
            return await interaction.response.send_message("Cannot kick someone with equal or higher role.", ephemeral=True)
        await member.kick(reason=reason)
        await interaction.response.send_message(f"**{member}** kicked.", ephemeral=True)
        await log_event(self.bot, interaction.guild, "kick", f"{member} kicked by {interaction.user}.")

    # ── Ban ───────────────────────────────────────────────────────────────────
    @commands.command()
    @ctx_mod()
    async def ban(self, ctx, member: discord.Member = None, *, reason="No reason provided"):
        if not member:
            return await ctx.reply("Mention a member to ban.")
        if ctx.author.id != BOT_OWNER_ID and ctx.author.top_role <= member.top_role:
            return await ctx.reply("Cannot ban someone with an equal or higher role.")
        await member.ban(reason=reason)
        await ctx.reply(embed=discord.Embed(
            description=f"**{member}** has been banned.\nReason: {reason}", color=0x2B2D31
        ))
        await log_event(self.bot, ctx.guild, "ban", f"{member} banned by {ctx.author}. {reason}")

    @app_commands.command(name="ban", description="Ban a member")
    @is_mod_or_owner()
    async def slash_ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        await member.ban(reason=reason)
        await interaction.response.send_message(f"**{member}** banned.", ephemeral=True)

    # ── Unban ─────────────────────────────────────────────────────────────────
    @commands.command()
    @ctx_mod()
    async def unban(self, ctx, user_id: int):
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user)
            await ctx.reply(f"Unbanned **{user}**.")
        except discord.NotFound:
            await ctx.reply("User not found or not banned.")

    # ── Mute (timeout) ────────────────────────────────────────────────────────
    @commands.command()
    @ctx_mod()
    async def mute(self, ctx, member: discord.Member = None, minutes: int = 10, *, reason="No reason provided"):
        if not member:
            return await ctx.reply("Usage: `,mute @user <minutes> [reason]`")
        if ctx.author.id != BOT_OWNER_ID and ctx.author.top_role <= member.top_role:
            return await ctx.reply("Cannot mute someone with an equal or higher role.")
        await member.timeout(timedelta(minutes=minutes), reason=reason)
        await ctx.reply(embed=discord.Embed(
            description=f"**{member}** muted for **{minutes}** min.\nReason: {reason}", color=0x2B2D31
        ))
        await log_event(self.bot, ctx.guild, "mute", f"{member} muted {minutes}m by {ctx.author}.")

    @app_commands.command(name="mute", description="Timeout a member")
    @is_mod_or_owner()
    async def slash_mute(self, interaction: discord.Interaction, member: discord.Member, minutes: int = 10, reason: str = "No reason"):
        await member.timeout(timedelta(minutes=minutes), reason=reason)
        await interaction.response.send_message(f"**{member}** muted for {minutes} min.", ephemeral=True)

    @commands.command()
    @ctx_mod()
    async def unmute(self, ctx, member: discord.Member = None):
        if not member:
            return await ctx.reply("Mention a member to unmute.")
        await member.timeout(None)
        await ctx.reply(f"Timeout removed for **{member}**.")

    # ── Warn ──────────────────────────────────────────────────────────────────
    @commands.command()
    @ctx_mod()
    async def warn(self, ctx, member: discord.Member = None, *, reason="No reason provided"):
        if not member:
            return await ctx.reply("Mention a member to warn.")
        sid, uid = str(ctx.guild.id), str(member.id)
        doc   = await warns_col.find_one({"server_id": sid, "user_id": uid})
        count = (doc["count"] + 1) if doc else 1
        await warns_col.update_one(
            {"server_id": sid, "user_id": uid}, {"$set": {"count": count}}, upsert=True
        )
        embed = discord.Embed(title="Warning Issued", color=0xff4444)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Member",   value=member.mention,    inline=True)
        embed.add_field(name="By",       value=ctx.author.mention, inline=True)
        embed.add_field(name="Count",    value=f"**{count}**",     inline=True)
        embed.add_field(name="Reason",   value=reason,             inline=False)
        await ctx.reply(embed=embed)
        try:
            await member.send(embed=discord.Embed(
                description=f"You have been warned in **{ctx.guild.name}**.\n"
                            f"Warning #{count} — Reason: {reason}",
                color=0xff4444
            ))
        except:
            pass
        await log_event(self.bot, ctx.guild, "warn", f"{member} warned by {ctx.author}. #{count} {reason}")

    @app_commands.command(name="warn", description="Warn a member")
    @is_mod_or_owner()
    async def slash_warn(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        await interaction.response.defer()
        sid, uid = str(interaction.guild.id), str(member.id)
        doc   = await warns_col.find_one({"server_id": sid, "user_id": uid})
        count = (doc["count"] + 1) if doc else 1
        await warns_col.update_one(
            {"server_id": sid, "user_id": uid}, {"$set": {"count": count}}, upsert=True
        )
        embed = discord.Embed(title="Warning Issued", color=0xff4444)
        embed.add_field(name="Member",  value=member.mention, inline=True)
        embed.add_field(name="Count",   value=count,          inline=True)
        embed.add_field(name="Reason",  value=reason,         inline=False)
        await interaction.followup.send(embed=embed)

    @commands.command(aliases=["warnlist"])
    @ctx_mod()
    async def warnings(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        doc   = await warns_col.find_one({"server_id": str(ctx.guild.id), "user_id": str(member.id)})
        count = doc["count"] if doc else 0
        await ctx.reply(embed=discord.Embed(
            description=f"**{member}** has **{count}** warning(s).", color=0x2B2D31
        ))

    @commands.command(aliases=["cw"])
    @ctx_mod()
    async def clearwarns(self, ctx, member: discord.Member = None):
        if not member:
            return await ctx.reply("Mention a member.")
        await warns_col.delete_one({"server_id": str(ctx.guild.id), "user_id": str(member.id)})
        await ctx.reply(f"All warnings cleared for **{member}**.")

    # ── Softban ───────────────────────────────────────────────────────────────
    @commands.command(aliases=["sb"])
    @ctx_mod()
    async def softban(self, ctx, member: discord.Member = None, *, reason="No reason provided"):
        if not member:
            return await ctx.reply("Usage: `,softban @user [reason]`")
        if ctx.author.id != BOT_OWNER_ID and ctx.author.top_role <= member.top_role:
            return await ctx.reply("Cannot softban someone with equal or higher role.")
        try:
            await member.send(embed=discord.Embed(
                description=f"You were softbanned from **{ctx.guild.name}**.\nReason: {reason}",
                color=0xff4444
            ))
        except:
            pass
        await ctx.guild.ban(member, reason=f"Softban: {reason}", delete_message_days=7)
        await ctx.guild.unban(member)
        await ctx.reply(embed=discord.Embed(
            description=f"**{member}** softbanned. Messages cleared.\nReason: {reason}", color=0x2B2D31
        ))

    # ── Nickname ──────────────────────────────────────────────────────────────
    @commands.command(aliases=["nick"])
    @ctx_mod()
    async def nickname(self, ctx, member: discord.Member = None, *, new_name: str = None):
        if not member:
            return await ctx.reply("Usage: `,nickname @user [new name]`")
        if ctx.author.id != BOT_OWNER_ID and ctx.author.top_role <= member.top_role:
            return await ctx.reply("Cannot change nickname of someone with equal or higher role.")
        if member.id == ctx.guild.owner_id:
            return await ctx.reply("Cannot change the server owner's nickname.")
        if new_name and len(new_name) > 32:
            return await ctx.reply("Nickname must be 32 characters or fewer.")
        await member.edit(nick=new_name or None)
        msg = f"Nickname of {member.mention} set to `{new_name}`." if new_name else f"Nickname of {member.mention} reset."
        await ctx.reply(msg)

    # ── Lock / Unlock ─────────────────────────────────────────────────────────
    @commands.command()
    @ctx_mod()
    async def lock(self, ctx, channel: discord.abc.GuildChannel = None, *, reason="No reason provided"):
        channel = channel or ctx.channel
        await _toggle_lock(channel, True, f"Locked by {ctx.author}: {reason}")
        await ctx.reply(embed=discord.Embed(
            description=f"**{channel.mention}** locked.\nReason: {reason}", color=0x2B2D31
        ))
        await log_event(self.bot, ctx.guild, "channel_lock", f"{channel} locked by {ctx.author}.")

    @app_commands.command(name="lock", description="Lock a channel")
    @is_mod_or_owner()
    async def slash_lock(self, interaction: discord.Interaction, channel: discord.TextChannel = None, reason: str = "No reason"):
        ch = channel or interaction.channel
        await _toggle_lock(ch, True, reason)
        await interaction.response.send_message(f"{ch.mention} locked.", ephemeral=True)

    @commands.command()
    @ctx_mod()
    async def unlock(self, ctx, channel: discord.abc.GuildChannel = None, *, reason="No reason provided"):
        channel = channel or ctx.channel
        await _toggle_lock(channel, False, f"Unlocked by {ctx.author}: {reason}")
        await ctx.reply(embed=discord.Embed(
            description=f"**{channel.mention}** unlocked.", color=0x2B2D31
        ))

    @app_commands.command(name="unlock", description="Unlock a channel")
    @is_mod_or_owner()
    async def slash_unlock(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        ch = channel or interaction.channel
        await _toggle_lock(ch, False, "Unlocked")
        await interaction.response.send_message(f"{ch.mention} unlocked.", ephemeral=True)

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def lockdown(self, ctx, *, reason="Emergency lockdown"):
        msg = await ctx.reply("Initiating server lockdown...")
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
        await log_event(self.bot, ctx.guild, "lockdown", f"Server locked by {ctx.author}. {reason}")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def unlockdown(self, ctx):
        msg = await ctx.reply("Lifting lockdown...")
        count = 0
        for ch in ctx.guild.channels:
            if isinstance(ch, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel)):
                try:
                    await _toggle_lock(ch, False, "Lockdown lifted")
                    count += 1
                except:
                    pass
        await msg.edit(content=None, embed=discord.Embed(
            description=f"Lockdown lifted. {count} channels unlocked.", color=0x2B2D31
        ))

    # ── VCLock ────────────────────────────────────────────────────────────────
    @commands.command(aliases=["vlock"])
    @ctx_mod()
    async def vclock(self, ctx, channel: discord.VoiceChannel = None):
        if not channel:
            if ctx.author.voice:
                channel = ctx.author.voice.channel
            else:
                return await ctx.reply("Join a voice channel or mention one.")
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.connect = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.reply(f"Voice channel **{channel.name}** locked.")

    @commands.command(aliases=["vunlock"])
    @ctx_mod()
    async def vcunlock(self, ctx, channel: discord.VoiceChannel = None):
        if not channel:
            if ctx.author.voice:
                channel = ctx.author.voice.channel
            else:
                return await ctx.reply("Join a voice channel or mention one.")
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.connect = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.reply(f"Voice channel **{channel.name}** unlocked.")

    # ── Purge ─────────────────────────────────────────────────────────────────
    @commands.command(aliases=["clear", "c"])
    @ctx_mod()
    async def purge(self, ctx, target: str = None, limit: int = None):
        if not target:
            embed = discord.Embed(title="Purge", color=0x2B2D31, description=(
                "`,purge <amount>` — recent messages\n"
                "`,purge bots <amount>` — bot messages only\n"
                "`,purge @user <amount>` — specific user\n"
                "`,purge links <amount>` — messages with links\n"
                "`,purge images <amount>` — messages with images"
            ))
            return await ctx.reply(embed=embed)

        try:
            await ctx.message.delete()
        except:
            pass

        deleted = 0
        tl = target.lower()

        if tl == "bots":
            msgs = await ctx.channel.purge(limit=limit or 50, check=lambda m: m.author.bot)
            deleted = len(msgs)
        elif tl == "links":
            msgs = await ctx.channel.purge(limit=limit or 50, check=lambda m: "http" in m.content)
            deleted = len(msgs)
        elif tl == "images":
            msgs = await ctx.channel.purge(limit=limit or 50, check=lambda m: bool(m.attachments))
            deleted = len(msgs)
        elif target.startswith("<@"):
            uid = int(re.sub(r"[<@!>]", "", target))
            msgs = await ctx.channel.purge(limit=limit or 50, check=lambda m, u=uid: m.author.id == u)
            deleted = len(msgs)
        else:
            try:
                amount = int(target)
                msgs   = await ctx.channel.purge(limit=amount)
                deleted = len(msgs)
            except ValueError:
                return await ctx.send("Invalid format. Use `,purge` for help.", delete_after=5)

        await ctx.send(embed=discord.Embed(
            description=f"Purged **{deleted}** message(s).", color=0x2B2D31
        ), delete_after=4)

    @app_commands.command(name="clear", description="Delete messages in bulk")
    @is_mod_or_owner()
    async def slash_clear(self, interaction: discord.Interaction, amount: int):
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"Deleted **{len(deleted)}** messages.", ephemeral=True)

    # ── Mass Role ─────────────────────────────────────────────────────────────
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def massrole(self, ctx, action: str = None, target: str = None, role: discord.Role = None):
        """
        ,massrole add @everyone @Role    — give role to all humans
        ,massrole add bots @Role         — give role to all bots
        ,massrole remove @everyone @Role
        """
        if not action or action.lower() not in ("add", "remove") or not role:
            return await ctx.reply(
                "Usage:\n"
                "`,massrole add @everyone @Role` — All members\n"
                "`,massrole add bots @Role` — All bots\n"
                "`,massrole remove @everyone @Role` — Remove from all"
            )

        if ctx.guild.me.top_role <= role:
            return await ctx.reply("My role is below that role. Move my role higher first.")

        # Determine target list
        tl = (target or "").lower().replace("<@&","").replace(">","").strip()
        if tl in ("bots", "bot"):
            members = [m for m in ctx.guild.members if m.bot]
            label   = "bots"
        else:
            members = [m for m in ctx.guild.members if not m.bot]
            label   = "members"

        msg   = await ctx.reply(f"Processing **{len(members)}** {label}... this may take a while.")
        count = 0

        for member in members:
            try:
                if action.lower() == "add" and role not in member.roles:
                    await member.add_roles(role, reason=f"Massrole by {ctx.author}")
                    count += 1
                elif action.lower() == "remove" and role in member.roles:
                    await member.remove_roles(role, reason=f"Massrole by {ctx.author}")
                    count += 1
                await asyncio.sleep(0.35)  # Discord rate limit safety
            except discord.Forbidden:
                continue

        await msg.edit(content=None, embed=discord.Embed(
            description=f"**{action.capitalize()}d** {role.mention} for **{count}** {label}.",
            color=0x2B2D31
        ))
        await log_event(self.bot, ctx.guild, "massrole", f"{action} {role} for {count} {label} by {ctx.author}.")

    # ── Jail system ───────────────────────────────────────────────────────────
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def jailsetup(self, ctx):
        jailed = discord.utils.get(ctx.guild.roles, name="Jailed")
        if not jailed:
            jailed = await ctx.guild.create_role(name="Jailed", color=discord.Color.dark_gray())

        for ch in ctx.guild.channels:
            try:
                await ch.set_permissions(jailed, view_channel=False, send_messages=False)
            except:
                pass

        jail_ch = discord.utils.get(ctx.guild.channels, name="jail")
        if not jail_ch:
            ow = {
                ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                jailed: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                ctx.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            }
            jail_ch = await ctx.guild.create_text_channel("jail", overwrites=ow)

        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"jail_role_id": jailed.id, "jail_channel_id": jail_ch.id}},
            upsert=True
        )
        await ctx.reply(f"Jail system ready. Role: {jailed.mention} | Channel: {jail_ch.mention}")

    @commands.command()
    @ctx_mod()
    async def jail(self, ctx, member: discord.Member = None, *, reason="No reason provided"):
        if not member:
            return await ctx.reply("Mention a member to jail.")
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)})
        if not gs or not gs.get("jail_role_id"):
            return await ctx.reply("Jail system not set up. Run `,jailsetup` first.")
        jailed_role = ctx.guild.get_role(gs["jail_role_id"])
        if not jailed_role:
            return await ctx.reply("Jailed role missing. Run `,jailsetup` again.")
        old_roles = [r.id for r in member.roles if r != ctx.guild.default_role]
        await jail_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(member.id)},
            {"$set": {"old_roles": old_roles}}, upsert=True
        )
        await member.edit(roles=[jailed_role], reason=reason)
        await ctx.reply(embed=discord.Embed(
            description=f"**{member}** sent to jail.\nReason: {reason}", color=0x2B2D31
        ))
        await log_event(self.bot, ctx.guild, "jail", f"{member} jailed by {ctx.author}.")

    @commands.command()
    @ctx_mod()
    async def unjail(self, ctx, member: discord.Member = None):
        if not member:
            return await ctx.reply("Mention a member to unjail.")
        doc = await jail_col.find_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
        if not doc:
            return await ctx.reply(f"**{member}** is not jailed.")
        roles = [ctx.guild.get_role(rid) for rid in doc.get("old_roles", [])
                 if ctx.guild.get_role(rid)]
        await member.edit(roles=roles)
        await jail_col.delete_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
        await ctx.reply(f"**{member}** has been released.")

    # ── Mute setup ────────────────────────────────────────────────────────────
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setupmute(self, ctx):
        msg = await ctx.reply("Setting up mute roles...")
        guild = ctx.guild
        role_defs = {
            "Muted":          discord.Color.dark_grey(),
            "Image Muted":    discord.Color.blue(),
            "Reaction Muted": discord.Color.orange(),
        }
        role_objs = {}
        for name, color in role_defs.items():
            role = discord.utils.get(guild.roles, name=name) or \
                   await guild.create_role(name=name, color=color)
            role_objs[name] = role

        text_count = 0
        for ch in guild.channels:
            try:
                if isinstance(ch, discord.TextChannel):
                    await ch.set_permissions(role_objs["Muted"],          send_messages=False, add_reactions=False)
                    await ch.set_permissions(role_objs["Image Muted"],    attach_files=False, embed_links=False)
                    await ch.set_permissions(role_objs["Reaction Muted"], add_reactions=False)
                    text_count += 1
                elif isinstance(ch, discord.VoiceChannel):
                    await ch.set_permissions(role_objs["Muted"], speak=False, send_messages=False)
            except:
                pass

        await msg.edit(content=None, embed=discord.Embed(
            title="Mute System Ready",
            description=f"3 roles created/verified. {text_count} text channels configured.",
            color=0x2B2D31
        ))


async def setup(bot):
    await bot.add_cog(Moderation(bot))
