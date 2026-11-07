import time
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils.db import antinuke_col
from utils.helpers import BOT_OWNER_ID

NEUTRAL = 0x2B2D31
GOOD = 0x3BA55D
BAD = 0xED4245
ALERT = 0x7A0010
NO_MENTIONS = discord.AllowedMentions.none()
TRUST_SECONDS = 20

_trust_cache = {}


def make(title=None, description=None, color=NEUTRAL):
    return discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )


def ok(text):
    return make(description=f"✅  {text}", color=GOOD)


def fail(text):
    return make(description=f"❌  {text}", color=BAD)


def state(on):
    return "🟢 On" if on else "⚪ Off"


def footer(embed, guild):
    embed.set_footer(text=guild.name, icon_url=guild.icon.url if guild.icon else None)
    return embed


async def send(ctx, embed, **kwargs):
    return await ctx.reply(
        embed=embed,
        mention_author=False,
        allowed_mentions=NO_MENTIONS,
        **kwargs,
    )


async def trusted_ids(guild):
    now = time.monotonic()
    hit = _trust_cache.get(guild.id)
    if hit and hit[1] > now:
        return hit[0]
    doc = await antinuke_col.find_one({"guild_id": str(guild.id)}, {"whitelist": 1}) or {}
    ids = set(doc.get("whitelist", []))
    _trust_cache[guild.id] = (ids, now + TRUST_SECONDS)
    return ids


def forget_trust(guild_id):
    _trust_cache.pop(guild_id, None)


async def is_trusted(bot, guild, user_id):
    if user_id in (BOT_OWNER_ID, guild.owner_id, bot.user.id):
        return True
    ids = await trusted_ids(guild)
    return str(user_id) in ids


def trusted_only():
    async def predicate(ctx):
        if ctx.guild is None:
            return False
        return await is_trusted(ctx.bot, ctx.guild, ctx.author.id)

    return commands.check(predicate)


def can_act(guild, member):
    if member.id == guild.owner_id or member.id == guild.me.id:
        return False
    return guild.me.top_role > member.top_role


async def ban_id(guild, user_id, reason):
    try:
        try:
            await guild.ban(
                discord.Object(id=user_id),
                reason=reason[:500],
                delete_message_seconds=86400,
            )
        except TypeError:
            await guild.ban(
                discord.Object(id=user_id),
                reason=reason[:500],
                delete_message_days=1,
            )
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


async def post(guild, channel_id, embed):
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if channel is None or not channel.permissions_for(guild.me).send_messages:
        return
    try:
        await channel.send(embed=embed, allowed_mentions=NO_MENTIONS)
    except discord.HTTPException:
        pass


def access_line(guild, *names):
    perms = guild.me.guild_permissions
    return "\n".join(
        f"{'✅' if getattr(perms, name) else '❌'}  {name.replace('_', ' ').title()}"
        for name in names
    )


class Panel(discord.ui.View):
    def __init__(self, author_id, builder, timeout=180):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.builder = builder
        self.message = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=fail("This panel belongs to someone else. Run the command to open your own."),
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Toggle(discord.ui.Button):
    def __init__(self, col, guild_id, key, label, state_now, default=False, row=0, after=None):
        super().__init__(style=discord.ButtonStyle.secondary, row=row)
        self.col = col
        self.gid = str(guild_id)
        self.key = key
        self.base = label
        self.default = default
        self.after = after
        self.paint(state_now)

    def paint(self, on):
        self.label = f"✓ {self.base}" if on else self.base
        self.style = discord.ButtonStyle.success if on else discord.ButtonStyle.secondary

    async def callback(self, interaction):
        doc = await self.col.find_one({"guild_id": self.gid}) or {}
        value = not doc.get(self.key, self.default)
        await self.col.update_one(
            {"guild_id": self.gid}, {"$set": {self.key: value}}, upsert=True
        )
        if self.after:
            self.after(interaction.guild.id)
        self.paint(value)
        await interaction.response.edit_message(
            embed=await self.view.builder(interaction.guild), view=self.view
        )


class Choice(discord.ui.Select):
    def __init__(self, col, guild_id, key, placeholder, options, current, row=1, after=None):
        super().__init__(
            placeholder=placeholder,
            row=row,
            options=[
                discord.SelectOption(
                    label=label, value=value, description=desc, default=(value == current)
                )
                for value, label, desc in options
            ],
        )
        self.col = col
        self.gid = str(guild_id)
        self.key = key
        self.after = after

    async def callback(self, interaction):
        chosen = self.values[0]
        await self.col.update_one(
            {"guild_id": self.gid}, {"$set": {self.key: chosen}}, upsert=True
        )
        if self.after:
            self.after(interaction.guild.id)
        for option in self.options:
            option.default = option.value == chosen
        await interaction.response.edit_message(
            embed=await self.view.builder(interaction.guild), view=self.view
        )