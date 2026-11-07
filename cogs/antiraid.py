import asyncio
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from utils.db import antiraid_col as raid_col
from utils.helpers import log_event
from utils import secui

DEFAULTS = {
    "enabled": False,
    "action": "ban",
    "join_limit": 6,
    "join_window": 10,
    "min_age_days": 3,
    "raid_minutes": 10,
    "lock_on_raid": True,
}

ACTIONS = {
    "ban": ("Ban", "Bans raiders, even if they already left"),
    "kick": ("Kick", "Removes raiders, they can come back later"),
    "timeout": ("Timeout 24 hours", "Mutes raiders for a full day"),
}

ACTION_TEXT = {key: value[0] for key, value in ACTIONS.items()}


class AntiRaid(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._joins = {}
        self._raid_until = {}
        self._stats = {}
        self._tasks = {}

    async def cog_load(self):
        asyncio.create_task(self._resume())

    def cog_unload(self):
        for task in self._tasks.values():
            task.cancel()

    async def _resume(self):
        await self.bot.wait_until_ready()
        async for doc in raid_col.find({"raid_until": {"$exists": True}}):
            gid = int(doc["guild_id"])
            self._raid_until[gid] = doc["raid_until"]
            self._stats.setdefault(gid, 0)
            self._schedule(gid, doc["raid_until"] - time.time())

    async def _cfg(self, guild_id):
        doc = await raid_col.find_one({"guild_id": str(guild_id)}) or {}
        return {**DEFAULTS, **doc}

    def _schedule(self, guild_id, delay):
        old = self._tasks.pop(guild_id, None)
        if old:
            old.cancel()
        self._tasks[guild_id] = asyncio.create_task(self._finish(guild_id, delay))

    async def _remove(self, guild, user_id, action, reason):
        reason = f"Anti-Raid: {reason}"
        if action == "ban":
            return await secui.ban_id(guild, user_id, reason)
        member = guild.get_member(user_id)
        if member is None or not secui.can_act(guild, member):
            return False
        try:
            if action == "kick":
                await member.kick(reason=reason)
            else:
                await member.timeout(timedelta(hours=24), reason=reason)
        except discord.HTTPException:
            return False
        return True

    async def _lock(self, guild):
        done = []
        try:
            await guild.edit(
                verification_level=discord.VerificationLevel.highest,
                reason="Anti-Raid: raid mode",
            )
            done.append("Verification raised to the highest level")
        except (discord.HTTPException, TypeError):
            pass
        try:
            await guild.edit(invites_disabled=True, reason="Anti-Raid: raid mode")
            done.append("Invites paused")
        except (discord.HTTPException, TypeError):
            pass
        return done

    async def _unlock(self, guild, doc):
        level = doc.get("prev_level")
        if level is not None:
            try:
                await guild.edit(
                    verification_level=discord.VerificationLevel(level),
                    reason="Anti-Raid: raid mode ended",
                )
            except (discord.HTTPException, ValueError):
                pass
        if doc.get("prev_invites") is not None:
            try:
                await guild.edit(
                    invites_disabled=bool(doc["prev_invites"]),
                    reason="Anti-Raid: raid mode ended",
                )
            except (discord.HTTPException, TypeError):
                pass

    async def _start_raid(self, guild, cfg, user_ids, manual=False):
        gid = guild.id
        if self._raid_until.get(gid, 0) > time.time():
            return
        until = time.time() + cfg["raid_minutes"] * 60
        self._raid_until[gid] = until
        self._stats[gid] = 0

        update = {"raid_until": until}
        locked = []
        if cfg["lock_on_raid"]:
            update["prev_level"] = guild.verification_level.value
            update["prev_invites"] = "INVITES_DISABLED" in guild.features
            locked = await self._lock(guild)
        await raid_col.update_one({"guild_id": str(gid)}, {"$set": update}, upsert=True)
        self._schedule(gid, until - time.time())

        embed = secui.make("🚨  Raid detected", color=secui.ALERT)
        embed.add_field(
            name="Trigger",
            value="Started by a moderator" if manual else f"{len(user_ids)} joins in {cfg['join_window']} seconds",
            inline=True,
        )
        embed.add_field(name="Action", value=ACTION_TEXT[cfg["action"]], inline=True)
        embed.add_field(name="Raid mode ends", value=f"<t:{int(until)}:R>", inline=True)
        if locked:
            embed.add_field(name="Server lock", value="\n".join(f"• {x}" for x in locked), inline=False)
        secui.footer(embed, guild)
        await secui.post(guild, cfg.get("log_channel_id"), embed)

        for uid in set(user_ids):
            if await self._remove(guild, uid, cfg["action"], "Part of a join raid"):
                self._stats[gid] = self._stats.get(gid, 0) + 1

    async def _finish(self, guild_id, delay):
        if delay > 0:
            await asyncio.sleep(delay)
        self._tasks.pop(guild_id, None)
        self._raid_until.pop(guild_id, None)
        removed = self._stats.pop(guild_id, 0)
        doc = await raid_col.find_one({"guild_id": str(guild_id)}) or {}
        guild = self.bot.get_guild(guild_id)
        await raid_col.update_one(
            {"guild_id": str(guild_id)},
            {"$unset": {"raid_until": "", "prev_level": "", "prev_invites": ""}},
        )
        if guild is None:
            return
        await self._unlock(guild, doc)
        embed = secui.make("✅  Raid mode ended", color=secui.GOOD)
        embed.add_field(name="Accounts removed", value=str(removed), inline=True)
        embed.add_field(name="Server", value="Back to normal", inline=True)
        secui.footer(embed, guild)
        await secui.post(guild, doc.get("log_channel_id"), embed)
        await log_event(self.bot, guild, "antiraid", f"Raid mode ended. {removed} accounts removed.")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot:
            return
        guild = member.guild
        cfg = await self._cfg(guild.id)
        if not cfg["enabled"]:
            return

        now = time.time()
        queue = self._joins.setdefault(guild.id, deque())
        queue.append((now, member.id))
        while queue and now - queue[0][0] > cfg["join_window"]:
            queue.popleft()

        if self._raid_until.get(guild.id, 0) > now:
            if await self._remove(guild, member.id, cfg["action"], "Joined during a raid"):
                self._stats[guild.id] = self._stats.get(guild.id, 0) + 1
            return

        if len(queue) >= cfg["join_limit"]:
            await self._start_raid(guild, cfg, [uid for _, uid in queue])
            return

        min_age = cfg["min_age_days"]
        if min_age:
            age = datetime.now(timezone.utc) - member.created_at
            if age < timedelta(days=min_age):
                removed = await self._remove(
                    guild, member.id, "kick", f"Account is only {age.days} days old"
                )
                embed = secui.make("New account turned away", color=secui.NEUTRAL)
                embed.add_field(name="Person", value=f"{member.mention}\n`{member.id}`", inline=True)
                embed.add_field(name="Account age", value=f"{age.days} days  (needs {min_age})", inline=True)
                embed.add_field(name="Result", value="Kicked" if removed else "Could not kick", inline=True)
                embed.set_thumbnail(url=member.display_avatar.url)
                await secui.post(guild, cfg.get("log_channel_id"), embed)

    async def _panel_embed(self, guild):
        cfg = await self._cfg(guild.id)
        raid_end = self._raid_until.get(guild.id, 0)
        active = raid_end > time.time()
        log_id = cfg.get("log_channel_id")

        embed = secui.make(
            "🚨  Anti-Raid",
            "Spots sudden floods of new members and removes them before they can do damage.",
            secui.ALERT if cfg["enabled"] else secui.NEUTRAL,
        )
        embed.add_field(name="Status", value=secui.state(cfg["enabled"]), inline=True)
        embed.add_field(name="Action", value=ACTION_TEXT[cfg["action"]], inline=True)
        embed.add_field(name="Log channel", value=f"<#{log_id}>" if log_id else "Not set", inline=True)
        embed.add_field(
            name="Raid trigger",
            value=f"**{cfg['join_limit']}** joins in **{cfg['join_window']}** seconds",
            inline=True,
        )
        embed.add_field(
            name="Account age rule",
            value=f"Under **{cfg['min_age_days']}** days gets kicked" if cfg["min_age_days"] else "Off",
            inline=True,
        )
        embed.add_field(name="Server lock in a raid", value=secui.state(cfg["lock_on_raid"]), inline=True)
        embed.add_field(
            name="Raid mode",
            value=f"🔴 Active, ends <t:{int(raid_end)}:R>" if active else "⚪ Calm",
            inline=False,
        )
        embed.add_field(
            name="My access",
            value=secui.access_line(guild, "ban_members", "kick_members", "moderate_members", "manage_guild"),
            inline=False,
        )
        return secui.footer(embed, guild)

    @commands.group(name="antiraid", aliases=["antir"], invoke_without_command=True)
    @secui.trusted_only()
    async def antiraid(self, ctx):
        cfg = await self._cfg(ctx.guild.id)
        gid = ctx.guild.id
        panel = secui.Panel(ctx.author.id, self._panel_embed)
        panel.add_item(secui.Toggle(raid_col, gid, "enabled", "Anti-Raid", cfg["enabled"], False, 0))
        panel.add_item(secui.Toggle(raid_col, gid, "lock_on_raid", "Lock server in a raid", cfg["lock_on_raid"], True, 0))
        panel.add_item(
            secui.Choice(
                raid_col, gid, "action", "Choose what happens to raiders",
                [(k, v[0], v[1]) for k, v in ACTIONS.items()], cfg["action"], 1,
            )
        )
        panel.message = await secui.send(ctx, await self._panel_embed(ctx.guild), view=panel)

    @antiraid.command(name="enable")
    @secui.trusted_only()
    async def ar_enable(self, ctx):
        await raid_col.update_one({"guild_id": str(ctx.guild.id)}, {"$set": {"enabled": True}}, upsert=True)
        await secui.send(ctx, secui.ok("Anti-Raid is now **on**."))

    @antiraid.command(name="disable")
    @secui.trusted_only()
    async def ar_disable(self, ctx):
        await raid_col.update_one({"guild_id": str(ctx.guild.id)}, {"$set": {"enabled": False}}, upsert=True)
        await secui.send(ctx, secui.ok("Anti-Raid is now **off**."))

    @antiraid.command(name="action")
    @secui.trusted_only()
    async def ar_action(self, ctx, mode: str = None):
        if mode is None or mode.lower() not in ACTIONS:
            return await secui.send(ctx, secui.fail(f"Use `{ctx.clean_prefix}antiraid action ban`, `kick` or `timeout`."))
        await raid_col.update_one({"guild_id": str(ctx.guild.id)}, {"$set": {"action": mode.lower()}}, upsert=True)
        await secui.send(ctx, secui.ok(f"Raiders will now get: **{ACTION_TEXT[mode.lower()]}**."))

    @antiraid.command(name="limit")
    @secui.trusted_only()
    async def ar_limit(self, ctx, joins: int = None, seconds: int = None):
        if joins is None or seconds is None or not 2 <= joins <= 50 or not 3 <= seconds <= 120:
            return await secui.send(
                ctx,
                secui.fail(f"Use `{ctx.clean_prefix}antiraid limit <joins 2-50> <seconds 3-120>`.\nExample: `{ctx.clean_prefix}antiraid limit 6 10`"),
            )
        await raid_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"join_limit": joins, "join_window": seconds}},
            upsert=True,
        )
        await secui.send(ctx, secui.ok(f"A raid starts at **{joins}** joins in **{seconds}** seconds."))

    @antiraid.command(name="age")
    @secui.trusted_only()
    async def ar_age(self, ctx, days: int = None):
        if days is None or not 0 <= days <= 60:
            return await secui.send(ctx, secui.fail(f"Use `{ctx.clean_prefix}antiraid age <0-60>`. Use `0` to turn it off."))
        await raid_col.update_one({"guild_id": str(ctx.guild.id)}, {"$set": {"min_age_days": days}}, upsert=True)
        text = "Account age rule is now off." if days == 0 else f"Accounts younger than **{days}** days will be kicked."
        await secui.send(ctx, secui.ok(text))

    @antiraid.command(name="duration")
    @secui.trusted_only()
    async def ar_duration(self, ctx, minutes: int = None):
        if minutes is None or not 1 <= minutes <= 120:
            return await secui.send(ctx, secui.fail(f"Use `{ctx.clean_prefix}antiraid duration <1-120>` minutes."))
        await raid_col.update_one({"guild_id": str(ctx.guild.id)}, {"$set": {"raid_minutes": minutes}}, upsert=True)
        await secui.send(ctx, secui.ok(f"Raid mode will last **{minutes}** minutes."))

    @antiraid.command(name="logs")
    @secui.trusted_only()
    async def ar_logs(self, ctx, channel: discord.TextChannel = None):
        if channel is None:
            return await secui.send(ctx, secui.fail(f"Use `{ctx.clean_prefix}antiraid logs #channel`."))
        await raid_col.update_one(
            {"guild_id": str(ctx.guild.id)}, {"$set": {"log_channel_id": str(channel.id)}}, upsert=True
        )
        await secui.send(ctx, secui.ok(f"Raid alerts will be sent to {channel.mention}."))

    @antiraid.command(name="start")
    @secui.trusted_only()
    async def ar_start(self, ctx):
        cfg = await self._cfg(ctx.guild.id)
        if self._raid_until.get(ctx.guild.id, 0) > time.time():
            return await secui.send(ctx, secui.fail("Raid mode is already active."))
        await self._start_raid(ctx.guild, cfg, [], manual=True)
        await secui.send(ctx, secui.ok(f"Raid mode is **active** for {cfg['raid_minutes']} minutes. New members will be handled."))

    @antiraid.command(name="stop")
    @secui.trusted_only()
    async def ar_stop(self, ctx):
        if self._raid_until.get(ctx.guild.id, 0) <= time.time():
            return await secui.send(ctx, secui.fail("Raid mode is not active."))
        task = self._tasks.pop(ctx.guild.id, None)
        if task:
            task.cancel()
        await self._finish(ctx.guild.id, 0)
        await secui.send(ctx, secui.ok("Raid mode ended. The server is back to normal."))


async def setup(bot):
    await bot.add_cog(AntiRaid(bot))