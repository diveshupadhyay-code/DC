import asyncio
import time
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils.helpers import log_event
from utils import secui
from utils.secui import antinuke_col

WINDOW_SECONDS = 15
GLOBAL_BURST_LIMIT = 5
PUNISH_COOLDOWN = 30

DEFAULT_THRESHOLDS = {
    "channel_delete": 2,
    "channel_create": 4,
    "role_delete": 2,
    "role_create": 4,
    "ban": 3,
    "kick": 3,
    "webhook_create": 2,
}

ACTION_LABELS = {
    "channel_delete": "Channel delete",
    "channel_create": "Channel create",
    "role_delete": "Role delete",
    "role_create": "Role create",
    "ban": "Ban",
    "kick": "Kick",
    "webhook_create": "Webhook create",
}

DANGEROUS = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "manage_webhooks",
    "ban_members",
    "kick_members",
    "mention_everyone",
)

PUNISHMENTS = {
    "strip": ("Remove roles", "Takes away every role they have"),
    "kick": ("Remove roles and kick", "Strips roles, then kicks them out"),
    "ban": ("Ban", "Bans them, even if they already left"),
}

PUNISH_TEXT = {key: value[0] for key, value in PUNISHMENTS.items()}

_action_log = {}
_recent_punish = {}


def _track(guild_id, user_id, action):
    now = datetime.now(timezone.utc).timestamp()
    log = _action_log.setdefault((guild_id, user_id), {})
    mine = [t for t in log.get(action, []) if now - t < WINDOW_SECONDS]
    mine.append(now)
    log[action] = mine
    total = sum(1 for times in log.values() for t in times if now - t < WINDOW_SECONDS)
    return len(mine), total


def _reset(guild_id, user_id):
    _action_log.pop((guild_id, user_id), None)


def _risky(role):
    return any(getattr(role.permissions, name) for name in DANGEROUS)


class AntiNuke(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._seen = set()

    async def _cfg(self, guild_id):
        return await antinuke_col.find_one({"guild_id": str(guild_id)}) or {}

    def _remember(self, entry_id):
        if len(self._seen) > 3000:
            self._seen.clear()
        self._seen.add(entry_id)

    async def _find_executor(self, guild, action, target_id=None, attempts=3):
        if guild.me is None or not guild.me.guild_permissions.view_audit_log:
            return None
        for attempt in range(attempts):
            try:
                async for entry in guild.audit_logs(limit=6, action=action):
                    if entry.id in self._seen:
                        continue
                    age = (datetime.now(timezone.utc) - entry.created_at).total_seconds()
                    if age > 10:
                        continue
                    if target_id is not None and getattr(entry.target, "id", None) != target_id:
                        continue
                    self._remember(entry.id)
                    return entry.user
            except discord.HTTPException:
                return None
            if attempt < attempts - 1:
                await asyncio.sleep(0.8)
        return None

    async def _punish(self, guild, actor_id, cfg, reason):
        key = (guild.id, actor_id)
        now = time.monotonic()
        if now - _recent_punish.get(key, 0) < PUNISH_COOLDOWN:
            return
        _recent_punish[key] = now

        mode = cfg.get("punishment", "strip")
        member = guild.get_member(actor_id)
        me = guild.me
        stripped = banned = kicked = False

        if member is not None:
            roles = [
                r for r in member.roles
                if not r.is_default() and not r.managed and r < me.top_role
            ]
            if roles:
                try:
                    await member.remove_roles(*roles, reason=f"Anti-Nuke: {reason}")
                    stripped = True
                except discord.HTTPException:
                    pass

        if member is None or mode == "ban":
            banned = await secui.ban_id(guild, actor_id, f"Anti-Nuke: {reason}")
        elif mode == "kick":
            try:
                await member.kick(reason=f"Anti-Nuke: {reason}")
                kicked = True
            except discord.HTTPException:
                pass

        taken = []
        if stripped:
            taken.append("Roles removed")
        if kicked:
            taken.append("Kicked")
        if banned:
            taken.append("Banned" if member else "Banned (had already left)")
        success = bool(taken)

        embed = secui.make("🛑  Anti-Nuke stopped an attack", color=secui.ALERT)
        if member is not None:
            embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Person", value=f"<@{actor_id}>\n`{actor_id}`", inline=True)
        embed.add_field(
            name="What I did",
            value="\n".join(f"• {t}" for t in taken) if success else "Nothing went through",
            inline=True,
        )
        embed.add_field(name="Why", value=reason[:1000], inline=False)
        if not success:
            embed.add_field(
                name="How to fix",
                value="Move my role above theirs and give me Manage Roles and Ban Members.",
                inline=False,
            )
        secui.footer(embed, guild)

        await secui.post(guild, cfg.get("log_channel_id"), embed)
        await log_event(self.bot, guild, "antinuke", f"{actor_id} - {reason} - {mode}")

    async def _watch(self, guild, enum, key, reason, target_id=None):
        cfg = await self._cfg(guild.id)
        if not cfg.get("enabled"):
            return
        actor = await self._find_executor(guild, enum, target_id)
        if actor is None or actor.id == self.bot.user.id:
            return
        if await secui.is_trusted(self.bot, guild, actor.id):
            return
        limit = cfg.get("thresholds", {}).get(key, DEFAULT_THRESHOLDS[key])
        count, total = _track(guild.id, actor.id, key)
        if count >= limit:
            _reset(guild.id, actor.id)
            await self._punish(guild, actor.id, cfg, f"{reason}  ({count} in {WINDOW_SECONDS}s)")
        elif total >= GLOBAL_BURST_LIMIT:
            _reset(guild.id, actor.id)
            await self._punish(guild, actor.id, cfg, f"Too many mixed actions  ({total} in {WINDOW_SECONDS}s)")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        await self._watch(
            channel.guild, discord.AuditLogAction.channel_delete, "channel_delete",
            f"Deleted channel #{channel.name}", channel.id,
        )

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        await self._watch(
            channel.guild, discord.AuditLogAction.channel_create, "channel_create",
            f"Created channel #{channel.name}", channel.id,
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        await self._watch(
            role.guild, discord.AuditLogAction.role_delete, "role_delete",
            f"Deleted role @{role.name}", role.id,
        )

    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        await self._watch(
            role.guild, discord.AuditLogAction.role_create, "role_create",
            f"Created role @{role.name}", role.id,
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        await self._watch(
            guild, discord.AuditLogAction.ban, "ban", f"Banned {user}", user.id,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        await self._watch(
            member.guild, discord.AuditLogAction.kick, "kick", f"Kicked {member}", member.id,
        )

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel):
        await self._watch(
            channel.guild, discord.AuditLogAction.webhook_create, "webhook_create",
            f"Created a webhook in #{channel.name}",
        )

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if not member.bot:
            return
        guild = member.guild
        cfg = await self._cfg(guild.id)
        if not cfg.get("enabled") or not cfg.get("bot_guard", True):
            return
        if str(member.id) in cfg.get("allowed_bots", []):
            return
        adder = await self._find_executor(guild, discord.AuditLogAction.bot_add, member.id)
        if adder is not None and await secui.is_trusted(self.bot, guild, adder.id):
            return

        removed = False
        try:
            await member.kick(reason="Anti-Nuke: bot was not approved")
            removed = True
        except discord.HTTPException:
            pass

        embed = secui.make("🤖  Unapproved bot removed", color=secui.ALERT)
        embed.add_field(name="Bot", value=f"{member.mention}\n`{member.id}`", inline=True)
        embed.add_field(
            name="Added by",
            value=f"<@{adder.id}>\n`{adder.id}`" if adder else "Could not find out",
            inline=True,
        )
        embed.add_field(name="Result", value="✅ Kicked out" if removed else "⚠️ I could not kick it", inline=True)
        embed.add_field(
            name="Want this bot?",
            value=f"Run `antinuke bots allow {member.id}` and add it again.",
            inline=False,
        )
        secui.footer(embed, guild)
        await secui.post(guild, cfg.get("log_channel_id"), embed)

        if adder is not None:
            await self._punish(guild, adder.id, cfg, f"Added an unapproved bot: {member}")

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        gained = [
            name for name in DANGEROUS
            if getattr(after.permissions, name) and not getattr(before.permissions, name)
        ]
        if not gained:
            return
        guild = after.guild
        cfg = await self._cfg(guild.id)
        if not cfg.get("enabled") or not cfg.get("perm_guard", True):
            return
        actor = await self._find_executor(guild, discord.AuditLogAction.role_update, after.id)
        if actor is None or actor.id == self.bot.user.id:
            return
        if await secui.is_trusted(self.bot, guild, actor.id):
            return
        try:
            await after.edit(permissions=before.permissions, reason="Anti-Nuke: power was added without approval")
        except discord.HTTPException:
            pass
        names = ", ".join(n.replace("_", " ").title() for n in gained)
        await self._punish(guild, actor.id, cfg, f"Gave @{after.name} powerful permissions: {names}")

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.roles == after.roles:
            return
        added = [r for r in after.roles if r not in before.roles and _risky(r)]
        if not added:
            return
        guild = after.guild
        cfg = await self._cfg(guild.id)
        if not cfg.get("enabled") or not cfg.get("perm_guard", True):
            return
        actor = await self._find_executor(guild, discord.AuditLogAction.member_role_update, after.id)
        if actor is None or actor.id == self.bot.user.id:
            return
        if await secui.is_trusted(self.bot, guild, actor.id):
            return
        removable = [r for r in added if r < guild.me.top_role]
        if removable:
            try:
                await after.remove_roles(*removable, reason="Anti-Nuke: powerful role was given without approval")
            except discord.HTTPException:
                pass
        names = ", ".join(f"@{r.name}" for r in added)
        await self._punish(guild, actor.id, cfg, f"Gave {after} a powerful role: {names}")

    async def _panel_embed(self, guild):
        cfg = await self._cfg(guild.id)
        enabled = cfg.get("enabled", False)
        mode = cfg.get("punishment", "strip")
        log_id = cfg.get("log_channel_id")
        limits = cfg.get("thresholds", {})

        embed = secui.make(
            "🛡️  Anti-Nuke",
            "Stops anyone from wrecking the server by deleting, banning or handing out power too fast.",
            secui.ALERT if enabled else secui.NEUTRAL,
        )
        embed.add_field(name="Status", value=secui.state(enabled), inline=True)
        embed.add_field(name="Punishment", value=PUNISH_TEXT.get(mode, mode), inline=True)
        embed.add_field(name="Log channel", value=f"<#{log_id}>" if log_id else "Not set", inline=True)
        embed.add_field(name="Bot Guard", value=secui.state(cfg.get("bot_guard", True)), inline=True)
        embed.add_field(name="Permission Guard", value=secui.state(cfg.get("perm_guard", True)), inline=True)
        embed.add_field(name="Trusted people", value=str(len(cfg.get("whitelist", []))), inline=True)

        items = [
            f"{label}  **{limits.get(key, DEFAULT_THRESHOLDS[key])}**"
            for key, label in ACTION_LABELS.items()
        ]
        rows = ["  ·  ".join(items[i:i + 2]) for i in range(0, len(items), 2)]
        embed.add_field(name=f"Limits in {WINDOW_SECONDS} seconds", value="\n".join(rows), inline=False)
        embed.add_field(
            name="My access",
            value=secui.access_line(guild, "view_audit_log", "ban_members", "kick_members", "manage_roles"),
            inline=False,
        )
        return secui.footer(embed, guild)

    async def _open_panel(self, ctx):
        cfg = await self._cfg(ctx.guild.id)
        gid = ctx.guild.id
        panel = secui.Panel(ctx.author.id, self._panel_embed)
        panel.add_item(secui.Toggle(antinuke_col, gid, "enabled", "Anti-Nuke", cfg.get("enabled", False), False, 0))
        panel.add_item(secui.Toggle(antinuke_col, gid, "bot_guard", "Bot Guard", cfg.get("bot_guard", True), True, 0))
        panel.add_item(secui.Toggle(antinuke_col, gid, "perm_guard", "Permission Guard", cfg.get("perm_guard", True), True, 0))
        panel.add_item(
            secui.Choice(
                antinuke_col, gid, "punishment", "Choose the punishment",
                [(k, v[0], v[1]) for k, v in PUNISHMENTS.items()],
                cfg.get("punishment", "strip"), 1,
            )
        )
        panel.message = await secui.send(ctx, await self._panel_embed(ctx.guild), view=panel)

    @commands.group(name="antinuke", aliases=["an"], invoke_without_command=True)
    @secui.trusted_only()
    async def antinuke(self, ctx):
        await self._open_panel(ctx)

    @antinuke.command(name="enable")
    @secui.trusted_only()
    async def an_enable(self, ctx):
        await antinuke_col.update_one({"guild_id": str(ctx.guild.id)}, {"$set": {"enabled": True}}, upsert=True)
        await secui.send(ctx, secui.ok("Anti-Nuke is now **on**."))

    @antinuke.command(name="disable")
    @secui.trusted_only()
    async def an_disable(self, ctx):
        await antinuke_col.update_one({"guild_id": str(ctx.guild.id)}, {"$set": {"enabled": False}}, upsert=True)
        await secui.send(ctx, secui.ok("Anti-Nuke is now **off**."))

    @antinuke.group(name="whitelist", aliases=["wl"], invoke_without_command=True)
    @secui.trusted_only()
    async def an_whitelist(self, ctx):
        cfg = await self._cfg(ctx.guild.id)
        people = cfg.get("whitelist", [])
        embed = secui.make("Trusted people", color=secui.ALERT)
        embed.description = "\n".join(f"• <@{u}>  `{u}`" for u in people[:40]) or "Nobody yet. The server owner is always trusted."
        embed.set_footer(text="Trusted people are never stopped by Anti-Nuke, Anti-Raid or Auto-Mod")
        await secui.send(ctx, embed)

    @an_whitelist.command(name="add")
    @secui.trusted_only()
    async def an_whitelist_add(self, ctx, member: discord.Member = None):
        if member is None:
            return await secui.send(ctx, secui.fail(f"Use `{ctx.clean_prefix}antinuke whitelist add @member`."))
        await antinuke_col.update_one(
            {"guild_id": str(ctx.guild.id)}, {"$addToSet": {"whitelist": str(member.id)}}, upsert=True
        )
        secui.forget_trust(ctx.guild.id)
        await secui.send(ctx, secui.ok(f"{member.mention} is now trusted."))

    @an_whitelist.command(name="remove")
    @secui.trusted_only()
    async def an_whitelist_remove(self, ctx, member: discord.Member = None):
        if member is None:
            return await secui.send(ctx, secui.fail(f"Use `{ctx.clean_prefix}antinuke whitelist remove @member`."))
        await antinuke_col.update_one(
            {"guild_id": str(ctx.guild.id)}, {"$pull": {"whitelist": str(member.id)}}
        )
        secui.forget_trust(ctx.guild.id)
        await secui.send(ctx, secui.ok(f"{member.mention} is no longer trusted."))

    @antinuke.command(name="punishment")
    @secui.trusted_only()
    async def an_punishment(self, ctx, mode: str = None):
        if mode is None or mode.lower() not in PUNISHMENTS:
            return await secui.send(ctx, secui.fail(f"Use `{ctx.clean_prefix}antinuke punishment strip`, `kick` or `ban`."))
        await antinuke_col.update_one(
            {"guild_id": str(ctx.guild.id)}, {"$set": {"punishment": mode.lower()}}, upsert=True
        )
        await secui.send(ctx, secui.ok(f"Punishment is now **{PUNISH_TEXT[mode.lower()]}**."))

    @antinuke.command(name="logs")
    @secui.trusted_only()
    async def an_logs(self, ctx, channel: discord.TextChannel = None):
        if channel is None:
            return await secui.send(ctx, secui.fail(f"Use `{ctx.clean_prefix}antinuke logs #channel`."))
        await antinuke_col.update_one(
            {"guild_id": str(ctx.guild.id)}, {"$set": {"log_channel_id": str(channel.id)}}, upsert=True
        )
        await secui.send(ctx, secui.ok(f"Alerts will be sent to {channel.mention}."))

    @antinuke.command(name="limit")
    @secui.trusted_only()
    async def an_limit(self, ctx, action: str = None, amount: int = None):
        if action not in DEFAULT_THRESHOLDS or amount is None or not 1 <= amount <= 20:
            names = ", ".join(f"`{k}`" for k in DEFAULT_THRESHOLDS)
            return await secui.send(
                ctx,
                secui.fail(f"Use `{ctx.clean_prefix}antinuke limit <action> <1-20>`.\nActions: {names}"),
            )
        await antinuke_col.update_one(
            {"guild_id": str(ctx.guild.id)}, {"$set": {f"thresholds.{action}": amount}}, upsert=True
        )
        await secui.send(ctx, secui.ok(f"**{ACTION_LABELS[action]}** limit is now **{amount}** in {WINDOW_SECONDS} seconds."))

    @antinuke.group(name="bots", invoke_without_command=True)
    @secui.trusted_only()
    async def an_bots(self, ctx):
        cfg = await self._cfg(ctx.guild.id)
        allowed = cfg.get("allowed_bots", [])
        embed = secui.make("Approved bots", color=secui.ALERT)
        embed.description = "\n".join(f"• <@{b}>  `{b}`" for b in allowed) or "No bots approved yet."
        embed.add_field(
            name="How it works",
            value=(
                "A bot is kept only if a trusted person added it, or if you approved it here.\n"
                f"`{ctx.clean_prefix}antinuke bots allow <bot id>`\n"
                f"`{ctx.clean_prefix}antinuke bots deny <bot id>`"
            ),
            inline=False,
        )
        await secui.send(ctx, embed)

    @an_bots.command(name="allow")
    @secui.trusted_only()
    async def an_bots_allow(self, ctx, bot_id: int = None):
        if bot_id is None:
            return await secui.send(ctx, secui.fail(f"Use `{ctx.clean_prefix}antinuke bots allow <bot id>`."))
        await antinuke_col.update_one(
            {"guild_id": str(ctx.guild.id)}, {"$addToSet": {"allowed_bots": str(bot_id)}}, upsert=True
        )
        await secui.send(ctx, secui.ok(f"Bot `{bot_id}` is approved. You can add it now."))

    @an_bots.command(name="deny")
    @secui.trusted_only()
    async def an_bots_deny(self, ctx, bot_id: int = None):
        if bot_id is None:
            return await secui.send(ctx, secui.fail(f"Use `{ctx.clean_prefix}antinuke bots deny <bot id>`."))
        await antinuke_col.update_one(
            {"guild_id": str(ctx.guild.id)}, {"$pull": {"allowed_bots": str(bot_id)}}
        )
        await secui.send(ctx, secui.ok(f"Bot `{bot_id}` is no longer approved."))


async def setup(bot):
    await bot.add_cog(AntiNuke(bot))