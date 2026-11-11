import asyncio
import io
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_OK = True
except ImportError:
    PIL_OK = False

from utils.db import (
    premium_col, server_status_col, voicemaster_col, bump_col, settings_col,
    calls_col, aesthetic_col as studio_col, color_roles_col as color_col,
    milestone_col, badge_optout_col as badge_off_col
)
from utils.helpers import (
    BOT_OWNER_ID, ctx_owner, ctx_premium,
    is_premium_server, is_premium_user
)

GOLD = 0xF0C040
SILVER = 0xB8BCC8
DARK = 0x2B2D31

E_PREMIUM = "<:premium:1569047367565709322>"
E_CALL = "<a:call:1569047387492716594>"
E_CATCH = "<:caughtin4k:1569047342559137983>"

BADGE_DEFAULT = "✦"
CATCH_COST_DEFAULT = 250
CATCH_COOLDOWN = 20
MAX_COLORS = 24
MAX_RULES = 20
MAX_REACTS = 15
CALL_WAIT_MINUTES = 30

MILESTONES = [10, 25, 50, 100, 200, 250, 500, 750, 1000, 2000, 5000, 10000, 25000, 50000, 100000]

DEFAULT_COLORS = [
    ("Rose", 0xE8425A), ("Lavender", 0xA78BFA), ("Sky", 0x38BDF8),
    ("Mint", 0x34D399), ("Peach", 0xFB923C), ("Gold", 0xFBBF24),
    ("Crimson", 0xDC2626), ("Sapphire", 0x3B82F6), ("Emerald", 0x10B981),
    ("Violet", 0x8B5CF6), ("Coral", 0xF97316), ("Steel", 0x64748B),
]

CHANNEL_MODES = {
    "media": "Only pictures, videos and GIFs",
    "chat": "Only plain text messages",
    "links": "Only messages with a link",
    "embeds": "Only embeds, files and links",
}

CHANNEL_NOTICES = {
    "media": "This channel is only for pictures, videos and GIFs.",
    "chat": "This channel is only for text chat. Please share files and links in another channel.",
    "links": "This channel is only for links.",
    "embeds": "This channel is only for embeds, files and links.",
}

REACT_FILTERS = ("all", "media", "images", "videos", "links", "text")

UNSAFE_PERMS = (
    "administrator", "manage_guild", "manage_roles", "manage_channels",
    "manage_messages", "manage_webhooks", "kick_members", "ban_members",
    "moderate_members", "mention_everyone", "manage_nicknames",
)

IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
VID_EXT = (".mp4", ".mov", ".webm", ".mkv")
IMAGE_HOSTS = ("tenor.com", "giphy.com", "imgur.com", "i.redd.it")
VIDEO_HOSTS = ("youtube.com", "youtu.be", "streamable.com")
CDN_HOSTS = ("cdn.discordapp.com", "media.discordapp.net", "images-ext-1.discordapp.net")

URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
INVITE_RE = re.compile(r"(?:discord\.gg|discord(?:app)?\.com/invite)/\S+", re.I)
LINK_RE = re.compile(r"discord(?:app)?\.com/channels/(\d+|@me)/(\d+)/(\d+)")
ID_RE = re.compile(r"^\d{15,21}$")

FONT_FILES = (
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("segoeui.ttf", "segoeuib.ttf"),
    ("arial.ttf", "arialbd.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
)


def card(title=None, desc=None, color=GOLD, footer="Happy Premium", emoji=E_PREMIUM):
    embed = discord.Embed(color=color)
    if title:
        embed.title = f"{emoji}  {title}" if emoji else title
    if desc:
        embed.description = desc
    if footer:
        embed.set_footer(text=footer)
    return embed


def plain(text, color=DARK):
    return discord.Embed(description=text, color=color)


def tick(on):
    return "`●`" if on else "`○`"


def locked_card(feature):
    return card(
        "Premium feature",
        f"**{feature}** is a Happy Premium perk.\n\n"
        "See everything you can unlock with `,perks`.\n"
        f"To get Premium, message <@{BOT_OWNER_ID}>."
    )


def admin_only():
    async def check(ctx):
        if not ctx.guild:
            raise commands.NoPrivateMessage()
        if ctx.author.id == BOT_OWNER_ID or ctx.author.guild_permissions.administrator:
            return True
        raise commands.MissingPermissions(["administrator"])
    return commands.check(check)


def safe_role(role):
    if role.is_default() or role.managed:
        return False
    return not any(getattr(role.permissions, p, False) for p in UNSAFE_PERMS)


def short_list(items, empty="None", limit=8):
    if not items:
        return empty
    text = "\n".join(items[:limit])
    if len(items) > limit:
        text += f"\n+{len(items) - limit} more"
    return text[:1000]


def scrub(text, names=None):
    names = names or {}
    text = text or ""
    text = re.sub(r"<@!?(\d+)>", lambda m: "@" + names.get(int(m.group(1)), "user"), text)
    text = re.sub(r"<@&\d+>", "@role", text)
    text = re.sub(r"<#\d+>", "#channel", text)
    text = re.sub(r"<a?:(\w+):\d+>", r":\1:", text)
    text = re.sub(r"<t:\d+(?::\w)?>", "", text)
    text = re.sub(r"[\U00010000-\U0010FFFF\u200d\ufe0f]", "", text)
    return text.strip()[:1500]


def load_fonts(size):
    for regular, bold in FONT_FILES:
        try:
            return ImageFont.truetype(regular, size), ImageFont.truetype(bold, size + 1)
        except OSError:
            continue
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:
        font = ImageFont.load_default()
    return font, font


def wrap_text(draw, text, font, width):
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            while draw.textlength(word, font=font) > width:
                cut = len(word)
                while cut > 1 and draw.textlength(word[:cut], font=font) > width:
                    cut -= 1
                if line:
                    lines.append(line)
                    line = ""
                lines.append(word[:cut])
                word = word[cut:]
            trial = f"{line} {word}" if line else word
            if draw.textlength(trial, font=font) <= width:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def render_card(name, name_color, avatar_bytes, stamp, text, embed_lines, picture_bytes):
    width, pad, avatar_size = 760, 30, 48
    text_x = pad + avatar_size + 16
    text_w = width - text_x - pad
    regular, bold = load_fonts(18)
    small, _ = load_fonts(14)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    line_h = 26

    body = wrap_text(probe, text, regular, text_w)[:45] if text else []
    quote = []
    for entry in embed_lines:
        quote.extend(wrap_text(probe, entry, regular, text_w - 24))
    quote = quote[:25]

    picture = None
    if picture_bytes:
        try:
            picture = Image.open(io.BytesIO(picture_bytes)).convert("RGB")
            ratio = min(text_w / picture.width, 380 / picture.height, 1)
            picture = picture.resize((max(int(picture.width * ratio), 1), max(int(picture.height * ratio), 1)))
        except Exception:
            picture = None

    height = pad + 34 + len(body) * line_h
    if quote:
        height += 14 + len(quote) * line_h + 18
    if picture:
        height += 14 + picture.height
    height += 36 + 26 + pad

    img = Image.new("RGB", (width, height), (49, 51, 56))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, width, 4), fill=(240, 192, 64))

    if avatar_bytes:
        try:
            avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((avatar_size, avatar_size))
        except Exception:
            avatar = Image.new("RGBA", (avatar_size, avatar_size), (88, 101, 242, 255))
    else:
        avatar = Image.new("RGBA", (avatar_size, avatar_size), (88, 101, 242, 255))
    mask = Image.new("L", (avatar_size, avatar_size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, avatar_size, avatar_size), fill=255)
    img.paste(avatar, (pad, pad + 6), mask)

    draw.text((text_x, pad + 4), name[:40], font=bold, fill=name_color)
    name_w = draw.textlength(name[:40], font=bold)
    draw.text((text_x + name_w + 12, pad + 8), stamp, font=small, fill=(148, 155, 164))

    y = pad + 34
    for line in body:
        draw.text((text_x, y), line, font=regular, fill=(219, 222, 225))
        y += line_h

    if quote:
        y += 14
        box_h = len(quote) * line_h + 18
        draw.rounded_rectangle((text_x, y, width - pad, y + box_h), radius=6, fill=(43, 45, 49))
        draw.rectangle((text_x, y, text_x + 4, y + box_h), fill=(88, 101, 242))
        ty = y + 9
        for line in quote:
            draw.text((text_x + 18, ty), line, font=regular, fill=(219, 222, 225))
            ty += line_h
        y += box_h

    if picture:
        y += 14
        img.paste(picture, (text_x, y))
        y += picture.height

    footer_y = height - pad - 16
    draw.line((pad, footer_y - 12, width - pad, footer_y - 12), fill=(63, 65, 71), width=1)
    draw.text((pad, footer_y), "Caught with Happy", font=small, fill=(240, 192, 64))

    out = io.BytesIO()
    img.save(out, "PNG")
    out.seek(0)
    return out


class ColorPicker(discord.ui.View):
    def __init__(self, cog, options=None):
        super().__init__(timeout=None)
        self.cog = cog
        select = discord.ui.Select(
            custom_id="happy:color:pick",
            placeholder="Pick your name color",
            min_values=1,
            max_values=1,
            options=options or [discord.SelectOption(label="No color", value="none")]
        )
        select.callback = self.picked
        self.add_item(select)

    async def picked(self, interaction: discord.Interaction):
        values = interaction.data.get("values") or ["none"]
        guild, member = interaction.guild, interaction.user
        if not guild or not isinstance(member, discord.Member):
            return await interaction.response.send_message(embed=plain("Please use this inside a server."), ephemeral=True)
        if not await self.cog.srv_prem(guild.id):
            return await interaction.response.send_message(embed=locked_card("Color roles"), ephemeral=True)
        text, color = await self.cog.set_color(guild, member, values[0])
        await interaction.response.send_message(embed=plain(text, color), ephemeral=True)


class Premium(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cache = {}
        self.webhooks = {}
        self.relay_last = {}
        self.vc_last = {}
        self.notice_last = {}
        self.badge_last = {}
        self.catch_last = {}
        self.catching = set()
        self.session = None
        self.catch_menu, self.catch_slash = self.build_catch_commands()

    async def cog_load(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        self.bot.add_view(ColorPicker(self))
        self.bot.tree.add_command(self.catch_menu, override=True)
        self.bot.tree.add_command(self.catch_slash, override=True)

    async def cog_unload(self):
        self.bot.tree.remove_command("Catch", type=discord.AppCommandType.message)
        self.bot.tree.remove_command("catch")
        if self.session:
            await self.session.close()

    async def cached(self, key, factory, ttl=30):
        now = time.monotonic()
        hit = self.cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        value = await factory()
        if len(self.cache) > 5000:
            self.cache.clear()
        self.cache[key] = (now, value)
        return value

    async def srv_prem(self, guild_id):
        return await self.cached(("sp", guild_id), lambda: is_premium_server(guild_id), 60)

    async def usr_prem(self, user_id):
        if user_id == BOT_OWNER_ID:
            return True
        return await self.cached(("up", user_id), lambda: is_premium_user(user_id), 60)

    async def badge_off(self, user_id):
        async def load():
            return await badge_off_col.find_one({"user_id": str(user_id)}) is not None
        return await self.cached(("bo", user_id), load, 60)

    async def get_cfg(self, guild_id):
        doc = await self.cached(("cfg", guild_id), lambda: studio_col.find_one({"guild_id": str(guild_id)}), 30)
        return doc or {}

    async def save_cfg(self, guild_id, update):
        await studio_col.update_one({"guild_id": str(guild_id)}, update, upsert=True)
        self.cache.pop(("cfg", guild_id), None)

    async def gate(self, ctx, feature):
        if await self.srv_prem(ctx.guild.id):
            return True
        await ctx.reply(embed=locked_card(feature))
        return False

    @commands.command(name="perks", aliases=["mypremium", "myprem", "ispremium"])
    @commands.guild_only()
    async def perks(self, ctx):
        uid = ctx.author.id
        owner = uid == BOT_OWNER_ID
        user_prem = await self.usr_prem(uid)
        srv_prem = await self.srv_prem(ctx.guild.id)

        if not (user_prem or srv_prem):
            embed = card("Happy Premium", "Make your server and your name stand out.", SILVER)
            embed.add_field(
                name="For you",
                value=(
                    "AI chat with Happy\n"
                    f"{E_CATCH} Catch any message as a picture, half price\n"
                    "A premium badge on your name"
                ),
                inline=False
            )
            embed.add_field(
                name="For your server",
                value=(
                    f"{E_CALL} Global Call with other servers\n"
                    "Channel rules for media and chat\n"
                    "Auto reactions on pictures and posts\n"
                    "Color roles members pick themselves\n"
                    "Temporary voice rooms, bump reminders and more"
                ),
                inline=False
            )
            embed.add_field(name="How to get it", value=f"Message <@{BOT_OWNER_ID}> to turn on Premium.", inline=False)
            embed.set_thumbnail(url=ctx.author.display_avatar.url)
            return await ctx.reply(embed=embed)

        status = []
        if owner:
            status.append("Bot owner")
        if user_prem and not owner:
            status.append("Personal Premium")
        if srv_prem:
            status.append("Server Premium")

        embed = card("Happy Premium", f"You are all set, **{ctx.author.display_name}**.\n{' · '.join(status)}", GOLD)
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        embed.add_field(
            name="Your perks",
            value=(
                f"{tick(user_prem and srv_prem)} AI chat\n"
                f"{tick(True)} {E_CATCH} Catch\n"
                f"{tick(user_prem)} Premium badge"
            ),
            inline=True
        )
        embed.add_field(
            name="Server perks",
            value=(
                f"{tick(srv_prem)} {E_CALL} Global Call\n"
                f"{tick(srv_prem)} Channel rules\n"
                f"{tick(srv_prem)} Auto reactions\n"
                f"{tick(srv_prem)} Color roles\n"
                f"{tick(srv_prem)} Voice rooms\n"
                f"{tick(srv_prem)} Bump reminder"
            ),
            inline=True
        )
        embed.add_field(
            name="Good to know",
            value=(
                "AI chat needs Premium on you and on the server.\n"
                "Catch: add Happy to your profile, then right click a message and choose Apps, then Catch."
            ),
            inline=False
        )
        embed.set_footer(text="Admins can use ,studio to set up the server")
        await ctx.reply(embed=embed)

    @commands.group(invoke_without_command=True)
    @ctx_owner()
    async def premium(self, ctx):
        items = await premium_col.find({}).to_list(500)
        servers = [f"`{i['id']}` {i.get('name', '')}"[:60] for i in items if i.get("type") == "server"]
        users = [f"`{i['id']}` {i.get('name', '')}"[:60] for i in items if i.get("type") == "user"]
        embed = card("Premium Control", "Manage who has Happy Premium.", GOLD)
        embed.timestamp = datetime.now(timezone.utc)
        embed.add_field(name=f"Servers ({len(servers)})", value=short_list(servers, limit=12), inline=True)
        embed.add_field(name=f"Users ({len(users)})", value=short_list(users, limit=12), inline=True)
        embed.add_field(
            name="Commands",
            value=(
                "`,premium add server <id>`\n"
                "`,premium add user <id>`\n"
                "`,premium remove server <id>`\n"
                "`,premium remove user <id>`\n"
                "`,premium info <server id>`"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @premium.command(name="add")
    @ctx_owner()
    async def premium_add(self, ctx, kind: str = None, target: str = None):
        kind = (kind or "").lower()
        if kind not in ("server", "user") or not target or not ID_RE.match(target):
            return await ctx.reply(embed=plain("Use `,premium add server <id>` or `,premium add user <id>`."))

        name = target
        if kind == "server":
            guild = self.bot.get_guild(int(target))
            name = guild.name if guild else target
        else:
            try:
                name = str(await self.bot.fetch_user(int(target)))
            except discord.HTTPException:
                pass

        await premium_col.update_one(
            {"type": kind, "id": target},
            {"$set": {"type": kind, "id": target, "name": name, "activated_at": datetime.now(timezone.utc)}},
            upsert=True
        )
        self.cache.pop(("sp" if kind == "server" else "up", int(target)), None)

        embed = card("Premium Activated", f"**{name}** now has Premium.", GOLD)
        embed.add_field(name="Type", value=kind.title(), inline=True)
        embed.add_field(name="ID", value=f"`{target}`", inline=True)
        await ctx.reply(embed=embed)

        if kind == "user":
            asyncio.create_task(self.sync_user_badges(int(target)))
            return

        guild = self.bot.get_guild(int(target))
        if guild and guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
            welcome = card(
                "Premium is now on",
                "Thank you. Your server now has Happy Premium.\n\n"
                f"{E_CALL} Global Call · `,call`\n"
                "Channel rules and auto reactions · `,studio`\n"
                "Color roles · `,studio colors setup`\n"
                "Voice rooms · `,vcsetup`\n"
                "Bump reminder · `,bumpreminder on`\n\n"
                "Type `,perks` to see everything."
            )
            try:
                await guild.system_channel.send(embed=welcome)
            except discord.HTTPException:
                pass

    @premium.command(name="remove")
    @ctx_owner()
    async def premium_remove(self, ctx, kind: str = None, target: str = None):
        kind = (kind or "").lower()
        if kind not in ("server", "user") or not target or not ID_RE.match(target):
            return await ctx.reply(embed=plain("Use `,premium remove server <id>` or `,premium remove user <id>`."))
        result = await premium_col.delete_one({"type": kind, "id": target})
        self.cache.pop(("sp" if kind == "server" else "up", int(target)), None)
        if not result.deleted_count:
            return await ctx.reply(embed=plain(f"No Premium found for {kind} `{target}`."))
        await ctx.reply(embed=plain(f"Premium removed from {kind} `{target}`."))
        if kind == "user":
            asyncio.create_task(self.sync_user_badges(int(target)))

    @premium.command(name="info")
    @ctx_owner()
    async def premium_info(self, ctx, guild_id: int = None):
        guild = self.bot.get_guild(guild_id) if guild_id else None
        if not guild:
            return await ctx.reply(embed=plain("Use `,premium info <server id>` with a server the bot is in."))
        gid = str(guild.id)
        doc = await premium_col.find_one({"type": "server", "id": gid})
        vc = await voicemaster_col.find_one({"guild_id": gid})
        bump = await bump_col.find_one({"guild_id": gid})
        status = await server_status_col.find_one({"guild_id": gid})

        embed = card(guild.name, None, GOLD)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Premium", value="Active" if doc else "Not active", inline=True)
        embed.add_field(name="Members", value=f"{guild.member_count:,}", inline=True)
        embed.add_field(name="Voice rooms", value="On" if vc else "Off", inline=True)
        embed.add_field(name="Bump reminder", value="On" if bump and bump.get("enabled") else "Off", inline=True)
        embed.add_field(name="Bot status", value=f"`{status['status']}`" if status else "Not set", inline=True)
        if doc and doc.get("activated_at"):
            embed.add_field(name="Since", value=f"<t:{int(doc['activated_at'].timestamp())}:R>", inline=True)
        await ctx.reply(embed=embed)

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @ctx_premium()
    @admin_only()
    async def setstatus(self, ctx, *, status: str = None):
        doc = await server_status_col.find_one({"guild_id": str(ctx.guild.id)})
        if not status:
            if not doc:
                return await ctx.reply(embed=plain("No status set yet.\nUse `,setstatus <text>` to add one."))
            return await ctx.reply(embed=card(
                "Server Status",
                f"Now showing: **[{ctx.guild.name}] {doc['status']}**\n\n"
                "Change it with `,setstatus <text>`\nRemove it with `,setstatus remove`"
            ))

        status = " ".join(status.split())
        if len(status) > 100:
            return await ctx.reply(embed=plain("Your status must be 100 characters or less."))

        await server_status_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {
                "status": status,
                "guild_name": ctx.guild.name,
                "guild_id": str(ctx.guild.id),
                "set_by": str(ctx.author.id),
            }},
            upsert=True
        )
        await ctx.reply(embed=card(
            "Status saved",
            f"Happy will show:\n> Watching **[{ctx.guild.name}] {status}**\n\n"
            "It appears when the bot reaches your server in its status rotation."
        ))

    @setstatus.command(name="remove")
    @commands.guild_only()
    @ctx_premium()
    @admin_only()
    async def setstatus_remove(self, ctx):
        result = await server_status_col.delete_one({"guild_id": str(ctx.guild.id)})
        await ctx.reply(embed=plain("Status removed." if result.deleted_count else "No status was set."))

    @commands.command(name="bumpreminder")
    @commands.guild_only()
    @ctx_premium()
    @admin_only()
    async def bumpreminder(self, ctx, state: str = "on", role: discord.Role = None):
        enabled = state.lower() in ("on", "true", "1", "yes")
        update = {"enabled": enabled, "channel_id": str(ctx.channel.id), "set_by": str(ctx.author.id)}
        if enabled and role and not role.is_default():
            update["ping_role_id"] = str(role.id)
        if not enabled:
            update["ping_role_id"] = None
        await bump_col.update_one({"guild_id": str(ctx.guild.id)}, {"$set": update}, upsert=True)

        if not enabled:
            return await ctx.reply(embed=plain("Bump reminder is off."))
        ping = role.mention if role and not role.is_default() else "No role. Add one with `,bumpreminder on @role`"
        embed = card(
            "Bump reminder is on",
            f"After someone bumps with DISBOARD, I will remind this channel in 2 hours.\n\nChannel: {ctx.channel.mention}"
        )
        embed.add_field(name="Ping role", value=ping, inline=False)
        await ctx.reply(embed=embed)

    @commands.command(name="bumppingrole")
    @commands.guild_only()
    @ctx_premium()
    @admin_only()
    async def bumppingrole(self, ctx, role: discord.Role = None):
        doc = await bump_col.find_one({"guild_id": str(ctx.guild.id)}) or {}
        if not role:
            rid = doc.get("ping_role_id")
            current = ctx.guild.get_role(int(rid)) if rid else None
            return await ctx.reply(embed=plain(
                f"Ping role: {current.mention if current else 'Not set'}\n"
                "Use `,bumppingrole @role` to change it."
            ))
        if role.is_default():
            return await ctx.reply(embed=plain("Please pick a normal role, not everyone."))
        await bump_col.update_one({"guild_id": str(ctx.guild.id)}, {"$set": {"ping_role_id": str(role.id)}}, upsert=True)
        await ctx.reply(embed=plain(f"I will ping {role.mention} when it is time to bump.", GOLD))

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @ctx_premium()
    @admin_only()
    async def vcsetup(self, ctx):
        doc = await voicemaster_col.find_one({"guild_id": str(ctx.guild.id)})
        if not doc:
            return await ctx.reply(embed=card(
                "Voice Rooms",
                "Let members make their own temporary voice rooms.\n\nRun `,vcsetup create` to start.",
                SILVER
            ))
        channel = ctx.guild.get_channel(int(doc.get("create_channel_id", 0)))
        embed = card(
            "Voice Rooms",
            f"Members join {channel.mention if channel else 'the create channel'} to get their own room.\n"
            "The room is deleted when everyone leaves."
        )
        embed.add_field(name="Member limit", value="Unlimited" if doc.get("default_limit", 10) == 0 else str(doc.get("default_limit", 10)), inline=True)
        embed.add_field(name="Room name", value=f"`{doc.get('name_template', '{user}')}`", inline=True)
        embed.add_field(
            name="Commands",
            value=(
                "`,vcsetup limit <0-99>`\n"
                "`,vcsetup name <name>` use `{user}` for the member name\n"
                "`,vcsetup remove`"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @vcsetup.command(name="create")
    @commands.guild_only()
    @ctx_premium()
    @admin_only()
    async def vcsetup_create(self, ctx):
        me = ctx.guild.me.guild_permissions
        if not (me.manage_channels and me.move_members):
            return await ctx.reply(embed=plain("I need the Manage Channels and Move Members permissions first."))

        doc = await voicemaster_col.find_one({"guild_id": str(ctx.guild.id)})
        if doc:
            existing = ctx.guild.get_channel(int(doc.get("create_channel_id", 0)))
            if existing:
                return await ctx.reply(embed=plain(f"Voice rooms are already on. Join {existing.mention} to make one.", GOLD))

        category = discord.utils.get(ctx.guild.categories, name="Voice Rooms")
        if not category:
            category = await ctx.guild.create_category("Voice Rooms")
        channel = await ctx.guild.create_voice_channel("Join to Create", category=category, reason="Voice rooms setup")

        await voicemaster_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {
                "create_channel_id": str(channel.id),
                "category_id": str(category.id),
                "default_limit": 10,
                "name_template": "{user}'s Room",
                "temp_channels": [],
            }},
            upsert=True
        )
        embed = card("Voice rooms are ready", f"Members can join {channel.mention} to get their own room.")
        embed.add_field(name="Member limit", value="10", inline=True)
        embed.add_field(name="Room name", value="`{user}'s Room`", inline=True)
        await ctx.reply(embed=embed)

    @vcsetup.command(name="limit")
    @commands.guild_only()
    @ctx_premium()
    @admin_only()
    async def vcsetup_limit(self, ctx, limit: int = None):
        if limit is None or not 0 <= limit <= 99:
            return await ctx.reply(embed=plain("Use `,vcsetup limit <0-99>`. Use 0 for no limit."))
        await voicemaster_col.update_one({"guild_id": str(ctx.guild.id)}, {"$set": {"default_limit": limit}}, upsert=True)
        await ctx.reply(embed=plain(f"Room limit is now **{'unlimited' if limit == 0 else limit}**.", GOLD))

    @vcsetup.command(name="name")
    @commands.guild_only()
    @ctx_premium()
    @admin_only()
    async def vcsetup_name(self, ctx, *, template: str = None):
        if not template or len(template) > 50:
            return await ctx.reply(embed=plain("Use `,vcsetup name <name>` (up to 50 characters). Example: `{user}'s Room`"))
        await voicemaster_col.update_one({"guild_id": str(ctx.guild.id)}, {"$set": {"name_template": template}}, upsert=True)
        preview = template.replace("{user}", ctx.author.display_name)
        await ctx.reply(embed=plain(f"Room name saved.\nExample: **{preview}**", GOLD))

    @vcsetup.command(name="remove")
    @commands.guild_only()
    @ctx_premium()
    @admin_only()
    async def vcsetup_remove(self, ctx):
        doc = await voicemaster_col.find_one({"guild_id": str(ctx.guild.id)})
        if not doc:
            return await ctx.reply(embed=plain("Voice rooms are not set up."))
        ids = [doc.get("create_channel_id")] + list(doc.get("temp_channels", []))
        for cid in ids:
            channel = ctx.guild.get_channel(int(cid)) if cid else None
            if channel:
                try:
                    await channel.delete(reason="Voice rooms removed")
                except discord.HTTPException:
                    pass
        await voicemaster_col.delete_one({"guild_id": str(ctx.guild.id)})
        await ctx.reply(embed=plain("Voice rooms removed. All temporary rooms were deleted."))

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        guild = member.guild
        doc = await voicemaster_col.find_one({"guild_id": str(guild.id)})
        if not doc:
            return

        if after.channel and str(after.channel.id) == doc.get("create_channel_id"):
            await self.make_room(member, doc)

        if before.channel and before.channel != after.channel:
            fresh = await voicemaster_col.find_one({"guild_id": str(guild.id)}) or {}
            if str(before.channel.id) in fresh.get("temp_channels", []) and not before.channel.members:
                try:
                    await before.channel.delete(reason="Voice room is empty")
                except discord.HTTPException:
                    pass
                await voicemaster_col.update_one(
                    {"guild_id": str(guild.id)},
                    {"$pull": {"temp_channels": str(before.channel.id)}}
                )

    async def make_room(self, member, doc):
        guild = member.guild
        now = time.monotonic()
        if now - self.vc_last.get(member.id, 0) < 8:
            return
        self.vc_last[member.id] = now
        if len(self.vc_last) > 5000:
            self.vc_last.clear()
        if len(doc.get("temp_channels", [])) >= 40:
            return
        perms = guild.me.guild_permissions
        if not (perms.manage_channels and perms.move_members):
            return

        category = guild.get_channel(int(doc.get("category_id", 0)))
        name = doc.get("name_template", "{user}'s Room").replace("{user}", member.display_name)[:100] or "Room"
        try:
            room = await guild.create_voice_channel(
                name=name,
                category=category,
                user_limit=doc.get("default_limit", 10),
                reason=f"Voice room for {member}"
            )
            await room.set_permissions(
                member, connect=True, speak=True, manage_channels=True,
                move_members=True, mute_members=True
            )
        except discord.HTTPException:
            return
        try:
            await member.move_to(room)
        except discord.HTTPException:
            try:
                await room.delete(reason="Member left before moving")
            except discord.HTTPException:
                pass
            return
        await voicemaster_col.update_one(
            {"guild_id": str(guild.id)},
            {"$push": {"temp_channels": str(room.id)}}
        )

    async def webhook_for(self, channel):
        hook = self.webhooks.get(channel.id)
        if hook:
            return hook
        for existing in await channel.webhooks():
            if existing.user and existing.user.id == self.bot.user.id and existing.token:
                self.webhooks[channel.id] = existing
                return existing
        hook = await channel.create_webhook(name="Happy Call")
        self.webhooks[channel.id] = hook
        return hook

    @commands.command(name="call")
    @commands.guild_only()
    @ctx_premium()
    @admin_only()
    async def call(self, ctx):
        sid, cid = ctx.guild.id, ctx.channel.id
        if not ctx.channel.permissions_for(ctx.guild.me).manage_webhooks:
            return await ctx.reply(embed=plain("I need the Manage Webhooks permission in this channel to run a call."))

        mine = await calls_col.find_one({"server_id": sid})
        if mine and not mine.get("waiting"):
            return await ctx.reply(embed=card(
                "Already on a call",
                f"You are connected to **{mine.get('guild_name', 'another server')}**.\nUse `,hangup` to end it first.",
                emoji=E_CALL
            ))
        if mine and mine.get("waiting"):
            queue = await calls_col.find({"waiting": True}).to_list(100)
            pos = next((i for i, d in enumerate(queue) if d["server_id"] == sid), 0) + 1
            return await ctx.reply(embed=card(
                "Still waiting",
                f"You are number **{pos}** in line.\nUse `,hangup` to leave the line.",
                emoji=E_CALL
            ))

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=CALL_WAIT_MINUTES)
        await calls_col.delete_many({"waiting": True, "queued_at": {"$lt": cutoff}})

        partner = None
        for _ in range(5):
            candidate = await calls_col.find_one_and_delete(
                {"waiting": True, "server_id": {"$ne": sid}},
                sort=[("queued_at", 1)]
            )
            if not candidate:
                break
            if self.bot.get_channel(candidate["channel_id"]):
                partner = candidate
                break

        if partner:
            p_sid, p_cid = partner["server_id"], partner["channel_id"]
            p_name = partner.get("guild_name", "Another server")
            await calls_col.replace_one(
                {"server_id": sid},
                {"server_id": sid, "my_channel": cid, "partner_channel": p_cid, "partner_guild_id": p_sid, "guild_name": p_name},
                upsert=True
            )
            await calls_col.replace_one(
                {"server_id": p_sid},
                {"server_id": p_sid, "my_channel": p_cid, "partner_channel": cid, "partner_guild_id": sid, "guild_name": ctx.guild.name},
                upsert=True
            )
            await ctx.send(embed=card(
                "Call connected",
                f"You are now live with **{p_name}**.\nMessages in this channel go to them in real time.\nUse `,hangup` to end the call.",
                footer="Happy Global Call",
                emoji=E_CALL
            ))
            partner_channel = self.bot.get_channel(p_cid)
            if partner_channel:
                try:
                    await partner_channel.send(embed=card(
                        "Call connected",
                        f"**{ctx.guild.name}** joined the call.\nMessages in this channel go to them in real time.\nUse `,hangup` to end the call.",
                        footer="Happy Global Call",
                        emoji=E_CALL
                    ))
                except discord.HTTPException:
                    pass
            return

        await calls_col.replace_one(
            {"server_id": sid},
            {"server_id": sid, "channel_id": cid, "guild_name": ctx.guild.name, "waiting": True, "queued_at": datetime.now(timezone.utc)},
            upsert=True
        )
        queue = await calls_col.find({"waiting": True}).to_list(100)
        await ctx.reply(embed=card(
            "Looking for a partner",
            f"You are number **{len(queue)}** in line.\n"
            "When another Premium server uses `,call`, you will connect right away.\n"
            f"If nobody joins in {CALL_WAIT_MINUTES} minutes, the line clears. Use `,hangup` to leave.",
            footer="Happy Global Call",
            emoji=E_CALL
        ))

    @commands.command(name="hangup")
    @commands.guild_only()
    @admin_only()
    async def hangup(self, ctx):
        sid = ctx.guild.id
        doc = await calls_col.find_one({"server_id": sid})
        if not doc:
            return await ctx.reply(embed=plain("You are not on a call or in the line."))

        if doc.get("waiting"):
            await calls_col.delete_one({"server_id": sid})
            return await ctx.reply(embed=plain("You left the line."))

        p_cid, p_sid = doc.get("partner_channel"), doc.get("partner_guild_id")
        p_name = doc.get("guild_name", "the other server")
        await calls_col.delete_one({"server_id": sid})
        if p_sid:
            await calls_col.delete_one({"server_id": p_sid})

        await ctx.reply(embed=card("Call ended", f"Your call with **{p_name}** has ended.", DARK, "Happy Global Call", E_CALL))
        partner_channel = self.bot.get_channel(p_cid) if p_cid else None
        if partner_channel:
            try:
                await partner_channel.send(embed=card("Call ended", f"**{ctx.guild.name}** ended the call.", DARK, "Happy Global Call", E_CALL))
            except discord.HTTPException:
                pass

    @commands.command(name="callinfo", aliases=["callstatus", "cs"])
    @commands.guild_only()
    async def callinfo(self, ctx):
        sid = ctx.guild.id
        doc = await calls_col.find_one({"server_id": sid})
        queue = await calls_col.find({"waiting": True}).to_list(100)

        if doc and not doc.get("waiting"):
            channel = self.bot.get_channel(doc["partner_channel"])
            embed = card(
                "On a call",
                f"Connected to **{doc.get('guild_name', 'another server')}**.\nMessages in this channel are being shared.",
                footer="Happy Global Call", emoji=E_CALL
            )
            embed.add_field(name="Their channel", value=channel.mention if channel else "Unknown", inline=True)
            embed.add_field(name="End call", value="`,hangup`", inline=True)
        elif doc:
            pos = next((i + 1 for i, d in enumerate(queue) if d["server_id"] == sid), 1)
            embed = card(
                "Waiting in line",
                f"You are number **{pos}**.\nUse `,hangup` to leave the line.",
                footer="Happy Global Call", emoji=E_CALL
            )
        else:
            embed = card(
                "No call",
                "Use `,call` to talk with another Premium server.",
                DARK, "Happy Global Call", E_CALL
            )
            embed.add_field(name="Servers waiting", value=str(len(queue)), inline=True)
        await ctx.reply(embed=embed)

    async def relay_call(self, message: discord.Message):
        if message.author.bot or not message.guild or message.mention_everyone:
            return
        doc = await calls_col.find_one({"server_id": message.guild.id})
        if not doc or doc.get("waiting") or message.channel.id != doc.get("my_channel"):
            return

        now = time.monotonic()
        if now - self.relay_last.get(message.author.id, 0) < 0.8:
            return
        self.relay_last[message.author.id] = now
        if len(self.relay_last) > 5000:
            self.relay_last.clear()

        target = self.bot.get_channel(doc["partner_channel"])
        if not target:
            return

        text = INVITE_RE.sub("[invite removed]", message.content or "")
        text = discord.utils.escape_mentions(text)[:1800]
        if message.attachments:
            links = "\n".join(a.url for a in message.attachments[:3])
            text = f"{text}\n{links}".strip()
        if not text:
            return

        tag = f"{message.author.display_name} [{message.guild.name}]"
        tag = re.sub(r"(?i)(discord|clyde)", lambda m: m.group()[0] + "\u200b" + m.group()[1:], tag)[:80]
        avatar = message.guild.icon.url if message.guild.icon else message.author.display_avatar.url

        for attempt in range(2):
            try:
                hook = await self.webhook_for(target)
                await hook.send(
                    content=text,
                    username=tag,
                    avatar_url=avatar,
                    allowed_mentions=discord.AllowedMentions.none()
                )
                return
            except discord.NotFound:
                self.webhooks.pop(target.id, None)
            except discord.HTTPException:
                return

    @commands.command(name="quicksetup")
    @commands.guild_only()
    @admin_only()
    async def quicksetup(self, ctx):
        guild = ctx.guild
        me = guild.me.guild_permissions
        if not (me.manage_roles and me.manage_channels):
            return await ctx.reply(embed=plain("I need the Manage Roles and Manage Channels permissions first."))

        msg = await ctx.reply(embed=card("Setting up your server", "This takes a moment..."))
        created, skipped = [], []

        role_plan = [
            ("Admin", discord.Color.from_rgb(237, 66, 69)),
            ("Moderator", discord.Color.from_rgb(87, 242, 135)),
            ("Member", discord.Color.from_rgb(88, 101, 242)),
            ("Muted", discord.Color.dark_gray()),
        ]
        roles = {}
        for name, color in role_plan:
            role = discord.utils.get(guild.roles, name=name)
            if role:
                skipped.append(f"Role {name}")
            else:
                role = await guild.create_role(name=name, color=color, permissions=discord.Permissions.none(), reason="Quick setup")
                created.append(f"Role {name}")
            roles[name] = role
            await asyncio.sleep(0.2)

        staff_roles = [roles["Admin"], roles["Moderator"]]
        layout = {
            "Information": (["rules", "announcements", "roles"], "readonly"),
            "General": (["general", "off-topic", "media", "bot-commands"], "open"),
            "Staff Only": (["mod-logs", "mod-chat", "staff-chat"], "staff"),
        }
        media_channel = None
        for cat_name, (names, kind) in layout.items():
            category = discord.utils.get(guild.categories, name=cat_name)
            if not category:
                overwrites = {}
                if kind == "readonly":
                    overwrites = {
                        guild.default_role: discord.PermissionOverwrite(send_messages=False),
                        guild.me: discord.PermissionOverwrite(send_messages=True),
                    }
                    for role in staff_roles:
                        overwrites[role] = discord.PermissionOverwrite(send_messages=True)
                elif kind == "staff":
                    overwrites = {
                        guild.default_role: discord.PermissionOverwrite(view_channel=False),
                        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                    }
                    for role in staff_roles:
                        overwrites[role] = discord.PermissionOverwrite(view_channel=True)
                category = await guild.create_category(cat_name, overwrites=overwrites)
                created.append(f"Category {cat_name}")
            for ch_name in names:
                if discord.utils.get(guild.text_channels, name=ch_name):
                    skipped.append(f"#{ch_name}")
                else:
                    channel = await guild.create_text_channel(ch_name, category=category)
                    created.append(f"#{ch_name}")
                    if ch_name == "media":
                        media_channel = channel
                await asyncio.sleep(0.2)

        if media_channel and await self.srv_prem(guild.id):
            await self.set_rule(guild.id, media_channel.id, "media")

        result = card("Setup complete", "Your server is ready.")
        result.timestamp = datetime.now(timezone.utc)
        if created:
            result.add_field(name=f"Created ({len(created)})", value=short_list([f"+ {c}" for c in created], limit=18), inline=True)
        if skipped:
            result.add_field(name=f"Already there ({len(skipped)})", value=short_list(skipped, limit=10), inline=True)
        result.add_field(
            name="Next steps",
            value=(
                "`,welcome set #channel`\n"
                "`,bye set #channel`\n"
                "`,logs set #mod-logs`\n"
                "`,ticket setup`\n"
                "`,studio` for premium server tools"
            ),
            inline=False
        )
        await msg.edit(embed=result)

    def kinds(self, message):
        image = video = link = other_file = False
        for a in message.attachments:
            ctype = (a.content_type or "").lower()
            name = a.filename.lower()
            if ctype.startswith("image/") or name.endswith(IMG_EXT):
                image = True
            elif ctype.startswith("video/") or name.endswith(VID_EXT):
                video = True
            else:
                other_file = True
        content = message.content or ""
        for url in URL_RE.findall(content):
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            path = parsed.path.lower()
            link = True
            if path.endswith(IMG_EXT) or any(host == h or host.endswith("." + h) for h in IMAGE_HOSTS):
                image = True
            elif path.endswith(VID_EXT) or any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS):
                video = True
        if message.stickers:
            image = True
        for e in message.embeds:
            if e.type in ("image", "gifv"):
                image = True
            elif e.type == "video":
                video = True
        has_text = bool(URL_RE.sub("", content).strip())
        return {
            "image": image, "video": video, "link": link, "file": other_file,
            "attach": bool(message.attachments or message.stickers),
            "text": has_text,
        }

    def allowed_in_mode(self, mode, k, message):
        if mode == "media":
            return k["image"] or k["video"]
        if mode == "chat":
            return k["text"] and not (k["attach"] or k["link"])
        if mode == "links":
            return k["link"]
        if mode == "embeds":
            return bool(message.embeds) or k["attach"] or k["link"]
        return True

    def react_matches(self, kind, k):
        if kind == "all":
            return True
        if kind == "media":
            return k["image"] or k["video"]
        if kind == "images":
            return k["image"]
        if kind == "videos":
            return k["video"]
        if kind == "links":
            return k["link"]
        if kind == "text":
            return k["text"] and not (k["attach"] or k["link"])
        return False

    @commands.Cog.listener("on_message")
    async def studio_on_message(self, message):
        if message.author.bot or not message.guild:
            return
        cfg = await self.get_cfg(message.guild.id)
        if not cfg:
            return

        cid = str(message.channel.id)
        mode = next((r["mode"] for r in cfg.get("channel_rules", []) if r["channel_id"] == cid), None)
        reacts = [r for r in cfg.get("autoreact", []) if r["channel_id"] == cid]
        pin = cid in cfg.get("auto_pin_channels", [])
        if not (mode or reacts or pin):
            return
        if not await self.srv_prem(message.guild.id):
            return

        perms = message.channel.permissions_for(message.guild.me)
        k = self.kinds(message)

        if mode:
            exempt = message.author.id == BOT_OWNER_ID or (
                isinstance(message.author, discord.Member) and message.author.guild_permissions.manage_messages
            )
            if not exempt and not self.allowed_in_mode(mode, k, message):
                if perms.manage_messages:
                    try:
                        await message.delete()
                    except discord.HTTPException:
                        return
                    key = (message.channel.id, message.author.id)
                    now = time.monotonic()
                    if now - self.notice_last.get(key, 0) > 15:
                        self.notice_last[key] = now
                        if len(self.notice_last) > 5000:
                            self.notice_last.clear()
                        try:
                            await message.channel.send(
                                f"{message.author.mention} {CHANNEL_NOTICES.get(mode, 'That message is not allowed here.')}",
                                delete_after=6,
                                allowed_mentions=discord.AllowedMentions(users=[message.author])
                            )
                        except discord.HTTPException:
                            pass
                return

        if reacts and perms.add_reactions:
            for rule in reacts:
                if self.react_matches(rule.get("filter", "all"), k):
                    try:
                        await message.add_reaction(rule["emoji"])
                    except discord.HTTPException:
                        pass
                    await asyncio.sleep(0.3)

        if pin and perms.manage_messages:
            try:
                await message.pin()
            except discord.HTTPException:
                pass

    async def set_rule(self, guild_id, channel_id, mode):
        await self.save_cfg(guild_id, {"$pull": {"channel_rules": {"channel_id": str(channel_id)}}})
        if mode:
            await self.save_cfg(guild_id, {"$push": {"channel_rules": {"channel_id": str(channel_id), "mode": mode}}})

    @commands.group(name="studio", invoke_without_command=True)
    @commands.guild_only()
    @admin_only()
    async def studio(self, ctx):
        cfg = await self.get_cfg(ctx.guild.id)
        prem = await self.srv_prem(ctx.guild.id)
        palette = (await color_col.find_one({"guild_id": str(ctx.guild.id)}) or {}).get("roles", [])

        rules = [f"<#{r['channel_id']}> · {r['mode']}" for r in cfg.get("channel_rules", [])]
        reacts = [f"<#{r['channel_id']}> · {r['emoji']} · {r.get('filter', 'all')}" for r in cfg.get("autoreact", [])]
        pins = [f"<#{c}>" for c in cfg.get("auto_pin_channels", [])]
        auto_role = f"<@&{cfg['auto_role_id']}>" if cfg.get("auto_role_id") else "Off"
        milestone = f"<#{cfg['milestone_channel_id']}>" if cfg.get("milestone_channel_id") else "Default"
        badge = f"On · {cfg.get('badge_symbol', BADGE_DEFAULT)}" if cfg.get("badge_enabled") else "Off"

        embed = card("Server Studio", "Set up your server in a clean, premium way.", GOLD if prem else SILVER)
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        embed.add_field(name="Premium", value="Active" if prem else "Not active", inline=True)
        embed.add_field(name="Auto role", value=auto_role, inline=True)
        embed.add_field(name="Milestones", value=milestone, inline=True)
        embed.add_field(name="Name badge", value=badge, inline=True)
        embed.add_field(name="Color roles", value=f"{len(palette)} colors", inline=True)
        embed.add_field(name="Auto pin", value=short_list(pins, "Off", 4), inline=True)
        embed.add_field(name="Channel rules", value=short_list(rules, "None yet"), inline=False)
        embed.add_field(name="Auto reactions", value=short_list(reacts, "None yet"), inline=False)
        embed.add_field(
            name="Commands",
            value=(
                "`,studio channel #chat media`\n"
                "`,studio autoreact add #pics 🔥 images`\n"
                "`,studio colors setup`\n"
                "`,studio badge on`\n"
                "`,studio autorole @role`\n"
                "`,studio milestone #channel`\n"
                "`,studio autopin #channel`"
            ),
            inline=False
        )
        if not prem:
            embed.set_footer(text="Premium is needed to use these tools. Type ,perks to learn more.")
        await ctx.reply(embed=embed)

    @studio.command(name="channel")
    @commands.guild_only()
    @admin_only()
    async def studio_channel(self, ctx, channel: discord.TextChannel = None, mode: str = None):
        if not await self.gate(ctx, "Channel rules"):
            return
        mode = (mode or "").lower()
        if not channel or (mode not in CHANNEL_MODES and mode != "off"):
            lines = "\n".join(f"`{m}` · {d}" for m, d in CHANNEL_MODES.items())
            return await ctx.reply(embed=card(
                "Channel rules",
                f"Choose what a channel is for.\n\n{lines}\n`off` · Remove the rule\n\n"
                "Example: `,studio channel #selfies media`"
            ))
        cfg = await self.get_cfg(ctx.guild.id)
        current = cfg.get("channel_rules", [])
        if mode != "off" and len(current) >= MAX_RULES and not any(r["channel_id"] == str(channel.id) for r in current):
            return await ctx.reply(embed=plain(f"You can set rules on up to {MAX_RULES} channels."))

        await self.set_rule(ctx.guild.id, channel.id, None if mode == "off" else mode)
        if mode == "off":
            return await ctx.reply(embed=plain(f"Rule removed from {channel.mention}.", GOLD))

        embed = card("Channel rule saved", f"{channel.mention} now allows: **{CHANNEL_MODES[mode].lower()}**.\nStaff with Manage Messages can post anything.")
        if not channel.permissions_for(ctx.guild.me).manage_messages:
            embed.add_field(name="One more step", value=f"Give me the Manage Messages permission in {channel.mention} so I can remove other messages.", inline=False)
        await ctx.reply(embed=embed)

    @studio.group(name="autoreact", invoke_without_command=True)
    @commands.guild_only()
    @admin_only()
    async def studio_autoreact(self, ctx):
        cfg = await self.get_cfg(ctx.guild.id)
        reacts = [f"<#{r['channel_id']}> · {r['emoji']} · {r.get('filter', 'all')}" for r in cfg.get("autoreact", [])]
        embed = card(
            "Auto reactions",
            "I react to messages in the channels you pick.\n\n"
            "`,studio autoreact add #channel <emoji> [filter]`\n"
            "`,studio autoreact remove #channel [emoji]`\n\n"
            f"Filters: {', '.join(f'`{f}`' for f in REACT_FILTERS)}"
        )
        embed.add_field(name="Active", value=short_list(reacts, "None yet", 12), inline=False)
        await ctx.reply(embed=embed)

    @studio_autoreact.command(name="add")
    @commands.guild_only()
    @admin_only()
    async def autoreact_add(self, ctx, channel: discord.TextChannel = None, emoji: str = None, kind: str = "all"):
        if not await self.gate(ctx, "Auto reactions"):
            return
        kind = kind.lower()
        if not channel or not emoji or kind not in REACT_FILTERS:
            return await ctx.reply(embed=plain(
                "Use `,studio autoreact add #channel <emoji> [filter]`\n"
                f"Filters: {', '.join(REACT_FILTERS)}"
            ))
        if not channel.permissions_for(ctx.guild.me).add_reactions:
            return await ctx.reply(embed=plain(f"I need the Add Reactions permission in {channel.mention}."))

        parsed = discord.PartialEmoji.from_str(emoji)
        value = str(parsed)
        try:
            await ctx.message.add_reaction(value)
        except discord.HTTPException:
            return await ctx.reply(embed=plain("I cannot use that emoji. Pick a normal emoji or one from a server I am in."))
        try:
            await ctx.message.remove_reaction(value, ctx.guild.me)
        except discord.HTTPException:
            pass

        cfg = await self.get_cfg(ctx.guild.id)
        existing = cfg.get("autoreact", [])
        cid = str(channel.id)
        if any(r["channel_id"] == cid and r["emoji"] == value for r in existing):
            return await ctx.reply(embed=plain("That emoji is already set for this channel."))
        if sum(1 for r in existing if r["channel_id"] == cid) >= 3:
            return await ctx.reply(embed=plain("Each channel can have up to 3 reactions."))
        if len(existing) >= MAX_REACTS:
            return await ctx.reply(embed=plain(f"You can have up to {MAX_REACTS} auto reactions."))

        await self.save_cfg(ctx.guild.id, {"$push": {"autoreact": {"channel_id": cid, "emoji": value, "filter": kind}}})
        await ctx.reply(embed=card("Auto reaction saved", f"I will react with {value} in {channel.mention} on **{kind}** messages."))

    @studio_autoreact.command(name="remove")
    @commands.guild_only()
    @admin_only()
    async def autoreact_remove(self, ctx, channel: discord.TextChannel = None, emoji: str = None):
        if not channel:
            return await ctx.reply(embed=plain("Use `,studio autoreact remove #channel [emoji]`."))
        match = {"channel_id": str(channel.id)}
        if emoji:
            match["emoji"] = str(discord.PartialEmoji.from_str(emoji))
        await self.save_cfg(ctx.guild.id, {"$pull": {"autoreact": match}})
        await ctx.reply(embed=plain(f"Auto reactions removed from {channel.mention}.", GOLD))

    @studio.command(name="autopin")
    @commands.guild_only()
    @admin_only()
    async def studio_autopin(self, ctx, channel: discord.TextChannel = None):
        if not await self.gate(ctx, "Auto pin"):
            return
        if not channel:
            return await ctx.reply(embed=plain("Use `,studio autopin #channel` to turn it on or off."))
        cid = str(channel.id)
        cfg = await self.get_cfg(ctx.guild.id)
        if cid in cfg.get("auto_pin_channels", []):
            await self.save_cfg(ctx.guild.id, {"$pull": {"auto_pin_channels": cid}})
            return await ctx.reply(embed=plain(f"Auto pin is off for {channel.mention}.", GOLD))
        await self.save_cfg(ctx.guild.id, {"$addToSet": {"auto_pin_channels": cid}})
        await ctx.reply(embed=plain(f"Auto pin is on for {channel.mention}.", GOLD))

    @studio.command(name="autorole")
    @commands.guild_only()
    @admin_only()
    async def studio_autorole(self, ctx, role: discord.Role = None):
        if not await self.gate(ctx, "Auto role"):
            return
        if not role:
            await self.save_cfg(ctx.guild.id, {"$unset": {"auto_role_id": ""}})
            return await ctx.reply(embed=plain("Auto role is off.", GOLD))
        if not safe_role(role):
            return await ctx.reply(embed=plain("For safety, I only give new members roles that have no admin or moderation powers."))
        if ctx.guild.me.top_role <= role:
            return await ctx.reply(embed=plain("That role is above my highest role. Move my role higher first."))
        await self.save_cfg(ctx.guild.id, {"$set": {"auto_role_id": str(role.id)}})
        await ctx.reply(embed=plain(f"New members will get {role.mention}.", GOLD))

    @studio.command(name="milestone")
    @commands.guild_only()
    @admin_only()
    async def studio_milestone(self, ctx, channel: discord.TextChannel = None):
        if not await self.gate(ctx, "Member milestones"):
            return
        if not channel:
            return await ctx.reply(embed=plain("Use `,studio milestone #channel` to pick where milestones are announced."))
        await self.save_cfg(ctx.guild.id, {"$set": {"milestone_channel_id": str(channel.id)}})
        await ctx.reply(embed=plain(f"Milestones will be announced in {channel.mention}.", GOLD))

    @studio.command(name="badge")
    @commands.guild_only()
    @admin_only()
    async def studio_badge(self, ctx, action: str = None, *, symbol: str = None):
        cfg = await self.get_cfg(ctx.guild.id)
        current = cfg.get("badge_symbol", BADGE_DEFAULT)
        action = (action or "").lower()

        if action not in ("on", "off", "symbol", "sync"):
            on = cfg.get("badge_enabled", False)
            return await ctx.reply(embed=card(
                "Premium name badge",
                f"Status: **{'On' if on else 'Off'}**   Badge: **{current}**\n\n"
                "Members with personal Premium get a badge in front of their nickname here.\n\n"
                "`,studio badge on`\n`,studio badge off`\n`,studio badge symbol ✦`\n`,studio badge sync`"
            ))

        me = ctx.guild.me.guild_permissions
        if action in ("on", "sync") and not me.manage_nicknames:
            return await ctx.reply(embed=plain("I need the Manage Nicknames permission first."))

        if action == "on":
            await self.save_cfg(ctx.guild.id, {"$set": {"badge_enabled": True}})
            asyncio.create_task(self.sync_guild_badges(ctx.guild))
            return await ctx.reply(embed=plain(f"Name badge is on. Premium members will get **{current}** in their name.", GOLD))
        if action == "off":
            await self.save_cfg(ctx.guild.id, {"$set": {"badge_enabled": False}})
            asyncio.create_task(self.sync_guild_badges(ctx.guild, remove=True))
            return await ctx.reply(embed=plain("Name badge is off. Badges are being removed.", GOLD))
        if action == "sync":
            asyncio.create_task(self.sync_guild_badges(ctx.guild))
            return await ctx.reply(embed=plain("Updating names now. This can take a minute.", GOLD))

        symbol = (symbol or "").strip()
        if not 1 <= len(symbol) <= 2 or any(c.isalnum() or c.isspace() or c in "<>:@#" for c in symbol):
            return await ctx.reply(embed=plain("Pick a single symbol such as ✦ ★ ♦ ❖. Letters and custom emojis do not work in names."))
        await self.save_cfg(ctx.guild.id, {"$set": {"badge_symbol": symbol}})
        if cfg.get("badge_enabled"):
            asyncio.create_task(self.sync_guild_badges(ctx.guild, extra=(current,)))
        await ctx.reply(embed=plain(f"Badge changed to **{symbol}**.", GOLD))

    @commands.command(name="badge")
    @commands.guild_only()
    async def badge(self, ctx, state: str = None):
        if not await self.usr_prem(ctx.author.id):
            return await ctx.reply(embed=locked_card("Premium badge"))
        cfg = await self.get_cfg(ctx.guild.id)
        if not cfg.get("badge_enabled"):
            return await ctx.reply(embed=plain("This server has not turned on name badges yet. Ask an admin to use `,studio badge on`."))

        off = await self.badge_off(ctx.author.id)
        state = (state or "").lower()
        if state not in ("on", "off"):
            return await ctx.reply(embed=card(
                "Your badge",
                f"Your badge is **{'hidden' if off else 'showing'}**.\n\nUse `,badge on` or `,badge off`."
            ))
        if state == "off":
            await badge_off_col.update_one({"user_id": str(ctx.author.id)}, {"$set": {"user_id": str(ctx.author.id)}}, upsert=True)
        else:
            await badge_off_col.delete_one({"user_id": str(ctx.author.id)})
        self.cache.pop(("bo", ctx.author.id), None)
        await self.apply_badge(ctx.author, cfg)
        await ctx.reply(embed=plain(f"Your badge is now **{'hidden' if state == 'off' else 'showing'}**.", GOLD))

    def strip_badge(self, name, symbols):
        name = name or ""
        changed = True
        while changed:
            changed = False
            for s in symbols:
                if s and name.startswith(s):
                    name = name[len(s):].lstrip()
                    changed = True
        return name.strip()

    async def apply_badge(self, member, cfg, remove=False, extra=()):
        guild = member.guild
        if member.bot or member.id == guild.owner_id:
            return
        me = guild.me
        if not me.guild_permissions.manage_nicknames or me.top_role <= member.top_role:
            return

        symbol = cfg.get("badge_symbol", BADGE_DEFAULT)
        want = (
            not remove
            and cfg.get("badge_enabled", False)
            and await self.usr_prem(member.id)
            and not await self.badge_off(member.id)
        )
        current = member.nick
        base = self.strip_badge(current or member.display_name, (symbol, *extra)) or member.name

        if want:
            target = f"{symbol} {base}"
            if len(target) > 32:
                target = f"{symbol} {base[:32 - len(symbol) - 1]}"
            if current == target:
                return
        else:
            if current is None or current == base:
                return
            target = None if base in (member.name, member.global_name) else base

        try:
            await member.edit(nick=target, reason="Premium name badge")
        except discord.HTTPException:
            pass

    async def sync_guild_badges(self, guild, remove=False, extra=()):
        cfg = await self.get_cfg(guild.id)
        docs = await premium_col.find({"type": "user"}).to_list(1000)
        for doc in docs:
            if not str(doc.get("id", "")).isdigit():
                continue
            member = guild.get_member(int(doc["id"]))
            if member:
                await self.apply_badge(member, cfg, remove=remove, extra=extra)
                await asyncio.sleep(1.2)

    async def sync_user_badges(self, user_id):
        for guild in self.bot.guilds:
            member = guild.get_member(user_id)
            if not member:
                continue
            cfg = await self.get_cfg(guild.id)
            if cfg.get("badge_enabled"):
                await self.apply_badge(member, cfg)
                await asyncio.sleep(1.2)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.nick == after.nick or after.bot:
            return
        cfg = await self.get_cfg(after.guild.id)
        if not cfg.get("badge_enabled"):
            return
        key = (after.guild.id, after.id)
        now = time.monotonic()
        if now - self.badge_last.get(key, 0) < 30:
            return
        self.badge_last[key] = now
        if len(self.badge_last) > 5000:
            self.badge_last.clear()
        await self.apply_badge(after, cfg)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot:
            return
        cfg = await self.get_cfg(member.guild.id)
        if cfg.get("badge_enabled"):
            await self.apply_badge(member, cfg)
        if not await self.srv_prem(member.guild.id):
            return

        role_id = cfg.get("auto_role_id")
        role = member.guild.get_role(int(role_id)) if role_id else None
        me = member.guild.me
        if role and safe_role(role) and me.guild_permissions.manage_roles and me.top_role > role:
            try:
                await member.add_roles(role, reason="Auto role")
            except discord.HTTPException:
                pass
        await self.check_milestone(member.guild, cfg)

    async def check_milestone(self, guild, cfg):
        count = guild.member_count
        if count not in MILESTONES:
            return
        result = await milestone_col.update_one(
            {"guild_id": str(guild.id)},
            {"$addToSet": {"announced": count}},
            upsert=True
        )
        if result.modified_count == 0 and result.upserted_id is None:
            return

        gs = await settings_col.find_one({"_id": str(guild.id)}) or {}
        target = cfg.get("milestone_channel_id") or gs.get("welcome_channel")
        channel = guild.get_channel(int(target)) if target and str(target).isdigit() else guild.system_channel
        if not channel or not channel.permissions_for(guild.me).send_messages:
            return

        embed = card("Milestone reached", f"**{guild.name}** just reached **{count:,} members**.\nThank you for being part of it.", GOLD, "Community milestone")
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    async def color_panel(self, guild):
        doc = await color_col.find_one({"guild_id": str(guild.id)}) or {}
        palette = [r for r in doc.get("roles", []) if guild.get_role(int(r["role_id"]))]
        if not palette:
            return None
        options = [
            discord.SelectOption(label=r["name"][:100], value=str(r["role_id"]), description=f"#{r['color']:06X}")
            for r in palette[:MAX_COLORS]
        ]
        options.append(discord.SelectOption(label="No color", value="none", description="Remove your color"))
        lines = [f"`#{r['color']:06X}`  {r['name']}" for r in palette[:MAX_COLORS]]
        embed = card(
            "Choose your color",
            "Pick a color from the menu below. It shows on your name in chat.\n\n" + "\n".join(lines),
            palette[0]["color"]
        )
        embed.set_footer(text=f"{guild.name}  ·  Happy Premium")
        return embed, ColorPicker(self, options)

    async def set_color(self, guild, member, value):
        me = guild.me
        if not me.guild_permissions.manage_roles:
            return "I need the Manage Roles permission to do this.", DARK
        doc = await color_col.find_one({"guild_id": str(guild.id)}) or {}
        entries = {str(r["role_id"]): r for r in doc.get("roles", [])}
        palette_roles = [guild.get_role(int(rid)) for rid in entries]
        palette_roles = [r for r in palette_roles if r]
        old = [r for r in palette_roles if r in member.roles]

        if value == "none":
            if not old:
                return "You do not have a color right now.", DARK
            try:
                await member.remove_roles(*old, reason="Color removed")
            except discord.HTTPException:
                return "I could not remove your color. Please tell an admin.", DARK
            return "Your color was removed.", GOLD

        entry = entries.get(value)
        role = guild.get_role(int(value)) if entry else None
        if not role or not safe_role(role):
            return "That color is not available. Ask an admin to refresh the color menu.", DARK
        if me.top_role <= role:
            return "I cannot give that color yet. An admin needs to move my role higher.", DARK
        try:
            remove = [r for r in old if r != role]
            if remove:
                await member.remove_roles(*remove, reason="Color changed")
            if role not in member.roles:
                await member.add_roles(role, reason=f"Picked color {entry['name']}")
        except discord.HTTPException:
            return "I could not change your color. Please tell an admin.", DARK
        return f"Your color is now **{entry['name']}**.", entry["color"]

    @commands.command(name="color", aliases=["colour"])
    @commands.guild_only()
    async def color(self, ctx, *, name: str = None):
        if not await self.srv_prem(ctx.guild.id):
            return await ctx.reply(embed=locked_card("Color roles"))
        if name:
            doc = await color_col.find_one({"guild_id": str(ctx.guild.id)}) or {}
            if name.lower() in ("none", "remove", "clear", "off"):
                value = "none"
            else:
                match = next((r for r in doc.get("roles", []) if r["name"].lower() == name.lower()), None)
                value = str(match["role_id"]) if match else ""
            text, tone = await self.set_color(ctx.guild, ctx.author, value)
            return await ctx.reply(embed=plain(text, tone))
        panel = await self.color_panel(ctx.guild)
        if not panel:
            return await ctx.reply(embed=plain("No colors are set up yet. An admin can run `,studio colors setup`."))
        embed, view = panel
        await ctx.reply(embed=embed, view=view)

    async def build_palette(self, guild, defaults):
        doc = await color_col.find_one({"guild_id": str(guild.id)}) or {}
        roles = list(doc.get("roles", []))
        have = {r["name"].lower() for r in roles}
        added = 0
        for name, hex_color in defaults:
            if name.lower() in have or len(roles) >= MAX_COLORS:
                continue
            role = discord.utils.get(guild.roles, name=f"✦ {name}")
            if not role:
                try:
                    role = await guild.create_role(
                        name=f"✦ {name}",
                        color=discord.Color(hex_color),
                        permissions=discord.Permissions.none(),
                        reason="Color roles setup"
                    )
                except discord.HTTPException:
                    continue
            roles.append({"name": name, "role_id": str(role.id), "color": hex_color})
            added += 1
            await asyncio.sleep(0.3)
        await color_col.update_one({"guild_id": str(guild.id)}, {"$set": {"roles": roles}}, upsert=True)
        return added

    @studio.group(name="colors", invoke_without_command=True)
    @commands.guild_only()
    @admin_only()
    async def studio_colors(self, ctx):
        if not await self.gate(ctx, "Color roles"):
            return
        panel = await self.color_panel(ctx.guild)
        if not panel:
            return await ctx.reply(embed=plain("No colors yet. Run `,studio colors setup` first."))
        embed, view = panel
        await ctx.send(embed=embed, view=view)

    @studio_colors.command(name="setup")
    @commands.guild_only()
    @admin_only()
    async def colors_setup(self, ctx):
        if not await self.gate(ctx, "Color roles"):
            return
        if not ctx.guild.me.guild_permissions.manage_roles:
            return await ctx.reply(embed=plain("I need the Manage Roles permission first."))
        msg = await ctx.reply(embed=card("Creating colors", "One moment..."))
        added = await self.build_palette(ctx.guild, DEFAULT_COLORS)
        await msg.edit(embed=card(
            "Colors are ready",
            f"Added **{added}** color roles.\n\n"
            "Tip: in Server Settings, drag the color roles above your other colored roles so the color shows on names."
        ))
        panel = await self.color_panel(ctx.guild)
        if panel:
            embed, view = panel
            await ctx.send(embed=embed, view=view)

    @studio_colors.command(name="add")
    @commands.guild_only()
    @admin_only()
    async def colors_add(self, ctx, name: str = None, hex_color: str = None):
        if not await self.gate(ctx, "Color roles"):
            return
        if not name or not hex_color:
            return await ctx.reply(embed=plain("Use `,studio colors add <name> <hex>`. Example: `,studio colors add Ocean #1E90FF`"))
        clean = hex_color.lstrip("#")
        if len(name) > 20 or not re.fullmatch(r"[A-Za-z0-9 _-]+", name) or not re.fullmatch(r"[0-9a-fA-F]{6}", clean):
            return await ctx.reply(embed=plain("Use a short name (letters and numbers) and a 6 digit hex code like `#1E90FF`."))
        if not ctx.guild.me.guild_permissions.manage_roles:
            return await ctx.reply(embed=plain("I need the Manage Roles permission first."))

        doc = await color_col.find_one({"guild_id": str(ctx.guild.id)}) or {}
        roles = doc.get("roles", [])
        if len(roles) >= MAX_COLORS:
            return await ctx.reply(embed=plain(f"You can have up to {MAX_COLORS} colors."))
        if any(r["name"].lower() == name.lower() for r in roles):
            return await ctx.reply(embed=plain("A color with that name already exists."))

        value = int(clean, 16)
        try:
            role = await ctx.guild.create_role(
                name=f"✦ {name}", color=discord.Color(value),
                permissions=discord.Permissions.none(), reason="Custom color"
            )
        except discord.HTTPException:
            return await ctx.reply(embed=plain("I could not create that role."))
        await color_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$push": {"roles": {"name": name, "role_id": str(role.id), "color": value}}},
            upsert=True
        )
        await ctx.reply(embed=plain(f"Added **{name}**. Post a fresh menu with `,studio colors`.", value))

    @studio_colors.command(name="remove")
    @commands.guild_only()
    @admin_only()
    async def colors_remove(self, ctx, *, name: str = None):
        if not name:
            return await ctx.reply(embed=plain("Use `,studio colors remove <name>`."))
        doc = await color_col.find_one({"guild_id": str(ctx.guild.id)}) or {}
        entry = next((r for r in doc.get("roles", []) if r["name"].lower() == name.lower()), None)
        if not entry:
            return await ctx.reply(embed=plain("I could not find that color."))
        role = ctx.guild.get_role(int(entry["role_id"]))
        if role:
            try:
                await role.delete(reason="Color removed")
            except discord.HTTPException:
                pass
        await color_col.update_one({"guild_id": str(ctx.guild.id)}, {"$pull": {"roles": {"role_id": entry["role_id"]}}})
        await ctx.reply(embed=plain(f"Removed **{entry['name']}**. Post a fresh menu with `,studio colors`.", GOLD))

    @commands.command(name="glowup")
    @commands.guild_only()
    @admin_only()
    async def glowup(self, ctx):
        if not await self.gate(ctx, "Glow up"):
            return
        msg = await ctx.reply(embed=card("Glow up", "Improving your server..."))
        done = []

        media = next((c for c in ctx.guild.text_channels if any(k in c.name for k in ("media", "gallery", "art", "selfie"))), None)
        if media:
            cfg = await self.get_cfg(ctx.guild.id)
            if not any(r["channel_id"] == str(media.id) for r in cfg.get("channel_rules", [])):
                await self.set_rule(ctx.guild.id, media.id, "media")
                done.append(f"{media.mention} is now a media only channel")
            if not any(r["channel_id"] == str(media.id) for r in cfg.get("autoreact", [])) and media.permissions_for(ctx.guild.me).add_reactions:
                await self.save_cfg(ctx.guild.id, {"$push": {"autoreact": {"channel_id": str(media.id), "emoji": "❤️", "filter": "media"}}})
                done.append(f"{media.mention} gets a ❤️ on every picture")

        if ctx.guild.me.guild_permissions.manage_roles:
            added = await self.build_palette(ctx.guild, DEFAULT_COLORS[:6])
            if added:
                done.append(f"Added {added} color roles")

        await msg.edit(embed=card(
            "Glow up complete",
            "\n".join(f"· {d}" for d in done) if done else "Everything already looks great.\nUse `,studio` for more tools."
        ))

    @commands.command(name="servercard", aliases=["scard"])
    @commands.guild_only()
    async def servercard(self, ctx):
        guild = ctx.guild
        prem = await self.srv_prem(guild.id)
        cfg = await self.get_cfg(guild.id)

        invite = cfg.get("invite_url")
        if not invite:
            if guild.vanity_url_code:
                invite = f"https://discord.gg/{guild.vanity_url_code}"
            else:
                for channel in guild.text_channels:
                    perms = channel.permissions_for(guild.me)
                    if perms.create_instant_invite:
                        try:
                            made = await channel.create_invite(max_age=0, max_uses=0, reason="Server card")
                        except discord.HTTPException:
                            continue
                        invite = made.url
                        await self.save_cfg(guild.id, {"$set": {"invite_url": invite}})
                        break

        bots = sum(1 for m in guild.members if m.bot)
        humans = max(guild.member_count - bots, 0)
        embed = card(guild.name, guild.description or "Welcome to our server.", GOLD if prem else SILVER, "Server card" + ("  ·  Premium server" if prem else ""))
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if guild.banner:
            embed.set_image(url=guild.banner.url)
        embed.add_field(name="Members", value=f"{humans:,} people · {bots} bots", inline=True)
        embed.add_field(name="Boost level", value=f"Level {guild.premium_tier}", inline=True)
        embed.add_field(name="Created", value=f"<t:{int(guild.created_at.timestamp())}:D>", inline=True)
        if invite:
            embed.add_field(name="Join", value=f"[Click to join]({invite})", inline=False)
        await ctx.reply(embed=embed)

    def build_catch_commands(self):
        cog = self

        @app_commands.context_menu(name="Catch")
        @app_commands.allowed_installs(guilds=True, users=True)
        @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def catch_menu(interaction: discord.Interaction, message: discord.Message):
            await cog.run_catch(interaction, message)

        @app_commands.command(name="catch", description="Save a message as a picture and get it in your DMs")
        @app_commands.describe(link="Right click the message and choose Copy Message Link")
        @app_commands.allowed_installs(guilds=True, users=True)
        @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def catch_slash(interaction: discord.Interaction, link: str):
            await cog.run_catch_link(interaction, link)

        return catch_menu, catch_slash

    def added_to_profile(self, interaction):
        owners = getattr(interaction, "authorizing_integration_owners", None)
        return bool(owners and getattr(owners, "user_id", None) == interaction.user.id)

    async def fetch_image(self, url, limit=8_000_000):
        host = (urlparse(url).hostname or "").lower()
        if host not in CDN_HOSTS or not self.session:
            return None
        try:
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.content.read(limit + 1)
                return data if len(data) <= limit else None
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

    async def run_catch_link(self, interaction, link):
        found = LINK_RE.search(link or "")
        hint = "I cannot see that message. Right click the message, choose **Apps**, then **Catch**."
        if not found:
            return await interaction.response.send_message(embed=plain("That does not look like a message link. " + hint), ephemeral=True)
        guild_id, channel_id, message_id = found.groups()
        guild = self.bot.get_guild(int(guild_id)) if guild_id != "@me" else None
        channel = guild.get_channel_or_thread(int(channel_id)) if guild else None
        member = guild.get_member(interaction.user.id) if guild else None
        if not channel or not member:
            return await interaction.response.send_message(embed=plain(hint), ephemeral=True)
        perms = channel.permissions_for(member)
        if not (perms.view_channel and perms.read_message_history):
            return await interaction.response.send_message(embed=plain(hint), ephemeral=True)
        try:
            message = await channel.fetch_message(int(message_id))
        except discord.HTTPException:
            return await interaction.response.send_message(embed=plain(hint), ephemeral=True)
        await self.run_catch(interaction, message)

    async def run_catch(self, interaction, message):
        uid = interaction.user.id
        if not PIL_OK:
            return await interaction.response.send_message(embed=plain("Catch is not ready yet. Please tell the bot owner."), ephemeral=True)
        if uid != BOT_OWNER_ID and not self.added_to_profile(interaction):
            return await interaction.response.send_message(embed=card(
                "Add Happy to your profile",
                "Catch works for members who added Happy to their own Discord profile.\n\n"
                "Open Happy's profile, tap **Add App**, and choose **Add to My Apps**. Then try again.",
                emoji=E_CATCH
            ), ephemeral=True)

        now = time.monotonic()
        if uid in self.catching or now - self.catch_last.get(uid, 0) < CATCH_COOLDOWN:
            return await interaction.response.send_message(embed=plain("Please wait a few seconds before using Catch again."), ephemeral=True)
        self.catch_last[uid] = now
        if len(self.catch_last) > 5000:
            self.catch_last.clear()

        self.catching.add(uid)
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            await self.catch_flow(interaction, message)
        finally:
            self.catching.discard(uid)

    async def catch_flow(self, interaction, message):
        from cogs.economy import get_account, add_wallet
        from cogs.hc_features import get_costs

        uid = interaction.user.id
        cost = 0
        if uid != BOT_OWNER_ID:
            costs = await get_costs()
            cost = int(costs.get("catch_cost", CATCH_COST_DEFAULT))
            if await self.usr_prem(uid):
                cost //= 2

        if cost > 0:
            account = await get_account(uid)
            if account["wallet"] < cost:
                return await interaction.followup.send(embed=plain(
                    f"Catch costs **{cost:,} HC** and you have **{account['wallet']:,} HC**.\n"
                    "Earn more with `,work` or `,daily`."
                ), ephemeral=True)
            await add_wallet(uid, -cost)

        problem = None
        try:
            image = await self.make_image(message)
            await interaction.user.send(
                content=f"{E_CATCH} Here is the message you caught.",
                file=discord.File(image, filename="caught.png")
            )
        except discord.Forbidden:
            problem = "I could not send you a DM. Allow DMs from server members or from this app, then try again."
        except Exception as error:
            print(f"[Catch] {error}")
            problem = "Something went wrong while making the picture. Please try again."

        if problem:
            if cost > 0:
                await add_wallet(uid, cost)
            return await interaction.followup.send(embed=plain(f"{problem}\nYou were not charged."), ephemeral=True)

        paid = f"Cost: **{cost:,} HC**" if cost else "Free for you"
        await interaction.followup.send(embed=card("Caught", f"Check your DMs. {E_CATCH}\n{paid}", emoji=E_CATCH), ephemeral=True)

    async def make_image(self, message):
        author = message.author
        name = author.display_name
        color = (255, 255, 255)
        if isinstance(author, discord.Member) and author.color.value:
            color = (author.color.r, author.color.g, author.color.b)

        names = {m.id: m.display_name for m in message.mentions}
        avatar = await self.fetch_image(author.display_avatar.with_size(128).url)
        text = scrub(message.content, names)

        lines = []
        if message.embeds:
            first = message.embeds[0]
            if first.title:
                lines.append(scrub(first.title, names))
            if first.description:
                lines.append(scrub(first.description, names))
            for field in first.fields[:6]:
                lines.append(scrub(f"{field.name}: {field.value}", names))
        lines = [x for x in lines if x]

        picture, extra = None, 0
        for a in message.attachments:
            is_image = (a.content_type or "").startswith("image/") or a.filename.lower().endswith(IMG_EXT)
            if is_image and picture is None and a.size <= 8_000_000:
                picture = await self.fetch_image(a.url)
            else:
                extra += 1
        if extra:
            text = f"{text}\n[+{extra} more file{'s' if extra > 1 else ''}]".strip()
        if not text and not lines and not picture:
            text = "(no text)"

        stamp = message.created_at.strftime("%d %b %Y, %H:%M UTC")
        return await asyncio.to_thread(render_card, name, color, avatar, stamp, text, lines, picture)


async def setup(bot):
    await bot.add_cog(Premium(bot))