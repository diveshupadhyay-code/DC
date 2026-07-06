import discord
from discord.ext import commands
from datetime import datetime, timezone
import asyncio

from utils.db import db
from utils.helpers import BOT_OWNER_ID, ctx_admin, log_event

antinuke_col = db["antinuke_config"]

WINDOW_SECONDS = 15
GLOBAL_BURST_LIMIT = 6  
DARK_RED = 0x7A0010     

DEFAULT_THRESHOLDS = {
    "channel_delete": 3,
    "channel_create": 5,
    "role_delete":    3,
    "role_create":    5,
    "ban":            3,
    "kick":           5,
    "webhook_create": 3,
}

ACTION_LABELS = {
    "channel_delete": "Channel Delete",
    "channel_create": "Channel Create",
    "role_delete":    "Role Delete",
    "role_create":    "Role Create",
    "ban":            "Ban",
    "kick":           "Kick",
    "webhook_create": "Webhook Create",
}

_action_log: dict = {}


def _track_advanced(guild_id: int, user_id: int, action: str) -> tuple[int, int]:
    now = datetime.now(timezone.utc).timestamp()
    key = (guild_id, user_id)
    
    if key not in _action_log:
        _action_log[key] = {"global": []}
        
    if action not in _action_log[key]:
        _action_log[key][action] = []
        
    _action_log[key][action] = [t for t in _action_log[key][action] if now - t < WINDOW_SECONDS]
    _action_log[key]["global"] = [t for t in _action_log[key]["global"] if now - t < WINDOW_SECONDS]
    
    _action_log[key][action].append(now)
    _action_log[key]["global"].append(now)
    
    return len(_action_log[key][action]), len(_action_log[key]["global"])


def _reset_user(guild_id: int, user_id: int):
    _action_log.pop((guild_id, user_id), None)


class AntiNuke(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _cfg(self, guild_id: int) -> dict:
        return await antinuke_col.find_one({"guild_id": str(guild_id)}) or {}

    async def _is_whitelisted(self, guild: discord.Guild, user_id: int, cfg: dict) -> bool:
        if user_id in (BOT_OWNER_ID, guild.owner_id, self.bot.user.id):
            return True
        return str(user_id) in cfg.get("whitelist", [])

    async def _get_executor(self, guild: discord.Guild, action, target_id: int = None):
        if not guild.me.guild_permissions.view_audit_log:
            return None
        try:
            async for entry in guild.audit_logs(limit=3, action=action):
                age = (datetime.now(timezone.utc) - entry.created_at).total_seconds()
                if age > 8: 
                    continue
                if target_id is None or (entry.target and getattr(entry.target, "id", None) == target_id):
                    return entry.user
        except discord.Forbidden:
            return None
        return None

    async def _punish(self, guild: discord.Guild, member: discord.Member, cfg: dict, reason: str):
        punishment = cfg.get("punishment", "strip")
        execution_success = False
        
        try:
            roles_to_strip = [r for r in member.roles if r != guild.default_role and not r.managed]
            if roles_to_strip:
                await member.remove_roles(*roles_to_strip, reason=f"Anti-Nuke: {reason}")
                execution_success = True
        except discord.Forbidden:
            pass

        try:
            if punishment == "ban":
                await guild.ban(member, reason=f"Anti-Nuke: {reason}", delete_message_days=1)
                execution_success = True
            elif punishment == "kick":
                await guild.kick(member, reason=f"Anti-Nuke: {reason}")
                execution_success = True
        except discord.Forbidden:
            pass

        embed = discord.Embed(
            title="🛑 SYSTEM TRIGGERED",
            description=f"Action taken immediately.",
            color=DARK_RED,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User", value=f"{member.mention}\n`{member.id}`", inline=True)
        embed.add_field(name="Punishment", value=f"`{punishment.upper()}`", inline=True)
        embed.add_field(name="Reason", value=f"```{reason}```", inline=False)
        embed.add_field(name="Status", value="✅ Done" if execution_success else "⚠️ Failed", inline=False)

        log_ch_id = cfg.get("log_channel_id")
        if log_ch_id:
            ch = guild.get_channel(int(log_ch_id))
            if ch:
                try:
                    await ch.send(embed=embed)
                except:
                    pass
        await log_event(self.bot, guild, "antinuke", f"{member} - {reason} - {punishment}")

    async def _check(self, guild: discord.Guild, actor, action_key: str, reason: str):
        if actor is None or actor.id == self.bot.user.id:
            return
        cfg = await self._cfg(guild.id)
        if not cfg.get("enabled"):
            return
        if await self._is_whitelisted(guild, actor.id, cfg):
            return

        member = guild.get_member(actor.id)
        if not member:
            return

        thresholds = cfg.get("thresholds", {})
        limit = thresholds.get(action_key, DEFAULT_THRESHOLDS.get(action_key, 3))
        
        action_count, global_count = _track_advanced(guild.id, actor.id, action_key)

        if action_count >= limit:
            _reset_user(guild.id, actor.id)
            await self._punish(guild, member, cfg, f"{reason} ({action_count}/{WINDOW_SECONDS}s)")
        elif global_count >= GLOBAL_BURST_LIMIT:
            _reset_user(guild.id, actor.id)
            await self._punish(guild, member, cfg, f"Too many mixed actions ({global_count}).")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        executor = await self._get_executor(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
        if executor:
            await self._check(channel.guild, executor, "channel_delete", f"Deleted channel: #{channel.name}")

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        executor = await self._get_executor(channel.guild, discord.AuditLogAction.channel_create, channel.id)
        if executor:
            await self._check(channel.guild, executor, "channel_create", f"Created channel: #{channel.name}")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        executor = await self._get_executor(role.guild, discord.AuditLogAction.role_delete, role.id)
        if executor:
            await self._check(role.guild, executor, "role_delete", f"Deleted role: @{role.name}")

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        executor = await self._get_executor(role.guild, discord.AuditLogAction.role_create, role.id)
        if executor:
            await self._check(role.guild, executor, "role_create", f"Created role: @{role.name}")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        executor = await self._get_executor(guild, discord.AuditLogAction.ban, user.id)
        if executor:
            await self._check(guild, executor, "ban", f"Banned member: {user.name}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        executor = await self._get_executor(member.guild, discord.AuditLogAction.kick, member.id)
        if executor:
            await self._check(member.guild, executor, "kick", f"Kicked member: {member.name}")

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel):
        executor = await self._get_executor(channel.guild, discord.AuditLogAction.webhook_create)
        if executor:
            await self._check(channel.guild, executor, "webhook_create", f"Created webhook in #{channel.name}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not member.bot:
            return
        cfg = await self._cfg(member.guild.id)
        if not cfg.get("enabled"):
            return
        executor = await self._get_executor(member.guild, discord.AuditLogAction.bot_add, member.id)
        if not executor or await self._is_whitelisted(member.guild, executor.id, cfg):
            return
        
        try:
            await member.guild.kick(member, reason="Anti-Nuke: Unauthorized bot.")
        except discord.Forbidden:
            pass
        inviter = member.guild.get_member(executor.id)
        if inviter:
            await self._punish(member.guild, inviter, cfg, f"Added unauthorized bot: {member.name}")

    @commands.group(name="antinuke", aliases=["an"], invoke_without_command=True)
    @ctx_admin()
    async def antinuke(self, ctx):
        cfg        = await self._cfg(ctx.guild.id)
        enabled    = cfg.get("enabled", False)
        punishment = cfg.get("punishment", "strip")
        whitelist  = cfg.get("whitelist", [])
        log_ch_id  = cfg.get("log_channel_id")
        log_ch     = ctx.guild.get_channel(int(log_ch_id)) if log_ch_id else None

        embed = discord.Embed(
            title="🛡️ Control Matrix",
            color=DARK_RED if enabled else 0x2B2D31,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Status", value="🟢 Enabled" if enabled else "🔴 Disabled", inline=True)
        embed.add_field(name="Punishment", value=f"`{punishment.upper()}`", inline=True)
        embed.add_field(name="Log Channel", value=log_ch.mention if log_ch else "Not Set", inline=True)
        embed.add_field(
            name=f"Whitelist ({len(whitelist)})",
            value=" ".join(f"<@{u}>" for u in whitelist[:10]) or "None",
            inline=False
        )

        thresholds = cfg.get("thresholds", {})
        lines = [
            f"⚡ `{ACTION_LABELS[key]}` ➔ **{thresholds.get(key, default)}** / {WINDOW_SECONDS}s"
            for key, default in DEFAULT_THRESHOLDS.items()
        ]
        lines.append(f"🚨 `Global Limit` ➔ **{GLOBAL_BURST_LIMIT}** / {WINDOW_SECONDS}s")
        
        embed.add_field(name="Thresholds", value="\n".join(lines), inline=False)
        await ctx.reply(embed=embed)

    @antinuke.command(name="enable")
    @ctx_admin()
    async def an_enable(self, ctx):
        await antinuke_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"enabled": True}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(description="🛡️ Anti-Nuke **enabled**.", color=DARK_RED))

    @antinuke.command(name="disable")
    @ctx_admin()
    async def an_disable(self, ctx):
        await antinuke_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"enabled": False}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(description="⚠️ Anti-Nuke **disabled**.", color=0x2B2D31))

    @antinuke.group(name="whitelist", aliases=["wl"], invoke_without_command=True)
    @ctx_admin()
    async def an_whitelist(self, ctx):
        cfg       = await self._cfg(ctx.guild.id)
        whitelist = cfg.get("whitelist", [])
        embed = discord.Embed(title="Whitelist", color=DARK_RED)
        embed.description = "\n".join(f"▫️ <@{u}> (`{u}`)" for u in whitelist) or "None"
        await ctx.reply(embed=embed)

    @an_whitelist.command(name="add")
    @ctx_admin()
    async def an_whitelist_add(self, ctx, member: discord.Member = None):
        if not member:
            return await ctx.reply("Usage: `,antinuke whitelist add @member`")
        await antinuke_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$addToSet": {"whitelist": str(member.id)}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(description=f"✅ {member.mention} whitelisted.", color=DARK_RED))

    @an_whitelist.command(name="remove")
    @ctx_admin()
    async def an_whitelist_remove(self, ctx, member: discord.Member = None):
        if not member:
            return await ctx.reply("Usage: `,antinuke whitelist remove @member`")
        await antinuke_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$pull": {"whitelist": str(member.id)}}
        )
        await ctx.reply(embed=discord.Embed(description=f"❌ {member.mention} removed.", color=DARK_RED))

    @antinuke.command(name="punishment")
    @ctx_admin()
    async def an_punishment(self, ctx, mode: str = None):
        if not mode or mode.lower() not in ("strip", "kick", "ban"):
            return await ctx.reply("Usage: `,antinuke punishment strip/kick/ban`")
        await antinuke_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"punishment": mode.lower()}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(description=f"⚙️ Punishment set to **{mode.upper()}**.", color=DARK_RED))

    @antinuke.command(name="logs")
    @ctx_admin()
    async def an_logs(self, ctx, channel: discord.TextChannel = None):
        if not channel:
            return await ctx.reply("Usage: `,antinuke logs #channel`")
        await antinuke_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"log_channel_id": str(channel.id)}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(description=f"🛰️ Logs set to {channel.mention}.", color=DARK_RED))


async def setup(bot):
    await bot.add_cog(AntiNuke(bot))