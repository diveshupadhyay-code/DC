import datetime
import os

import discord
import pytz
from discord import app_commands
from discord.ext import commands
from pymongo.errors import PyMongoError

from utils.db import (
    blacklist_col,
    logs_col,
    personal_prefix_col,
    premium_col,
    settings_col,
)

BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "876629015144828939"))
IST = pytz.timezone("Asia/Kolkata")
DEFAULT_PREFIXES = (",", "happy ")
LOG_COLOR = 0x2B2D31


async def get_prefix(bot, message):
    prefixes = []

    if message.author and not message.author.bot:
        personal = await personal_prefix_col.find_one({"user_id": str(message.author.id)})
        if personal and isinstance(personal.get("prefix"), str) and personal["prefix"]:
            prefixes.append(personal["prefix"])

    if message.guild:
        data = await settings_col.find_one({"_id": str(message.guild.id)})
        if data and isinstance(data.get("prefix"), str) and data["prefix"]:
            prefixes.append(data["prefix"])

    prefixes.extend(DEFAULT_PREFIXES)
    unique = list(dict.fromkeys(prefixes))
    return commands.when_mentioned_or(*unique)(bot, message)


async def is_premium_server(guild_id: int) -> bool:
    doc = await premium_col.find_one({"type": "server", "id": str(guild_id)})
    return doc is not None


async def is_premium_user(user_id: int) -> bool:
    doc = await premium_col.find_one({"type": "user", "id": str(user_id)})
    return doc is not None


async def has_premium(ctx_or_interaction) -> bool:
    user = getattr(ctx_or_interaction, "author", None) or ctx_or_interaction.user
    guild = ctx_or_interaction.guild

    if user.id == BOT_OWNER_ID:
        return True
    if await is_premium_user(user.id):
        return True
    return bool(guild) and await is_premium_server(guild.id)


def is_owner():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.id != BOT_OWNER_ID:
            raise app_commands.CheckFailure("Only the bot owner can use this.")
        return True

    return app_commands.check(predicate)


def is_mod_or_owner():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.id == BOT_OWNER_ID:
            return True
        if interaction.guild:
            perms = interaction.user.guild_permissions
            if perms.manage_messages or perms.kick_members or perms.administrator:
                return True
        raise app_commands.CheckFailure(
            "You need the Manage Messages or Kick Members permission to use this."
        )

    return app_commands.check(predicate)


def is_admin_or_owner():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.id == BOT_OWNER_ID:
            return True
        if interaction.guild and interaction.user.guild_permissions.administrator:
            return True
        raise app_commands.CheckFailure("You need the Administrator permission to use this.")

    return app_commands.check(predicate)


def slash_premium_required():
    async def predicate(interaction: discord.Interaction):
        if await has_premium(interaction):
            return True
        raise app_commands.CheckFailure(
            "This needs **Happy Premium**. Please ask the bot owner to turn it on."
        )

    return app_commands.check(predicate)


class OwnerOnlyError(commands.CheckFailure):
    pass


class BlacklistedError(commands.CheckFailure):
    pass


def ctx_owner():
    async def predicate(ctx):
        if ctx.author.id == BOT_OWNER_ID:
            return True
        raise OwnerOnlyError()

    return commands.check(predicate)


def ctx_mod():
    async def predicate(ctx):
        if ctx.author.id == BOT_OWNER_ID:
            return True
        if getattr(ctx, "has_fake_perms", False):
            return True
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        perms = ctx.author.guild_permissions
        if perms.manage_messages or perms.kick_members or perms.administrator:
            return True
        raise commands.CheckFailure(
            "You need the Manage Messages or Kick Members permission to use this."
        )

    return commands.check(predicate)


def ctx_admin():
    async def predicate(ctx):
        if ctx.author.id == BOT_OWNER_ID:
            return True
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if ctx.author.guild_permissions.administrator:
            return True
        raise commands.CheckFailure("You need the Administrator permission to use this.")

    return commands.check(predicate)


def ctx_premium():
    async def predicate(ctx):
        if await has_premium(ctx):
            return True
        raise commands.CheckFailure(
            "This needs **Happy Premium**. Please ask the bot owner to turn it on."
        )

    return commands.check(predicate)


def parse_color(color_str: str) -> discord.Color:
    try:
        text = str(color_str).strip()
        return discord.Color.from_str(text if text.startswith("#") else f"#{text}")
    except (ValueError, TypeError):
        return discord.Color(LOG_COLOR)


async def log_event(bot, guild: discord.Guild, event_type: str, description: str):
    try:
        config = await logs_col.find_one({"guild_id": str(guild.id)})
        if not config or not config.get("channel_id"):
            return
        channel = guild.get_channel(int(config["channel_id"]))
        if channel is None:
            return
        perms = channel.permissions_for(guild.me)
        if not (perms.send_messages and perms.embed_links):
            return
        embed = discord.Embed(
            title=f"📋 {str(event_type).replace('_', ' ').title()}",
            description=str(description)[:4000],
            color=LOG_COLOR,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_footer(
            text=guild.name,
            icon_url=guild.icon.url if guild.icon else None,
        )
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except (discord.HTTPException, PyMongoError, ValueError):
        return


async def get_server_data(server_id):
    data = await settings_col.find_one({"_id": str(server_id)})
    return data or {}


async def update_server_data(server_id, key, value):
    if not isinstance(key, str) or not key or key.startswith("$") or key == "_id":
        raise ValueError("That setting name is not allowed.")
    await settings_col.update_one(
        {"_id": str(server_id)},
        {"$set": {key: value}},
        upsert=True,
    )


async def is_blacklisted(user_id: int, guild_id: int = None) -> bool:
    ids = [str(user_id)]
    if guild_id:
        ids.append(str(guild_id))
    return await blacklist_col.find_one({"id": {"$in": ids}}) is not None