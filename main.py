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

REQUIRED_PERMISSIONS = discord.Permissions(
    send_messages=True,
    read_messages=True,
    read_message_history=True,
    embed_links=True,
    attach_files=True,
    add_reactions=True,
    manage_messages=True,
    manage_roles=True,
    manage_channels=True,
    kick_members=True,
    ban_members=True,
    moderate_members=True,
    manage_nicknames=True,
    create_instant_invite=True,
    view_audit_log=True,
    manage_webhooks=True,
    mention_everyone=True,
    use_external_emojis=True,
    connect=True,
    speak=True,
    move_members=True,
    use_voice_activation=True,
)

bot = commands.Bot(
    command_prefix=get_prefix,
    intents=intents,
    help_command=None,
)

# ── Suppress Bot's default on_message ────────────────────────────────────────
@bot.event
async def on_guild_join(guild: discord.Guild):
    me = guild.me
    missing = []
    needed = {
        "Send Messages":        me.guild_permissions.send_messages,
        "Read Messages":        me.guild_permissions.read_messages,
        "Read Message History": me.guild_permissions.read_message_history,
        "Embed Links":          me.guild_permissions.embed_links,
        "Attach Files":         me.guild_permissions.attach_files,
        "Add Reactions":        me.guild_permissions.add_reactions,
        "Manage Messages":      me.guild_permissions.manage_messages,
        "Manage Roles":         me.guild_permissions.manage_roles,
        "Manage Channels":      me.guild_permissions.manage_channels,
        "Kick Members":         me.guild_permissions.kick_members,
        "Ban Members":          me.guild_permissions.ban_members,
        "Timeout Members":      me.guild_permissions.moderate_members,
        "Manage Nicknames":     me.guild_permissions.manage_nicknames,
        "Create Invites":       me.guild_permissions.create_instant_invite,
        "View Audit Log":       me.guild_permissions.view_audit_log,
        "Manage Webhooks":      me.guild_permissions.manage_webhooks,
        "Mention Everyone":     me.guild_permissions.mention_everyone,
        "External Emojis":      me.guild_permissions.use_external_emojis,
        "Connect Voice":        me.guild_permissions.connect,
        "Speak":                me.guild_permissions.speak,
        "Move Members":         me.guild_permissions.move_members,
    }
    missing = [name for name, has in needed.items() if not has]
    ch = guild.system_channel or next(
        (c for c in guild.text_channels if c.permissions_for(me).send_messages), None
    )
    if not ch:
        return
    if missing:
        missing_str = "\n".join(f"— {m}" for m in missing)
        embed = discord.Embed(
            title="Missing Permissions",
            description=(
                f"Thanks for adding **Happy**!\n\n"
                f"I am missing these permissions which some features need:\n"
                f"{missing_str}\n\n"
                f"Please go to **Server Settings → Roles → Happy** and enable them."
            ),
            color=0xED4245
        )
        embed.set_footer(text="Without these, some commands will not work correctly.")
        await ch.send(embed=embed)
    else:
        embed = discord.Embed(
            title="Happy is ready!",
            description=(
                f"Thanks for adding **Happy** to **{guild.name}**!\n\nUse `,help` to see all commands.\nPrefix: `,` — change with `,prefix set <symbol>`"
            ),
            color=0x57F287
        )
        await ch.send(embed=embed)

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
    "cogs.tracker",      # ← NEW: invite tracker + message counter
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