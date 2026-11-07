import asyncio
import io
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import ctx_admin, BOT_OWNER_ID
from cogs.hc_features import get_costs, try_charge

CUSTOM_EMOJI = re.compile(r"<(a?):([A-Za-z0-9_]{2,32}):(\d{15,20})>")
EMOJI_LINK = re.compile(
    r"https://(?:cdn\.discordapp\.com|media\.discordapp\.net)/emojis/(\d{15,20})(?:\.(\w{3,4}))?(?:\?([^\s>)]*))?"
)
TYPED_EMOJI = re.compile(
    r"```.*?```|`[^`\n]*`|<a?:\w{2,32}:\d{15,20}>|:([A-Za-z0-9_]{2,32}):",
    re.DOTALL,
)
VALID_NAME = re.compile(r"[A-Za-z0-9_]{2,32}")
EMOJI_URL = "https://cdn.discordapp.com/emojis/{id}.{ext}"
TRUSTED_HOSTS = ("cdn.discordapp.com", "media.discordapp.net")

MAX_EMOJI_BYTES = 256 * 1024
MAX_STICKER_BYTES = 512 * 1024
MAX_PER_RUN = 10
HOOK_NAME = "Emoji Helper"
MENU_NAME = "Steal Emojis & Stickers"
SETTINGS_FILE = Path("data/emoji_settings.json")

BASE = 0x2B2D31
GOOD = 0x57F287
BAD = 0xED4245
WARN = 0xFEE75C

NO_PING = discord.AllowedMentions.none()


def _card(title: str, text: str | None = None, color: int = BASE) -> discord.Embed:
    return discord.Embed(title=title, description=text, color=color)


def _permission_card() -> discord.Embed:
    return _card(
        "Permission needed",
        "Kindly give me the **Manage Emojis and Stickers** permission and try again.",
        BAD,
    )


def _bar(used: int, total: int, size: int = 10) -> str:
    if total <= 0:
        return "▱" * size
    filled = min(size, round(size * used / total))
    return "▰" * filled + "▱" * (size - filled)


def _clean_name(raw: str | None) -> str | None:
    if not raw:
        return None
    name = raw.strip().strip(":").replace(" ", "_")
    return name if VALID_NAME.fullmatch(name) else None


def _is_admin(user) -> bool:
    if user.id == BOT_OWNER_ID:
        return True
    return isinstance(user, discord.Member) and user.guild_permissions.administrator


def _can_manage(guild: discord.Guild) -> bool:
    return guild.me.guild_permissions.manage_emojis_and_stickers


def _slots(guild: discord.Guild) -> tuple[int, int, int]:
    static = sum(1 for e in guild.emojis if not e.animated)
    moving = sum(1 for e in guild.emojis if e.animated)
    return static, moving, guild.emoji_limit


def _state(guild: discord.Guild) -> tuple[dict, int]:
    static, moving, limit = _slots(guild)
    used = {
        "static": static,
        "animated": moving,
        "names": {e.name.lower() for e in guild.emojis},
        "stickers": len(guild.stickers),
        "sticker_limit": guild.sticker_limit,
    }
    return used, limit


def _find_own(guild: discord.Guild, text: str | None) -> discord.Emoji | None:
    if not text:
        return None
    match = CUSTOM_EMOJI.search(text)
    if match:
        return guild.get_emoji(int(match.group(3)))
    wanted = text.strip().strip(":").lower()
    return next((e for e in guild.emojis if e.name.lower() == wanted), None)


def _fit(text: str, limit: int = 1000) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _overview(guild: discord.Guild, title: str) -> discord.Embed:
    static, moving, limit = _slots(guild)
    stickers = len(guild.stickers)
    sticker_limit = guild.sticker_limit
    embed = discord.Embed(title=title, color=BASE)
    embed.add_field(
        name="Normal emojis",
        value=f"{_bar(static, limit)}\n`{static} of {limit}` used",
        inline=True,
    )
    embed.add_field(
        name="Moving emojis",
        value=f"{_bar(moving, limit)}\n`{moving} of {limit}` used",
        inline=True,
    )
    embed.add_field(
        name="Stickers",
        value=f"{_bar(stickers, sticker_limit)}\n`{stickers} of {sticker_limit}` used",
        inline=True,
    )
    embed.set_footer(text=f"{guild.name}  •  Boost level {guild.premium_tier}")
    return embed


def _results(added: list, skipped: list, failed: list, extra: int = 0) -> discord.Embed:
    embed = _card("Results", color=GOOD if added else BAD)
    if added:
        embed.add_field(name=f"Added ({len(added)})", value=_fit(" ".join(added)), inline=False)
    if skipped:
        embed.add_field(name=f"Already here ({len(skipped)})", value=_fit(", ".join(skipped)), inline=False)
    if failed:
        embed.add_field(name=f"Not added ({len(failed)})", value=_fit("\n".join(failed)), inline=False)
    if extra:
        embed.set_footer(text=f"Only {MAX_PER_RUN} emojis can be added at one time. {extra} more were left out.")
    return embed


def _is_trusted(url: str) -> bool:
    try:
        parts = urlparse(url)
    except ValueError:
        return False
    return parts.scheme == "https" and parts.hostname in TRUSTED_HOSTS


async def _download(session: aiohttp.ClientSession, url: str, limit: int) -> bytes | None:
    if not _is_trusted(url):
        return None
    try:
        async with session.get(url, allow_redirects=False) as resp:
            if resp.status != 200:
                return None
            if resp.content_length and resp.content_length > limit:
                return None
            data = bytearray()
            async for chunk in resp.content.iter_chunked(16384):
                data.extend(chunk)
                if len(data) > limit:
                    return None
            return bytes(data)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None


async def _fetch_emoji(
    session: aiohttp.ClientSession, emoji_id: int, animated: bool | None
) -> tuple[bytes, bool] | None:
    if animated is not False:
        data = await _download(
            session, EMOJI_URL.format(id=emoji_id, ext="gif"), MAX_EMOJI_BYTES
        )
        if data and data[:3] == b"GIF":
            return data, True
    if animated is True:
        return None
    data = await _download(
        session, EMOJI_URL.format(id=emoji_id, ext="png"), MAX_EMOJI_BYTES
    )
    if data:
        return data, False
    return None


async def _charge(user_id: int) -> int | None:
    costs = await get_costs()
    price = costs.get("emoji_cost", 0)
    if not await try_charge(user_id, price):
        return None
    return price


async def _refund(user_id: int, price: int) -> None:
    if price:
        from cogs.economy import add_wallet

        await add_wallet(user_id, price)


class Settings:
    def __init__(self):
        self.enabled: set[int] = set()
        try:
            self.enabled = set(json.loads(SETTINGS_FILE.read_text())["enabled"])
        except (OSError, ValueError, KeyError, TypeError):
            self.enabled = set()

    def save(self) -> None:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = SETTINGS_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps({"enabled": sorted(self.enabled)}))
        temp.replace(SETTINGS_FILE)

    def set(self, guild_id: int, value: bool) -> None:
        if value:
            self.enabled.add(guild_id)
        else:
            self.enabled.discard(guild_id)
        self.save()


class OwnerView(discord.ui.View):
    def __init__(self, owner_id: int, timeout: float = 120):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.message: discord.Message | None = None
        self.origin: discord.Interaction | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=_card("Not for you", "Only the person who started this can use the menu.", BAD),
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            if self.origin:
                await self.origin.edit_original_response(view=self)
            elif self.message:
                await self.message.edit(view=self)
        except discord.HTTPException:
            pass


class PickMenu(discord.ui.Select):
    def __init__(self, cog: "Emotes", results: list[discord.Emoji]):
        options = [
            discord.SelectOption(label=e.name[:100], value=str(e.id), emoji=e)
            for e in results[:25]
        ]
        super().__init__(placeholder="Pick an emoji to add", options=options)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None or not _is_admin(interaction.user):
            return await interaction.response.send_message(
                embed=_card("Not allowed", "Sorry, only admins can add emojis.", BAD),
                ephemeral=True,
            )

        source = interaction.client.get_emoji(int(self.values[0]))
        if source is None or not source.available:
            return await interaction.response.edit_message(
                embed=_card("Emoji not found", "This emoji is not available anymore.", BAD),
                view=None,
            )
        if not _can_manage(guild):
            return await interaction.response.edit_message(embed=_permission_card(), view=None)

        await interaction.response.defer()
        async with self.cog.lock(guild.id):
            used, limit = _state(guild)
            emoji, kind, why = await self.cog.import_emoji(
                guild, interaction.user, source.name, source.id, source.animated, used, limit
            )

        if kind == "added":
            embed = _card("Emoji added", f"{emoji}  is now in your server as `:{emoji.name}:`", GOOD)
        elif kind == "skipped":
            embed = _card("Already here", f"An emoji named `{source.name}` is already in your server.", WARN)
        else:
            embed = _card("Could not add emoji", f"Reason: {why}.", BAD)
        await interaction.edit_original_response(embed=embed, view=None)


class StealMenu(discord.ui.Select):
    def __init__(self, cog: "Emotes", targets: list[discord.Guild], current_id: int | None, emojis: list, stickers: list):
        options = [
            discord.SelectOption(
                label=g.name[:100],
                value=str(g.id),
                description="This server" if g.id == current_id else None,
            )
            for g in targets[:25]
        ]
        super().__init__(placeholder="Choose where to add", options=options)
        self.cog = cog
        self.found_emojis = emojis
        self.found_stickers = stickers

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.client.get_guild(int(self.values[0]))
        member = guild.get_member(interaction.user.id) if guild else None
        if guild is None or member is None or not _is_admin(member) or not _can_manage(guild):
            return await interaction.response.send_message(
                embed=_card("Not allowed", "Sorry, you cannot add to this server.", BAD),
                ephemeral=True,
            )
        await interaction.response.defer()
        added, skipped, failed = await self.cog.run_items(
            guild, interaction.user, self.found_emojis, self.found_stickers
        )
        await interaction.edit_original_response(embed=_results(added, skipped, failed), view=None)


class Emotes(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self.settings = Settings()
        self.hooks: dict[int, discord.Webhook] = {}
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.cooldown = commands.CooldownMapping.from_cooldown(
            5, 10, commands.BucketType.user
        )
        self.menu = app_commands.ContextMenu(
            name=MENU_NAME,
            callback=self.steal_menu,
            allowed_contexts=app_commands.AppCommandContext(
                guild=True, dm_channel=True, private_channel=True
            ),
            allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
        )

    async def cog_load(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        self.bot.tree.add_command(self.menu)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.menu.name, type=self.menu.type)
        if self.session:
            await self.session.close()

    def lock(self, key: int) -> asyncio.Lock:
        return self._locks[key]

    def collect(self, text: str) -> list[tuple[bool | None, str, int]]:
        items, seen = [], set()
        for flag, name, eid in CUSTOM_EMOJI.findall(text):
            key = int(eid)
            if key not in seen:
                seen.add(key)
                items.append((bool(flag), name, key))
        for eid, ext, query in EMOJI_LINK.findall(text):
            key = int(eid)
            if key in seen:
                continue
            seen.add(key)
            animated = True if ext.lower() == "gif" or "animated=true" in query.lower() else None
            known = self.bot.get_emoji(key)
            if known and animated is None:
                animated = known.animated
            items.append((animated, known.name if known else f"emote_{eid[-4:]}", key))
        return items

    async def import_emoji(self, guild, user, name, emoji_id, animated, used, limit):
        clean = _clean_name(name)
        if not clean:
            return None, "failed", "the name is not valid"
        if clean.lower() in used["names"]:
            return None, "skipped", "the name is already used"
        if animated is not None and used["animated" if animated else "static"] >= limit:
            return None, "failed", "there is no space left in your server"

        got = await _fetch_emoji(self.session, emoji_id, animated)
        if not got:
            return None, "failed", "I could not download it"
        data, animated = got

        key = "animated" if animated else "static"
        if used[key] >= limit:
            return None, "failed", "there is no space left in your server"

        price = await _charge(user.id)
        if price is None:
            return None, "failed", "your balance is not enough"

        try:
            new = await guild.create_custom_emoji(
                name=clean, image=data, reason=f"Added by {user} ({user.id})"
            )
        except discord.Forbidden:
            await _refund(user.id, price)
            return None, "failed", "I do not have permission"
        except discord.HTTPException:
            await _refund(user.id, price)
            return None, "failed", "Discord did not accept it"

        used[key] += 1
        used["names"].add(clean.lower())
        return new, "added", ""

    async def import_sticker(self, guild, user, item, name, used):
        if item.format.name == "lottie":
            return None, "failed", "this type of sticker cannot be copied"
        label = (name or item.name).strip()
        if not 2 <= len(label) <= 30:
            return None, "failed", "the name must be 2 to 30 letters long"
        if used["stickers"] >= used["sticker_limit"]:
            return None, "failed", "there is no space left for stickers"

        data = await _download(self.session, item.url, MAX_STICKER_BYTES)
        if not data:
            return None, "failed", "I could not download it"

        price = await _charge(user.id)
        if price is None:
            return None, "failed", "your balance is not enough"

        ext = "gif" if item.format.name == "gif" else "png"
        try:
            new = await guild.create_sticker(
                name=label,
                description=label,
                emoji="⭐",
                file=discord.File(io.BytesIO(data), filename=f"sticker.{ext}"),
                reason=f"Added by {user} ({user.id})",
            )
        except discord.HTTPException:
            await _refund(user.id, price)
            return None, "failed", "Discord did not accept it"

        used["stickers"] += 1
        return new, "added", ""

    async def run_items(self, guild, user, emojis, stickers):
        added, skipped, failed = [], [], []
        async with self.lock(guild.id):
            used, limit = _state(guild)
            for animated, name, eid in emojis:
                emoji, kind, why = await self.import_emoji(
                    guild, user, name, eid, animated, used, limit
                )
                if kind == "added":
                    added.append(str(emoji))
                    await asyncio.sleep(1)
                elif kind == "skipped":
                    skipped.append(f"`{name}`")
                else:
                    failed.append(f"`{name}`  {why}")
            for item in stickers:
                sticker, kind, why = await self.import_sticker(guild, user, item, None, used)
                if kind == "added":
                    added.append(f"**{discord.utils.escape_markdown(sticker.name)}** (sticker)")
                    await asyncio.sleep(1)
                else:
                    failed.append(f"`{item.name}`  {why}")
        return added, skipped, failed

    def search(self, user_id: int, query: str, skip_guild: int | None) -> list[discord.Emoji]:
        q = query.lower().strip()[:32]
        if not q:
            return []
        found: list[discord.Emoji] = []
        seen: set[int] = set()
        for guild in self.bot.guilds:
            if guild.id == skip_guild or guild.get_member(user_id) is None:
                continue
            for e in guild.emojis:
                if e.available and e.id not in seen and q in e.name.lower():
                    seen.add(e.id)
                    found.append(e)
        found.sort(key=lambda e: (e.name.lower() != q, not e.name.lower().startswith(q), e.name.lower()))
        return found[:25]

    def search_card(self, results: list[discord.Emoji], query: str) -> discord.Embed:
        embed = _card(
            "Emoji search",
            f"Found **{len(results)}** emoji for `{query[:32]}`.\nPick one below to add it to this server.",
        )
        embed.set_footer(text="Only emojis from servers you are in are shown")
        return embed

    def targets_for(self, user_id: int, current_id: int | None) -> list[discord.Guild]:
        out = []
        for guild in self.bot.guilds:
            member = guild.get_member(user_id)
            if member and _is_admin(member) and _can_manage(guild):
                out.append(guild)
        out.sort(key=lambda g: (g.id != current_id, g.name.lower()))
        return out[:25]

    def emoji_index(self, user_id: int, first_guild_id: int | None = None) -> dict[str, discord.Emoji]:
        guilds = sorted(self.bot.guilds, key=lambda g: g.id != first_guild_id)
        index: dict[str, discord.Emoji] = {}
        for guild in guilds:
            inside = guild.get_member(user_id)
            if inside is None:
                continue
            for e in guild.emojis:
                if not e.available:
                    continue
                if e.roles and not set(e.roles) & set(inside.roles):
                    continue
                index.setdefault(e.name.lower(), e)
        return index

    def render(self, text: str, index: dict[str, discord.Emoji]) -> tuple[str, bool]:
        changed = False

        def swap(match: re.Match) -> str:
            nonlocal changed
            name = match.group(1)
            if not name:
                return match.group(0)
            emoji = index.get(name.lower())
            if emoji is None:
                return match.group(0)
            changed = True
            return str(emoji)

        return TYPED_EMOJI.sub(swap, text), changed

    async def hook_for(self, channel: discord.TextChannel) -> discord.Webhook:
        hook = self.hooks.get(channel.id)
        if hook:
            return hook
        async with self.lock(channel.id):
            hook = self.hooks.get(channel.id)
            if hook:
                return hook
            for existing in await channel.webhooks():
                owned = existing.user and existing.user.id == self.bot.user.id
                if owned and existing.name == HOOK_NAME and existing.token:
                    self.hooks[channel.id] = existing
                    return existing
            hook = await channel.create_webhook(
                name=HOOK_NAME, reason="Needed to send emojis for members"
            )
            self.hooks[channel.id] = hook
            return hook

    async def steal_menu(self, interaction: discord.Interaction, message: discord.Message):
        emojis = self.collect(message.content or "")[:MAX_PER_RUN]
        stickers = list(message.stickers)
        if not emojis and not stickers:
            return await interaction.response.send_message(
                embed=_card("Nothing found", "This message has no emojis or stickers.", WARN),
                ephemeral=True,
            )

        embed = _card(
            "Steal Emojis & Stickers",
            f"Found **{len(emojis)}** emoji and **{len(stickers)}** sticker.",
        )
        links = []
        if emojis:
            shown = " ".join(
                str(discord.PartialEmoji(name=n, animated=bool(a), id=i)) for a, n, i in emojis
            )
            embed.add_field(name="Emojis", value=_fit(shown), inline=False)
            for animated, name, eid in emojis:
                ext = "gif" if animated else "png"
                links.append(f"[{name}]({EMOJI_URL.format(id=eid, ext=ext)})")
        if stickers:
            names = ", ".join(f"**{discord.utils.escape_markdown(s.name)}**" for s in stickers)
            embed.add_field(name="Stickers", value=_fit(names), inline=False)
            for s in stickers:
                links.append(f"[{discord.utils.escape_markdown(s.name)}]({s.url})")
        embed.add_field(name="Download images", value=_fit("  •  ".join(links)), inline=False)

        targets = self.targets_for(interaction.user.id, interaction.guild_id)
        if not targets:
            embed.set_footer(text="You can add them only to a server where you are admin and I am present")
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        embed.set_footer(text="Choose a server below to add them")
        view = OwnerView(interaction.user.id)
        view.add_item(StealMenu(self, targets, interaction.guild_id, emojis, stickers))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.origin = interaction

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.webhook_id or message.guild is None:
            return
        if message.guild.id not in self.settings.enabled:
            return
        content = message.content
        if not content or ":" not in content or len(content) > 1900:
            return
        if message.attachments or message.stickers or message.reference:
            return
        if not any(m.group(1) for m in TYPED_EMOJI.finditer(content)):
            return
        if not isinstance(message.author, discord.Member):
            return

        channel = message.channel
        thread = discord.utils.MISSING
        if isinstance(channel, discord.Thread):
            thread = channel
            channel = channel.parent
        if not isinstance(channel, discord.TextChannel) or channel.slowmode_delay:
            return

        mine = channel.permissions_for(message.guild.me)
        if not (mine.manage_webhooks and mine.manage_messages):
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return
        if self.cooldown.get_bucket(message).update_rate_limit():
            return

        text, changed = self.render(content, self.emoji_index(message.author.id, message.guild.id))
        if not changed or len(text) > 2000:
            return

        can_ping = message.author.guild_permissions.mention_everyone
        mentions = discord.AllowedMentions(
            everyone=can_ping,
            users=True,
            roles=True if can_ping else [r for r in message.role_mentions if r.mentionable],
        )

        for _ in range(2):
            try:
                hook = await self.hook_for(channel)
                await hook.send(
                    content=text,
                    username=(message.author.display_name or "Member")[:80],
                    avatar_url=message.author.display_avatar.url,
                    allowed_mentions=mentions,
                    thread=thread,
                )
                break
            except discord.NotFound:
                self.hooks.pop(channel.id, None)
            except discord.HTTPException:
                return
        else:
            return

        try:
            await message.delete()
        except discord.HTTPException:
            pass

    @commands.group(name="emoji", aliases=["emote", "emojis"], invoke_without_command=True)
    @commands.guild_only()
    @ctx_admin()
    async def emoji(self, ctx):
        p = ctx.clean_prefix
        embed = _overview(ctx.guild, "Emoji Manager")
        embed.add_field(
            name="Commands",
            value=(
                f"`{p}emoji steal`  Reply to a message, or add emojis and links after it\n"
                f"`{p}emoji link <link> [name]`  Add an emoji from its image link\n"
                f"`{p}emoji sticker [name]`  Reply to a sticker to add it\n"
                f"`{p}emoji search <name>`  Find emojis from your other servers\n"
                f"`{p}emoji addid <id> <name>`  Add an emoji using its ID\n"
                f"`{p}emoji rename <emoji> <name>`  Change an emoji name\n"
                f"`{p}emoji remove <emoji>`  Delete an emoji\n"
                f"`{p}emoji list`  See all emojis here\n"
                f"`{p}emoji auto on|off`  Let members type `:name:` to use any emoji"
            ),
            inline=False,
        )
        embed.add_field(
            name="Anywhere on Discord",
            value=(
                "Add me to your profile, then long press or right click any message, "
                f"choose **Apps** and tap **{MENU_NAME}**. "
                "Use `/sendemoji` to send any emoji without Nitro."
            ),
            inline=False,
        )
        await ctx.reply(embed=embed, allowed_mentions=NO_PING)

    @emoji.command(name="steal", aliases=["add", "copy"])
    @ctx_admin()
    async def emoji_steal(self, ctx, *, args: str = ""):
        text = args or ""
        stickers = []
        ref = ctx.message.reference
        if ref and isinstance(ref.resolved, discord.Message):
            text += " " + ref.resolved.content
            stickers = list(ref.resolved.stickers)

        emojis = self.collect(text)
        if not emojis and not stickers:
            return await ctx.reply(
                embed=_card(
                    "Nothing to add",
                    f"Kindly send emojis or emoji links after the command, or reply to a message that has emojis or a sticker.\n`{ctx.clean_prefix}emoji steal <emojis>`",
                    WARN,
                ),
                allowed_mentions=NO_PING,
            )
        if not _can_manage(ctx.guild):
            return await ctx.reply(embed=_permission_card(), allowed_mentions=NO_PING)

        extra = max(0, len(emojis) - MAX_PER_RUN)
        async with ctx.typing():
            added, skipped, failed = await self.run_items(
                ctx.guild, ctx.author, emojis[:MAX_PER_RUN], stickers
            )
        await ctx.reply(embed=_results(added, skipped, failed, extra), allowed_mentions=NO_PING)

    @emoji.command(name="link", aliases=["url"])
    @ctx_admin()
    async def emoji_link(self, ctx, url: str = None, *, name: str = None):
        items = self.collect(url or "")
        if not items:
            return await ctx.reply(
                embed=_card(
                    "Link not correct",
                    f"Kindly use the image link of a Discord emoji.\n`{ctx.clean_prefix}emoji link <link> [name]`",
                    WARN,
                ),
                allowed_mentions=NO_PING,
            )
        if not _can_manage(ctx.guild):
            return await ctx.reply(embed=_permission_card(), allowed_mentions=NO_PING)

        animated, default, eid = items[0]
        async with ctx.typing():
            added, skipped, failed = await self.run_items(
                ctx.guild, ctx.author, [(animated, name or default, eid)], []
            )
        await ctx.reply(embed=_results(added, skipped, failed), allowed_mentions=NO_PING)

    @emoji.command(name="addid")
    @ctx_admin()
    async def emoji_addid(self, ctx, emoji_id: str = None, *, name: str = None):
        if not emoji_id or not name or not emoji_id.isdigit() or not 15 <= len(emoji_id) <= 20:
            return await ctx.reply(
                embed=_card(
                    "Details missing",
                    f"Please use it like this:\n`{ctx.clean_prefix}emoji addid <emoji id> <name>`",
                    WARN,
                ),
                allowed_mentions=NO_PING,
            )
        if not _can_manage(ctx.guild):
            return await ctx.reply(embed=_permission_card(), allowed_mentions=NO_PING)

        known = self.bot.get_emoji(int(emoji_id))
        async with ctx.typing(), self.lock(ctx.guild.id):
            used, limit = _state(ctx.guild)
            emoji, kind, why = await self.import_emoji(
                ctx.guild, ctx.author, name, int(emoji_id),
                known.animated if known else None, used, limit,
            )

        if kind == "added":
            embed = _card("Emoji added", f"{emoji}  is now in your server as `:{emoji.name}:`", GOOD)
        elif kind == "skipped":
            embed = _card("Already here", "An emoji with this name is already in your server.", WARN)
        else:
            embed = _card("Could not add emoji", f"Reason: {why}.", BAD)
        await ctx.reply(embed=embed, allowed_mentions=NO_PING)

    @emoji.command(name="search", aliases=["find"])
    @ctx_admin()
    async def emoji_search(self, ctx, *, query: str = None):
        if not query:
            return await ctx.reply(
                embed=_card("Name missing", f"Please use it like this:\n`{ctx.clean_prefix}emoji search <name>`", WARN),
                allowed_mentions=NO_PING,
            )
        results = self.search(ctx.author.id, query, ctx.guild.id)
        if not results:
            return await ctx.reply(
                embed=_card("No results", f"Nothing was found for `{query[:32]}`.", WARN),
                allowed_mentions=NO_PING,
            )
        view = OwnerView(ctx.author.id, timeout=90)
        view.add_item(PickMenu(self, results))
        view.message = await ctx.reply(
            embed=self.search_card(results, query), view=view, allowed_mentions=NO_PING
        )

    @emoji.command(name="sticker", aliases=["stickers"])
    @ctx_admin()
    async def emoji_sticker(self, ctx, *, name: str = None):
        ref = ctx.message.reference
        target = ref.resolved if ref and isinstance(ref.resolved, discord.Message) else None
        if not target or not target.stickers:
            return await ctx.reply(
                embed=_card(
                    "Reply to a sticker",
                    f"Kindly reply to a message that has a sticker, then use\n`{ctx.clean_prefix}emoji sticker [name]`",
                    WARN,
                ),
                allowed_mentions=NO_PING,
            )
        if not _can_manage(ctx.guild):
            return await ctx.reply(embed=_permission_card(), allowed_mentions=NO_PING)

        async with ctx.typing(), self.lock(ctx.guild.id):
            used, _ = _state(ctx.guild)
            sticker, kind, why = await self.import_sticker(
                ctx.guild, ctx.author, target.stickers[0], name, used
            )

        if kind == "added":
            embed = _card(
                "Sticker added",
                f"**{discord.utils.escape_markdown(sticker.name)}** is now in your server.",
                GOOD,
            )
        else:
            embed = _card("Could not add sticker", f"Reason: {why}.", BAD)
        await ctx.reply(embed=embed, allowed_mentions=NO_PING)

    @emoji.command(name="remove", aliases=["delete"])
    @ctx_admin()
    async def emoji_remove(self, ctx, *, emoji: str = None):
        target = _find_own(ctx.guild, emoji)
        if target is None:
            return await ctx.reply(
                embed=_card(
                    "Emoji not found",
                    f"Please give the emoji or its name from this server.\n`{ctx.clean_prefix}emoji remove <emoji>`",
                    WARN,
                ),
                allowed_mentions=NO_PING,
            )
        name = target.name
        try:
            await target.delete(reason=f"Removed by {ctx.author} ({ctx.author.id})")
        except discord.HTTPException:
            return await ctx.reply(
                embed=_card("Could not remove", "I am not allowed to remove this emoji.", BAD),
                allowed_mentions=NO_PING,
            )
        await ctx.reply(
            embed=_card("Emoji removed", f"`:{name}:` has been removed.", GOOD),
            allowed_mentions=NO_PING,
        )

    @emoji.command(name="rename")
    @ctx_admin()
    async def emoji_rename(self, ctx, emoji: str = None, *, new_name: str = None):
        target = _find_own(ctx.guild, emoji)
        clean = _clean_name(new_name)
        if target is None or clean is None:
            return await ctx.reply(
                embed=_card(
                    "Details missing",
                    f"Names can have letters, numbers and `_`, and must be 2 to 32 letters long.\n`{ctx.clean_prefix}emoji rename <emoji> <new name>`",
                    WARN,
                ),
                allowed_mentions=NO_PING,
            )
        if any(e.name.lower() == clean.lower() and e.id != target.id for e in ctx.guild.emojis):
            return await ctx.reply(
                embed=_card("Name already used", f"An emoji named `{clean}` already exists.", WARN),
                allowed_mentions=NO_PING,
            )
        old = target.name
        try:
            await target.edit(name=clean, reason=f"Renamed by {ctx.author} ({ctx.author.id})")
        except discord.HTTPException:
            return await ctx.reply(
                embed=_card("Could not rename", "I am not allowed to rename this emoji.", BAD),
                allowed_mentions=NO_PING,
            )
        await ctx.reply(
            embed=_card("Emoji renamed", f"`:{old}:`  →  `:{clean}:`", GOOD),
            allowed_mentions=NO_PING,
        )

    @emoji.command(name="list")
    @ctx_admin()
    async def emoji_list(self, ctx):
        embed = _overview(ctx.guild, "Server Emojis")
        normal = [str(e) for e in ctx.guild.emojis if not e.animated]
        moving = [str(e) for e in ctx.guild.emojis if e.animated]
        for title, items in (("Normal", normal), ("Moving", moving)):
            if not items:
                continue
            shown = items[:35]
            more = f"\n+{len(items) - len(shown)} more" if len(items) > len(shown) else ""
            embed.add_field(
                name=f"{title} ({len(items)})",
                value=_fit(" ".join(shown) + more),
                inline=False,
            )
        await ctx.reply(embed=embed, allowed_mentions=NO_PING)

    @emoji.command(name="auto")
    @ctx_admin()
    async def emoji_auto(self, ctx, mode: str = None):
        mode = (mode or "").lower()
        on = ctx.guild.id in self.settings.enabled
        if mode not in {"on", "off"}:
            return await ctx.reply(
                embed=_card(
                    "Type :name: to use emojis",
                    f"Right now this is **{'on' if on else 'off'}**.\n"
                    "When it is on, members can type `:name:` and I will send their message with the emoji, "
                    "even if they do not have Nitro.\n\n"
                    f"`{ctx.clean_prefix}emoji auto on`  or  `{ctx.clean_prefix}emoji auto off`",
                ),
                allowed_mentions=NO_PING,
            )
        if mode == "on":
            perms = ctx.guild.me.guild_permissions
            if not (perms.manage_webhooks and perms.manage_messages):
                return await ctx.reply(
                    embed=_card(
                        "Permission needed",
                        "Kindly give me **Manage Webhooks** and **Manage Messages** first.",
                        BAD,
                    ),
                    allowed_mentions=NO_PING,
                )
        self.settings.set(ctx.guild.id, mode == "on")
        await ctx.reply(
            embed=_card(
                "Saved",
                f"Typing `:name:` is now **{'on' if mode == 'on' else 'off'}** in this server.",
                GOOD,
            ),
            allowed_mentions=NO_PING,
        )

    @app_commands.command(name="emojisearch", description="Find emojis from your other servers and add them here")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_emoji_search(self, interaction: discord.Interaction, query: str):
        results = self.search(interaction.user.id, query, interaction.guild.id)
        if not results:
            return await interaction.response.send_message(
                embed=_card("No results", f"Nothing was found for `{query[:32]}`.", WARN),
                ephemeral=True,
            )
        view = OwnerView(interaction.user.id, timeout=90)
        view.add_item(PickMenu(self, results))
        await interaction.response.send_message(
            embed=self.search_card(results, query), view=view, ephemeral=True
        )
        view.origin = interaction

    @app_commands.command(name="sendemoji", description="Send a message with any emoji, even without Nitro")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.checks.cooldown(5, 10.0)
    @app_commands.describe(text="Write emoji names like :fire: and I will fill them in")
    async def slash_send_emoji(self, interaction: discord.Interaction, text: app_commands.Range[str, 1, 1500]):
        index = self.emoji_index(interaction.user.id, interaction.guild_id)
        result, changed = self.render(text, index)
        if not changed:
            return await interaction.response.send_message(
                embed=_card(
                    "No emoji matched",
                    "Write the emoji name between colons, like `:fire:`. Only emojis from servers you are in will work.",
                    WARN,
                ),
                ephemeral=True,
            )
        if len(result) > 2000:
            return await interaction.response.send_message(
                embed=_card("Message too long", "Kindly make your message a little shorter.", WARN),
                ephemeral=True,
            )
        await interaction.response.send_message(result, allowed_mentions=NO_PING)

    async def cog_app_command_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            text = "Sorry, only admins can use this command."
        elif isinstance(error, app_commands.CommandOnCooldown):
            text = f"Please wait {error.retry_after:.0f} seconds and try again."
        else:
            text = "Something went wrong. Please try again after some time."
        embed = _card("Could not finish", text, BAD)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Emotes(bot))