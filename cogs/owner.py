import asyncio
import ipaddress
import logging
import os
import platform
import re
import socket
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from pymongo import ReturnDocument

from utils.db import (
    blacklist_col, premium_col, logs_col,
    global_status_col, server_status_col,
    updates_col, updates_seq_col, bot_state_col
)
from utils.helpers import BOT_OWNER_ID

log = logging.getLogger(__name__)

START_TIME = time.time()

THEME = 0x2B2D31
SUCCESS = 0x57F287
DANGER = 0xED4245
WARNING = 0xFEE75C
ACCENT = 0x5865F2

NO_MENTIONS = discord.AllowedMentions.none()

OWNER_GUILD_IDS = [int(x) for x in re.split(r"[,\s]+", os.getenv("OWNER_GUILD_ID") or "") if x.isdigit()]

MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

COG_NAME = re.compile(r"[a-z0-9_]{1,40}")
SNOWFLAKE = re.compile(r"\d{15,21}")
LOCKED_COGS = {"owner"}

MAX_REASON_LENGTH = 200
MAX_MAINTENANCE_REASON = 300
NOTICE_GAP_SECONDS = 15
MAX_STATUS_LENGTH = 128
MAX_BROADCAST_LENGTH = 3500
MAX_DM_LENGTH = 2000
BROADCAST_DELAY = 0.7
BROADCAST_NAMES = ("happy", "bot", "bots", "command", "commands", "cmd", "cmds")

ACTIVITY_TYPES = {"watching", "playing", "listening", "competing"}
TRUE_WORDS = {"on", "true", "1", "yes", "enable", "enabled"}
FALSE_WORDS = {"off", "false", "0", "no", "disable", "disabled"}

NEWS_LIMIT = 25
UPDATE_KINDS = {
    "update": ("Update", ACCENT),
    "fix": ("Fix", SUCCESS),
    "maintenance": ("Maintenance", WARNING),
    "notice": ("Notice", THEME),
}
KIND_CHOICES = [app_commands.Choice(name=label, value=key) for key, (label, _) in UPDATE_KINDS.items()]


def card(title=None, description=None, color=THEME):
    return discord.Embed(title=title, description=description, color=color)


def ok(text, title=None):
    return card(title, text, SUCCESS)


def fail(text, title=None):
    return card(title, text, DANGER)


async def reply(ctx, embed=None, **kwargs):
    return await ctx.reply(
        embed=embed,
        mention_author=False,
        allowed_mentions=NO_MENTIONS,
        **kwargs,
    )


def state_text(value):
    return "On" if value else "Off"


def parse_switch(value):
    if value is None:
        return None
    word = value.strip().lower()
    if word in TRUE_WORDS:
        return True
    if word in FALSE_WORDS:
        return False
    return None


def clean_text(value, limit):
    return (value or "").replace("`", "'").strip()[:limit]


def as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def format_uptime(seconds):
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


async def is_bot_owner(bot, user):
    return user.id == BOT_OWNER_ID and await bot.is_owner(user)


async def owner_check(interaction):
    return await is_bot_owner(interaction.client, interaction.user)


async def is_public_host(host):
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            if not ipaddress.ip_address(info[4][0]).is_global:
                return False
        except ValueError:
            return False
    return True


async def fetch_image(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return None, "Only secure links that start with https are allowed."
    if not await is_public_host(parsed.hostname):
        return None, "That link cannot be used."
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=False) as resp:
                if resp.status != 200:
                    return None, "The link did not give back an image."
                kind = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if kind not in IMAGE_TYPES:
                    return None, "The link must be a PNG, JPG, GIF or WEBP image."
                if resp.content_length and resp.content_length > MAX_IMAGE_BYTES:
                    return None, "The image is too big. Keep it under 10 MB."
                data = bytearray()
                async for chunk in resp.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > MAX_IMAGE_BYTES:
                        return None, "The image is too big. Keep it under 10 MB."
                return bytes(data), None
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None, "The image could not be downloaded. Please try again."


class Confirm(discord.ui.View):
    def __init__(self, owner_id, timeout=45):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.value = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=fail("These buttons are not for you."), ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.value = False
        await interaction.response.defer()
        self.stop()


async def ask(ctx, embeds):
    view = Confirm(ctx.author.id)
    message = await ctx.reply(
        embeds=embeds,
        view=view,
        mention_author=False,
        allowed_mentions=NO_MENTIONS,
    )
    await view.wait()
    return view.value is True, message


def news_embed(bot, entry, index, total, maintenance):
    label, color = UPDATE_KINDS.get(entry.get("kind"), UPDATE_KINDS["notice"])
    created = as_utc(entry["created_at"])
    embed = discord.Embed(
        title=entry["title"],
        description=entry["body"],
        color=color,
        timestamp=created,
    )
    embed.set_author(name=f"{bot.user.name} News", icon_url=bot.user.display_avatar.url)
    embed.add_field(name="Type", value=label, inline=True)
    embed.add_field(name="Posted", value=f"<t:{int(created.timestamp())}:R>", inline=True)
    embed.add_field(
        name="Bot status",
        value="Under maintenance" if maintenance else "Running normally",
        inline=True,
    )
    if entry.get("when"):
        embed.add_field(name="Schedule", value=entry["when"], inline=False)
    embed.set_footer(text=f"Post {index + 1} of {total}  |  Number {entry['number']}")
    return embed


class NewsView(discord.ui.View):
    def __init__(self, bot, interaction, entries):
        super().__init__(timeout=180)
        self.bot = bot
        self.interaction = interaction
        self.user_id = interaction.user.id
        self.entries = entries
        self.index = 0
        self.refresh()

    def refresh(self):
        self.newer.disabled = self.index == 0
        self.older.disabled = self.index >= len(self.entries) - 1

    def render(self):
        return news_embed(
            self.bot,
            self.entries[self.index],
            self.index,
            len(self.entries),
            bool(getattr(self.bot, "maintenance", False)),
        )

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                embed=fail("Use the news command to open your own copy."), ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Newer", style=discord.ButtonStyle.secondary)
    async def newer(self, interaction, button):
        self.index = max(0, self.index - 1)
        self.refresh()
        await interaction.response.edit_message(embed=self.render(), view=self)

    @discord.ui.button(label="Older", style=discord.ButtonStyle.primary)
    async def older(self, interaction, button):
        self.index = min(len(self.entries) - 1, self.index + 1)
        self.refresh()
        await interaction.response.edit_message(embed=self.render(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.interaction.edit_original_response(view=self)
        except discord.HTTPException:
            pass


class PostModal(discord.ui.Modal):
    def __init__(self, cog, kind):
        super().__init__(title=f"New {UPDATE_KINDS[kind][0]} post", timeout=900)
        self.cog = cog
        self.kind = kind
        self.heading = discord.ui.TextInput(
            label="Title",
            max_length=100,
            placeholder="A short and clear title",
        )
        self.details = discord.ui.TextInput(
            label="Details",
            style=discord.TextStyle.paragraph,
            max_length=3000,
            placeholder="Tell everyone what is new or what is changing",
        )
        self.schedule = discord.ui.TextInput(
            label="When (optional)",
            required=False,
            max_length=100,
            placeholder="Example: Tonight 10 PM to 11 PM UTC",
        )
        self.add_item(self.heading)
        self.add_item(self.details)
        self.add_item(self.schedule)

    async def on_submit(self, interaction):
        if not await is_bot_owner(interaction.client, interaction.user):
            return await interaction.response.send_message(
                embed=fail("Only the bot owner can post news."), ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        entry = await self.cog.save_post(
            self.kind,
            self.heading.value.strip(),
            self.details.value.strip(),
            self.schedule.value.strip(),
            interaction.user.id,
        )
        preview = news_embed(
            interaction.client, entry, 0, 1, bool(getattr(interaction.client, "maintenance", False))
        )
        await interaction.followup.send(
            embeds=[ok(f"Your post is live. Everyone can now see it with `/news`.", "Published"), preview],
            ephemeral=True,
        )

    async def on_error(self, interaction, error):
        log.exception("News post failed", exc_info=error)
        embed = fail("The post could not be saved. Please try again.")
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


class Owner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.broadcast_lock = asyncio.Lock()
        self.notice_times = {}
        self.original_tree_check = None
        if not hasattr(bot, "maintenance"):
            bot.maintenance = False
        bot.maintenance_reason = getattr(bot, "maintenance_reason", None)
        bot.maintenance_since = getattr(bot, "maintenance_since", None)
        for command in self.walk_commands():
            command.hidden = True

    async def cog_load(self):
        try:
            doc = await bot_state_col.find_one({"_id": "maintenance"}) or {}
        except Exception:
            log.exception("Could not read the maintenance state")
            doc = {}
        self.bot.maintenance = bool(doc.get("enabled"))
        self.bot.maintenance_reason = doc.get("reason")
        self.bot.maintenance_since = doc.get("since")
        if self.bot.tree.interaction_check != self.tree_check:
            self.original_tree_check = self.bot.tree.interaction_check
            self.bot.tree.interaction_check = self.tree_check

    def cog_unload(self):
        if self.original_tree_check is not None:
            self.bot.tree.interaction_check = self.original_tree_check

    @commands.Cog.listener()
    async def on_ready(self):
        if self.bot.maintenance:
            await self.apply_presence()

    def should_notify(self, user_id):
        now = time.monotonic()
        if len(self.notice_times) > 1000:
            self.notice_times = {k: v for k, v in self.notice_times.items() if now - v < NOTICE_GAP_SECONDS}
        last = self.notice_times.get(user_id)
        if last is not None and now - last < NOTICE_GAP_SECONDS:
            return False
        self.notice_times[user_id] = now
        return True

    def maintenance_embed(self):
        embed = card(
            "Under maintenance",
            "I am being updated right now, so commands are paused. Please try again soon.",
            WARNING,
        )
        if self.bot.maintenance_reason:
            embed.add_field(name="Details", value=self.bot.maintenance_reason, inline=False)
        embed.set_footer(text="Use /news to read the latest notice")
        return embed

    async def bot_check(self, ctx):
        if not self.bot.maintenance:
            return True
        if await is_bot_owner(self.bot, ctx.author):
            return True
        if self.should_notify(ctx.author.id):
            try:
                await reply(ctx, self.maintenance_embed(), delete_after=12)
            except discord.HTTPException:
                pass
        return False

    async def tree_check(self, interaction):
        if self.original_tree_check is not None and not await self.original_tree_check(interaction):
            return False
        if not self.bot.maintenance:
            return True
        data = interaction.data or {}
        if data.get("type", 1) == 1 and data.get("name") == "news":
            return True
        if await is_bot_owner(self.bot, interaction.user):
            return True
        try:
            await interaction.response.send_message(embed=self.maintenance_embed(), ephemeral=True)
        except discord.HTTPException:
            pass
        return False

    async def apply_presence(self):
        try:
            if self.bot.maintenance:
                await self.bot.change_presence(
                    status=discord.Status.dnd,
                    activity=discord.Activity(type=discord.ActivityType.playing, name="Under maintenance"),
                )
                return
            override = await global_status_col.find_one({"type": "owner"})
            if override and override.get("activity") in ACTIVITY_TYPES:
                activity = discord.Activity(
                    type=getattr(discord.ActivityType, override["activity"]), name=override["status"]
                )
                await self.bot.change_presence(status=discord.Status.online, activity=activity)
            else:
                await self.bot.change_presence(status=discord.Status.online, activity=None)
        except Exception:
            log.exception("Could not update the bot presence")

    async def cog_check(self, ctx):
        return await is_bot_owner(self.bot, ctx.author)

    async def cog_app_command_error(self, interaction, error):
        if isinstance(error, app_commands.CheckFailure) and not isinstance(
            error, app_commands.CommandOnCooldown
        ):
            text = "Only the bot owner can use this command."
        elif isinstance(error, app_commands.CommandOnCooldown):
            text = f"Please wait {error.retry_after:.0f} seconds before trying again."
        else:
            log.exception("Slash command failed", exc_info=error)
            text = "Something went wrong. Please try again in a moment."
        if interaction.response.is_done():
            await interaction.followup.send(embed=fail(text), ephemeral=True)
        else:
            await interaction.response.send_message(embed=fail(text), ephemeral=True)

    async def _change_image(self, ctx, field, url):
        label = "avatar" if field == "avatar" else "banner"
        source = url or (ctx.message.attachments[0].url if ctx.message.attachments else None)
        if not source:
            embed = card(f"Change bot {label}", "Send an image link or attach an image to your message.")
            embed.add_field(name="How to use", value=f"`{ctx.clean_prefix}set{label} <link>`", inline=False)
            return await reply(ctx, embed)

        async with ctx.typing():
            data, problem = await fetch_image(source)
        if problem:
            return await reply(ctx, fail(problem, "Image problem"))

        try:
            await self.bot.user.edit(**{field: data})
        except discord.HTTPException:
            log.exception("Could not change bot %s", label)
            return await reply(
                ctx,
                fail(
                    "Discord did not accept this image. You may be changing it too often. Wait a little and try again.",
                    "Could not update",
                ),
            )

        embed = ok(f"The bot {label} was updated.", f"{label.title()} updated")
        if field == "avatar":
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        await reply(ctx, embed)

    @commands.command(name="setavatar", aliases=["setpfp", "botavatar"])
    async def set_avatar(self, ctx, url: str = None):
        await self._change_image(ctx, "avatar", url)

    @commands.command(name="setbanner")
    async def set_banner(self, ctx, url: str = None):
        await self._change_image(ctx, "banner", url)

    @commands.command(name="setdisplayname", aliases=["setnick", "setname"])
    async def set_display_name(self, ctx, *, name: str = None):
        if not ctx.guild:
            return await reply(ctx, fail("This command works only inside a server."))
        if not ctx.guild.me.guild_permissions.change_nickname:
            return await reply(ctx, fail("I need the Change Nickname permission in this server."))
        name = name.strip() if name else None
        if name and len(name) > 32:
            return await reply(ctx, fail("A display name can have at most 32 characters.", "Name too long"))
        try:
            await ctx.guild.me.edit(nick=name or None)
        except discord.Forbidden:
            return await reply(
                ctx,
                fail("My role is too low to change my name here. Move my role higher.", "Not allowed"),
            )
        except discord.HTTPException:
            log.exception("Could not change display name")
            return await reply(ctx, fail("Discord did not accept that name. Try another one."))
        text = f"My display name is now **{discord.utils.escape_markdown(name)}**." if name else "My display name was reset."
        await reply(ctx, ok(text, "Display name updated"))

    @commands.group(invoke_without_command=True)
    async def blacklist(self, ctx):
        docs = await blacklist_col.find({}).sort("added_at", -1).to_list(200)
        shown = docs[:15]
        lines = [
            f"`{d['id']}`  **{d.get('type', 'user').title()}**  {clean_text(d.get('reason'), 80) or 'No reason given'}"
            for d in shown
        ]
        if len(docs) > len(shown):
            lines.append(f"And {len(docs) - len(shown)} more.")
        p = ctx.clean_prefix
        embed = card(
            f"Blacklist ({len(docs)})",
            "\n".join(lines) if lines else "Nobody is blacklisted right now.",
        )
        embed.add_field(
            name="Commands",
            value=(
                f"`{p}blacklist add user <id> [reason]`\n"
                f"`{p}blacklist add server <id> [reason]`\n"
                f"`{p}blacklist remove <id>`"
            ),
            inline=False,
        )
        await reply(ctx, embed)

    @blacklist.command(name="add")
    async def blacklist_add(self, ctx, type_: str = None, target_id: str = None, *, reason: str = None):
        kind = (type_ or "").lower()
        if kind not in ("user", "server") or not target_id or not SNOWFLAKE.fullmatch(target_id):
            return await reply(
                ctx,
                fail(f"Use `{ctx.clean_prefix}blacklist add <user|server> <id> [reason]`.", "Check your command"),
            )
        if kind == "user" and int(target_id) in (BOT_OWNER_ID, self.bot.user.id):
            return await reply(ctx, fail("You cannot blacklist the owner or the bot.", "Not allowed"))

        reason = clean_text(reason, MAX_REASON_LENGTH) or "No reason given"
        await blacklist_col.update_one(
            {"id": target_id},
            {"$set": {
                "id": target_id,
                "type": kind,
                "reason": reason,
                "added_by": str(ctx.author.id),
                "added_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )

        embed = ok(f"The {kind} `{target_id}` is now blacklisted.", "Blacklist updated")
        embed.add_field(name="Reason", value=reason, inline=False)

        if kind == "server":
            guild = self.bot.get_guild(int(target_id))
            if guild:
                try:
                    await guild.leave()
                    embed.add_field(name="Action", value="The bot left that server.", inline=False)
                except discord.HTTPException:
                    log.exception("Could not leave blacklisted server")
                    embed.add_field(name="Action", value="The bot could not leave that server yet.", inline=False)
        await reply(ctx, embed)

    @blacklist.command(name="remove")
    async def blacklist_remove(self, ctx, target_id: str = None):
        if not target_id or not SNOWFLAKE.fullmatch(target_id):
            return await reply(ctx, fail(f"Use `{ctx.clean_prefix}blacklist remove <id>`.", "Check your command"))
        result = await blacklist_col.delete_one({"id": target_id})
        if result.deleted_count:
            return await reply(ctx, ok(f"`{target_id}` was removed from the blacklist.", "Blacklist updated"))
        await reply(ctx, fail(f"`{target_id}` is not on the blacklist.", "Not found"))

    async def _manage_cog(self, ctx, action, cog):
        methods = {
            "reload": (self.bot.reload_extension, "reloaded"),
            "load": (self.bot.load_extension, "loaded"),
            "unload": (self.bot.unload_extension, "unloaded"),
        }
        if not cog:
            return await reply(ctx, fail(f"Use `{ctx.clean_prefix}{action} <cog name>`.", "Check your command"))
        name = cog.strip().lower()
        if not COG_NAME.fullmatch(name):
            return await reply(ctx, fail("A cog name can only use letters, numbers and underscores.", "Invalid name"))
        if action == "unload" and name in LOCKED_COGS:
            return await reply(
                ctx,
                fail("This cog cannot be unloaded. You would lose control of the bot.", "Not allowed"),
            )
        method, past = methods[action]
        try:
            await method(f"cogs.{name}")
        except commands.ExtensionNotFound:
            return await reply(ctx, fail("No cog with that name exists.", "Not found"))
        except commands.ExtensionAlreadyLoaded:
            return await reply(ctx, fail("That cog is already loaded.", "Already loaded"))
        except commands.ExtensionNotLoaded:
            return await reply(ctx, fail("That cog is not loaded.", "Not loaded"))
        except Exception:
            log.exception("Cog %s failed for %s", action, name)
            return await reply(
                ctx,
                fail(f"The cog could not be {past}. The full details were saved in the bot log.", "Something went wrong"),
            )
        await reply(ctx, ok(f"`{name}` was {past}.", f"Cog {past}"))

    @commands.command()
    async def reload(self, ctx, cog: str = None):
        await self._manage_cog(ctx, "reload", cog)

    @commands.command()
    async def load(self, ctx, cog: str = None):
        await self._manage_cog(ctx, "load", cog)

    @commands.command()
    async def unload(self, ctx, cog: str = None):
        await self._manage_cog(ctx, "unload", cog)

    @commands.command(aliases=["cogslist"])
    async def cogs(self, ctx):
        loaded = sorted(self.bot.extensions.keys())
        embed = card(
            f"Loaded cogs ({len(loaded)})",
            "\n".join(f"`{c}`" for c in loaded) if loaded else "No cogs are loaded.",
        )
        await reply(ctx, embed)

    @commands.command()
    async def botinfo(self, ctx):
        premium_count = await premium_col.count_documents({})
        blacklist_count = await blacklist_col.count_documents({})
        embed = card("Bot overview", color=ACCENT)
        embed.timestamp = datetime.now(timezone.utc)
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="Servers", value=f"**{len(self.bot.guilds):,}**", inline=True)
        embed.add_field(name="Users", value=f"**{len(self.bot.users):,}**", inline=True)
        embed.add_field(name="Latency", value=f"**{round(self.bot.latency * 1000)} ms**", inline=True)
        embed.add_field(name="Uptime", value=f"**{format_uptime(time.time() - START_TIME)}**", inline=True)
        embed.add_field(name="Premium", value=f"**{premium_count:,}**", inline=True)
        embed.add_field(name="Blacklisted", value=f"**{blacklist_count:,}**", inline=True)
        embed.add_field(
            name="Maintenance",
            value=f"**{state_text(getattr(self.bot, 'maintenance', False))}**",
            inline=True,
        )
        embed.add_field(name="Cogs", value=f"**{len(self.bot.extensions)}**", inline=True)
        embed.add_field(
            name="Versions",
            value=f"Python {platform.python_version()}\ndiscord.py {discord.__version__}",
            inline=True,
        )
        await reply(ctx, embed)

    async def _log_channel_map(self):
        result = {}
        async for doc in logs_col.find({}, {"guild_id": 1, "channel_id": 1}):
            if doc.get("guild_id") and doc.get("channel_id"):
                result[str(doc["guild_id"])] = doc["channel_id"]
        return result

    def _pick_channel(self, guild, log_map):
        me = guild.me
        if me is None:
            return None, None

        def usable(channel):
            perms = channel.permissions_for(me)
            return perms.view_channel and perms.send_messages and perms.embed_links

        best, best_rank = None, None
        for channel in guild.text_channels:
            if not usable(channel):
                continue
            tokens = re.split(r"[^a-z0-9]+", channel.name.lower())
            ranks = [BROADCAST_NAMES.index(t) for t in tokens if t in BROADCAST_NAMES]
            if ranks and (best_rank is None or min(ranks) < best_rank):
                best, best_rank = channel, min(ranks)
        if best:
            return best, "name"

        raw = log_map.get(str(guild.id))
        if raw:
            try:
                channel = guild.get_channel(int(raw))
            except (TypeError, ValueError):
                channel = None
            if isinstance(channel, discord.TextChannel) and usable(channel):
                return channel, "log"

        for channel in guild.text_channels:
            if usable(channel) and {"log", "logs"} & set(re.split(r"[^a-z0-9]+", channel.name.lower())):
                return channel, "log"
        return None, None

    def _notice_embed(self, message):
        embed = card("Notice", message, ACCENT)
        embed.set_author(name=self.bot.user.name, icon_url=self.bot.user.display_avatar.url)
        embed.set_footer(text="A message from the bot team")
        embed.timestamp = datetime.now(timezone.utc)
        return embed

    @commands.command()
    async def broadcast(self, ctx, *, message: str = None):
        if not message or not message.strip():
            embed = card("Broadcast", "Write the message you want to send to every server.")
            embed.add_field(name="How to use", value=f"`{ctx.clean_prefix}broadcast <message>`", inline=False)
            embed.add_field(
                name="Where it is sent",
                value=(
                    "A channel named happy, bot or command.\n"
                    "If none exists, the server log channel is used."
                ),
                inline=False,
            )
            return await reply(ctx, embed)

        message = message.strip()
        if len(message) > MAX_BROADCAST_LENGTH:
            return await reply(ctx, fail(f"Keep the message under {MAX_BROADCAST_LENGTH} characters.", "Too long"))
        if self.broadcast_lock.locked():
            return await reply(ctx, fail("A broadcast is already in progress.", "Please wait"))

        async with self.broadcast_lock:
            log_map = await self._log_channel_map()
            targets = []
            for guild in self.bot.guilds:
                channel, via = self._pick_channel(guild, log_map)
                if channel:
                    targets.append((channel, via))

            total = len(self.bot.guilds)
            if not targets:
                return await reply(ctx, fail("No server has a channel I can send to.", "Nothing to send"))

            notice = self._notice_embed(message)
            preview = card(
                "Confirm broadcast",
                f"This message will be sent to **{len(targets)}** of **{total}** servers.\nCheck the preview below.",
                WARNING,
            )
            confirmed, prompt = await ask(ctx, [preview, notice])
            if not confirmed:
                return await prompt.edit(
                    embeds=[card("Broadcast cancelled", "Nothing was sent.")], view=None
                )

            await prompt.edit(
                embeds=[card("Sending", f"Sending to **{len(targets)}** servers. This can take a while.", ACCENT)],
                view=None,
            )

            sent = failed = by_name = by_log = 0
            for channel, via in targets:
                try:
                    await channel.send(embed=notice, allowed_mentions=NO_MENTIONS)
                    sent += 1
                    if via == "name":
                        by_name += 1
                    else:
                        by_log += 1
                except discord.HTTPException:
                    failed += 1
                await asyncio.sleep(BROADCAST_DELAY)

            log.info("Broadcast by %s: sent=%s failed=%s", ctx.author.id, sent, failed)
            result = ok("The broadcast is finished.", "Broadcast complete")
            result.add_field(name="Delivered", value=f"**{sent}**", inline=True)
            result.add_field(name="Failed", value=f"**{failed}**", inline=True)
            result.add_field(name="No channel found", value=f"**{total - len(targets)}**", inline=True)
            result.add_field(
                name="How channels were found",
                value=f"By channel name: {by_name}\nBy log channel: {by_log}",
                inline=False,
            )
            await prompt.edit(embeds=[result], view=None)

    @commands.command()
    async def shutdown(self, ctx):
        warning = card("Shut down the bot?", "The bot will go offline until you start it again.", DANGER)
        confirmed, prompt = await ask(ctx, [warning])
        if not confirmed:
            return await prompt.edit(embeds=[card("Shutdown cancelled", "The bot is still running.")], view=None)
        await prompt.edit(embeds=[card("Shutting down", "Goodbye for now.", DANGER)], view=None)
        log.warning("Shutdown requested by %s", ctx.author.id)
        await self.bot.close()

    def maintenance_status_embed(self, prefix):
        on = bool(self.bot.maintenance)
        embed = card("Maintenance mode", color=WARNING if on else SUCCESS)
        embed.add_field(name="Status", value="**On**" if on else "**Off**", inline=True)
        if on and self.bot.maintenance_since:
            since = int(as_utc(self.bot.maintenance_since).timestamp())
            embed.add_field(name="Started", value=f"<t:{since}:R>", inline=True)
        if on and self.bot.maintenance_reason:
            embed.add_field(name="Details", value=self.bot.maintenance_reason, inline=False)
        embed.add_field(
            name="While it is on",
            value="Everyone except you is paused. Members can still read `/news`.",
            inline=False,
        )
        embed.add_field(
            name="How to use",
            value=f"`{prefix}maintenance on [details]`\n`{prefix}maintenance off`",
            inline=False,
        )
        return embed

    @commands.command()
    async def maintenance(self, ctx, status: str = None, *, reason: str = None):
        p = ctx.clean_prefix
        if status is None:
            return await reply(ctx, self.maintenance_status_embed(p))
        value = parse_switch(status)
        if value is None:
            return await reply(ctx, fail(f"Use `{p}maintenance on` or `{p}maintenance off`.", "Check your command"))
        if not value and not self.bot.maintenance:
            return await reply(ctx, card("Already off", "Maintenance mode is not running."))

        now = datetime.now(timezone.utc)
        details = clean_text(reason, MAX_MAINTENANCE_REASON) or None if value else None
        since = (self.bot.maintenance_since if self.bot.maintenance and value else now) if value else None
        try:
            await bot_state_col.update_one(
                {"_id": "maintenance"},
                {"$set": {
                    "enabled": value,
                    "reason": details,
                    "since": since,
                    "changed_by": str(ctx.author.id),
                    "changed_at": now,
                }},
                upsert=True,
            )
        except Exception:
            log.exception("Could not save the maintenance state")
            return await reply(ctx, fail("The change could not be saved. Please try again.", "Not saved"))

        self.bot.maintenance = value
        self.bot.maintenance_reason = details
        self.bot.maintenance_since = since
        self.notice_times.clear()
        await self.apply_presence()
        log.warning("Maintenance mode set to %s by %s", value, ctx.author.id)

        if value:
            embed = ok(
                "The bot is now paused for everyone except you. Members will see a short notice.",
                "Maintenance is on",
            )
            if details:
                embed.add_field(name="Details shown to members", value=details, inline=False)
        else:
            embed = ok("The bot is open for everyone again.", "Maintenance is off")
        await reply(ctx, embed)

    async def _status_embed(self, prefix):
        override = await global_status_col.find_one({"type": "owner"})
        docs = await server_status_col.find({}).to_list(50)

        embed = card("Bot status")
        if override:
            expires = override.get("expires_at")
            ends = f"<t:{int(as_utc(expires).timestamp())}:R>" if expires else "Never"
            embed.add_field(
                name="Global status",
                value=f"{override.get('activity', 'watching').title()} `{override['status']}`\nEnds: {ends}",
                inline=False,
            )
        else:
            embed.add_field(name="Global status", value="Not set. The normal rotation is running.", inline=False)

        if docs:
            lines = [f"`{d.get('guild_name', 'Unknown')}`  `{d['status']}`" for d in docs[:10]]
            if len(docs) > 10:
                lines.append(f"And {len(docs) - 10} more.")
            embed.add_field(name=f"Server statuses ({len(docs)})", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Server statuses", value="None set", inline=False)

        embed.add_field(
            name="Commands",
            value=(
                f"`{prefix}botstatus set <activity> <text>`\n"
                f"`{prefix}botstatus set24h <activity> <text>`\n"
                f"`{prefix}botstatus reset`\n"
                f"`{prefix}botstatus removeserver <server id>`"
            ),
            inline=False,
        )
        return embed

    async def _set_status(self, ctx, activity, text, hours):
        kind = (activity or "").lower()
        text = " ".join((text or "").split())
        if kind not in ACTIVITY_TYPES or not text:
            return await reply(
                ctx,
                fail("Pick watching, playing, listening or competing, then write the status text.", "Check your command"),
            )
        if len(text) > MAX_STATUS_LENGTH:
            return await reply(ctx, fail(f"Keep the status under {MAX_STATUS_LENGTH} characters.", "Too long"))

        expires = datetime.now(timezone.utc) + timedelta(hours=hours) if hours else None
        await global_status_col.update_one(
            {"type": "owner"},
            {"$set": {"type": "owner", "status": text, "activity": kind, "expires_at": expires}},
            upsert=True,
        )
        await self.bot.change_presence(
            activity=discord.Activity(type=getattr(discord.ActivityType, kind), name=text)
        )
        description = f"Status set to **{kind.title()}** `{text}`."
        if expires:
            description += f"\nIt ends <t:{int(expires.timestamp())}:R>."
        await reply(ctx, ok(description, "Status updated"))

    @commands.group(invoke_without_command=True)
    async def botstatus(self, ctx):
        await reply(ctx, await self._status_embed(ctx.clean_prefix))

    @botstatus.command(name="set")
    async def botstatus_set(self, ctx, activity: str = None, *, text: str = None):
        await self._set_status(ctx, activity, text, None)

    @botstatus.command(name="set24h")
    async def botstatus_set24h(self, ctx, activity: str = None, *, text: str = None):
        await self._set_status(ctx, activity, text, 24)

    @botstatus.command(name="reset")
    async def botstatus_reset(self, ctx):
        result = await global_status_col.delete_one({"type": "owner"})
        if result.deleted_count:
            return await reply(ctx, ok("The global status was removed. The normal rotation is back.", "Status reset"))
        await reply(ctx, fail("There is no global status to remove.", "Nothing to reset"))

    @botstatus.command(name="view")
    async def botstatus_view(self, ctx):
        await reply(ctx, await self._status_embed(ctx.clean_prefix))

    @botstatus.command(name="removeserver")
    async def botstatus_removeserver(self, ctx, guild_id: str = None):
        if not guild_id or not SNOWFLAKE.fullmatch(guild_id):
            return await reply(
                ctx, fail(f"Use `{ctx.clean_prefix}botstatus removeserver <server id>`.", "Check your command")
            )
        doc = await server_status_col.find_one_and_delete({"guild_id": guild_id})
        if not doc:
            return await reply(ctx, fail(f"No custom status was found for `{guild_id}`.", "Not found"))
        name = discord.utils.escape_markdown(str(doc.get("guild_name", guild_id)))
        await reply(ctx, ok(f"Removed the status of **{name}**.", "Server status removed"))

    @commands.command(aliases=["guildlist"])
    async def serverlist(self, ctx):
        guilds = sorted(self.bot.guilds, key=lambda g: g.member_count or 0, reverse=True)
        lines = [
            f"`{i + 1}.` **{discord.utils.escape_markdown(g.name)}**\n"
            f"{(g.member_count or 0):,} members  |  `{g.id}`"
            for i, g in enumerate(guilds[:15])
        ]
        if len(guilds) > 15:
            lines.append(f"And {len(guilds) - 15} more.")
        embed = card(f"Servers ({len(guilds)})", "\n\n".join(lines) if lines else "The bot is not in any server.")
        await reply(ctx, embed)

    @commands.command()
    async def leaveguild(self, ctx, guild_id: str = None):
        if not guild_id or not SNOWFLAKE.fullmatch(guild_id):
            return await reply(ctx, fail(f"Use `{ctx.clean_prefix}leaveguild <server id>`.", "Check your command"))
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            return await reply(ctx, fail(f"I am not in a server with the ID `{guild_id}`.", "Not found"))

        name = discord.utils.escape_markdown(guild.name)
        warning = card("Leave this server?", f"The bot will leave **{name}**.", WARNING)
        confirmed, prompt = await ask(ctx, [warning])
        if not confirmed:
            return await prompt.edit(embeds=[card("Cancelled", "The bot stayed in the server.")], view=None)
        try:
            await guild.leave()
        except discord.HTTPException:
            log.exception("Could not leave guild %s", guild_id)
            return await prompt.edit(embeds=[fail("I could not leave that server. Please try again.")], view=None)
        await prompt.edit(embeds=[ok(f"The bot left **{name}**.", "Left server")], view=None)

    @commands.command()
    async def dm(self, ctx, user: discord.User = None, *, message: str = None):
        if not user or not message or not message.strip():
            return await reply(ctx, fail(f"Use `{ctx.clean_prefix}dm @user <message>`.", "Check your command"))
        message = message.strip()
        if len(message) > MAX_DM_LENGTH:
            return await reply(ctx, fail(f"Keep the message under {MAX_DM_LENGTH} characters.", "Too long"))
        if user.bot:
            return await reply(ctx, fail("You cannot send a message to a bot.", "Not allowed"))
        embed = card("Message from the bot team", message, ACCENT)
        embed.set_author(name=self.bot.user.name, icon_url=self.bot.user.display_avatar.url)
        try:
            await user.send(embed=embed, allowed_mentions=NO_MENTIONS)
        except discord.HTTPException:
            return await reply(
                ctx, fail(f"I could not message **{discord.utils.escape_markdown(str(user))}**. Their DMs may be closed.")
            )
        await reply(ctx, ok(f"Your message was sent to **{discord.utils.escape_markdown(str(user))}**.", "Message sent"))

    @commands.command()
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def sync(self, ctx, target: str = None):
        p = ctx.clean_prefix
        word = (target or "").lower()
        if word == "owner" and not OWNER_GUILD_IDS:
            return await reply(
                ctx, fail("Add `OWNER_GUILD_ID` to your .env file first, then restart the bot.", "Not set up")
            )
        if target and word != "owner" and not SNOWFLAKE.fullmatch(target):
            return await reply(
                ctx, fail(f"Use `{p}sync`, `{p}sync owner` or `{p}sync <server id>`.", "Check your command")
            )
        try:
            if word == "owner":
                count = 0
                for guild_id in OWNER_GUILD_IDS:
                    count += len(await self.bot.tree.sync(guild=discord.Object(id=guild_id)))
                where = "your owner server"
            elif target:
                server = discord.Object(id=int(target))
                self.bot.tree.copy_global_to(guild=server)
                count = len(await self.bot.tree.sync(guild=server))
                where = f"server `{target}`"
            else:
                count = len(await self.bot.tree.sync())
                where = "all servers"
        except discord.HTTPException:
            log.exception("Slash command sync failed")
            return await reply(ctx, fail("Discord did not accept the sync. Please try again later.", "Sync failed"))
        await reply(ctx, ok(f"**{count}** slash commands were synced to {where}.", "Sync complete"))

    async def save_post(self, kind, title, body, when, author_id):
        counter = await updates_seq_col.find_one_and_update(
            {"_id": "bot_updates"},
            {"$inc": {"n": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        entry = {
            "number": counter["n"],
            "kind": kind,
            "title": title,
            "body": body,
            "when": when or None,
            "author_id": str(author_id),
            "created_at": datetime.now(timezone.utc),
        }
        await updates_col.insert_one(dict(entry))
        return entry

    @app_commands.command(name="news", description="See the latest bot updates and maintenance notices")
    @app_commands.describe(
        kind="Show only one type of post",
        public="Show the news to everyone in this channel",
    )
    @app_commands.choices(kind=KIND_CHOICES)
    @app_commands.checks.cooldown(1, 5.0)
    async def news(
        self,
        interaction: discord.Interaction,
        kind: app_commands.Choice[str] = None,
        public: bool = False,
    ):
        await interaction.response.defer(ephemeral=not public)
        query = {"kind": kind.value} if kind else {}
        entries = await updates_col.find(query).sort("number", -1).to_list(NEWS_LIMIT)
        if not entries:
            return await interaction.followup.send(
                embed=card("No news yet", "There are no posts to show right now. Check back soon."),
                ephemeral=not public,
            )
        view = NewsView(self.bot, interaction, entries)
        await interaction.followup.send(embed=view.render(), view=view, ephemeral=not public)

    update = app_commands.Group(
        name="update",
        description="Publish and manage bot news",
        guild_ids=OWNER_GUILD_IDS or None,
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    @update.command(name="post", description="Publish a new update or maintenance notice")
    @app_commands.describe(kind="The type of post")
    @app_commands.choices(kind=KIND_CHOICES)
    @app_commands.check(owner_check)
    async def update_post(self, interaction: discord.Interaction, kind: app_commands.Choice[str]):
        await interaction.response.send_modal(PostModal(self, kind.value))

    @update.command(name="list", description="See your recent posts and their numbers")
    @app_commands.check(owner_check)
    async def update_list(self, interaction: discord.Interaction):
        entries = await updates_col.find({}).sort("number", -1).to_list(15)
        if not entries:
            return await interaction.response.send_message(
                embed=card("No posts", "You have not published anything yet."), ephemeral=True
            )
        lines = []
        for entry in entries:
            label = UPDATE_KINDS.get(entry.get("kind"), UPDATE_KINDS["notice"])[0]
            stamp = int(as_utc(entry["created_at"]).timestamp())
            lines.append(f"`#{entry['number']}`  **{label}**  {entry['title']}  <t:{stamp}:R>")
        await interaction.response.send_message(
            embed=card("Recent posts", "\n".join(lines)), ephemeral=True
        )

    @update.command(name="remove", description="Delete a post by its number")
    @app_commands.describe(number="The post number shown in the list")
    @app_commands.check(owner_check)
    async def update_remove(self, interaction: discord.Interaction, number: app_commands.Range[int, 1, 1000000]):
        entry = await updates_col.find_one_and_delete({"number": number})
        if not entry:
            return await interaction.response.send_message(
                embed=fail(f"There is no post with the number `{number}`.", "Not found"), ephemeral=True
            )
        await interaction.response.send_message(
            embed=ok(f"Removed post `#{number}`: **{entry['title']}**", "Post removed"), ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(Owner(bot))