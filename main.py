import discord
from discord.ext import commands
from flask import Flask, render_template, redirect
from threading import Thread
import os, asyncio
from dotenv import load_dotenv
from utils.helpers import get_prefix, BOT_OWNER_ID, BlacklistedError
from utils.db import blacklist_col

load_dotenv()

app = Flask('')

@app.route('/')
def home():
    return render_template('happy_bot_dashboard.html')

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

intents = discord.Intents.all()

bot = commands.Bot(
    command_prefix=get_prefix,
    intents=intents,
    help_command=None,
)

@bot.event
async def on_message(message):
    pass

@bot.check
async def globally_block_blacklisted(ctx):
    if ctx.author.id == BOT_OWNER_ID:
        return True
    if await blacklist_col.find_one({"id": str(ctx.author.id)}):
        raise BlacklistedError()
    if ctx.guild and await blacklist_col.find_one({"id": str(ctx.guild.id)}):
        raise BlacklistedError()
    return True

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
    "cogs.levelroles",
    "cogs.tracker",
    "cogs.extraperm",
    "cogs.aliases",
    "cogs.antinuke",
    "cogs.emotes",
    "cogs.hc_features",
    "cogs.owner",

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