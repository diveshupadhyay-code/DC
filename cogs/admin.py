import re
import time
import random
import logging
import asyncio
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks
from discord import app_commands

from utils.db import (
    settings_col, personal_prefix_col, premium_col,
    logs_col, bump_col, tickets_col, disabled_cmds_col,
    voicemaster_col, counters_col, global_status_col,
    server_status_col, db
)
from utils.helpers import (
    BOT_OWNER_ID, ctx_owner, ctx_mod, ctx_premium,
    is_premium_server, is_premium_user,
    update_server_data, log_event
)

log = logging.getLogger(__name__)

DEFAULT_PREFIX = ","
PREFIX_CACHE_SECONDS = 300
_guild_prefix_cache = {}
_user_prefix_cache = {}

giveaways_col = db["giveaways"]
message_counts_col = db["message_counts"]
invite_tracker_col = db["invite_tracker"]

NEUTRAL = 0x2B2D31
SUCCESS = 0x3BA55D
DANGER = 0xED4245

GIVEAWAY_EMOJI = "\U0001F389"
NO_MENTIONS = discord.AllowedMentions.none()

BLOCKED_PREFIX_CHARS = set("<>@#/\\`")
MAX_PREFIX_LENGTH = 3
MAX_GIVEAWAY_WINNERS = 20
MAX_GIVEAWAY_MINUTES = 43200
MAX_ACTIVE_GIVEAWAYS = 25
MAX_REQUIREMENT = 100000
MAX_DISABLED_COMMANDS = 100
MAX_STATUS_LENGTH = 128
MAX_ENTRIES_FETCHED = 5000

PROTECTED_COMMANDS = {
    "settings", "prefix", "disable", "enable", "help",
    "premium", "maintenance", "aimode", "sync", "purge",
}

ACTIVITY_TYPES = {"watching", "playing", "listening", "competing"}
TRUE_WORDS = {"on", "true", "1", "yes", "enable", "enabled"}
FALSE_WORDS = {"off", "false", "0", "no", "disable", "disabled"}
HEX_COLOR = re.compile(r"#?([0-9a-fA-F]{6})")


def card(title=None, description=None, color=NEUTRAL):
    return discord.Embed(title=title, description=description, color=color)


def ok(text):
    return card(description=text, color=SUCCESS)


def fail(text):
    return card(description=text, color=DANGER)


async def reply(ctx, embed, **kwargs):
    return await ctx.reply(
        embed=embed,
        mention_author=False,
        allowed_mentions=NO_MENTIONS,
        **kwargs,
    )


def state_text(value):
    return "On" if value else "Off"


def channel_text(channel_id):
    return f"<#{channel_id}>" if channel_id else "Not set"


def parse_switch(value):
    if value is None:
        return None
    word = value.strip().lower()
    if word in TRUE_WORDS:
        return True
    if word in FALSE_WORDS:
        return False
    return None


def validate_prefix(value):
    if not value:
        return "Enter a prefix."
    if len(value) > MAX_PREFIX_LENGTH:
        return f"A prefix can have at most {MAX_PREFIX_LENGTH} characters."
    if not value.isascii() or not value.isprintable():
        return "Use plain keyboard characters only."
    if any(ch.isspace() for ch in value):
        return "A prefix cannot contain spaces."
    if any(ch in BLOCKED_PREFIX_CHARS for ch in value):
        return "That prefix uses a reserved character. Pick a different symbol."
    return None


def parse_duration(text):
    match = re.fullmatch(r"(\d{1,6})(m|h|d)?", text.strip().lower())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2) or "m"
    return value * {"m": 1, "h": 60, "d": 1440}[unit]


def pull_requirement(text, flag):
    match = re.search(rf"--{flag}\s+(\d{{1,6}})", text)
    if not match:
        return 0, text
    amount = min(int(match.group(1)), MAX_REQUIREMENT)
    return amount, text[:match.start()] + text[match.end():]


def invalidate_prefix_cache(guild_id=None, user_id=None):
    if guild_id is not None:
        _guild_prefix_cache.pop(int(guild_id), None)
    if user_id is not None:
        _user_prefix_cache.pop(int(user_id), None)


async def _server_prefix(guild_id):
    now = time.monotonic()
    hit = _guild_prefix_cache.get(guild_id)
    if hit and hit[1] > now:
        return hit[0]
    doc = await settings_col.find_one({"_id": str(guild_id)}, {"prefix": 1})
    value = (doc or {}).get("prefix") or DEFAULT_PREFIX
    _guild_prefix_cache[guild_id] = (value, now + PREFIX_CACHE_SECONDS)
    return value


async def _personal_prefix(user_id):
    now = time.monotonic()
    hit = _user_prefix_cache.get(user_id)
    if hit and hit[1] > now:
        return hit[0]
    doc = await personal_prefix_col.find_one({"user_id": str(user_id)}, {"prefix": 1})
    value = (doc or {}).get("prefix")
    _user_prefix_cache[user_id] = (value, now + PREFIX_CACHE_SECONDS)
    return value


async def resolve_prefix(bot, message):
    if message.guild:
        prefixes = [await _server_prefix(message.guild.id)]
    else:
        prefixes = [DEFAULT_PREFIX]
    personal = await _personal_prefix(message.author.id)
    if personal and personal not in prefixes:
        prefixes.append(personal)
    prefixes.sort(key=len, reverse=True)
    return commands.when_mentioned_or(*prefixes)(bot, message)


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.giveaway_checker.start()

    def cog_unload(self):
        self.giveaway_checker.cancel()

    async def bot_check(self, ctx):
        if not ctx.guild or ctx.command is None or ctx.author.id == BOT_OWNER_ID:
            return True
        root = ctx.command.root_parent or ctx.command
        if root.qualified_name in PROTECTED_COMMANDS:
            return True
        names = list({ctx.command.qualified_name, root.qualified_name})
        found = await disabled_cmds_col.find_one(
            {"guild_id": str(ctx.guild.id), "command_name": {"$in": names}}
        )
        if found:
            await reply(
                ctx,
                fail(f"The `{root.qualified_name}` command is disabled in this server."),
                delete_after=6,
            )
            return False
        return True

    async def cog_app_command_error(self, interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            text = "You do not have permission to use this command."
        elif isinstance(error, app_commands.NoPrivateMessage):
            text = "This command works only inside a server."
        else:
            log.exception("Slash command failed", exc_info=error)
            text = "Something went wrong. Try again in a moment."
        if interaction.response.is_done():
            await interaction.followup.send(embed=fail(text), ephemeral=True)
        else:
            await interaction.response.send_message(embed=fail(text), ephemeral=True)

    @tasks.loop(seconds=20)
    async def giveaway_checker(self):
        try:
            now = datetime.now(timezone.utc)
            cursor = giveaways_col.find({"status": "active", "end_time": {"$lte": now}})
            async for doc in cursor:
                channel = self.bot.get_channel(int(doc["channel_id"]))
                if not channel:
                    await giveaways_col.update_one(
                        {"_id": doc["_id"]}, {"$set": {"status": "ended"}}
                    )
                    continue
                await self._end_giveaway(channel, int(doc["message_id"]))
        except Exception:
            log.exception("Giveaway checker failed")

    @giveaway_checker.before_loop
    async def before_giveaway_checker(self):
        await self.bot.wait_until_ready()

    @commands.command(aliases=["config", "panel"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def settings(self, ctx):
        gid = str(ctx.guild.id)
        gs, log_cfg, bump_cfg, ticket_cfg, vc_cfg = await asyncio.gather(
            settings_col.find_one({"_id": gid}),
            logs_col.find_one({"guild_id": gid}),
            bump_col.find_one({"guild_id": gid}),
            tickets_col.find_one({"_id": gid}),
            voicemaster_col.find_one({"guild_id": gid}),
        )
        gs = gs or {}
        log_cfg = log_cfg or {}
        ticket_cfg = ticket_cfg or {}

        counter_docs = await counters_col.find({"guild_id": gid}).to_list(10)
        counters = ", ".join(f"`{d['type']}`" for d in counter_docs) or "None"

        disabled_docs = await disabled_cmds_col.find({"guild_id": gid}).to_list(20)
        disabled = ", ".join(f"`{d['command_name']}`" for d in disabled_docs) or "None"

        is_prem = await is_premium_server(ctx.guild.id)
        prefix = gs.get("prefix") or DEFAULT_PREFIX

        embed = card(title=f"{ctx.guild.name} settings")
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)

        embed.add_field(
            name="General",
            value=f"Prefix: `{prefix}`\nPremium: {'Active' if is_prem else 'Not active'}",
            inline=True,
        )
        embed.add_field(
            name="Welcome and goodbye",
            value=(
                f"Welcome: {state_text(gs.get('welcome_enabled'))}, {channel_text(gs.get('welcome_channel'))}\n"
                f"Goodbye: {state_text(gs.get('bye_enabled'))}, {channel_text(gs.get('bye_channel'))}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Logs",
            value=f"Channel: {channel_text(log_cfg.get('channel_id'))}",
            inline=True,
        )
        embed.add_field(
            name="Protection",
            value=(
                f"Invite blocking: {state_text(gs.get('invite_block'))}\n"
                f"Jail role: {'Set' if gs.get('jail_role_id') else 'Not set'}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Extras",
            value=(
                f"Level alerts: {state_text(gs.get('levels_enabled', True))}\n"
                f"Auto reactions: {state_text(gs.get('reactions_enabled', True))}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Tickets",
            value=(
                f"Opened so far: `{ticket_cfg.get('ticket_count', 0)}`\n"
                f"Staff role: {'Set' if ticket_cfg.get('staff_role_id') else 'Not set'}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Premium features",
            value=(
                f"Bump alerts: {state_text(bump_cfg and bump_cfg.get('enabled'))}\n"
                f"VoiceMaster: {'Ready' if vc_cfg else 'Not set'}\n"
                f"Counters: {counters}"
            ),
            inline=False,
        )
        embed.add_field(name="Disabled commands", value=disabled, inline=False)
        embed.set_footer(text=f"Server ID {gid}  |  {ctx.clean_prefix}help lists all commands")
        await reply(ctx, embed)

    @app_commands.command(name="settings", description="View the server settings summary")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_settings(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gs = await settings_col.find_one({"_id": str(interaction.guild.id)}) or {}
        is_prem = await is_premium_server(interaction.guild.id)
        embed = card(title=f"{interaction.guild.name} settings")
        embed.add_field(name="Prefix", value=f"`{gs.get('prefix') or DEFAULT_PREFIX}`", inline=True)
        embed.add_field(name="Premium", value="Active" if is_prem else "Not active", inline=True)
        embed.add_field(name="Welcome", value=state_text(gs.get("welcome_enabled")), inline=True)
        embed.set_footer(text="Use the text command settings to see every option")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    async def prefix(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        current = gs.get("prefix") or DEFAULT_PREFIX
        personal = await personal_prefix_col.find_one({"user_id": str(ctx.author.id)})
        personal_value = personal.get("prefix") if personal else None
        is_prem = await is_premium_user(ctx.author.id) or await is_premium_server(ctx.guild.id)
        p = ctx.clean_prefix

        embed = card(title="Prefix settings")
        embed.add_field(name="Server prefix", value=f"`{current}`", inline=True)
        embed.add_field(
            name="Your prefix",
            value=f"`{personal_value}`" if personal_value else "Not set",
            inline=True,
        )
        embed.add_field(name="Premium", value="Active" if is_prem else "Not active", inline=True)
        embed.add_field(
            name="Commands",
            value=(
                f"`{p}prefix set <symbol>` changes the server prefix\n"
                f"`{p}prefix remove` resets the server prefix to `{DEFAULT_PREFIX}`\n"
                f"`{p}prefix self <symbol>` sets your own prefix (premium)\n"
                f"`{p}prefix selfremove` removes your own prefix"
            ),
            inline=False,
        )
        embed.set_footer(text="Only the current prefix works. The old one stops working right away.")
        await reply(ctx, embed)

    @prefix.command(name="set")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.cooldown(2, 30, commands.BucketType.guild)
    async def prefix_set(self, ctx, new_prefix: str = None):
        problem = validate_prefix(new_prefix)
        if problem:
            return await reply(ctx, fail(f"{problem}\nUse `{ctx.clean_prefix}prefix set <symbol>`."))
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)}, {"$set": {"prefix": new_prefix}}, upsert=True
        )
        invalidate_prefix_cache(guild_id=ctx.guild.id)
        await reply(ctx, ok(f"Server prefix is now `{new_prefix}`. The old prefix no longer works."))
        await log_event(
            self.bot, ctx.guild, "prefix_change",
            f"{ctx.author} changed the server prefix to `{new_prefix}`",
        )

    @prefix.command(name="remove")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.cooldown(2, 30, commands.BucketType.guild)
    async def prefix_remove(self, ctx):
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)}, {"$unset": {"prefix": ""}}, upsert=True
        )
        invalidate_prefix_cache(guild_id=ctx.guild.id)
        await reply(ctx, ok(f"Server prefix reset to `{DEFAULT_PREFIX}`."))
        await log_event(
            self.bot, ctx.guild, "prefix_change",
            f"{ctx.author} reset the server prefix to `{DEFAULT_PREFIX}`",
        )

    @prefix.command(name="self")
    @ctx_premium()
    @commands.cooldown(3, 60, commands.BucketType.user)
    async def prefix_self(self, ctx, new_prefix: str = None):
        problem = validate_prefix(new_prefix)
        if problem:
            return await reply(ctx, fail(f"{problem}\nUse `{ctx.clean_prefix}prefix self <symbol>`."))
        await personal_prefix_col.update_one(
            {"user_id": str(ctx.author.id)}, {"$set": {"prefix": new_prefix}}, upsert=True
        )
        invalidate_prefix_cache(user_id=ctx.author.id)
        await reply(ctx, ok(f"Your personal prefix is now `{new_prefix}`."))

    @prefix.command(name="selfremove")
    @commands.cooldown(3, 60, commands.BucketType.user)
    async def prefix_selfremove(self, ctx):
        result = await personal_prefix_col.delete_one({"user_id": str(ctx.author.id)})
        invalidate_prefix_cache(user_id=ctx.author.id)
        if result.deleted_count:
            await reply(ctx, ok("Your personal prefix was removed."))
        else:
            await reply(ctx, fail("You do not have a personal prefix."))

    @commands.command()
    @commands.guild_only()
    @ctx_mod()
    @commands.cooldown(3, 30, commands.BucketType.guild)
    async def announce(self, ctx, channel: discord.TextChannel = None, *, content: str = None):
        if not content:
            return await reply(
                ctx,
                fail(
                    f"Use `{ctx.clean_prefix}announce [#channel] <message>`.\n"
                    "Add `--ping` to notify everyone."
                ),
            )

        ping = "--ping" in content
        content = content.replace("--ping", "").strip()
        if not content:
            return await reply(ctx, fail("The announcement cannot be empty."))
        if len(content) > 4000:
            return await reply(ctx, fail("The announcement is too long. Keep it under 4000 characters."))

        target = channel or ctx.channel
        if not target.permissions_for(ctx.author).send_messages:
            return await reply(ctx, fail("You cannot send messages in that channel."))
        if not target.permissions_for(ctx.guild.me).send_messages:
            return await reply(ctx, fail("I cannot send messages in that channel."))
        if ping and not target.permissions_for(ctx.author).mention_everyone:
            return await reply(ctx, fail("You need the Mention Everyone permission to use `--ping`."))

        embed = card(description=content)
        embed.set_author(
            name=ctx.guild.name,
            icon_url=ctx.guild.icon.url if ctx.guild.icon else None,
        )
        embed.set_footer(
            text=f"Posted by {ctx.author.display_name}",
            icon_url=ctx.author.display_avatar.url,
        )

        try:
            await target.send(
                content="@everyone" if ping else None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(everyone=ping),
            )
        except discord.HTTPException:
            return await reply(ctx, fail("I could not send the announcement. Check my permissions."))

        if target != ctx.channel:
            await reply(ctx, ok(f"Announcement sent to {target.mention}."), delete_after=5)
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        await log_event(
            self.bot, ctx.guild, "announcement",
            f"{ctx.author} posted an announcement in {target.mention}. Ping: {ping}",
        )

    @app_commands.command(name="announce", description="Send a server announcement")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_announce(
        self,
        interaction: discord.Interaction,
        title: app_commands.Range[str, 1, 256],
        description: app_commands.Range[str, 1, 4000],
        channel: discord.TextChannel = None,
        color: str = None,
        ping_everyone: bool = False,
    ):
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            return await interaction.response.send_message(
                embed=fail("Pick a text channel."), ephemeral=True
            )
        user_perms = target.permissions_for(interaction.user)
        if not user_perms.send_messages:
            return await interaction.response.send_message(
                embed=fail("You cannot send messages in that channel."), ephemeral=True
            )
        if not target.permissions_for(interaction.guild.me).send_messages:
            return await interaction.response.send_message(
                embed=fail("I cannot send messages in that channel."), ephemeral=True
            )
        if ping_everyone and not user_perms.mention_everyone:
            return await interaction.response.send_message(
                embed=fail("You need the Mention Everyone permission to ping everyone."),
                ephemeral=True,
            )

        embed_color = NEUTRAL
        if color:
            match = HEX_COLOR.fullmatch(color.strip())
            if not match:
                return await interaction.response.send_message(
                    embed=fail("Use a color like `#2B2D31`."), ephemeral=True
                )
            embed_color = int(match.group(1), 16)

        embed = card(title=title, description=description, color=embed_color)
        embed.set_author(
            name=interaction.guild.name,
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None,
        )
        embed.set_footer(text=f"Posted by {interaction.user.display_name}")

        try:
            await target.send(
                content="@everyone" if ping_everyone else None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(everyone=ping_everyone),
            )
        except discord.HTTPException:
            return await interaction.response.send_message(
                embed=fail("I could not send the announcement. Check my permissions."),
                ephemeral=True,
            )
        await interaction.response.send_message(
            embed=ok(f"Announcement sent to {target.mention}."), ephemeral=True
        )

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @ctx_mod()
    async def giveaway(self, ctx, duration: str = None, winners: int = 1, *, prize: str = None):
        p = ctx.clean_prefix
        if not duration or not prize:
            embed = card(title="Start a giveaway")
            embed.add_field(
                name="Usage",
                value=(
                    f"`{p}giveaway <duration> <winners> <prize>`\n"
                    "Duration examples: `30m`, `2h`, `1d`"
                ),
                inline=False,
            )
            embed.add_field(
                name="Optional requirements",
                value=(
                    "`--msgs <count>` minimum messages sent\n"
                    "`--invites <count>` minimum invites"
                ),
                inline=False,
            )
            embed.add_field(name="Example", value=f"`{p}giveaway 1h 1 Nitro --msgs 50 --invites 2`", inline=False)
            embed.add_field(name="End early", value=f"`{p}giveaway end <message_id>`", inline=True)
            embed.add_field(name="Pick new winners", value=f"`{p}giveaway reroll <message_id>`", inline=True)
            return await reply(ctx, embed)

        minutes = parse_duration(duration)
        if minutes is None:
            return await reply(ctx, fail("Invalid duration. Use a value like `15m`, `4h` or `7d`."))
        if minutes < 1:
            return await reply(ctx, fail("A giveaway must last at least 1 minute."))
        if minutes > MAX_GIVEAWAY_MINUTES:
            return await reply(ctx, fail("A giveaway cannot last longer than 30 days."))
        if not 1 <= winners <= MAX_GIVEAWAY_WINNERS:
            return await reply(ctx, fail(f"Winners must be between 1 and {MAX_GIVEAWAY_WINNERS}."))

        active = await giveaways_col.count_documents(
            {"guild_id": str(ctx.guild.id), "status": "active"}
        )
        if active >= MAX_ACTIVE_GIVEAWAYS:
            return await reply(ctx, fail(f"This server already has {MAX_ACTIVE_GIVEAWAYS} active giveaways."))

        min_msgs, prize = pull_requirement(prize, "msgs")
        min_invites, prize = pull_requirement(prize, "invites")
        prize = " ".join(prize.split())
        if not prize:
            return await reply(ctx, fail("The prize cannot be empty."))
        if len(prize) > 200:
            return await reply(ctx, fail("The prize is too long. Keep it under 200 characters."))

        if not ctx.channel.permissions_for(ctx.guild.me).add_reactions:
            return await reply(ctx, fail("I need the Add Reactions permission in this channel."))

        end_time = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        ts = int(end_time.timestamp())

        lines = [f"React with {GIVEAWAY_EMOJI} to enter.", ""]
        lines.append(f"Winners: **{winners}**")
        lines.append(f"Ends: <t:{ts}:R> (<t:{ts}:f>)")
        if min_msgs or min_invites:
            lines.append("")
            lines.append("**Requirements**")
            if min_msgs:
                lines.append(f"At least {min_msgs} messages sent")
            if min_invites:
                lines.append(f"At least {min_invites} invites")

        embed = card(title=prize, description="\n".join(lines))
        embed.set_footer(
            text=f"Hosted by {ctx.author.display_name}",
            icon_url=ctx.author.display_avatar.url,
        )

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

        msg = await ctx.channel.send(embed=embed, allowed_mentions=NO_MENTIONS)
        await msg.add_reaction(GIVEAWAY_EMOJI)

        await giveaways_col.insert_one({
            "message_id": str(msg.id),
            "channel_id": str(ctx.channel.id),
            "guild_id": str(ctx.guild.id),
            "host_id": str(ctx.author.id),
            "prize": prize,
            "winners": winners,
            "min_msgs": min_msgs,
            "min_invites": min_invites,
            "end_time": end_time,
            "status": "active",
        })

    @giveaway.command(name="end")
    @commands.guild_only()
    @ctx_mod()
    async def giveaway_end(self, ctx, message_id: int = None):
        if not message_id:
            return await reply(ctx, fail(f"Use `{ctx.clean_prefix}giveaway end <message_id>`."))
        doc = await giveaways_col.find_one(
            {"message_id": str(message_id), "guild_id": str(ctx.guild.id), "status": "active"}
        )
        if not doc:
            return await reply(ctx, fail("No active giveaway found with that message ID."))
        channel = self.bot.get_channel(int(doc["channel_id"])) or ctx.channel
        await self._end_giveaway(channel, message_id)
        await reply(ctx, ok("The giveaway has ended."), delete_after=4)

    @giveaway.command(name="reroll")
    @commands.guild_only()
    @ctx_mod()
    @commands.cooldown(2, 15, commands.BucketType.guild)
    async def giveaway_reroll(self, ctx, message_id: int = None):
        if not message_id:
            return await reply(ctx, fail(f"Use `{ctx.clean_prefix}giveaway reroll <message_id>`."))
        data = await giveaways_col.find_one(
            {"message_id": str(message_id), "guild_id": str(ctx.guild.id)}
        )
        if not data:
            return await reply(ctx, fail("No giveaway found with that message ID."))
        if data.get("status") != "ended":
            return await reply(ctx, fail("That giveaway is still running. End it first."))

        channel = self.bot.get_channel(int(data["channel_id"]))
        if not channel:
            return await reply(ctx, fail("I can no longer see the channel of that giveaway."))
        try:
            msg = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            return await reply(ctx, fail("I could not find the giveaway message."))

        reaction = next((r for r in msg.reactions if str(r.emoji) == GIVEAWAY_EMOJI), None)
        if not reaction:
            return await reply(ctx, fail("There are no entries on that message."))

        users = [u async for u in reaction.users(limit=MAX_ENTRIES_FETCHED) if not u.bot]
        eligible = await self._filter_eligible(
            ctx.guild, users, data.get("min_msgs", 0), data.get("min_invites", 0)
        )
        if not eligible:
            return await reply(ctx, fail("No entries meet the giveaway requirements."))

        chosen = random.sample(eligible, min(data["winners"], len(eligible)))
        mentions = ", ".join(w.mention for w in chosen)
        embed = card(
            title="New winners",
            description=f"Prize: **{data['prize']}**\nWinners: {mentions}",
        )
        await ctx.reply(
            embed=embed,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions(users=chosen),
        )

    async def _filter_eligible(self, guild, users, min_msgs, min_invites):
        members = [u for u in users if guild.get_member(u.id)]
        if not min_msgs and not min_invites:
            return members

        gid = str(guild.id)
        ids = [str(u.id) for u in members]

        msg_counts = {}
        if min_msgs:
            async for doc in message_counts_col.find(
                {"guild_id": gid, "user_id": {"$in": ids}}
            ):
                msg_counts[doc["user_id"]] = doc.get("count", 0)

        invite_counts = {}
        if min_invites:
            async for doc in invite_tracker_col.find(
                {"guild_id": gid, "inviter_id": {"$in": ids}}
            ):
                key = doc["inviter_id"]
                invite_counts[key] = invite_counts.get(key, 0) + doc.get("uses", 0)

        return [
            u for u in members
            if msg_counts.get(str(u.id), 0) >= min_msgs
            and invite_counts.get(str(u.id), 0) >= min_invites
        ]

    async def _finish_giveaway(self, channel, msg, embed, **send_kwargs):
        try:
            await msg.edit(embed=embed)
        except discord.HTTPException:
            pass
        await channel.send(embed=embed, allowed_mentions=NO_MENTIONS, **send_kwargs)

    async def _end_giveaway(self, channel, msg_id: int):
        data = await giveaways_col.find_one_and_update(
            {"message_id": str(msg_id), "status": "active"},
            {"$set": {"status": "ended"}},
        )
        if not data:
            return

        try:
            msg = await channel.fetch_message(msg_id)
        except (discord.NotFound, discord.Forbidden):
            return

        prize = data["prize"]
        reaction = next((r for r in msg.reactions if str(r.emoji) == GIVEAWAY_EMOJI), None)
        reactors = []
        if reaction:
            reactors = [u async for u in reaction.users(limit=MAX_ENTRIES_FETCHED) if not u.bot]

        if not reactors:
            embed = card(title="Giveaway ended", description=f"Prize: **{prize}**\n\nNobody entered.")
            return await self._finish_giveaway(channel, msg, embed)

        min_msgs = data.get("min_msgs", 0)
        min_invites = data.get("min_invites", 0)
        eligible = await self._filter_eligible(channel.guild, reactors, min_msgs, min_invites)

        if not eligible:
            needs = []
            if min_msgs:
                needs.append(f"{min_msgs}+ messages")
            if min_invites:
                needs.append(f"{min_invites}+ invites")
            embed = card(
                title="Giveaway ended",
                description=(
                    f"Prize: **{prize}**\n\n"
                    f"No winner. Nobody met the requirements: {' and '.join(needs) or 'server membership'}.\n"
                    f"Entries: `{len(reactors)}`  |  Passed: `0`"
                ),
            )
            return await self._finish_giveaway(channel, msg, embed)

        chosen = random.sample(eligible, min(data["winners"], len(eligible)))
        mentions = ", ".join(w.mention for w in chosen)

        lines = [f"Prize: **{prize}**", f"Winners: {mentions}"]
        if min_msgs or min_invites:
            needs = []
            if min_msgs:
                needs.append(f"{min_msgs}+ messages")
            if min_invites:
                needs.append(f"{min_invites}+ invites")
            lines.append(f"Requirements: {', '.join(needs)}")
            lines.append(f"Passed checks: `{len(eligible)}` of `{len(reactors)}`")

        result = card(title="Giveaway results", description="\n".join(lines))
        result.set_footer(text="Use the giveaway reroll command to pick new winners")
        try:
            await msg.edit(embed=result)
        except discord.HTTPException:
            pass

        await channel.send(
            content=mentions,
            embed=card(description=f"Congratulations {mentions}. You won **{prize}**."),
            allowed_mentions=discord.AllowedMentions(users=chosen),
        )

        removed = len(reactors) - len(eligible)
        if removed:
            await channel.send(
                embed=card(description=f"`{removed}` entries were removed because they did not meet the requirements."),
                delete_after=30,
                allowed_mentions=NO_MENTIONS,
            )

    async def _apply_slowmode(self, target, seconds):
        try:
            await target.edit(slowmode_delay=seconds)
        except discord.Forbidden:
            return "I need the Manage Channels permission in that channel."
        except discord.HTTPException:
            return "Discord rejected the change. Try again."
        return None

    @commands.command()
    @commands.guild_only()
    @ctx_mod()
    async def slowmode(self, ctx, seconds: int = None, channel: discord.TextChannel = None):
        if seconds is None:
            return await reply(
                ctx,
                fail(
                    f"Use `{ctx.clean_prefix}slowmode <seconds> [#channel]`.\n"
                    "Use `0` to turn it off. The maximum is `21600`."
                ),
            )
        if not 0 <= seconds <= 21600:
            return await reply(ctx, fail("Seconds must be between 0 and 21600."))
        target = channel or ctx.channel
        problem = await self._apply_slowmode(target, seconds)
        if problem:
            return await reply(ctx, fail(problem))
        text = "Slowmode turned off" if seconds == 0 else f"Slowmode set to {seconds}s"
        await reply(ctx, ok(f"{text} in {target.mention}."))
        await log_event(
            self.bot, ctx.guild, "slowmode",
            f"{ctx.author} set slowmode to {seconds}s in #{target.name}",
        )

    @app_commands.command(name="slowmode", description="Set the slowmode delay of a channel")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slash_slowmode(
        self,
        interaction: discord.Interaction,
        seconds: app_commands.Range[int, 0, 21600],
        channel: discord.TextChannel = None,
    ):
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            return await interaction.response.send_message(
                embed=fail("Pick a text channel."), ephemeral=True
            )
        problem = await self._apply_slowmode(target, seconds)
        if problem:
            return await interaction.response.send_message(embed=fail(problem), ephemeral=True)
        text = "Slowmode turned off" if seconds == 0 else f"Slowmode set to {seconds}s"
        await interaction.response.send_message(
            embed=ok(f"{text} in {target.mention}."), ephemeral=True
        )

    @commands.command(name="disable")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def disable_command(self, ctx, *, cmd_name: str = None):
        gid = str(ctx.guild.id)
        p = ctx.clean_prefix
        if not cmd_name:
            docs = await disabled_cmds_col.find({"guild_id": gid}).to_list(50)
            names = [d["command_name"] for d in docs]
            listing = ", ".join(f"`{n}`" for n in names) if names else "None"
            embed = card(
                title="Disabled commands",
                description=(
                    f"Currently disabled: {listing}\n\n"
                    f"`{p}disable <command>` turns a command off\n"
                    f"`{p}enable <command>` turns it back on"
                ),
            )
            return await reply(ctx, embed)

        command = self.bot.get_command(cmd_name.strip().lower())
        if command is None:
            return await reply(ctx, fail("That command does not exist."))
        root = command.root_parent or command
        if root.qualified_name in PROTECTED_COMMANDS:
            return await reply(ctx, fail(f"The `{root.qualified_name}` command cannot be disabled."))

        count = await disabled_cmds_col.count_documents({"guild_id": gid})
        if count >= MAX_DISABLED_COMMANDS:
            return await reply(ctx, fail("Too many commands are disabled. Enable some first."))

        name = command.qualified_name
        await disabled_cmds_col.update_one(
            {"guild_id": gid, "command_name": name},
            {"$set": {"guild_id": gid, "command_name": name}},
            upsert=True,
        )
        await reply(ctx, ok(f"The `{name}` command is now disabled."))
        await log_event(self.bot, ctx.guild, "command_disabled", f"{ctx.author} disabled `{name}`")

    @commands.command(name="enable")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def enable_command(self, ctx, *, cmd_name: str = None):
        if not cmd_name:
            return await reply(ctx, fail(f"Use `{ctx.clean_prefix}enable <command>`."))
        command = self.bot.get_command(cmd_name.strip().lower())
        name = command.qualified_name if command else cmd_name.strip().lower()[:100]
        result = await disabled_cmds_col.delete_one(
            {"guild_id": str(ctx.guild.id), "command_name": name}
        )
        if result.deleted_count:
            await reply(ctx, ok(f"The `{name}` command is enabled again."))
            await log_event(self.bot, ctx.guild, "command_enabled", f"{ctx.author} enabled `{name}`")
        else:
            await reply(ctx, fail(f"The `{name}` command is not disabled."))

    @commands.command()
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def premiumrole(self, ctx, role: discord.Role = None):
        if not role:
            gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
            rid = gs.get("premium_role_id")
            current = ctx.guild.get_role(rid) if rid else None
            return await reply(
                ctx,
                card(
                    title="Premium role",
                    description=(
                        f"Current role: {current.mention if current else 'Not set'}\n"
                        f"Use `{ctx.clean_prefix}premiumrole @role` to change it."
                    ),
                ),
            )
        if role.is_default():
            return await reply(ctx, fail("You cannot use the everyone role."))
        await update_server_data(ctx.guild.id, "premium_role_id", role.id)
        await reply(ctx, ok(f"Premium role is now {role.mention}."))
        await log_event(
            self.bot, ctx.guild, "premium_role",
            f"{ctx.author} set the premium role to {role.name}",
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        before_ids = {r.id for r in before.roles}
        after_ids = {r.id for r in after.roles}
        if before_ids == after_ids:
            return
        gs = await settings_col.find_one({"_id": str(after.guild.id)}, {"premium_role_id": 1})
        rid = (gs or {}).get("premium_role_id")
        if not rid:
            return
        gained = rid in after_ids and rid not in before_ids
        lost = rid in before_ids and rid not in after_ids
        if gained:
            await premium_col.update_one(
                {"type": "user", "id": str(after.id)},
                {"$setOnInsert": {
                    "type": "user",
                    "id": str(after.id),
                    "via_role": True,
                    "guild_id": str(after.guild.id),
                }},
                upsert=True,
            )
        elif lost:
            await premium_col.delete_one(
                {"type": "user", "id": str(after.id), "via_role": True, "guild_id": str(after.guild.id)}
            )

    @commands.command()
    @commands.guild_only()
    @ctx_mod()
    async def topic(self, ctx, *, text: str = None):
        if text and len(text) > 1024:
            return await reply(ctx, fail("The topic is too long. Keep it under 1024 characters."))
        try:
            await ctx.channel.edit(topic=text or "")
        except discord.Forbidden:
            return await reply(ctx, fail("I need the Manage Channels permission here."))
        except discord.HTTPException:
            return await reply(ctx, fail("Discord rejected the change. Try again."))
        await reply(ctx, ok("Channel topic updated." if text else "Channel topic cleared."), delete_after=5)
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @commands.command()
    @commands.guild_only()
    @ctx_mod()
    @commands.cooldown(2, 30, commands.BucketType.channel)
    async def rename(self, ctx, *, name: str = None):
        if not name:
            return await reply(ctx, fail(f"Use `{ctx.clean_prefix}rename <new name>`."))
        name = name.strip()
        if not 1 <= len(name) <= 100:
            return await reply(ctx, fail("The name must be between 1 and 100 characters."))
        old = ctx.channel.name
        try:
            await ctx.channel.edit(name=name)
        except discord.Forbidden:
            return await reply(ctx, fail("I need the Manage Channels permission here."))
        except discord.HTTPException:
            return await reply(ctx, fail("Discord rejected that name. Try a different one."))
        await reply(ctx, ok(f"Channel renamed from `{old}` to `{ctx.channel.name}`."))
        await log_event(
            self.bot, ctx.guild, "channel_rename",
            f"{ctx.author} renamed #{old} to #{ctx.channel.name}",
        )

    @commands.command()
    @ctx_owner()
    async def maintenance(self, ctx, status: str = None):
        if not hasattr(self.bot, "maintenance"):
            self.bot.maintenance = False
        if status is None:
            return await reply(ctx, card(description=f"Maintenance mode is **{state_text(self.bot.maintenance)}**."))
        value = parse_switch(status)
        if value is None:
            return await reply(ctx, fail(f"Use `{ctx.clean_prefix}maintenance on` or `off`."))
        self.bot.maintenance = value
        await reply(ctx, ok(f"Maintenance mode is now **{state_text(value)}**."))

    async def _toggle_setting(self, ctx, key, label, status):
        gid = str(ctx.guild.id)
        if status is None:
            gs = await settings_col.find_one({"_id": gid}) or {}
            return await reply(
                ctx,
                card(description=f"{label} is **{state_text(gs.get(key, True))}**."),
            )
        value = parse_switch(status)
        if value is None:
            return await reply(ctx, fail("Use `on` or `off`."))
        await settings_col.update_one({"_id": gid}, {"$set": {key: value}}, upsert=True)
        await reply(ctx, ok(f"{label} is now **{state_text(value)}**."))

    @commands.command()
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def togglelevels(self, ctx, status: str = None):
        await self._toggle_setting(ctx, "levels_enabled", "Level alerts", status)

    @commands.command()
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def togglereactions(self, ctx, status: str = None):
        await self._toggle_setting(ctx, "reactions_enabled", "Auto reactions", status)

    async def _status_embed(self):
        override = await global_status_col.find_one({"type": "owner"})
        docs = await server_status_col.find({}).to_list(50)

        embed = card(title="Bot status")
        if override:
            expires = override.get("expires_at")
            expiry = f"<t:{int(expires.timestamp())}:R>" if expires else "Never"
            embed.add_field(
                name="Global override",
                value=(
                    f"{override.get('activity', 'watching').title()} `{override['status']}`\n"
                    f"Expires: {expiry}"
                ),
                inline=False,
            )
        else:
            embed.add_field(name="Global override", value="None. The normal rotation is running.", inline=False)

        if docs:
            lines = [f"`{d.get('guild_name', '?')}`: `{d['status']}`" for d in docs[:10]]
            embed.add_field(name=f"Server statuses ({len(docs)})", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Server statuses", value="None set", inline=False)
        return embed

    async def _set_status(self, ctx, activity, text, hours):
        atype = (activity or "").lower()
        if atype not in ACTIVITY_TYPES or not text:
            return await reply(
                ctx,
                fail("Use an activity of watching, playing, listening or competing, followed by the text."),
            )
        text = text.strip()
        if len(text) > MAX_STATUS_LENGTH:
            return await reply(ctx, fail(f"Keep the status under {MAX_STATUS_LENGTH} characters."))

        expires = datetime.now(timezone.utc) + timedelta(hours=hours) if hours else None
        await global_status_col.update_one(
            {"type": "owner"},
            {"$set": {"type": "owner", "status": text, "activity": atype, "expires_at": expires}},
            upsert=True,
        )
        await self.bot.change_presence(
            activity=discord.Activity(type=getattr(discord.ActivityType, atype), name=text)
        )
        description = f"Status set to **{atype.title()}** `{text}`."
        if expires:
            description += f"\nEnds <t:{int(expires.timestamp())}:R>."
        await reply(ctx, ok(description))

    @commands.group(invoke_without_command=True)
    @ctx_owner()
    async def botstatus(self, ctx):
        p = ctx.clean_prefix
        embed = await self._status_embed()
        embed.add_field(
            name="Commands",
            value=(
                f"`{p}botstatus set <activity> <text>` sets a permanent status\n"
                f"`{p}botstatus set24h <activity> <text>` sets a 24 hour status\n"
                f"`{p}botstatus reset` removes the override\n"
                f"`{p}botstatus view` refreshes this view\n"
                f"`{p}botstatus removeserver <guild_id>` removes a server status"
            ),
            inline=False,
        )
        await reply(ctx, embed)

    @botstatus.command(name="set")
    @ctx_owner()
    async def botstatus_set(self, ctx, activity: str = None, *, text: str = None):
        await self._set_status(ctx, activity, text, None)

    @botstatus.command(name="set24h")
    @ctx_owner()
    async def botstatus_set24h(self, ctx, activity: str = None, *, text: str = None):
        await self._set_status(ctx, activity, text, 24)

    @botstatus.command(name="reset")
    @ctx_owner()
    async def botstatus_reset(self, ctx):
        result = await global_status_col.delete_one({"type": "owner"})
        if result.deleted_count:
            await reply(ctx, ok("The global override was removed. The default status is back."))
        else:
            await reply(ctx, fail("There is no global override to remove."))

    @botstatus.command(name="view")
    @ctx_owner()
    async def botstatus_view(self, ctx):
        await reply(ctx, await self._status_embed())

    @botstatus.command(name="removeserver")
    @ctx_owner()
    async def botstatus_removeserver(self, ctx, guild_id: str = None):
        if not guild_id or not guild_id.isdigit():
            return await reply(ctx, fail(f"Use `{ctx.clean_prefix}botstatus removeserver <guild_id>`."))
        doc = await server_status_col.find_one({"guild_id": guild_id})
        if not doc:
            return await reply(ctx, fail(f"No custom status found for server `{guild_id}`."))
        await server_status_col.delete_one({"guild_id": guild_id})
        await reply(
            ctx,
            ok(f"Removed the status of **{doc.get('guild_name', guild_id)}**: `{doc.get('status', '?')}`"),
        )

    @commands.command(aliases=["guildlist"])
    @ctx_owner()
    async def serverlist(self, ctx):
        guilds = sorted(self.bot.guilds, key=lambda g: g.member_count or 0, reverse=True)
        lines = [
            f"`{i + 1}.` **{g.name}**  `{g.member_count or 0}` members  ID `{g.id}`"
            for i, g in enumerate(guilds[:20])
        ]
        if len(guilds) > 20:
            lines.append(f"And {len(guilds) - 20} more.")
        await reply(ctx, card(title=f"Servers ({len(guilds)})", description="\n".join(lines)))

    @commands.command()
    @ctx_owner()
    async def leaveguild(self, ctx, guild_id: int = None):
        if not guild_id:
            return await reply(ctx, fail(f"Use `{ctx.clean_prefix}leaveguild <guild_id>`."))
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return await reply(ctx, fail(f"I am not in a server with ID `{guild_id}`."))
        name = guild.name
        await guild.leave()
        await reply(ctx, ok(f"Left **{name}**."))

    @commands.command()
    @ctx_owner()
    async def dm(self, ctx, user: discord.User = None, *, message: str = None):
        if not user or not message:
            return await reply(ctx, fail(f"Use `{ctx.clean_prefix}dm @user <message>`."))
        if len(message) > 2000:
            return await reply(ctx, fail("The message is too long. Keep it under 2000 characters."))
        try:
            await user.send(message, allowed_mentions=NO_MENTIONS)
        except discord.Forbidden:
            return await reply(ctx, fail(f"I cannot message **{user}**. Their DMs are closed."))
        await reply(ctx, ok(f"Message sent to **{user}**."))

    @commands.command()
    @ctx_owner()
    async def sync(self, ctx, guild_id: int = None):
        if guild_id:
            target = discord.Object(id=guild_id)
            self.bot.tree.copy_global_to(guild=target)
            synced = await self.bot.tree.sync(guild=target)
            await reply(ctx, ok(f"Synced `{len(synced)}` slash commands to server `{guild_id}`."))
        else:
            synced = await self.bot.tree.sync()
            await reply(ctx, ok(f"Synced `{len(synced)}` slash commands globally."))


async def setup(bot):
    bot.command_prefix = resolve_prefix
    await bot.add_cog(Admin(bot))