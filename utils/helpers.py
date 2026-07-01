import discord
from discord.ext import commands
from discord import app_commands
from utils.db import (
    settings_col, personal_prefix_col, premium_col, logs_col
)
import datetime, asyncio
import pytz

BOT_OWNER_ID = 876629015144828939
IST = pytz.timezone("Asia/Kolkata")


async def get_prefix(bot, message):
    if message.author and not message.author.bot:
        personal = await personal_prefix_col.find_one({"user_id": str(message.author.id)})
        if personal and personal.get("prefix"):
            return commands.when_mentioned_or(personal["prefix"], ",", "happy ")(bot, message)

    if not message.guild:
        return commands.when_mentioned_or(",", "happy ")(bot, message)

    data = await settings_col.find_one({"_id": str(message.guild.id)})
    if data and "prefix" in data:
        server_prefix = data["prefix"]
        return commands.when_mentioned_or(server_prefix, ",", "happy ")(bot, message)

    return commands.when_mentioned_or(",", "happy ")(bot, message)


async def is_premium_server(guild_id: int) -> bool:
    doc = await premium_col.find_one({"type": "server", "id": str(guild_id)})
    return doc is not None

async def is_premium_user(user_id: int) -> bool:
    doc = await premium_col.find_one({"type": "user", "id": str(user_id)})
    return doc is not None

async def has_premium(ctx_or_interaction) -> bool:
    if hasattr(ctx_or_interaction, 'author'):
        user_id  = ctx_or_interaction.author.id
        guild_id = ctx_or_interaction.guild.id if ctx_or_interaction.guild else 0
    else:
        user_id  = ctx_or_interaction.user.id
        guild_id = ctx_or_interaction.guild.id if ctx_or_interaction.guild else 0

    if user_id == BOT_OWNER_ID:
        return True
    return await is_premium_user(user_id) or await is_premium_server(guild_id)


def is_owner():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.id != BOT_OWNER_ID:
            raise app_commands.AppCommandError("Owner only command.")
        return True
    return app_commands.check(predicate)

def is_mod_or_owner():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.id == BOT_OWNER_ID:
            return True
        if interaction.guild:
            p = interaction.user.guild_permissions
            if p.manage_messages or p.kick_members or p.administrator:
                return True
        raise app_commands.AppCommandError("Moderator permission required.")
    return app_commands.check(predicate)

def is_admin_or_owner():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.id == BOT_OWNER_ID:
            return True
        if interaction.guild and interaction.user.guild_permissions.administrator:
            return True
        raise app_commands.AppCommandError("Administrator permission required.")
    return app_commands.check(predicate)

def slash_premium_required():
    async def predicate(interaction: discord.Interaction):
        if await has_premium(interaction):
            return True
        raise app_commands.AppCommandError(
            "This feature requires **Happy Premium**. Contact the bot owner to activate."
        )
    return app_commands.check(predicate)

def ctx_owner():
    async def predicate(ctx):
        return ctx.author.id == BOT_OWNER_ID
    return commands.check(predicate)

def ctx_mod():
    async def predicate(ctx):
        if ctx.author.id == BOT_OWNER_ID:
            return True
        p = ctx.author.guild_permissions
        return p.manage_messages or p.kick_members or p.administrator
    return commands.check(predicate)

def ctx_admin():
    async def predicate(ctx):
        if ctx.author.id == BOT_OWNER_ID:
            return True
        return ctx.author.guild_permissions.administrator
    return commands.check(predicate)

def ctx_premium():
    async def predicate(ctx):
        if await has_premium(ctx):
            return True
        raise commands.CheckFailure(
            "This feature requires **Happy Premium**. Contact the bot owner to activate."
        )
    return commands.check(predicate)


def parse_color(color_str: str) -> discord.Color:
    try:
        return discord.Color.from_str(color_str if color_str.startswith("#") else f"#{color_str}")
    except:
        return discord.Color(0x2B2D31)


async def log_event(bot, guild: discord.Guild, event_type: str, description: str):
    try:
        cfg = await logs_col.find_one({"guild_id": str(guild.id)})
        if not cfg or not cfg.get("channel_id"):
            return
        channel = bot.get_channel(int(cfg["channel_id"]))
        if not channel:
            return
        embed = discord.Embed(
            title=event_type.replace("_", " ").title(),
            description=description,
            color=0x2B2D31,
            timestamp=datetime.datetime.now(IST)
        )
        await channel.send(embed=embed)
    except:
        pass


async def get_server_data(server_id):
    data = await settings_col.find_one({"_id": str(server_id)})
    return data or {}

async def update_server_data(server_id, key, value):
    await settings_col.update_one(
        {"_id": str(server_id)},
        {"$set": {key: value}},
        upsert=True
    )