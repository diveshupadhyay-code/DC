import discord
from discord.ext import commands
from flask import Flask
from threading import Thread
import os, asyncio
from dotenv import load_dotenv
from utils.helpers import get_prefix

load_dotenv()

app = Flask('')

@app.route('/')
def home():
    return "Happy is Online!"

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
    "cogs.levelroles",
    "cogs.tracker",
    "cogs.extraperm",
    "cogs.aliases",
    "cogs.antinuke",
    "cogs.emotes",

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