import asyncio
import io
import logging
import math
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Union

import discord
from discord import app_commands
from discord.ext import commands, tasks
from pymongo import ASCENDING, DESCENDING, ReturnDocument, UpdateOne
from pymongo.errors import DuplicateKeyError

from utils.db import invite_log_col, invites_col, msg_count_col
from utils.helpers import ctx_admin

log = logging.getLogger(__name__)

NEUTRAL = 0x2B2D31
BRAND = 0x5865F2
SUCCESS = 0x57F287
DANGER = 0xED4245
WARNING = 0xFAA61A
TEAL = 0x1ABC9C
PURPLE = 0x9B59B6
MEDALS = ["🥇", "🥈", "🥉"]
CODE_PATTERN = re.compile(r"^[A-Za-z0-9-]{2,32}$")
NO_MENTIONS = discord.AllowedMentions.none()
IMAGE_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}
STORED_TYPES = {
    discord.MessageType.default,
    discord.MessageType.reply,
    discord.MessageType.chat_input_command,
    discord.MessageType.context_menu_command,
}

MESSAGE_CACHE_LIMIT = 20000
IMAGE_CACHE_BYTES = 120 * 1024 * 1024
IMAGE_MAX_BYTES = 8 * 1024 * 1024
IMAGES_PER_MESSAGE = 4
QUEUE_LIMIT = 150
AUDIT_WAIT = 1.2
AUDIT_WINDOW = 20
NEW_ACCOUNT_DAYS = 7
SETTINGS_TTL = 60
IGNORE_LIMIT = 50
PANEL_TIMEOUT = 300
UPLOAD_MARGIN = 262144

invite_cache: dict[int, dict[str, int]] = {}
join_locks: dict[int, asyncio.Lock] = {}
settings_cache: dict[int, tuple[float, dict]] = {}
settings_locks: dict[int, asyncio.Lock] = {}

log_settings_col = invites_col.database["server_log_settings"]


@dataclass(frozen=True)
class LogType:
    key: str
    label: str
    emoji: str
    summary: str


LOG_TYPES = (
    LogType("members", "Joins & Leaves", "👥", "Who joined, who invited them, and who left."),
    LogType("moderation", "Moderation", "🔨", "Kicks, bans, unbans, timeouts, and AutoMod actions."),
    LogType("member_changes", "Member Changes", "✏️", "Nicknames, roles, usernames, and profile pictures."),
    LogType("messages", "Messages", "💬", "Deleted and edited messages, deleted images, and cleared chats."),
    LogType("voice", "Voice", "🔊", "Joining, leaving, and moving between voice channels."),
    LogType("channels", "Channels", "📁", "Channels and threads that are made, changed, or removed."),
    LogType("roles", "Roles", "🛡️", "Roles that are made, changed, or removed."),
    LogType("server", "Server", "⚙️", "Server settings, emojis, stickers, webhooks, and boosts."),
    LogType("invites", "Invites", "🔗", "Invite links that are made or removed."),
)
LOG_BY_KEY = {item.key: item for item in LOG_TYPES}


def clip(text, limit):
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def plain(text, limit=100):
    flat = " ".join(str(text).split())
    flat = "".join(ch for ch in flat if ch.isprintable())
    return clip(discord.utils.escape_markdown(discord.utils.escape_mentions(flat)), limit)


def code(text, limit=60):
    flat = " ".join(str(text).split()).replace("`", "'")
    flat = "".join(ch for ch in flat if ch.isprintable())
    return f"`{clip(flat, limit) or '—'}`"


def quote(text, limit):
    safe = clip(discord.utils.escape_markdown(discord.utils.escape_mentions(str(text))), limit)
    return "\n".join(f"> {line}" for line in safe.splitlines()) or "> —"


def stamp(moment, style="R"):
    return f"<t:{int(moment.timestamp())}:{style}>"


def span_text(seconds):
    seconds = int(max(seconds, 0))
    units = (
        ("year", 31536000),
        ("month", 2592000),
        ("day", 86400),
        ("hour", 3600),
        ("minute", 60),
    )
    parts = []
    for name, size in units:
        amount, seconds = divmod(seconds, size)
        if amount:
            parts.append(f"{amount} {name}" + ("" if amount == 1 else "s"))
        if len(parts) == 2:
            break
    return ", ".join(parts) if parts else "less than a minute"


def human_size(number):
    size = float(number)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{int(number)} B"


def count_text(number, word):
    return f"{number:,} {word}" + ("" if number == 1 else "s")


def place(position):
    return MEDALS[position - 1] if position <= 3 else f"`{position}.`"


def icon_of(user):
    asset = getattr(user, "display_avatar", None)
    return asset.url if asset else None


def mention_of(target):
    return getattr(target, "mention", None) or f"<@{target.id}>"


def person(user):
    if user is None:
        return "Unknown"
    mention = mention_of(user)
    return f"{mention} 🤖" if getattr(user, "bot", False) else mention


def actor_text(entry, subject_id=None):
    if entry is None:
        return "Unknown"
    user = entry.user
    if user is not None and subject_id is not None and user.id == subject_id:
        return "Themselves"
    return person(user)


def reason_text(reason):
    return plain(reason, 300) if reason else "No reason given"


def jump_url(guild_id, channel_id, message_id):
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def make_embed(title=None, description=None, color=BRAND, guild=None):
    embed = discord.Embed(title=title, description=description, color=color)
    if guild:
        embed.set_footer(text=guild.name, icon_url=guild.icon.url if guild.icon else None)
    return embed


def error_embed(text, guild=None):
    return make_embed("❌ Oops", text, DANGER, guild)


def success_embed(text, guild=None):
    return make_embed("✅ Done", text, SUCCESS, guild)


def event_embed(title, color, icon=None, text=None, fields=(), footer=None, thumbnail=None, image=None):
    embed = discord.Embed(
        description=clip(text, 3500) if text else None,
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=clip(title, 200), icon_url=icon)
    for name, value, inline in list(fields)[:25]:
        embed.add_field(name=clip(name, 250), value=clip(value, 1024) if value else "—", inline=inline)
    if footer:
        embed.set_footer(text=clip(footer, 2000))
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if image:
        embed.set_image(url=image)
    return embed


def shown(value):
    if value is None or value == "":
        return "None"
    return code(value, 80)


def enum_name(value):
    return str(getattr(value, "name", value)).replace("_", " ").title()


def yes_no(value):
    return "Yes" if value else "No"


def change_row(label, old, new):
    return (label, f"{old} → {new}", False)


def nice_permission(name):
    return name.replace("_", " ").title()


def channel_kind(channel):
    return enum_name(channel.type)


def channel_changes(before, after):
    rows = []
    if before.name != after.name:
        rows.append(change_row("Name", shown(before.name), shown(after.name)))
    old_topic = getattr(before, "topic", None)
    new_topic = getattr(after, "topic", None)
    if old_topic != new_topic:
        rows.append(change_row("Topic", plain(old_topic or "None", 150), plain(new_topic or "None", 150)))
    options = (
        ("nsfw", "Age-restricted", yes_no),
        ("slowmode_delay", "Slowmode", lambda value: f"{value}s"),
        ("bitrate", "Bitrate", lambda value: f"{value // 1000} kbps"),
        ("user_limit", "User limit", lambda value: str(value) if value else "None"),
    )
    for attr, label, convert in options:
        old = getattr(before, attr, None)
        new = getattr(after, attr, None)
        if old is None or new is None or old == new:
            continue
        rows.append(change_row(label, convert(old), convert(new)))
    if before.category_id != after.category_id:
        old_category = plain(before.category.name, 60) if before.category else "None"
        new_category = plain(after.category.name, 60) if after.category else "None"
        rows.append(change_row("Category", old_category, new_category))
    targets = set(before.overwrites) | set(after.overwrites)
    changed = [target for target in targets if before.overwrites.get(target) != after.overwrites.get(target)]
    if changed:
        names = ", ".join(mention_of(target) for target in changed[:6])
        if len(changed) > 6:
            names += f" and {len(changed) - 6} more"
        rows.append(("Permissions changed for", names, False))
    return rows


def role_changes(before, after):
    rows = []
    if before.name != after.name:
        rows.append(change_row("Name", shown(before.name), shown(after.name)))
    if before.color != after.color:
        rows.append(change_row("Color", code(str(before.color)), code(str(after.color))))
    if before.hoist != after.hoist:
        rows.append(change_row("Shown separately", yes_no(before.hoist), yes_no(after.hoist)))
    if before.mentionable != after.mentionable:
        rows.append(change_row("Anyone can mention", yes_no(before.mentionable), yes_no(after.mentionable)))
    gained = [name for name, value in after.permissions if value and not getattr(before.permissions, name)]
    lost = [name for name, value in before.permissions if value and not getattr(after.permissions, name)]
    if gained:
        rows.append(("Permissions added", clip(", ".join(nice_permission(n) for n in gained), 1000), False))
    if lost:
        rows.append(("Permissions removed", clip(", ".join(nice_permission(n) for n in lost), 1000), False))
    return rows


def guild_changes(before, after):
    rows = []
    if before.name != after.name:
        rows.append(change_row("Name", shown(before.name), shown(after.name)))
    if before.description != after.description:
        rows.append(
            change_row(
                "Description",
                plain(before.description or "None", 150),
                plain(after.description or "None", 150),
            )
        )
    if before.owner_id != after.owner_id:
        rows.append(change_row("Owner", f"<@{before.owner_id}>", f"<@{after.owner_id}>"))
    levels = (
        ("verification_level", "Verification level"),
        ("default_notifications", "Default notifications"),
        ("explicit_content_filter", "Content filter"),
    )
    for attr, label in levels:
        old, new = getattr(before, attr), getattr(after, attr)
        if old != new:
            rows.append(change_row(label, enum_name(old), enum_name(new)))
    places = (
        ("afk_channel", "AFK channel"),
        ("system_channel", "Welcome channel"),
        ("rules_channel", "Rules channel"),
    )
    for attr, label in places:
        old, new = getattr(before, attr), getattr(after, attr)
        if getattr(old, "id", None) != getattr(new, "id", None):
            rows.append(change_row(label, old.mention if old else "None", new.mention if new else "None"))
    if before.afk_timeout != after.afk_timeout:
        rows.append(change_row("AFK timeout", f"{before.afk_timeout}s", f"{after.afk_timeout}s"))
    if before.vanity_url_code != after.vanity_url_code:
        rows.append(change_row("Custom link", shown(before.vanity_url_code), shown(after.vanity_url_code)))
    return rows


def asset_key(asset):
    return asset.key if asset else None


async def load_settings(guild_id):
    cached = settings_cache.get(guild_id)
    if cached and time.monotonic() - cached[0] < SETTINGS_TTL:
        return cached[1]
    lock = settings_locks.setdefault(guild_id, asyncio.Lock())
    async with lock:
        cached = settings_cache.get(guild_id)
        if cached and time.monotonic() - cached[0] < SETTINGS_TTL:
            return cached[1]
        gid = str(guild_id)
        doc = await log_settings_col.find_one({"guild_id": gid})
        if doc is None:
            channels = {}
            legacy = await invite_log_col.find_one({"guild_id": gid})
            if legacy and legacy.get("channel_id"):
                channels["members"] = str(legacy["channel_id"])
            try:
                await log_settings_col.update_one(
                    {"guild_id": gid},
                    {"$setOnInsert": {"channels": channels, "ignored": []}},
                    upsert=True,
                )
            except DuplicateKeyError:
                pass
            doc = {"channels": channels, "ignored": []}
        settings = {
            "channels": {
                key: str(value)
                for key, value in (doc.get("channels") or {}).items()
                if key in LOG_BY_KEY and value
            },
            "ignored": {int(item) for item in doc.get("ignored", []) if str(item).isdigit()},
        }
        settings_cache[guild_id] = (time.monotonic(), settings)
        return settings


async def save_channels(guild_id, keys, channel_id):
    await load_settings(guild_id)
    if channel_id is None:
        update = {"$unset": {f"channels.{key}": "" for key in keys}}
    else:
        update = {"$set": {f"channels.{key}": str(channel_id) for key in keys}}
    await log_settings_col.update_one({"guild_id": str(guild_id)}, update, upsert=True)
    settings_cache.pop(guild_id, None)


async def toggle_ignored(guild_id, channel_id):
    settings = await load_settings(guild_id)
    gid = str(guild_id)
    if channel_id in settings["ignored"]:
        await log_settings_col.update_one({"guild_id": gid}, {"$pull": {"ignored": str(channel_id)}})
        added = False
    else:
        if len(settings["ignored"]) >= IGNORE_LIMIT:
            return None
        await log_settings_col.update_one(
            {"guild_id": gid}, {"$addToSet": {"ignored": str(channel_id)}}, upsert=True
        )
        added = True
    settings_cache.pop(guild_id, None)
    return added


def is_ignored(settings, channel):
    if channel is None:
        return False
    ids = {
        getattr(channel, "id", None),
        getattr(channel, "category_id", None),
        getattr(channel, "parent_id", None),
    }
    return bool(ids & settings["ignored"])


def channel_problem(guild, channel):
    if not isinstance(channel, discord.TextChannel):
        return "Please choose a normal text channel."
    perms = channel.permissions_for(guild.me)
    needs = (
        ("View Channel", perms.view_channel),
        ("Send Messages", perms.send_messages),
        ("Embed Links", perms.embed_links),
        ("Attach Files", perms.attach_files),
    )
    missing = [name for name, allowed in needs if not allowed]
    if missing:
        return f"I cannot post in {channel.mention}. Please give me: {', '.join(missing)}."
    return None


async def read_invites(guild):
    me = guild.me
    if me is None or not me.guild_permissions.manage_guild:
        return None
    try:
        return await guild.invites()
    except discord.HTTPException:
        return None


def invite_operation(guild_id, invite, fresh=False):
    return UpdateOne(
        {"guild_id": str(guild_id), "code": invite.code},
        {
            "$set": {
                "inviter_id": str(invite.inviter.id) if invite.inviter else None,
                "max_uses": invite.max_uses,
                "url": invite.url,
                "deleted": False,
            },
            "$setOnInsert": {
                "uses": 0 if fresh else (invite.uses or 0),
                "created_at": invite.created_at or datetime.now(timezone.utc),
            },
        },
        upsert=True,
    )


async def sync_invites(guild, fresh=False):
    invites = await read_invites(guild)
    if invites is None:
        invite_cache.pop(guild.id, None)
        return
    invite_cache[guild.id] = {inv.code: inv.uses or 0 for inv in invites}
    operations = [invite_operation(guild.id, inv, fresh) for inv in invites]
    if operations:
        await invites_col.bulk_write(operations, ordered=False)
    await invites_col.update_many(
        {"guild_id": str(guild.id), "code": {"$nin": list(invite_cache[guild.id])}},
        {"$set": {"deleted": True}},
    )


async def find_used_invite(guild):
    fresh = await read_invites(guild)
    if fresh is None:
        invite_cache.pop(guild.id, None)
        return None, None

    fresh_by_code = {inv.code: inv for inv in fresh}
    new_counts = {code_: inv.uses or 0 for code_, inv in fresh_by_code.items()}
    old_counts = invite_cache.get(guild.id)
    invite_cache[guild.id] = new_counts

    if old_counts is None:
        return None, None

    used = next(
        (code_ for code_, uses in new_counts.items() if uses > old_counts.get(code_, 0)), None
    )

    if used is None:
        gone = [code_ for code_ in old_counts if code_ not in new_counts]
        if gone:
            docs = await invites_col.find(
                {"guild_id": str(guild.id), "code": {"$in": gone}}
            ).to_list(len(gone))
            limits = {doc["code"]: doc.get("max_uses") for doc in docs}
            maybe = [
                code_ for code_ in gone if limits.get(code_) and old_counts[code_] + 1 >= limits[code_]
            ]
            if len(maybe) == 1:
                used = maybe[0]

    if used is None:
        return None, None

    doc = await invites_col.find_one_and_update(
        {"guild_id": str(guild.id), "code": used},
        {"$inc": {"uses": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None and used in fresh_by_code:
        await invites_col.bulk_write([invite_operation(guild.id, fresh_by_code[used])])
        doc = await invites_col.find_one({"guild_id": str(guild.id), "code": used})

    return used, (doc.get("inviter_id") if doc else None)


async def invite_totals(guild_id, user_id):
    rows = await invites_col.aggregate(
        [
            {"$match": {"guild_id": str(guild_id), "inviter_id": str(user_id)}},
            {
                "$group": {
                    "_id": None,
                    "total": {"$sum": "$uses"},
                    "active": {"$sum": {"$cond": [{"$eq": ["$deleted", True]}, 0, 1]}},
                }
            },
        ]
    ).to_list(1)
    if not rows:
        return 0, 0
    return rows[0]["total"], rows[0]["active"]


async def build_invites_embed(guild, member):
    total, active = await invite_totals(guild.id, member.id)
    embed = make_embed(color=BRAND, guild=guild)
    embed.set_author(name=f"{member.display_name} • Invites", icon_url=member.display_avatar.url)
    embed.add_field(name="👥 People invited", value=f"**{total:,}**", inline=True)
    embed.add_field(name="🔗 Active links", value=f"**{active:,}**", inline=True)
    return embed


async def build_messages_embed(guild, member):
    doc = await msg_count_col.find_one({"guild_id": str(guild.id), "user_id": str(member.id)})
    count = doc.get("count", 0) if doc else 0
    if count > 0:
        ahead = await msg_count_col.count_documents(
            {"guild_id": str(guild.id), "count": {"$gt": count}}
        )
        rank = f"**#{ahead + 1:,}**"
    else:
        rank = "Not ranked yet"
    embed = make_embed(color=BRAND, guild=guild)
    embed.set_author(name=f"{member.display_name} • Messages", icon_url=member.display_avatar.url)
    embed.add_field(name="💬 Messages sent", value=f"**{count:,}**", inline=True)
    embed.add_field(name="🏆 Server rank", value=rank, inline=True)
    return embed


@dataclass
class StoredMessage:
    id: int
    channel_id: int
    author_id: int
    author_name: str
    author_bot: bool
    content: str
    created_at: datetime
    files: list
    stickers: list
    embeds: int
    reply_to: Optional[int]
    own: bool
    saving: Optional[asyncio.Task] = None


class ConfirmView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who used this command can press these buttons.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Yes, reset it", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()


async def ask_to_confirm(ctx, text):
    view = ConfirmView(ctx.author.id)
    message = await ctx.reply(
        embed=make_embed("⚠️ Are you sure?", f"{text}\n\nThis cannot be undone.", DANGER, ctx.guild),
        view=view,
        mention_author=False,
    )
    await view.wait()
    return view.confirmed, message


async def safe_edit(message, **kwargs):
    try:
        await message.edit(**kwargs)
    except discord.HTTPException:
        pass


class TypeSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="All log types",
                value="all",
                emoji="✨",
                description="Change every log at once",
                default=True,
            )
        ]
        options.extend(
            discord.SelectOption(
                label=item.label, value=item.key, emoji=item.emoji, description=clip(item.summary, 100)
            )
            for item in LOG_TYPES
        )
        super().__init__(placeholder="Step 1 · Choose a log type", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        view.selected = self.values[0]
        for option in self.options:
            option.default = option.value == view.selected
        await view.update(interaction)


class ChannelPicker(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            placeholder="Step 2 · Choose where it should be posted",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        channel = view.guild.get_channel(self.values[0].id)
        problem = channel_problem(view.guild, channel)
        if problem:
            return await interaction.response.send_message(problem, ephemeral=True)
        await save_channels(view.guild.id, view.keys(), channel.id)
        await view.update(interaction)


class LogPanel(discord.ui.View):
    def __init__(self, cog, ctx):
        super().__init__(timeout=PANEL_TIMEOUT)
        self.cog = cog
        self.guild = ctx.guild
        self.author = ctx.author
        self.prefix = ctx.clean_prefix
        self.selected = "all"
        self.message = None
        self.add_item(TypeSelect())
        self.add_item(ChannelPicker())

    def keys(self):
        if self.selected == "all":
            return [item.key for item in LOG_TYPES]
        return [self.selected]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "Only the person who opened this panel can use it.", ephemeral=True
            )
            return False
        return True

    async def update(self, interaction):
        embed = await self.cog.panel_embed(self.guild, self.selected, self.prefix)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            await safe_edit(self.message, view=self)

    @discord.ui.button(label="Turn off", style=discord.ButtonStyle.danger, emoji="🚫", row=2)
    async def turn_off(self, interaction: discord.Interaction, button: discord.ui.Button):
        await save_channels(self.guild.id, self.keys(), None)
        await self.update(interaction)

    @discord.ui.button(label="Auto setup", style=discord.ButtonStyle.success, emoji="✨", row=2)
    async def auto_setup(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.guild.me.guild_permissions.manage_channels:
            return await interaction.response.send_message(
                "I need the **Manage Channels** permission to set this up.", ephemeral=True
            )
        await interaction.response.defer()
        created = None
        try:
            created = await self.cog.create_log_channels(self.guild, self.author)
        except discord.HTTPException:
            await interaction.followup.send(
                "Something went wrong while making the channels. Please try again.", ephemeral=True
            )
        embed = await self.cog.panel_embed(self.guild, self.selected, self.prefix)
        await interaction.edit_original_response(embed=embed, view=self)
        if created is not None:
            note = (
                f"Done! I made **{len(created)}** private log channels. Only admins can see them."
                if created
                else "Every log already has a channel."
            )
            await interaction.followup.send(note, ephemeral=True)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(view=None)


class Tracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.recent = OrderedDict()
        self.image_total = 0
        self.image_order = deque(maxlen=MESSAGE_CACHE_LIMIT * IMAGES_PER_MESSAGE)
        self.queues = {}
        self.workers = {}
        self.dropped = {}
        self.audit_memo = {}
        self.audit_locks = {}
        self.audit_used = OrderedDict()
        self.audit_claims = {}
        self.recent_bans = {}
        self.count_buffer = {}
        self.slash_waits = {}
        self.background = set()

    async def cog_check(self, ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        return True

    async def cog_load(self):
        self.flush_loop.start()
        self.spawn(self.prepare_database())
        if self.bot.is_ready():
            self.spawn(self.sync_all())

    async def cog_unload(self):
        self.flush_loop.cancel()
        for worker in list(self.workers.values()):
            worker.cancel()
        for task in list(self.background):
            task.cancel()
        await self.flush_now()

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        return task

    async def prepare_database(self):
        jobs = (
            (invites_col, [("guild_id", ASCENDING), ("code", ASCENDING)], {"unique": True}),
            (invites_col, [("guild_id", ASCENDING), ("inviter_id", ASCENDING)], {}),
            (msg_count_col, [("guild_id", ASCENDING), ("user_id", ASCENDING)], {"unique": True}),
            (msg_count_col, [("guild_id", ASCENDING), ("count", DESCENDING)], {}),
            (log_settings_col, [("guild_id", ASCENDING)], {"unique": True}),
        )
        for collection, keys, options in jobs:
            try:
                await collection.create_index(keys, **options)
            except Exception:
                log.warning("Could not create a database index", exc_info=True)

    async def sync_all(self):
        for guild in list(self.bot.guilds):
            try:
                await sync_invites(guild)
            except Exception:
                log.warning("Could not read invites for a server", exc_info=True)

    @tasks.loop(seconds=10)
    async def flush_loop(self):
        await self.flush_now()

    async def flush_now(self):
        if not self.count_buffer:
            return
        batch, self.count_buffer = self.count_buffer, {}
        operations = [
            UpdateOne({"guild_id": guild_id, "user_id": user_id}, {"$inc": {"count": amount}}, upsert=True)
            for (guild_id, user_id), amount in batch.items()
        ]
        try:
            await msg_count_col.bulk_write(operations, ordered=False)
        except Exception:
            log.warning("Could not save message counts", exc_info=True)
            for key, amount in batch.items():
                self.count_buffer[key] = self.count_buffer.get(key, 0) + amount

    def drop_buffered(self, guild_id, user_id=None):
        for key in list(self.count_buffer):
            if key[0] == str(guild_id) and (user_id is None or key[1] == str(user_id)):
                self.count_buffer.pop(key, None)

    def slash_wait(self, user_id):
        now = time.monotonic()
        ready = self.slash_waits.get(user_id, 0)
        if ready > now:
            return ready - now
        self.slash_waits[user_id] = now + 3
        if len(self.slash_waits) > 2000:
            self.slash_waits = {key: value for key, value in self.slash_waits.items() if value > now}
        return 0

    def post(self, channel, embed, files=None):
        guild_id = channel.guild.id
        queue = self.queues.get(guild_id)
        if queue is None:
            queue = asyncio.Queue(maxsize=QUEUE_LIMIT)
            self.queues[guild_id] = queue
            self.workers[guild_id] = asyncio.create_task(self.drain(guild_id, queue))
        try:
            queue.put_nowait((channel, embed, files or []))
        except asyncio.QueueFull:
            self.dropped[guild_id] = self.dropped.get(guild_id, 0) + 1

    async def drain(self, guild_id, queue):
        while True:
            channel, embed, files = await queue.get()
            try:
                await channel.send(embed=embed, files=files, allowed_mentions=NO_MENTIONS)
            except discord.HTTPException:
                if files:
                    try:
                        await channel.send(embed=embed, allowed_mentions=NO_MENTIONS)
                    except discord.HTTPException:
                        pass
            except Exception:
                log.warning("Could not send a log message", exc_info=True)
            skipped = self.dropped.pop(guild_id, 0)
            if skipped:
                notice = event_embed(
                    "⚠️ Too much activity",
                    WARNING,
                    text=f"{count_text(skipped, 'log')} were skipped because the server was very busy.",
                )
                try:
                    await channel.send(embed=notice, allowed_mentions=NO_MENTIONS)
                except discord.HTTPException:
                    pass

    async def log_channel(self, guild, key):
        settings = await load_settings(guild.id)
        channel_id = settings["channels"].get(key)
        if not channel_id or guild.me is None:
            return None
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return None
        perms = channel.permissions_for(guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            return None
        return channel

    async def target(self, guild, key, fallback=None):
        channel = await self.log_channel(guild, key)
        if channel is None and fallback:
            channel = await self.log_channel(guild, fallback)
        return channel

    async def recent_audit(self, guild, action):
        key = (guild.id, action)
        memo = self.audit_memo.get(key)
        if memo and time.monotonic() - memo[0] < 1.0:
            return memo[1]
        lock = self.audit_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            memo = self.audit_memo.get(key)
            if memo and time.monotonic() - memo[0] < 1.0:
                return memo[1]
            try:
                entries = [entry async for entry in guild.audit_logs(limit=20, action=action)]
            except discord.HTTPException:
                entries = []
            self.audit_memo[key] = (time.monotonic(), entries)
            return entries

    def can_audit(self, guild):
        return guild.me is not None and guild.me.guild_permissions.view_audit_log

    async def find_audit(self, guild, action, match, wait=True):
        if not self.can_audit(guild):
            return None
        if wait:
            await asyncio.sleep(AUDIT_WAIT)
        many = isinstance(action, tuple)
        wanted = action if many else (action,)
        entries = await self.recent_audit(guild, None if many else action)
        now = discord.utils.utcnow()
        for entry in entries:
            if entry.action not in wanted:
                continue
            if (now - entry.created_at).total_seconds() > AUDIT_WINDOW:
                continue
            if entry.id in self.audit_used:
                continue
            try:
                matched = bool(match(entry))
            except Exception:
                matched = False
            if matched:
                self.audit_used[entry.id] = None
                while len(self.audit_used) > 300:
                    self.audit_used.popitem(last=False)
                return entry
        return None

    async def find_message_deleter(self, guild, author_id, channel_id):
        if not self.can_audit(guild):
            return None
        await asyncio.sleep(AUDIT_WAIT)
        entries = await self.recent_audit(guild, discord.AuditLogAction.message_delete)
        now = discord.utils.utcnow()
        if len(self.audit_claims) > 500:
            self.audit_claims.clear()
        for entry in entries:
            try:
                target_id = entry.target.id
                source_id = entry.extra.channel.id
                total = int(getattr(entry.extra, "count", 1) or 1)
            except Exception:
                continue
            known = self.audit_claims.get(entry.id)
            if known is None:
                fresh = (now - entry.created_at).total_seconds() <= AUDIT_WINDOW
                known = 0 if fresh else total
                self.audit_claims[entry.id] = known
            if target_id == author_id and source_id == channel_id and known < total:
                self.audit_claims[entry.id] = known + 1
                return entry
        return None

    def snapshot(self, message):
        files = [
            {
                "name": item.filename,
                "size": item.size,
                "type": (item.content_type or "").split(";")[0].strip().lower(),
                "url": item.url,
                "data": None,
            }
            for item in message.attachments
        ]
        author = message.author
        return StoredMessage(
            id=message.id,
            channel_id=message.channel.id,
            author_id=author.id,
            author_name=plain(author.display_name, 80),
            author_bot=bool(author.bot or message.webhook_id),
            content=message.content or "",
            created_at=message.created_at,
            files=files,
            stickers=[sticker.name for sticker in message.stickers],
            embeds=len(message.embeds),
            reply_to=message.reference.message_id if message.reference else None,
            own=self.bot.user is not None and author.id == self.bot.user.id,
        )

    def release(self, entry):
        for item in entry.files:
            data = item.get("data")
            if data is not None:
                self.image_total -= len(data)
                item["data"] = None

    def add_image(self, entry, index, data):
        entry.files[index]["data"] = data
        self.image_total += len(data)
        self.image_order.append(entry.id)
        while self.image_total > IMAGE_CACHE_BYTES and self.image_order:
            old = self.recent.get(self.image_order.popleft())
            if old is not None and old is not entry:
                self.release(old)

    async def save_images(self, message, entry):
        for index, attachment in enumerate(message.attachments[:IMAGES_PER_MESSAGE]):
            kind = entry.files[index]["type"]
            if kind not in IMAGE_TYPES or attachment.size > IMAGE_MAX_BYTES:
                continue
            try:
                data = await attachment.read()
            except discord.HTTPException:
                continue
            if len(data) > IMAGE_MAX_BYTES or self.recent.get(entry.id) is not entry:
                continue
            self.add_image(entry, index, data)

    async def remember(self, message):
        settings = await load_settings(message.guild.id)
        if "messages" not in settings["channels"] or message.type not in STORED_TYPES:
            return
        log_ids = {int(value) for value in settings["channels"].values()}
        if is_ignored(settings, message.channel) and message.channel.id not in log_ids:
            return
        entry = self.snapshot(message)
        self.recent[message.id] = entry
        while len(self.recent) > MESSAGE_CACHE_LIMIT:
            _, oldest = self.recent.popitem(last=False)
            self.release(oldest)
        wants_images = not entry.author_bot and any(item["type"] in IMAGE_TYPES for item in entry.files)
        if wants_images:
            entry.saving = self.spawn(self.save_images(message, entry))

    def collect_files(self, guild, entry):
        budget = guild.filesize_limit - UPLOAD_MARGIN
        lines = []
        files = []
        for index, item in enumerate(entry.files, 1):
            label = f"{index}. {code(item['name'], 40)} · {human_size(item['size'])}"
            data = item["data"]
            if item["type"] in IMAGE_TYPES and data is not None and len(data) <= budget:
                budget -= len(data)
                name = f"deleted_image_{len(files) + 1}.{IMAGE_TYPES[item['type']]}"
                files.append(discord.File(io.BytesIO(data), filename=name))
                lines.append(f"{label} · shown below")
            elif item["type"].startswith("image/"):
                lines.append(f"{label} · could not be saved")
            else:
                lines.append(f"{label} · file")
        return lines, files

    def deleted_by_text(self, guild, entry, executor):
        if executor is not None:
            user = executor.user
            if user is not None and user.id == entry.author_id:
                return "The author"
            return person(user)
        if not self.can_audit(guild):
            return "Unknown (I need View Audit Log)"
        return "Probably the bot itself" if entry.author_bot else "Probably the author"

    async def panel_embed(self, guild, selected="all", prefix="!"):
        settings = await load_settings(guild.id)
        embed = discord.Embed(
            title="Server Logs",
            description="Choose a log type, then pick the channel where it should be posted.",
            color=BRAND,
        )
        embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
        for item in LOG_TYPES:
            channel_id = settings["channels"].get(item.key)
            channel = guild.get_channel(int(channel_id)) if channel_id else None
            if channel is not None:
                state = f"🟢 {channel.mention}"
            elif channel_id:
                state = "🔴 Channel missing"
            else:
                state = "⚪ Off"
            pointer = "▸ " if item.key == selected else ""
            embed.add_field(name=f"{pointer}{item.emoji} {item.label}", value=state, inline=True)
        if selected == "all":
            info = "**All log types**\nThe channel you pick will be used for every log."
        else:
            item = LOG_BY_KEY[selected]
            info = f"**{item.label}**\n{item.summary}"
        embed.add_field(name="Selected", value=info, inline=False)
        embed.add_field(name="Checks", value=self.health_text(guild, settings), inline=False)
        embed.set_footer(
            text=f"Skipped places: {len(settings['ignored'])} • Use {prefix}logignore to skip a channel"
        )
        return embed

    def health_text(self, guild, settings):
        perms = guild.me.guild_permissions
        lines = []
        if perms.view_audit_log:
            lines.append("✅ I can see who did each action.")
        else:
            lines.append("⚠️ Give me **View Audit Log** so I can show who did each action.")
        if perms.manage_guild:
            lines.append("✅ I can track invites.")
        else:
            lines.append("⚠️ Give me **Manage Server** so I can track invites.")
        if self.bot.intents.message_content:
            lines.append("✅ I can read message text.")
        else:
            lines.append("⚠️ I cannot read message text. Ask the bot owner to turn on **Message Content**.")
        if self.bot.intents.members:
            lines.append("✅ I can see member changes.")
        else:
            lines.append("⚠️ I cannot see member changes. Ask the bot owner to turn on **Server Members**.")
        broken = []
        public = []
        for key, channel_id in settings["channels"].items():
            channel = guild.get_channel(int(channel_id))
            if channel is None:
                continue
            if channel_problem(guild, channel):
                broken.append(channel.mention)
            elif channel.permissions_for(guild.default_role).view_channel:
                public.append(channel.mention)
        if broken:
            lines.append(f"⚠️ I cannot post in: {', '.join(sorted(set(broken)))}")
        if public:
            lines.append(f"🔓 Everyone can see: {', '.join(sorted(set(public)))}. Make them private.")
        return clip("\n".join(lines), 1024)

    async def create_log_channels(self, guild, author):
        settings = await load_settings(guild.id)
        missing = []
        for item in LOG_TYPES:
            channel_id = settings["channels"].get(item.key)
            if not channel_id or guild.get_channel(int(channel_id)) is None:
                missing.append(item)
        if not missing:
            return []
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                read_message_history=True,
            ),
        }
        reason = f"Log setup by {author}"[:400]
        category = await guild.create_category("Server Logs", overwrites=overwrites, reason=reason)
        created = []
        for item in missing:
            channel = await category.create_text_channel(
                f"log-{item.key.replace('_', '-')}", topic=item.summary, reason=reason
            )
            await save_channels(guild.id, [item.key], channel.id)
            created.append(channel)
        return created

    @commands.Cog.listener()
    async def on_ready(self):
        await self.sync_all()

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        try:
            await sync_invites(guild)
        except Exception:
            log.warning("Could not read invites for a new server", exc_info=True)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        invite_cache.pop(guild.id, None)
        join_locks.pop(guild.id, None)
        settings_cache.pop(guild.id, None)
        settings_locks.pop(guild.id, None)
        self.queues.pop(guild.id, None)
        self.dropped.pop(guild.id, None)
        self.audit_locks.pop(guild.id, None)
        worker = self.workers.pop(guild.id, None)
        if worker:
            worker.cancel()
        for key in [key for key in self.audit_memo if key[0] == guild.id]:
            self.audit_memo.pop(key, None)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        guild = self.bot.get_guild(invite.guild.id) if invite.guild else None
        if guild is None:
            return
        if guild.id in invite_cache:
            invite_cache[guild.id][invite.code] = invite.uses or 0
        try:
            await invites_col.bulk_write([invite_operation(guild.id, invite)])
        except Exception:
            log.warning("Could not save a new invite", exc_info=True)
        channel = await self.log_channel(guild, "invites")
        if channel is None:
            return
        if invite.max_age:
            expires = stamp(discord.utils.utcnow() + timedelta(seconds=invite.max_age), "R")
        else:
            expires = "Never"
        fields = [
            ("Link", f"[{code(invite.code, 32)}]({invite.url})", True),
            ("Created by", person(invite.inviter), True),
            ("Channel", mention_of(invite.channel) if invite.channel else "Unknown", True),
            ("Expires", expires, True),
            ("Use limit", str(invite.max_uses) if invite.max_uses else "No limit", True),
            ("Temporary", yes_no(invite.temporary), True),
        ]
        embed = event_embed(
            "🔗 Invite created",
            TEAL,
            icon_of(invite.inviter),
            fields=fields,
            footer=f"Code {invite.code}",
        )
        self.post(channel, embed)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        guild = self.bot.get_guild(invite.guild.id) if invite.guild else None
        if guild is None:
            return
        doc = None
        try:
            doc = await invites_col.find_one_and_update(
                {"guild_id": str(guild.id), "code": invite.code},
                {"$set": {"deleted": True}},
            )
        except Exception:
            log.warning("Could not mark an invite as deleted", exc_info=True)
        channel = await self.log_channel(guild, "invites")
        if channel is None:
            return

        def same_invite(entry):
            found = getattr(entry.target, "code", None) or getattr(entry.before, "code", None)
            return found == invite.code

        entry = await self.find_audit(guild, discord.AuditLogAction.invite_delete, same_invite)
        if entry:
            removed_by = actor_text(entry)
        elif self.can_audit(guild):
            removed_by = "Expired or used up"
        else:
            removed_by = "Unknown"
        fields = [
            ("Link", code(invite.code, 32), True),
            ("Channel", mention_of(invite.channel) if invite.channel else "Unknown", True),
            ("Removed by", removed_by, True),
        ]
        if doc:
            if doc.get("inviter_id"):
                fields.append(("Created by", f"<@{doc['inviter_id']}>", True))
            fields.append(("Times used", f"{doc.get('uses', 0):,}", True))
        embed = event_embed(
            "🔗 Invite removed", WARNING, fields=fields, footer=f"Code {invite.code}"
        )
        self.post(channel, embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        used_code, inviter_id = None, None
        if not member.bot:
            try:
                lock = join_locks.setdefault(guild.id, asyncio.Lock())
                async with lock:
                    used_code, inviter_id = await find_used_invite(guild)
            except Exception:
                log.warning("Could not find the invite a member used", exc_info=True)

        channel = await self.log_channel(guild, "members")
        if channel is None:
            return

        age = discord.utils.utcnow() - member.created_at
        fields = [
            ("Account created", f"{stamp(member.created_at, 'D')}\n{stamp(member.created_at, 'R')}", True)
        ]
        if member.bot:
            entry = await self.find_audit(
                guild, discord.AuditLogAction.bot_add, lambda item: item.target.id == member.id
            )
            fields.append(("Added by", actor_text(entry), True))
        else:
            inviter_text = "Unknown"
            if inviter_id:
                inviter_text = f"<@{inviter_id}>"
                try:
                    total, _ = await invite_totals(guild.id, inviter_id)
                    inviter_text += f"\n{count_text(total, 'invite')}"
                except Exception:
                    pass
            fields.append(("Invited by", inviter_text, True))
            fields.append(("Invite", code(used_code, 32) if used_code else "Could not find it", True))
        fields.append(("Member count", f"{guild.member_count:,}", True))
        if age.days < NEW_ACCOUNT_DAYS:
            fields.append(("⚠️ New account", f"Only {span_text(age.total_seconds())} old", True))

        title = "🤖 Bot added" if member.bot else "📥 Member joined"
        embed = event_embed(
            title,
            SUCCESS,
            icon_of(member),
            text=f"{member.mention} joined the server.",
            fields=fields,
            footer=f"User ID {member.id}",
            thumbnail=member.display_avatar.url,
        )
        self.post(channel, embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        members_channel = await self.log_channel(guild, "members")
        moderation_channel = await self.log_channel(guild, "moderation")
        if members_channel is None and moderation_channel is None:
            return
        await asyncio.sleep(AUDIT_WAIT)
        if self.recent_bans.pop((guild.id, member.id), None):
            return

        kick = await self.find_audit(
            guild, discord.AuditLogAction.kick, lambda item: item.target.id == member.id, wait=False
        )
        if kick is not None:
            channel = moderation_channel or members_channel
            embed = event_embed(
                "👢 Member kicked",
                DANGER,
                icon_of(member),
                text=f"{member.mention} was kicked from the server.",
                fields=[
                    ("Kicked by", actor_text(kick), True),
                    ("Reason", reason_text(kick.reason), True),
                ],
                footer=f"User ID {member.id}",
                thumbnail=member.display_avatar.url,
            )
            self.post(channel, embed)
            return

        if members_channel is None:
            return
        roles = [role.mention for role in reversed(member.roles) if not role.is_default()]
        shown_roles = ", ".join(roles[:12]) + (f" and {len(roles) - 12} more" if len(roles) > 12 else "")
        fields = []
        if member.joined_at:
            stayed = (discord.utils.utcnow() - member.joined_at).total_seconds()
            fields.append(("Joined", f"{stamp(member.joined_at, 'D')}\n{stamp(member.joined_at, 'R')}", True))
            fields.append(("Time here", span_text(stayed), True))
        else:
            fields.append(("Joined", "Unknown", True))
        fields.append(("Members left", f"{guild.member_count:,}", True))
        fields.append(("Roles", shown_roles or "None", False))
        title = "🤖 Bot removed" if member.bot else "📤 Member left"
        embed = event_embed(
            title,
            DANGER,
            icon_of(member),
            text=f"**{plain(str(member), 80)}** left the server.",
            fields=fields,
            footer=f"User ID {member.id}",
            thumbnail=member.display_avatar.url,
        )
        self.post(members_channel, embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        now = time.monotonic()
        self.recent_bans = {key: value for key, value in self.recent_bans.items() if now - value < 60}
        self.recent_bans[(guild.id, user.id)] = now
        channel = await self.target(guild, "moderation", "members")
        if channel is None:
            return
        entry = await self.find_audit(guild, discord.AuditLogAction.ban, lambda item: item.target.id == user.id)
        reason = entry.reason if entry else None
        if reason is None:
            try:
                reason = (await guild.fetch_ban(user)).reason
            except discord.HTTPException:
                reason = None
        embed = event_embed(
            "🔨 Member banned",
            DANGER,
            icon_of(user),
            text=f"{user.mention} was banned from the server.",
            fields=[("Banned by", actor_text(entry), True), ("Reason", reason_text(reason), True)],
            footer=f"User ID {user.id}",
            thumbnail=user.display_avatar.url,
        )
        self.post(channel, embed)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        channel = await self.target(guild, "moderation", "members")
        if channel is None:
            return
        entry = await self.find_audit(guild, discord.AuditLogAction.unban, lambda item: item.target.id == user.id)
        embed = event_embed(
            "🔓 Member unbanned",
            SUCCESS,
            icon_of(user),
            text=f"{user.mention} was unbanned.",
            fields=[
                ("Unbanned by", actor_text(entry), True),
                ("Reason", reason_text(entry.reason if entry else None), True),
            ],
            footer=f"User ID {user.id}",
            thumbnail=user.display_avatar.url,
        )
        self.post(channel, embed)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        guild = after.guild
        icon = icon_of(after)
        footer = f"User ID {after.id}"

        if before.nick != after.nick:
            channel = await self.log_channel(guild, "member_changes")
            if channel is not None:
                entry = await self.find_audit(
                    guild,
                    discord.AuditLogAction.member_update,
                    lambda item: item.target.id == after.id and hasattr(item.after, "nick"),
                )
                embed = event_embed(
                    "✏️ Nickname changed",
                    PURPLE,
                    icon,
                    text=f"{after.mention} has a new nickname.",
                    fields=[
                        ("Before", shown(before.nick), True),
                        ("After", shown(after.nick), True),
                        ("Changed by", actor_text(entry, after.id), True),
                    ],
                    footer=footer,
                )
                self.post(channel, embed)

        added = [role for role in after.roles if role not in before.roles]
        removed = [role for role in before.roles if role not in after.roles]
        if added or removed:
            channel = await self.log_channel(guild, "member_changes")
            if channel is not None:
                entry = await self.find_audit(
                    guild,
                    discord.AuditLogAction.member_role_update,
                    lambda item: item.target.id == after.id,
                )
                fields = []
                if added:
                    fields.append(("Roles added", clip(", ".join(role.mention for role in added), 1000), False))
                if removed:
                    fields.append(("Roles removed", clip(", ".join(role.mention for role in removed), 1000), False))
                fields.append(("Changed by", actor_text(entry, after.id), True))
                embed = event_embed(
                    "🛡️ Roles changed",
                    PURPLE,
                    icon,
                    text=f"The roles of {after.mention} were changed.",
                    fields=fields,
                    footer=footer,
                )
                self.post(channel, embed)

        if before.timed_out_until != after.timed_out_until:
            channel = await self.target(guild, "moderation", "member_changes")
            if channel is not None:
                entry = await self.find_audit(
                    guild,
                    discord.AuditLogAction.member_update,
                    lambda item: item.target.id == after.id and hasattr(item.after, "timed_out_until"),
                )
                if after.timed_out_until:
                    title = "⏳ Member timed out"
                    until = f"{stamp(after.timed_out_until, 'F')}\n{stamp(after.timed_out_until, 'R')}"
                    fields = [("Until", until, True)]
                    color = WARNING
                    text = f"{after.mention} was timed out."
                else:
                    title = "✅ Timeout removed"
                    fields = []
                    color = SUCCESS
                    text = f"The timeout for {after.mention} was removed."
                fields.append(("By", actor_text(entry), True))
                if entry and after.timed_out_until:
                    fields.append(("Reason", reason_text(entry.reason), True))
                self.post(channel, event_embed(title, color, icon, text=text, fields=fields, footer=footer))

        if before.premium_since != after.premium_since:
            channel = await self.log_channel(guild, "server")
            if channel is not None:
                if after.premium_since and not before.premium_since:
                    title, text, color = "💎 Server boosted", f"{after.mention} boosted the server.", PURPLE
                else:
                    title, text, color = "💔 Boost ended", f"{after.mention} stopped boosting the server.", NEUTRAL
                fields = [("Total boosts", f"{guild.premium_subscription_count:,}", True)]
                self.post(channel, event_embed(title, color, icon, text=text, fields=fields, footer=footer))

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User):
        rows = []
        if before.name != after.name:
            rows.append(("Username", code(before.name), code(after.name)))
        old_display = getattr(before, "global_name", None)
        new_display = getattr(after, "global_name", None)
        if old_display != new_display:
            rows.append(("Display name", shown(old_display), shown(new_display)))
        avatar_changed = asset_key(before.avatar) != asset_key(after.avatar)
        if not rows and not avatar_changed:
            return
        fields = [(label, f"{old} → {new}", False) for label, old, new in rows]
        if avatar_changed:
            fields.append(("Old picture", f"[Open it]({before.display_avatar.url})", True))
        for guild in list(self.bot.guilds):
            if guild.get_member(after.id) is None:
                continue
            channel = await self.log_channel(guild, "member_changes")
            if channel is None:
                continue
            embed = event_embed(
                "🖼️ Profile changed",
                PURPLE,
                icon_of(after),
                text=f"{after.mention} updated their profile.",
                fields=fields,
                footer=f"User ID {after.id}",
                thumbnail=after.display_avatar.url,
            )
            self.post(channel, embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return
        if not (message.author.bot or message.webhook_id):
            key = (str(message.guild.id), str(message.author.id))
            self.count_buffer[key] = self.count_buffer.get(key, 0) + 1
        await self.remember(message)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        entry = self.recent.get(payload.message_id)
        if entry is None and payload.cached_message is not None:
            entry = self.snapshot(payload.cached_message)
        channel = await self.log_channel(guild, "messages")
        settings = await load_settings(guild.id)
        log_ids = {int(value) for value in settings["channels"].values()}
        in_log_channel = payload.channel_id in log_ids
        source = guild.get_channel_or_thread(payload.channel_id)

        skip = (
            channel is None
            or (is_ignored(settings, source) and not in_log_channel)
            or (entry is not None and entry.own and not in_log_channel and entry.id in self.recent)
        )
        if entry is not None and entry.saving is not None and not entry.saving.done() and not skip:
            try:
                await asyncio.wait_for(asyncio.shield(entry.saving), 8)
            except Exception:
                pass
        self.recent.pop(payload.message_id, None)
        if skip or (entry is not None and entry.own and not in_log_channel):
            if entry is not None:
                self.release(entry)
            return

        if entry is None:
            if in_log_channel:
                title, color = "🚨 Log entry deleted", DANGER
                text = "Someone removed a message from a log channel."
            else:
                title, color = "🗑️ Message deleted", DANGER
                text = "I do not have the details of this message. It is older than my memory."
            embed = event_embed(
                title,
                color,
                text=text,
                fields=[("Channel", f"<#{payload.channel_id}>", True), ("Message ID", str(payload.message_id), True)],
                footer=f"Message ID {payload.message_id}",
            )
            self.post(channel, embed)
            return

        executor = await self.find_message_deleter(guild, entry.author_id, entry.channel_id)
        deleted_by = self.deleted_by_text(guild, entry, executor)

        if entry.own:
            embed = event_embed(
                "🚨 Log entry deleted",
                DANGER,
                text="Someone removed a message from a log channel.",
                fields=[
                    ("Channel", f"<#{entry.channel_id}>", True),
                    ("Deleted by", deleted_by, True),
                    ("Sent", stamp(entry.created_at, "R"), True),
                ],
                footer=f"Message ID {entry.id}",
            )
            self.post(channel, embed)
            self.release(entry)
            return

        file_lines, files = self.collect_files(guild, entry)
        has_image = any(item["type"].startswith("image/") for item in entry.files)
        title = "🖼️ Image deleted" if has_image else "🗑️ Message deleted"
        text = quote(entry.content, 2500) if entry.content else "*No text in this message.*"
        author_text = f"<@{entry.author_id}>" + (" 🤖" if entry.author_bot else "")
        fields = [
            ("Author", f"{author_text}\n{code(entry.author_name, 40)}", True),
            ("Channel", f"<#{entry.channel_id}>", True),
            ("Deleted by", deleted_by, True),
            ("Sent", f"{stamp(entry.created_at, 'f')}\n{stamp(entry.created_at, 'R')}", True),
        ]
        if entry.reply_to:
            url = jump_url(guild.id, entry.channel_id, entry.reply_to)
            fields.append(("Replying to", f"[View message]({url})", True))
        if entry.stickers:
            fields.append(("Stickers", ", ".join(plain(name, 30) for name in entry.stickers[:5]), True))
        if entry.embeds:
            fields.append(("Embeds", str(entry.embeds), True))
        if file_lines:
            fields.append(("Files", "\n".join(file_lines), False))
        embed = event_embed(
            title,
            DANGER,
            None,
            text=text,
            fields=fields,
            footer=f"Message ID {entry.id} • User ID {entry.author_id}",
            image=f"attachment://{files[0].filename}" if files else None,
        )
        member = guild.get_member(entry.author_id)
        if member is not None:
            embed.set_author(name=title, icon_url=member.display_avatar.url)
        self.post(channel, embed, files)
        self.release(entry)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        entries = {}
        for message_id in payload.message_ids:
            entry = self.recent.pop(message_id, None)
            if entry is not None:
                entries[entry.id] = entry
        for message in payload.cached_messages:
            if message.id not in entries:
                entries[message.id] = self.snapshot(message)
        channel = await self.log_channel(guild, "messages")
        settings = await load_settings(guild.id)
        source = guild.get_channel_or_thread(payload.channel_id)
        if channel is None or is_ignored(settings, source):
            for entry in entries.values():
                self.release(entry)
            return

        executor = await self.find_audit(
            guild,
            discord.AuditLogAction.message_bulk_delete,
            lambda item: item.target.id == payload.channel_id,
        )
        saved = sorted(entries.values(), key=lambda item: item.id)
        files = []
        if saved:
            lines = []
            for item in saved[:2000]:
                when = item.created_at.strftime("%Y-%m-%d %H:%M:%S")
                words = " ".join(item.content.split()) or "(no text)"
                names = f" [files: {', '.join(f['name'] for f in item.files)}]" if item.files else ""
                lines.append(f"[{when} UTC] {item.author_name} ({item.author_id}): {clip(words, 1500)}{names}")
            data = "\n".join(lines).encode("utf-8")
            files.append(discord.File(io.BytesIO(data), filename="deleted_messages.txt"))
        total = len(payload.message_ids)
        fields = [
            ("Channel", f"<#{payload.channel_id}>", True),
            ("Cleared by", actor_text(executor) if executor else "Unknown", True),
            ("Saved copies", f"{len(saved):,} of {total:,}", True),
        ]
        note = "A text file with the saved messages is attached." if saved else "I did not have copies of these messages."
        embed = event_embed(
            "🧹 Messages cleared",
            DANGER,
            text=f"**{total:,}** messages were removed at once.\n{note}",
            fields=fields,
            footer=f"Channel ID {payload.channel_id}",
        )
        self.post(channel, embed, files)
        for entry in entries.values():
            self.release(entry)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        if payload.guild_id is None:
            return
        data = payload.data
        if "content" not in data:
            return
        new = data["content"] or ""
        entry = self.recent.get(payload.message_id)
        old = None
        if entry is not None:
            old = entry.content
            entry.content = new
            if old == new or entry.author_bot or entry.own:
                return
            author_id = entry.author_id
        else:
            edited = discord.utils.parse_time(data.get("edited_timestamp")) if data.get("edited_timestamp") else None
            if edited is None or (discord.utils.utcnow() - edited).total_seconds() > 20:
                return
            author = data.get("author") or {}
            if author.get("bot") or not str(author.get("id", "")).isdigit():
                return
            author_id = int(author["id"])
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        channel = await self.log_channel(guild, "messages")
        if channel is None:
            return
        settings = await load_settings(guild.id)
        if is_ignored(settings, guild.get_channel_or_thread(payload.channel_id)):
            return
        member = guild.get_member(author_id)
        url = jump_url(guild.id, payload.channel_id, payload.message_id)
        fields = [
            ("Author", f"<@{author_id}>", True),
            ("Channel", f"<#{payload.channel_id}>", True),
            ("Message", f"[Jump to it]({url})", True),
            ("Before", quote(old, 1000) if old else "*Not available*", False),
            ("After", quote(new, 1000) if new else "*No text*", False),
        ]
        embed = event_embed(
            "✏️ Message edited",
            WARNING,
            icon_of(member),
            fields=fields,
            footer=f"Message ID {payload.message_id} • User ID {author_id}",
        )
        self.post(channel, embed)

    @commands.Cog.listener()
    async def on_automod_action(self, execution):
        guild = execution.guild
        channel = await self.log_channel(guild, "moderation")
        if channel is None:
            return
        member = execution.member or guild.get_member(execution.user_id)
        action = enum_name(execution.action.type)
        fields = [
            ("Member", f"<@{execution.user_id}>", True),
            ("Action", action, True),
            ("Rule type", enum_name(execution.rule_trigger_type), True),
        ]
        if execution.channel_id:
            fields.append(("Channel", f"<#{execution.channel_id}>", True))
        keyword = getattr(execution, "matched_keyword", None)
        if keyword:
            fields.append(("Matched word", code(keyword, 80), True))
        content = getattr(execution, "content", None)
        if content:
            fields.append(("Message", quote(content, 1000), False))
        embed = event_embed(
            "🤖 AutoMod acted",
            WARNING,
            icon_of(member),
            fields=fields,
            footer=f"User ID {execution.user_id}",
        )
        self.post(channel, embed)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        guild = member.guild
        channel = await self.log_channel(guild, "voice")
        if channel is None:
            return
        settings = await load_settings(guild.id)
        icon = icon_of(member)
        footer = f"User ID {member.id}"

        if before.channel != after.channel:
            places = [place_ for place_ in (before.channel, after.channel) if place_ is not None]
            if not all(is_ignored(settings, place_) for place_ in places):
                if before.channel is None:
                    title, color = "🔊 Joined voice", SUCCESS
                    text = f"{member.mention} joined {after.channel.mention}."
                elif after.channel is None:
                    title, color = "🔇 Left voice", DANGER
                    text = f"{member.mention} left {before.channel.mention}."
                else:
                    title, color = "↔️ Moved voice", BRAND
                    text = f"{member.mention} moved from {before.channel.mention} to {after.channel.mention}."
                self.post(channel, event_embed(title, color, icon, text=text, footer=footer))

        if before.mute != after.mute or before.deaf != after.deaf:
            watched = after.channel or before.channel
            if is_ignored(settings, watched):
                return
            entry = await self.find_audit(
                guild,
                discord.AuditLogAction.member_update,
                lambda item: item.target.id == member.id
                and (hasattr(item.after, "mute") or hasattr(item.after, "deaf")),
            )
            lines = []
            if before.mute != after.mute:
                lines.append("Muted by a moderator" if after.mute else "Unmuted by a moderator")
            if before.deaf != after.deaf:
                lines.append("Deafened by a moderator" if after.deaf else "Undeafened by a moderator")
            embed = event_embed(
                "🎙️ Voice status changed",
                WARNING,
                icon,
                text=f"{member.mention}: {', '.join(lines).lower()}.",
                fields=[("By", actor_text(entry), True)],
                footer=footer,
            )
            self.post(channel, embed)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        target = await self.log_channel(guild, "channels")
        if target is None:
            return
        entry = await self.find_audit(
            guild, discord.AuditLogAction.channel_create, lambda item: item.target.id == channel.id
        )
        fields = [
            ("Name", code(channel.name), True),
            ("Type", channel_kind(channel), True),
            ("Category", plain(channel.category.name, 60) if channel.category else "None", True),
            ("Created by", actor_text(entry), True),
        ]
        embed = event_embed(
            "📁 Channel created",
            SUCCESS,
            icon_of(entry.user) if entry else None,
            text=f"{channel.mention} was created.",
            fields=fields,
            footer=f"Channel ID {channel.id}",
        )
        self.post(target, embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        target = await self.log_channel(guild, "channels")
        if target is None:
            return
        entry = await self.find_audit(
            guild, discord.AuditLogAction.channel_delete, lambda item: item.target.id == channel.id
        )
        fields = [
            ("Name", code(channel.name), True),
            ("Type", channel_kind(channel), True),
            ("Category", plain(channel.category.name, 60) if channel.category else "None", True),
            ("Deleted by", actor_text(entry), True),
        ]
        embed = event_embed(
            "📁 Channel deleted",
            DANGER,
            icon_of(entry.user) if entry else None,
            text=f"The channel {code(channel.name)} was deleted.",
            fields=fields,
            footer=f"Channel ID {channel.id}",
        )
        self.post(target, embed)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        guild = after.guild
        target = await self.log_channel(guild, "channels")
        if target is None:
            return
        rows = channel_changes(before, after)
        if not rows:
            return
        actions = (
            discord.AuditLogAction.channel_update,
            discord.AuditLogAction.overwrite_create,
            discord.AuditLogAction.overwrite_update,
            discord.AuditLogAction.overwrite_delete,
        )
        entry = await self.find_audit(guild, actions, lambda item: item.target.id == after.id)
        rows.append(("Changed by", actor_text(entry), True))
        embed = event_embed(
            "📁 Channel changed",
            BRAND,
            icon_of(entry.user) if entry else None,
            text=f"{after.mention} was changed.",
            fields=rows,
            footer=f"Channel ID {after.id}",
        )
        self.post(target, embed)

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        guild = thread.guild
        target = await self.log_channel(guild, "channels")
        if target is None:
            return
        fields = [
            ("Name", code(thread.name), True),
            ("Parent", thread.parent.mention if thread.parent else "Unknown", True),
            ("Started by", f"<@{thread.owner_id}>" if thread.owner_id else "Unknown", True),
        ]
        embed = event_embed(
            "🧵 Thread created",
            SUCCESS,
            text=f"{thread.mention} was started.",
            fields=fields,
            footer=f"Thread ID {thread.id}",
        )
        self.post(target, embed)

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        guild = thread.guild
        target = await self.log_channel(guild, "channels")
        if target is None:
            return
        entry = await self.find_audit(
            guild, discord.AuditLogAction.thread_delete, lambda item: item.target.id == thread.id
        )
        fields = [
            ("Name", code(thread.name), True),
            ("Parent", thread.parent.mention if thread.parent else "Unknown", True),
            ("Deleted by", actor_text(entry), True),
        ]
        embed = event_embed(
            "🧵 Thread deleted",
            DANGER,
            icon_of(entry.user) if entry else None,
            fields=fields,
            footer=f"Thread ID {thread.id}",
        )
        self.post(target, embed)

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        guild = after.guild
        target = await self.log_channel(guild, "channels")
        if target is None:
            return
        rows = []
        if before.name != after.name:
            rows.append(change_row("Name", shown(before.name), shown(after.name)))
        if before.archived != after.archived:
            rows.append(change_row("Archived", yes_no(before.archived), yes_no(after.archived)))
        if before.locked != after.locked:
            rows.append(change_row("Locked", yes_no(before.locked), yes_no(after.locked)))
        if before.slowmode_delay != after.slowmode_delay:
            rows.append(change_row("Slowmode", f"{before.slowmode_delay}s", f"{after.slowmode_delay}s"))
        if not rows:
            return
        embed = event_embed(
            "🧵 Thread changed",
            BRAND,
            text=f"{after.mention} was changed.",
            fields=rows,
            footer=f"Thread ID {after.id}",
        )
        self.post(target, embed)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        guild = role.guild
        target = await self.log_channel(guild, "roles")
        if target is None:
            return
        entry = await self.find_audit(
            guild, discord.AuditLogAction.role_create, lambda item: item.target.id == role.id
        )
        fields = [
            ("Name", code(role.name), True),
            ("Color", code(str(role.color)), True),
            ("Created by", actor_text(entry), True),
        ]
        embed = event_embed(
            "🛡️ Role created",
            SUCCESS,
            icon_of(entry.user) if entry else None,
            text=f"{role.mention} was created.",
            fields=fields,
            footer=f"Role ID {role.id}",
        )
        self.post(target, embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        guild = role.guild
        target = await self.log_channel(guild, "roles")
        if target is None:
            return
        entry = await self.find_audit(
            guild, discord.AuditLogAction.role_delete, lambda item: item.target.id == role.id
        )
        fields = [
            ("Name", code(role.name), True),
            ("Color", code(str(role.color)), True),
            ("Deleted by", actor_text(entry), True),
        ]
        embed = event_embed(
            "🛡️ Role deleted",
            DANGER,
            icon_of(entry.user) if entry else None,
            text=f"The role {code(role.name)} was deleted.",
            fields=fields,
            footer=f"Role ID {role.id}",
        )
        self.post(target, embed)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        guild = after.guild
        target = await self.log_channel(guild, "roles")
        if target is None:
            return
        rows = role_changes(before, after)
        if not rows:
            return
        entry = await self.find_audit(
            guild, discord.AuditLogAction.role_update, lambda item: item.target.id == after.id
        )
        rows.append(("Changed by", actor_text(entry), True))
        embed = event_embed(
            "🛡️ Role changed",
            BRAND,
            icon_of(entry.user) if entry else None,
            text=f"{after.mention} was changed.",
            fields=rows,
            footer=f"Role ID {after.id}",
        )
        self.post(target, embed)

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        target = await self.log_channel(after, "server")
        if target is None:
            return
        rows = guild_changes(before, after)
        icon_changed = asset_key(before.icon) != asset_key(after.icon)
        banner_changed = asset_key(before.banner) != asset_key(after.banner)
        if icon_changed:
            rows.append(("Server icon", "Changed", True))
        if banner_changed:
            rows.append(("Server banner", "Changed", True))
        if not rows:
            return
        entry = await self.find_audit(after, discord.AuditLogAction.guild_update, lambda item: True)
        rows.append(("Changed by", actor_text(entry), True))
        embed = event_embed(
            "⚙️ Server changed",
            BRAND,
            icon_of(entry.user) if entry else None,
            fields=rows,
            footer=f"Server ID {after.id}",
            thumbnail=after.icon.url if icon_changed and after.icon else None,
        )
        self.post(target, embed)

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild, before, after):
        target = await self.log_channel(guild, "server")
        if target is None:
            return
        old = {item.id: item for item in before}
        new = {item.id: item for item in after}
        added = [new[key] for key in new if key not in old]
        removed = [old[key] for key in old if key not in new]
        renamed = [(old[key], new[key]) for key in new if key in old and old[key].name != new[key].name]
        if not (added or removed or renamed):
            return
        ids = {item.id for item in added + removed} | {item[1].id for item in renamed}
        actions = (
            discord.AuditLogAction.emoji_create,
            discord.AuditLogAction.emoji_delete,
            discord.AuditLogAction.emoji_update,
        )
        entry = await self.find_audit(guild, actions, lambda item: getattr(item.target, "id", None) in ids)
        fields = []
        if added:
            fields.append(("Added", ", ".join(f"{item} {code(item.name, 30)}" for item in added[:8]), False))
        if removed:
            fields.append(("Removed", ", ".join(code(item.name, 30) for item in removed[:8]), False))
        if renamed:
            lines = [f"{code(a.name, 30)} → {code(b.name, 30)}" for a, b in renamed[:8]]
            fields.append(("Renamed", "\n".join(lines), False))
        fields.append(("By", actor_text(entry), True))
        embed = event_embed(
            "😀 Emojis changed",
            TEAL,
            icon_of(entry.user) if entry else None,
            fields=fields,
            footer=f"Server ID {guild.id}",
            thumbnail=added[0].url if len(added) == 1 and not removed and not renamed else None,
        )
        self.post(target, embed)

    @commands.Cog.listener()
    async def on_guild_stickers_update(self, guild, before, after):
        target = await self.log_channel(guild, "server")
        if target is None:
            return
        old = {item.id: item for item in before}
        new = {item.id: item for item in after}
        added = [new[key] for key in new if key not in old]
        removed = [old[key] for key in old if key not in new]
        renamed = [(old[key], new[key]) for key in new if key in old and old[key].name != new[key].name]
        if not (added or removed or renamed):
            return
        ids = {item.id for item in added + removed} | {item[1].id for item in renamed}
        actions = (
            discord.AuditLogAction.sticker_create,
            discord.AuditLogAction.sticker_delete,
            discord.AuditLogAction.sticker_update,
        )
        entry = await self.find_audit(guild, actions, lambda item: getattr(item.target, "id", None) in ids)
        fields = []
        if added:
            fields.append(("Added", ", ".join(code(item.name, 30) for item in added[:8]), False))
        if removed:
            fields.append(("Removed", ", ".join(code(item.name, 30) for item in removed[:8]), False))
        if renamed:
            lines = [f"{code(a.name, 30)} → {code(b.name, 30)}" for a, b in renamed[:8]]
            fields.append(("Renamed", "\n".join(lines), False))
        fields.append(("By", actor_text(entry), True))
        embed = event_embed(
            "🏷️ Stickers changed",
            TEAL,
            icon_of(entry.user) if entry else None,
            fields=fields,
            footer=f"Server ID {guild.id}",
            thumbnail=added[0].url if len(added) == 1 and not removed and not renamed else None,
        )
        self.post(target, embed)

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel):
        guild = channel.guild
        target = await self.log_channel(guild, "server")
        if target is None:
            return
        actions = (
            discord.AuditLogAction.webhook_create,
            discord.AuditLogAction.webhook_update,
            discord.AuditLogAction.webhook_delete,
        )
        entry = await self.find_audit(guild, actions, lambda item: True)
        verbs = {
            discord.AuditLogAction.webhook_create: "Created",
            discord.AuditLogAction.webhook_update: "Updated",
            discord.AuditLogAction.webhook_delete: "Deleted",
        }
        fields = [("Channel", channel.mention, True)]
        if entry:
            fields.append(("What happened", verbs.get(entry.action, "Changed"), True))
            name = (
                getattr(entry.target, "name", None)
                or getattr(entry.before, "name", None)
                or getattr(entry.after, "name", None)
            )
            if name:
                fields.append(("Webhook", code(name, 60), True))
        fields.append(("By", actor_text(entry), True))
        embed = event_embed(
            "🪝 Webhooks changed",
            TEAL,
            icon_of(entry.user) if entry else None,
            fields=fields,
            footer=f"Channel ID {channel.id}",
        )
        self.post(target, embed)

    @commands.command(
        name="logpanel",
        aliases=["serverlogs", "logsetup"],
        help="Open the server log settings.",
    )
    @ctx_admin()
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def logpanel(self, ctx):
        view = LogPanel(self, ctx)
        embed = await self.panel_embed(ctx.guild, "all", ctx.clean_prefix)
        view.message = await ctx.reply(embed=embed, view=view, mention_author=False)

    @commands.command(
        name="logignore",
        aliases=["ignorelog"],
        help="Skip message and voice logs for a channel or category.",
    )
    @ctx_admin()
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def logignore(self, ctx, channel: discord.abc.GuildChannel = None):
        if channel is None:
            settings = await load_settings(ctx.guild.id)
            listed = "\n".join(f"• <#{item}>" for item in sorted(settings["ignored"])) or "Nothing is skipped."
            embed = make_embed("🙈 Skipped places", clip(listed, 3500), NEUTRAL, ctx.guild)
            embed.add_field(
                name="How to change it",
                value=f"`{ctx.clean_prefix}logignore #channel` — Skip or stop skipping",
                inline=False,
            )
            return await ctx.reply(embed=embed, mention_author=False)

        result = await toggle_ignored(ctx.guild.id, channel.id)
        if result is None:
            return await ctx.reply(
                embed=error_embed(f"You can skip up to {IGNORE_LIMIT} places.", ctx.guild),
                mention_author=False,
            )
        text = (
            f"I will no longer log messages or voice activity in {channel.mention}."
            if result
            else f"I will log {channel.mention} again."
        )
        await ctx.reply(embed=success_embed(text, ctx.guild), mention_author=False)

    @commands.command(
        name="invites",
        aliases=["inv", "invcount"],
        help="See how many people someone has invited.",
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def invites(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        await ctx.reply(embed=await build_invites_embed(ctx.guild, member), mention_author=False)

    @commands.command(
        name="inviteleaderboard",
        aliases=["invlb", "topinviters"],
        help="See who has invited the most people.",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def inviteleaderboard(self, ctx):
        rows = await invites_col.aggregate(
            [
                {"$match": {"guild_id": str(ctx.guild.id), "inviter_id": {"$ne": None}}},
                {"$group": {"_id": "$inviter_id", "total": {"$sum": "$uses"}}},
                {"$match": {"total": {"$gt": 0}}},
                {"$sort": {"total": -1}},
                {"$limit": 10},
            ]
        ).to_list(10)

        if not rows:
            return await ctx.reply(
                embed=make_embed(
                    "🏆 Invite Leaderboard", "No invites yet. Share a link to get started!", NEUTRAL, ctx.guild
                ),
                mention_author=False,
            )

        lines = [
            f"{place(i)} <@{row['_id']}> — **{count_text(row['total'], 'invite')}**"
            for i, row in enumerate(rows, 1)
        ]
        embed = make_embed("🏆 Invite Leaderboard", "\n".join(lines), BRAND, ctx.guild)
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(
        name="inviteinfo",
        aliases=["infolink"],
        help="See details about an invite code.",
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def inviteinfo(self, ctx, code_: str = None):
        if not code_:
            return await ctx.reply(
                embed=error_embed(
                    f"Please give me an invite code. Example: `{ctx.clean_prefix}inviteinfo abc123`", ctx.guild
                ),
                mention_author=False,
            )

        clean = code_.strip().rstrip("/").split("/")[-1]
        if not CODE_PATTERN.match(clean):
            return await ctx.reply(
                embed=error_embed("That does not look like an invite code.", ctx.guild),
                mention_author=False,
            )

        doc = await invites_col.find_one({"guild_id": str(ctx.guild.id), "code": clean})
        if not doc:
            return await ctx.reply(
                embed=error_embed(f"I have no record of the invite `{clean}`.", ctx.guild),
                mention_author=False,
            )

        embed = make_embed(f"🔗 Invite {clean}", None, BRAND, ctx.guild)
        embed.add_field(name="Link", value=f"discord.gg/{clean}", inline=True)
        embed.add_field(
            name="Created by",
            value=f"<@{doc['inviter_id']}>" if doc.get("inviter_id") else "Unknown",
            inline=True,
        )
        embed.add_field(name="Status", value="Deleted" if doc.get("deleted") else "Active", inline=True)
        embed.add_field(name="Uses", value=f"{doc.get('uses', 0):,}", inline=True)
        embed.add_field(name="Use limit", value=str(doc.get("max_uses") or "No limit"), inline=True)
        created = doc.get("created_at")
        if created:
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            embed.add_field(name="Created", value=f"<t:{int(created.timestamp())}:R>", inline=True)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(
        name="invitelog",
        aliases=["invlog", "setinvitelog"],
        help="Choose where join and leave logs are posted.",
    )
    @ctx_admin()
    async def invitelog(self, ctx, setting: Union[discord.TextChannel, str] = None):
        prefix = ctx.clean_prefix

        if setting is None:
            settings = await load_settings(ctx.guild.id)
            channel_id = settings["channels"].get("members")
            current = f"<#{channel_id}>" if channel_id else "Not set"
            embed = make_embed("📋 Join & Leave Logs", None, NEUTRAL, ctx.guild)
            embed.add_field(name="Log channel", value=current, inline=False)
            embed.add_field(
                name="How to change it",
                value=(
                    f"`{prefix}invitelog #channel` — Turn on\n"
                    f"`{prefix}invitelog off` — Turn off\n"
                    f"`{prefix}logpanel` — See every log type"
                ),
                inline=False,
            )
            return await ctx.reply(embed=embed, mention_author=False)

        if isinstance(setting, str):
            if setting.lower() in ("off", "disable", "none"):
                await save_channels(ctx.guild.id, ["members"], None)
                return await ctx.reply(
                    embed=success_embed("Join and leave logs are now turned off.", ctx.guild),
                    mention_author=False,
                )
            return await ctx.reply(
                embed=error_embed("Please mention a channel, or type `off` to turn logs off.", ctx.guild),
                mention_author=False,
            )

        problem = channel_problem(ctx.guild, setting)
        if problem:
            return await ctx.reply(embed=error_embed(problem, ctx.guild), mention_author=False)

        await save_channels(ctx.guild.id, ["members"], setting.id)
        await ctx.reply(
            embed=success_embed(
                f"Join and leave logs will be posted in {setting.mention}.\n\n"
                "**What you will see**\n"
                "• Who joined, and who invited them\n"
                "• Who left the server\n\n"
                f"Want more? Use `{prefix}logpanel` to turn on message, image, voice, and moderation logs.",
                ctx.guild,
            ),
            mention_author=False,
        )

    @commands.command(
        name="invitelogdisable",
        aliases=["invitelogoff"],
        help="Turn off join and leave logs.",
    )
    @ctx_admin()
    async def invitelogdisable(self, ctx):
        await save_channels(ctx.guild.id, ["members"], None)
        await ctx.reply(
            embed=success_embed("Join and leave logs are now turned off.", ctx.guild), mention_author=False
        )

    @commands.command(
        name="invitereset",
        aliases=["resetinvite"],
        help="Reset invite counts for one person or the whole server.",
    )
    @ctx_admin()
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def invitereset(self, ctx, member: discord.Member = None):
        if member:
            await invites_col.update_many(
                {"guild_id": str(ctx.guild.id), "inviter_id": str(member.id)},
                {"$set": {"uses": 0}},
            )
            return await ctx.reply(
                embed=success_embed(f"Invite count reset for {member.mention}.", ctx.guild),
                mention_author=False,
            )

        confirmed, message = await ask_to_confirm(
            ctx, "This will erase the invite counts for everyone in this server."
        )
        if not confirmed:
            return await safe_edit(
                message, embed=make_embed("Cancelled", "Nothing was changed.", NEUTRAL, ctx.guild), view=None
            )
        await invites_col.delete_many({"guild_id": str(ctx.guild.id)})
        await sync_invites(ctx.guild, fresh=True)
        await safe_edit(
            message, embed=success_embed("All invite counts for this server were reset.", ctx.guild), view=None
        )

    @commands.command(
        name="messages",
        aliases=["msgs", "msgcount", "mc2"],
        help="See how many messages someone has sent.",
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def messages(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        await self.flush_now()
        await ctx.reply(embed=await build_messages_embed(ctx.guild, member), mention_author=False)

    @commands.command(
        name="msgleaderboard",
        aliases=["msglb", "topchatters", "chatleaderboard"],
        help="See the most active chatters.",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def msgleaderboard(self, ctx):
        await self.flush_now()
        docs = (
            await msg_count_col.find({"guild_id": str(ctx.guild.id), "count": {"$gt": 0}})
            .sort("count", -1)
            .limit(10)
            .to_list(10)
        )

        if not docs:
            return await ctx.reply(
                embed=make_embed("🏆 Message Leaderboard", "No messages counted yet.", NEUTRAL, ctx.guild),
                mention_author=False,
            )

        lines = [
            f"{place(i)} <@{doc['user_id']}> — **{count_text(doc['count'], 'message')}**"
            for i, doc in enumerate(docs, 1)
        ]
        embed = make_embed("🏆 Message Leaderboard", "\n".join(lines), BRAND, ctx.guild)
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(
        name="msgstats",
        aliases=["servermsgstats"],
        help="See message numbers for the whole server.",
    )
    @commands.cooldown(1, 10, commands.BucketType.guild)
    async def msgstats(self, ctx):
        await self.flush_now()
        rows = await msg_count_col.aggregate(
            [
                {"$match": {"guild_id": str(ctx.guild.id)}},
                {"$group": {"_id": None, "total": {"$sum": "$count"}, "members": {"$sum": 1}}},
            ]
        ).to_list(1)

        if not rows:
            return await ctx.reply(
                embed=make_embed("📊 Message Stats", "No messages counted yet.", NEUTRAL, ctx.guild),
                mention_author=False,
            )

        total = rows[0]["total"]
        members = rows[0]["members"]
        top = await msg_count_col.find_one({"guild_id": str(ctx.guild.id)}, sort=[("count", -1)])

        embed = make_embed("📊 Message Stats", None, BRAND, ctx.guild)
        embed.add_field(name="💬 Total messages", value=f"**{total:,}**", inline=True)
        embed.add_field(name="👥 Active members", value=f"**{members:,}**", inline=True)
        embed.add_field(name="📈 Average each", value=f"**{total // members:,}**", inline=True)
        embed.add_field(
            name="🏆 Top chatter",
            value=f"<@{top['user_id']}> — **{count_text(top['count'], 'message')}**",
            inline=False,
        )
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(
        name="msgreset",
        aliases=["resetmsg", "resetmessages"],
        help="Reset message counts for one person or the whole server.",
    )
    @ctx_admin()
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def msgreset(self, ctx, member: discord.Member = None):
        if member:
            self.drop_buffered(ctx.guild.id, member.id)
            await msg_count_col.delete_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
            return await ctx.reply(
                embed=success_embed(f"Message count reset for {member.mention}.", ctx.guild),
                mention_author=False,
            )

        confirmed, message = await ask_to_confirm(
            ctx, "This will erase the message counts for everyone in this server."
        )
        if not confirmed:
            return await safe_edit(
                message, embed=make_embed("Cancelled", "Nothing was changed.", NEUTRAL, ctx.guild), view=None
            )
        self.drop_buffered(ctx.guild.id)
        await msg_count_col.delete_many({"guild_id": str(ctx.guild.id)})
        await safe_edit(
            message, embed=success_embed("All message counts for this server were reset.", ctx.guild), view=None
        )

    @app_commands.command(name="invites", description="See how many people someone has invited")
    @app_commands.describe(member="The person to look up")
    @app_commands.guild_only()
    async def slash_invites(self, interaction: discord.Interaction, member: discord.Member = None):
        wait = self.slash_wait(interaction.user.id)
        if wait:
            return await interaction.response.send_message(
                f"Please wait {math.ceil(wait)} more seconds.", ephemeral=True
            )
        await interaction.response.defer()
        member = member or interaction.user
        await interaction.followup.send(embed=await build_invites_embed(interaction.guild, member))

    @app_commands.command(name="messages", description="See how many messages someone has sent")
    @app_commands.describe(member="The person to look up")
    @app_commands.guild_only()
    async def slash_messages(self, interaction: discord.Interaction, member: discord.Member = None):
        wait = self.slash_wait(interaction.user.id)
        if wait:
            return await interaction.response.send_message(
                f"Please wait {math.ceil(wait)} more seconds.", ephemeral=True
            )
        await interaction.response.defer()
        await self.flush_now()
        member = member or interaction.user
        await interaction.followup.send(embed=await build_messages_embed(interaction.guild, member))


async def setup(bot):
    await bot.add_cog(Tracker(bot))