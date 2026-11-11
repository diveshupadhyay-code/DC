import asyncio
import logging
import os
import time
from collections import OrderedDict
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from flask import Flask, render_template

from utils.db import blacklist_col, init_indexes
from utils.helpers import get_prefix, BOT_OWNER_ID, BlacklistedError

load_dotenv()

DARK = 0x2B2D31
AI_SESSION_SECONDS = 90

COGS = [
    "cogs.core",
    "cogs.moderation",
    "cogs.utility",
    "cogs.fun",
    "cogs.leveling",
    "cogs.tickets",
    "cogs.roles",
    "cogs.welcome",
    "cogs.ai_chat",
    "cogs.premium",
    "cogs.admin",
    "cogs.help",
    "cogs.games",
    "cogs.economy",
    "cogs.levelroles",
    "cogs.tracker",
    "cogs.extraperm",
    "cogs.aliases",
    "cogs.antinuke",
    "cogs.antiraid",
    "cogs.automod",
    "cogs.fakeperms",
    "cogs.emotes",
    "cogs.hc_features",
    "cogs.owner",
]

web = Flask(__name__)
web.config["MAX_CONTENT_LENGTH"] = 1024


@web.after_request
def add_safety_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


@web.route("/")
def home():
    return render_template("happy_bot_dashboard.html")


@web.route("/health")
def health():
    return "ok", 200


def run_web():
    port = int(os.environ.get("PORT", 10000))
    web.run(host="0.0.0.0", port=port)


blacklist_cache = {}


async def is_blacklisted(user_id, guild_id=None):
    if user_id == BOT_OWNER_ID:
        return False
    key = (user_id, guild_id)
    now = time.monotonic()
    hit = blacklist_cache.get(key)
    if hit and now - hit[0] < 30:
        return hit[1]
    ids = [str(user_id)]
    if guild_id:
        ids.append(str(guild_id))
    found = await blacklist_col.find_one({"id": {"$in": ids}}) is not None
    if len(blacklist_cache) > 5000:
        blacklist_cache.clear()
    blacklist_cache[key] = (now, found)
    return found


def build_intents():
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    return intents


def note(text):
    return discord.Embed(description=text, color=DARK)


class HappyTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction):
        if await is_blacklisted(interaction.user.id, interaction.guild_id):
            await interaction.response.send_message(embed=note("You cannot use Happy right now."), ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        print(f"[App command] {error}")
        text = "Something went wrong. Please try again in a moment."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=note(text), ephemeral=True)
            else:
                await interaction.response.send_message(embed=note(text), ephemeral=True)
        except discord.HTTPException:
            pass


class HappyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=get_prefix,
            intents=build_intents(),
            help_command=None,
            tree_cls=HappyTree,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=True, users=True, replied_user=False),
        )
        self.ai_sessions = {}
        self.seen_messages = OrderedDict()
        self.ready_once = False

    async def setup_hook(self):
        await init_indexes()
        loaded = 0
        for cog in COGS:
            try:
                await self.load_extension(cog)
                loaded += 1
                print(f"  [OK]   {cog}")
            except Exception as error:
                print(f"  [FAIL] {cog}: {error}")
        print(f"\n  Loaded {loaded} of {len(COGS)} cogs\n")

        if os.getenv("SYNC_COMMANDS") == "1":
            try:
                synced = await self.tree.sync()
                print(f"  Synced {len(synced)} slash commands\n")
            except discord.HTTPException as error:
                print(f"  Slash command sync failed: {error}\n")

    async def on_ready(self):
        if self.ready_once:
            return
        self.ready_once = True
        print(f"  Happy is online as {self.user} in {len(self.guilds)} servers\n")

    async def process_commands(self, message):
        if message.id in self.seen_messages:
            return
        self.seen_messages[message.id] = None
        while len(self.seen_messages) > 500:
            self.seen_messages.popitem(last=False)
        await super().process_commands(message)

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        ctx = await self.get_context(message)
        if ctx.valid:
            await self.process_commands(message)
            return
        if not message.guild:
            return
        if await is_blacklisted(message.author.id, message.guild.id):
            return

        if len(self.ai_sessions) > 2000:
            cutoff = time.time() - AI_SESSION_SECONDS
            self.ai_sessions = {k: v for k, v in self.ai_sessions.items() if v > cutoff}

        ai = self.get_cog("AIChat")
        if ai:
            await ai.handle_message(message, self.ai_sessions, AI_SESSION_SECONDS)
        calls = self.get_cog("Premium")
        if calls:
            await calls.relay_call(message)

    async def on_command_error(self, ctx, error):
        error = getattr(error, "original", error)
        if isinstance(error, (BlacklistedError, commands.CommandNotFound)):
            return
        if self.extra_events.get("on_command_error"):
            return

        if isinstance(error, commands.MissingPermissions):
            names = ", ".join(p.replace("_", " ").title() for p in error.missing_permissions)
            text = f"You need the **{names}** permission to use this."
        elif isinstance(error, commands.BotMissingPermissions):
            names = ", ".join(p.replace("_", " ").title() for p in error.missing_permissions)
            text = f"I need the **{names}** permission to do that."
        elif isinstance(error, commands.NoPrivateMessage):
            text = "Please use this command inside a server."
        elif isinstance(error, commands.CommandOnCooldown):
            text = f"Please wait **{error.retry_after:.0f} seconds** and try again."
        elif isinstance(error, commands.MissingRequiredArgument):
            text = f"You forgot `{error.param.name}`. Type `{ctx.clean_prefix}help` to see how to use this."
        elif isinstance(error, (commands.BadArgument, commands.ArgumentParsingError, commands.BadUnionArgument)):
            text = "I could not understand that. Please check the command and try again."
        elif isinstance(error, commands.CheckFailure):
            return
        else:
            print(f"[Command error] {ctx.command}: {error!r}")
            text = "Something went wrong. Please try again in a moment."

        try:
            await ctx.reply(embed=note(text), mention_author=False)
        except discord.HTTPException:
            pass


bot = HappyBot()


@bot.check
async def block_blacklisted(ctx):
    if await is_blacklisted(ctx.author.id, ctx.guild.id if ctx.guild else None):
        raise BlacklistedError()
    return True


async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is missing. Please add it to your .env file.")
    discord.utils.setup_logging(level=logging.INFO)
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    Thread(target=run_web, daemon=True).start()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass