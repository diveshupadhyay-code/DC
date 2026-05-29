"""
Happy Bot — Premium Edition
Entry point: loads all cogs and starts the bot.
"""

import discord
from discord.ext import commands
from flask import Flask
from threading import Thread
import os, asyncio
from dotenv import load_dotenv
from utils.db import settings_col
from utils.helpers import get_prefix

load_dotenv()

# ── Flask keep-alive (required for Render free tier) ─────────────────────────
app = Flask('')

@app.route('/')
def home():
    return "Happy is Online!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.all()

bot = commands.Bot(
    command_prefix=get_prefix,
    intents=intents,
    help_command=None,
)

# ── Suppress Bot's default on_message ────────────────────────────────────────
# discord.py's Bot.on_message calls process_commands automatically.
# Our Core cog's on_message ALSO calls process_commands at the end.
# Without this override, both fire → every command runs TWICE.
# This @bot.event replaces Bot.on_message with a no-op so that
# Core cog's on_message is the single place process_commands is called.
@bot.event
async def on_message(message):
    pass  # Core cog handles all message logic including process_commands

# ── Cog list ──────────────────────────────────────────────────────────────────
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
    "cogs.aesthetic",
    "cogs.games",
    "cogs.economy",
    "cogs.invest",
]

async def main():
    async with bot:
        for cog in COGS:
            try:
                await bot.load_extension(cog)
                print(f"  [OK] {cog}")
            except Exception as e:
                print(f"  [FAIL] {cog}: {e}")
        await bot.start(os.getenv("DISCORD_TOKEN"))

if __name__ == "__main__":
    Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())