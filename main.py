import discord
from discord.ext import commands, tasks
from discord import app_commands
from flask import Flask
from threading import Thread
import os, json, asyncio, random, time, re, urllib.parse
from dotenv import load_dotenv
import pytz
import datetime
from datetime import datetime, timedelta, timezone
from discord.ui import Button, View
from groq import Groq
from motor.motor_asyncio import AsyncIOMotorClient
import aiohttp

load_dotenv()

# ============================================================
#                        FLASK KEEP-ALIVE
# ============================================================
app = Flask('')

@app.route('/')
def home():
    return "Happy is Online!"

def run():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# ============================================================
#                        CONFIG & CONSTANTS
# ============================================================
TOKEN          = os.getenv('DISCORD_TOKEN')
GROQ_API_KEY   = os.getenv('GROQ_API_KEY')
MONGO_URL      = os.getenv('MONGO_URL')
BOT_OWNER_ID   = 876629015144828939   # your Discord ID

if not MONGO_URL:
    raise ValueError("MONGO_URL not found. Check .env or environment variables.")

groq_client = Groq(api_key=GROQ_API_KEY)

SESSION_TIMEOUT  = 300   # 5 minutes
PREMIUM_PRICE    = "Contact the bot owner to activate."

# ============================================================
#                        MONGODB SETUP
# ============================================================
cluster = AsyncIOMotorClient(MONGO_URL)
db      = cluster["HappyBotDB"]

settings_col          = db["server_settings"]
warns_col             = db["warnings"]
afk_col               = db["afk_users"]
sticky_col            = db["sticky_messages"]
reaction_roles_col    = db["reaction_roles"]
tickets_col           = db["tickets"]
disabled_commands_col = db["disabled_commands"]
profiles_col          = db["user_profiles"]
embed_col             = db["embed_drafts"]
premium_col           = db["premium"]          # premium users & servers
levels_col            = db["levels"]
birthdays_col         = db["birthdays"]
server_status_col     = db["server_status"]    # per-server bot status
jail_col              = db["jail"]
counters_col          = db["counters"]
logs_col              = db["logging_config"]
voicemaster_col       = db["voicemaster"]
bump_col              = db["bump_reminder"]
personal_prefix_col   = db["personal_prefix"]
booster_roles_col     = db["booster_roles"]

# ============================================================
#                    IN-MEMORY STATE
# ============================================================
user_memories    = {}
active_calls     = {}     # {server_id: {partner_channel, my_channel}}
waiting_list     = []     # [{server_id, channel_id, is_premium}]
active_sessions  = {}     # {channel_id: last_active_timestamp}
sticky_counter   = {}
ai_enabled       = True
maintenance_mode = False

# ============================================================
#                      DB HELPER FUNCTIONS
# ============================================================
async def get_server_data(server_id):
    data = await settings_col.find_one({"_id": str(server_id)})
    return data if data else {}

async def update_server_data(server_id, key, value):
    await settings_col.update_one(
        {"_id": str(server_id)},
        {"$set": {key: value}},
        upsert=True
    )

async def is_premium_server(guild_id: int) -> bool:
    doc = await premium_col.find_one({"type": "server", "id": str(guild_id)})
    return doc is not None

async def is_premium_user(user_id: int) -> bool:
    doc = await premium_col.find_one({"type": "user", "id": str(user_id)})
    return doc is not None

async def get_prefix_for_message(bot, message):
    """Determine prefix: personal > server > default ','"""
    # Personal prefix (premium users only)
    if message.author:
        personal = await personal_prefix_col.find_one({"user_id": str(message.author.id)})
        if personal and personal.get("prefix"):
            return commands.when_mentioned_or(personal["prefix"])(bot, message)

    if not message.guild:
        return commands.when_mentioned_or(",")( bot, message)

    data = await settings_col.find_one({"_id": str(message.guild.id)})
    if data and "prefix" in data:
        return commands.when_mentioned_or(data["prefix"])(bot, message)
    return commands.when_mentioned_or(",")(bot, message)

def get_color(color_str):
    try:
        return discord.Color.from_str(color_str)
    except:
        return discord.Color.blurple()

# ============================================================
#                     PERMISSION CHECKS
# ============================================================
def is_owner():
    async def predicate(interaction: discord.Interaction):
        return interaction.user.id == BOT_OWNER_ID
    return app_commands.check(predicate)

def is_admin_or_owner():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.id == BOT_OWNER_ID:
            return True
        if interaction.guild and interaction.user.guild_permissions.administrator:
            return True
        raise app_commands.AppCommandError("You need Administrator permission to use this command.")
    return app_commands.check(predicate)

def is_mod_or_owner():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.id == BOT_OWNER_ID:
            return True
        if interaction.guild:
            perms = interaction.user.guild_permissions
            if perms.manage_messages or perms.kick_members or perms.administrator:
                return True
        raise app_commands.AppCommandError("You need Moderator permission to use this command.")
    return app_commands.check(predicate)

def ctx_is_owner():
    async def predicate(ctx):
        return ctx.author.id == BOT_OWNER_ID
    return commands.check(predicate)

def ctx_mod_or_owner():
    async def predicate(ctx):
        if ctx.author.id == BOT_OWNER_ID:
            return True
        perms = ctx.author.guild_permissions
        return perms.manage_messages or perms.kick_members or perms.administrator
    return commands.check(predicate)

def premium_required():
    """Slash command check: user or server must be premium."""
    async def predicate(interaction: discord.Interaction):
        if interaction.user.id == BOT_OWNER_ID:
            return True
        if await is_premium_user(interaction.user.id):
            return True
        if interaction.guild and await is_premium_server(interaction.guild.id):
            return True
        raise app_commands.AppCommandError("This feature requires **Happy Premium**. Ask the bot owner to activate it.")
    return app_commands.check(predicate)

def ctx_premium_required():
    async def predicate(ctx):
        if ctx.author.id == BOT_OWNER_ID:
            return True
        if await is_premium_user(ctx.author.id):
            return True
        if ctx.guild and await is_premium_server(ctx.guild.id):
            return True
        raise commands.CheckFailure("This feature requires **Happy Premium**. Ask the bot owner to activate it.")
    return commands.check(predicate)

# ============================================================
#                        BOT INIT
# ============================================================
intents = discord.Intents.all()
bot = commands.Bot(
    command_prefix=get_prefix_for_message,
    intents=intents,
    help_command=None   # we have custom help
)

# ============================================================
#                        STATUS LOOP
# ============================================================
@tasks.loop(seconds=20)
async def change_status():
    await bot.wait_until_ready()

    # Check for any server-specific overrides (use random one if multiple)
    all_custom = await server_status_col.find({}).to_list(length=50)
    if all_custom:
        chosen = random.choice(all_custom)
        status_text = chosen.get("status", f"Watching {len(bot.guilds)} servers")
        await bot.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name=status_text)
        )
        return

    defaults = [
        f"over {len(bot.guilds)} servers",
        f"{len(bot.users)} members",
        "Type /help",
        "Happy Premium — now live",
    ]
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name=random.choice(defaults))
    )

# ============================================================
#                       ON_READY
# ============================================================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    bot.add_view(TicketCreateView())
    bot.add_view(ButtonRolesView())
    if not change_status.is_running():
        change_status.start()
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Sync error: {e}")

# ============================================================
#                   GLOBAL ERROR HANDLER (SLASH)
# ============================================================
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    msg = str(error)
    if isinstance(error, app_commands.MissingPermissions):
        msg = "You don't have permission to use this command."
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(f"**Error:** {msg}", ephemeral=True)
        else:
            await interaction.followup.send(f"**Error:** {msg}", ephemeral=True)
    except Exception:
        pass
    if "permission" not in msg.lower() and "premium" not in msg.lower():
        print(f"[SlashError] {error}")

# ============================================================
#                   GLOBAL ERROR HANDLER (PREFIX)
# ============================================================
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, (commands.MissingPermissions, commands.CheckFailure)):
        embed = discord.Embed(description=f"**Access Denied:** {str(error)}", color=0xff0000)
        return await ctx.reply(embed=embed, delete_after=8)
    if isinstance(error, commands.MissingRequiredArgument):
        embed = discord.Embed(
            description=f"Missing required argument. Use `,help {ctx.command.name}` for details.",
            color=0xff0000
        )
        return await ctx.reply(embed=embed, delete_after=8)
    if isinstance(error, commands.BadArgument):
        embed = discord.Embed(description=f"Invalid argument: {str(error)}", color=0xff0000)
        return await ctx.reply(embed=embed, delete_after=8)
    print(f"[PrefixError] {ctx.command}: {error}")

# ============================================================
#
#                  ███████╗ ██████╗ ██████╗ ███████╗
#                  ██╔════╝██╔═══██╗██╔══██╗██╔════╝
#                  ██║     ██║   ██║██████╔╝█████╗
#                  ██║     ██║   ██║██╔══██╗██╔══╝
#                  ╚██████╗╚██████╔╝██║  ██║███████╗
#                   ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚══════╝
#
# ============================================================

# -------  PREMIUM MANAGEMENT (owner only)  -------
@bot.command(name="premium")
@ctx_is_owner()
async def premium_cmd(ctx, action: str = None, type_: str = None, target: str = None):
    """
    ,premium add server <guild_id>
    ,premium add user <user_id>
    ,premium remove server <guild_id>
    ,premium remove user <user_id>
    ,premium list
    """
    if action == "list":
        cursor = premium_col.find({})
        items = await cursor.to_list(length=100)
        if not items:
            return await ctx.reply("No premium entries found.")
        lines = [f"`{i['type']}` — `{i['id']}`" for i in items]
        embed = discord.Embed(title="Premium Entries", description="\n".join(lines), color=0xffd700)
        return await ctx.reply(embed=embed)

    if not action or not type_ or not target:
        return await ctx.reply("Usage: `,premium add/remove server/user <id>`\n`,premium list`")

    if type_ not in ("server", "user"):
        return await ctx.reply("Type must be `server` or `user`.")

    if action == "add":
        await premium_col.update_one(
            {"type": type_, "id": target},
            {"$set": {"type": type_, "id": target}},
            upsert=True
        )
        await ctx.reply(f"Premium activated for {type_} `{target}`.")
    elif action == "remove":
        await premium_col.delete_one({"type": type_, "id": target})
        await ctx.reply(f"Premium removed from {type_} `{target}`.")
    else:
        await ctx.reply("Action must be `add` or `remove`.")

# ============================================================
#
#           ██████╗ ██████╗ ███████╗███████╗██╗██╗  ██╗
#          ██╔══██╗██╔══██╗██╔════╝██╔════╝██║╚██╗██╔╝
#          ██████╔╝██████╔╝█████╗  █████╗  ██║ ╚███╔╝
#          ██╔═══╝ ██╔══██╗██╔══╝  ██╔══╝  ██║ ██╔██╗
#          ██║     ██║  ██║███████╗██║      ██║██╔╝ ██╗
#          ╚═╝     ╚═╝  ╚═╝╚══════╝╚═╝      ╚═╝╚═╝  ╚═╝
#
# ============================================================

@bot.group(name="prefix", invoke_without_command=True)
async def prefix_group(ctx):
    data = await settings_col.find_one({"_id": str(ctx.guild.id)})
    current = data.get("prefix", ",") if data else ","
    personal = await personal_prefix_col.find_one({"user_id": str(ctx.author.id)})
    personal_p = personal.get("prefix") if personal else None

    embed = discord.Embed(title="Prefix Settings", color=0x2B2D31)
    embed.add_field(name="Server Prefix", value=f"`{current}`", inline=True)
    embed.add_field(name="Your Personal Prefix", value=f"`{personal_p}`" if personal_p else "Not set", inline=True)
    embed.add_field(
        name="Commands",
        value=(
            f"`,prefix set <symbol>` — Set server prefix\n"
            f"`,prefix remove` — Reset server prefix to `,`\n"
            f"`,prefix self <symbol>` — Set personal prefix (Premium)\n"
            f"`,prefix selfremove` — Remove personal prefix"
        ),
        inline=False
    )
    await ctx.reply(embed=embed)

@prefix_group.command(name="set")
@commands.has_permissions(administrator=True)
async def prefix_set(ctx, new_prefix: str):
    if len(new_prefix) > 3:
        return await ctx.reply("Prefix must be 3 characters or fewer.")
    await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$set": {"prefix": new_prefix}}, upsert=True)
    await ctx.reply(f"Server prefix updated to `{new_prefix}`.")

@prefix_group.command(name="remove")
@commands.has_permissions(administrator=True)
async def prefix_remove(ctx):
    await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$unset": {"prefix": ""}}, upsert=True)
    await ctx.reply("Server prefix reset to default `,`.")

@prefix_group.command(name="self")
@ctx_premium_required()
async def prefix_self(ctx, new_prefix: str):
    if len(new_prefix) > 3:
        return await ctx.reply("Personal prefix must be 3 characters or fewer.")
    await personal_prefix_col.update_one(
        {"user_id": str(ctx.author.id)},
        {"$set": {"prefix": new_prefix}},
        upsert=True
    )
    await ctx.reply(f"Your personal prefix is now `{new_prefix}` across all servers.")

@prefix_group.command(name="selfremove")
async def prefix_selfremove(ctx):
    await personal_prefix_col.delete_one({"user_id": str(ctx.author.id)})
    await ctx.reply("Personal prefix removed.")

# ============================================================
#                     SETTINGS DASHBOARD
# ============================================================
@bot.command(name="settings", aliases=["config", "panel"])
@commands.has_permissions(administrator=True)
async def server_settings(ctx):
    gid = str(ctx.guild.id)
    gs = await settings_col.find_one({"_id": gid}) or {}
    prefix_val      = gs.get("prefix", ",")
    invite_block    = "Enabled" if gs.get("invite_block") else "Disabled"
    welcome_enabled = "Enabled" if gs.get("welcome_enabled") else "Disabled"
    bye_enabled     = "Enabled" if gs.get("bye_enabled") else "Disabled"
    is_prem         = await is_premium_server(ctx.guild.id)
    logging_cfg     = await logs_col.find_one({"guild_id": gid}) or {}
    log_channel     = f"<#{logging_cfg['channel_id']}>" if logging_cfg.get("channel_id") else "Not set"
    bump_cfg        = await bump_col.find_one({"guild_id": gid})
    bump_status     = "Enabled" if bump_cfg and bump_cfg.get("enabled") else "Disabled"

    embed = discord.Embed(
        title=f"Configuration — {ctx.guild.name}",
        color=0x2B2D31
    )
    embed.add_field(name="Prefix",         value=f"`{prefix_val}`",    inline=True)
    embed.add_field(name="Premium",        value="Yes" if is_prem else "No", inline=True)
    embed.add_field(name="Anti-Invite",    value=invite_block,         inline=True)
    embed.add_field(name="Welcome Msg",    value=welcome_enabled,      inline=True)
    embed.add_field(name="Bye Msg",        value=bye_enabled,          inline=True)
    embed.add_field(name="Log Channel",    value=log_channel,          inline=True)
    embed.add_field(name="Bump Reminder",  value=bump_status,          inline=True)
    if ctx.guild.icon:
        embed.set_thumbnail(url=ctx.guild.icon.url)
    embed.set_footer(text=f"Server ID: {gid}")
    await ctx.reply(embed=embed)

@bot.command(name="premiumrole")
@commands.has_permissions(administrator=True)
async def set_premium_role(ctx, role: discord.Role = None):
    if not role:
        return await ctx.reply("Mention a role: `,premiumrole @PremiumMembers`")
    await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$set": {"premium_role_id": role.id}}, upsert=True)
    await ctx.reply(f"Premium Members role set to {role.mention}.")

# ============================================================
#
#       ███╗   ███╗ ██████╗ ██████╗
#       ████╗ ████║██╔═══██╗██╔══██╗
#       ██╔████╔██║██║   ██║██║  ██║
#       ██║╚██╔╝██║██║   ██║██║  ██║
#       ██║ ╚═╝ ██║╚██████╔╝██████╔╝
#       ╚═╝     ╚═╝ ╚═════╝ ╚═════╝
#
# ============================================================

@bot.command(name="kick")
@ctx_mod_or_owner()
async def kick(ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
    if not member:
        return await ctx.reply("Mention a member to kick.")
    if ctx.author.id != BOT_OWNER_ID and ctx.author.top_role <= member.top_role:
        return await ctx.reply("You cannot kick someone with an equal or higher role.")
    await member.kick(reason=reason)
    embed = discord.Embed(description=f"**{member}** has been kicked. Reason: {reason}", color=0x2B2D31)
    await ctx.reply(embed=embed)

@bot.command(name="ban")
@ctx_mod_or_owner()
async def ban(ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
    if not member:
        return await ctx.reply("Mention a member to ban.")
    if ctx.author.id != BOT_OWNER_ID and ctx.author.top_role <= member.top_role:
        return await ctx.reply("You cannot ban someone with an equal or higher role.")
    await member.ban(reason=reason)
    embed = discord.Embed(description=f"**{member}** has been banned. Reason: {reason}", color=0x2B2D31)
    await ctx.reply(embed=embed)

@bot.command(name="unban")
@ctx_mod_or_owner()
async def unban(ctx, user_id: int):
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user)
        await ctx.reply(f"Unbanned **{user}**.")
    except discord.NotFound:
        await ctx.reply("User not found or not banned.")

@bot.command(name="mute")
@ctx_mod_or_owner()
async def mute(ctx, member: discord.Member = None, minutes: int = 10, *, reason: str = "No reason provided"):
    if not member:
        return await ctx.reply("Mention a member to mute.")
    if ctx.author.id != BOT_OWNER_ID and ctx.author.top_role <= member.top_role:
        return await ctx.reply("You cannot mute someone with an equal or higher role.")
    duration = timedelta(minutes=minutes)
    await member.timeout(duration, reason=reason)
    embed = discord.Embed(description=f"**{member}** muted for {minutes} min. Reason: {reason}", color=0x2B2D31)
    await ctx.reply(embed=embed)

@bot.command(name="unmute")
@ctx_mod_or_owner()
async def unmute(ctx, member: discord.Member = None):
    if not member:
        return await ctx.reply("Mention a member to unmute.")
    await member.timeout(None)
    await ctx.reply(f"Timeout removed for **{member}**.")

@bot.command(name="warn")
@ctx_mod_or_owner()
async def warn(ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
    if not member:
        return await ctx.reply("Mention a member to warn.")
    sid = str(ctx.guild.id)
    uid = str(member.id)
    doc = await warns_col.find_one({"server_id": sid, "user_id": uid})
    if doc:
        count = doc["count"] + 1
        await warns_col.update_one({"_id": doc["_id"]}, {"$set": {"count": count}})
    else:
        count = 1
        await warns_col.insert_one({"server_id": sid, "user_id": uid, "count": 1})

    embed = discord.Embed(title="Warning Issued", color=0xff0000, timestamp=datetime.now())
    embed.add_field(name="Member",    value=member.mention, inline=True)
    embed.add_field(name="By",        value=ctx.author.mention, inline=True)
    embed.add_field(name="Warnings",  value=f"**{count}**", inline=True)
    embed.add_field(name="Reason",    value=reason, inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.reply(embed=embed)
    try:
        await member.send(embed=discord.Embed(
            description=f"You received warning #{count} in **{ctx.guild.name}**.\nReason: {reason}",
            color=0xff0000
        ))
    except:
        pass

@bot.command(name="warnings", aliases=["warnlist"])
@ctx_mod_or_owner()
async def warnings_list(ctx, member: discord.Member = None):
    member = member or ctx.author
    doc = await warns_col.find_one({"server_id": str(ctx.guild.id), "user_id": str(member.id)})
    count = doc["count"] if doc else 0
    embed = discord.Embed(
        description=f"**{member}** has **{count}** warning(s).",
        color=0x2B2D31
    )
    await ctx.reply(embed=embed)

@bot.command(name="clearwarns", aliases=["cw"])
@ctx_mod_or_owner()
async def clearwarns(ctx, member: discord.Member = None):
    if not member:
        return await ctx.reply("Mention a member.")
    await warns_col.delete_one({"server_id": str(ctx.guild.id), "user_id": str(member.id)})
    await ctx.reply(f"Warnings cleared for **{member}**.")

@bot.command(name="softban", aliases=["sb"])
@ctx_mod_or_owner()
async def softban(ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
    if not member:
        return await ctx.reply("Mention a member to softban.")
    if ctx.author.id != BOT_OWNER_ID and ctx.author.top_role <= member.top_role:
        return await ctx.reply("Cannot softban someone with equal or higher role.")
    try:
        await ctx.guild.ban(member, reason=f"Softban: {reason}", delete_message_days=7)
        await ctx.guild.unban(member)
        embed = discord.Embed(
            description=f"**{member}** has been softbanned. Messages cleared. Reason: {reason}",
            color=0x2B2D31
        )
        await ctx.reply(embed=embed)
    except discord.Forbidden:
        await ctx.reply("Missing permissions to ban/unban.")

@bot.command(name="nickname", aliases=["nick"])
@ctx_mod_or_owner()
async def nickname(ctx, member: discord.Member = None, *, new_name: str = None):
    if not member:
        return await ctx.reply("Mention a member.")
    if ctx.author.id != BOT_OWNER_ID and ctx.author.top_role <= member.top_role:
        return await ctx.reply("Cannot change nickname of someone with equal or higher role.")
    if member.id == ctx.guild.owner_id:
        return await ctx.reply("Cannot change the server owner's nickname.")
    await member.edit(nick=new_name or None)
    msg = f"Nickname of {member.mention} set to `{new_name}`." if new_name else f"Nickname of {member.mention} reset."
    await ctx.reply(msg)

# ============================================================
#                     LOCK / UNLOCK  (enhanced)
# ============================================================
async def _set_channel_lock(channel, lock: bool, reason: str):
    """Lock or unlock any text/thread/forum/voice channel."""
    overwrite = channel.overwrites_for(channel.guild.default_role)

    if isinstance(channel, discord.VoiceChannel):
        overwrite.connect = False if lock else None
        overwrite.speak   = False if lock else None
    else:
        overwrite.send_messages        = False if lock else None
        overwrite.send_messages_in_threads = False if lock else None
        overwrite.create_public_threads    = False if lock else None
        overwrite.add_reactions            = False if lock else None

    await channel.set_permissions(channel.guild.default_role, overwrite=overwrite, reason=reason)

@bot.command(name="lock")
@ctx_mod_or_owner()
async def lock_channel(ctx, channel: discord.abc.GuildChannel = None, *, reason: str = "No reason provided"):
    channel = channel or ctx.channel
    await _set_channel_lock(channel, True, f"Locked by {ctx.author}: {reason}")
    embed = discord.Embed(description=f"**{channel.mention} locked.** Reason: {reason}", color=0x2B2D31)
    await ctx.reply(embed=embed)

@bot.command(name="unlock")
@ctx_mod_or_owner()
async def unlock_channel(ctx, channel: discord.abc.GuildChannel = None, *, reason: str = "No reason provided"):
    channel = channel or ctx.channel
    await _set_channel_lock(channel, False, f"Unlocked by {ctx.author}: {reason}")
    embed = discord.Embed(description=f"**{channel.mention} unlocked.**", color=0x2B2D31)
    await ctx.reply(embed=embed)

@bot.command(name="lockdown")
@commands.has_permissions(administrator=True)
async def lockdown(ctx, *, reason: str = "Emergency lockdown"):
    msg = await ctx.reply("Initiating server lockdown...")
    locked = 0
    for ch in ctx.guild.channels:
        if isinstance(ch, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel)):
            try:
                await _set_channel_lock(ch, True, reason)
                locked += 1
            except:
                pass
    embed = discord.Embed(
        title="Server Lockdown Active",
        description=f"**{locked}** channels locked.\nReason: {reason}\n\nUse `,unlockdown` to lift.",
        color=0xff0000
    )
    await msg.edit(content=None, embed=embed)
    await _log_event(ctx.guild, "lockdown", f"Server locked by {ctx.author}. Reason: {reason}")

@bot.command(name="unlockdown")
@commands.has_permissions(administrator=True)
async def unlockdown(ctx):
    msg = await ctx.reply("Lifting lockdown...")
    unlocked = 0
    for ch in ctx.guild.channels:
        if isinstance(ch, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel)):
            try:
                await _set_channel_lock(ch, False, "Lockdown lifted")
                unlocked += 1
            except:
                pass
    await msg.edit(content=None, embed=discord.Embed(
        description=f"Lockdown lifted. {unlocked} channels unlocked.",
        color=0x2B2D31
    ))

# ============================================================
#                     PURGE (enhanced)
# ============================================================
@bot.command(name="purge", aliases=["clear", "c"])
@ctx_mod_or_owner()
async def purge_messages(ctx, target: str = None, limit: int = None):
    if not target:
        embed = discord.Embed(title="Purge", color=0x2B2D31, description=(
            "`,purge <amount>` — delete N recent messages\n"
            "`,purge bots <amount>` — bot messages only\n"
            "`,purge @user <amount>` — specific user\n"
            "`,purge links <amount>` — messages with links"
        ))
        return await ctx.reply(embed=embed)

    try:
        await ctx.message.delete()
    except:
        pass

    deleted = 0

    if target.lower() == "bots":
        deleted_msgs = await ctx.channel.purge(limit=limit or 50, check=lambda m: m.author.bot)
        deleted = len(deleted_msgs)
    elif target.lower() == "links":
        deleted_msgs = await ctx.channel.purge(limit=limit or 50, check=lambda m: "http" in m.content)
        deleted = len(deleted_msgs)
    elif target.startswith("<@"):
        uid = int(re.sub(r"[<@!>]", "", target))
        member = ctx.guild.get_member(uid)
        if not member:
            return await ctx.send("Member not found.", delete_after=5)
        deleted_msgs = await ctx.channel.purge(limit=limit or 50, check=lambda m: m.author.id == uid)
        deleted = len(deleted_msgs)
    else:
        try:
            amount = int(target)
        except ValueError:
            return await ctx.send("Invalid format. Use `,purge` to see options.", delete_after=5)
        deleted_msgs = await ctx.channel.purge(limit=amount)
        deleted = len(deleted_msgs)

    await ctx.send(
        embed=discord.Embed(description=f"Purged {deleted} message(s).", color=0x2B2D31),
        delete_after=4
    )

# ============================================================
#                    MASS ROLE COMMAND
# ============================================================
@bot.command(name="massrole")
@commands.has_permissions(administrator=True)
async def massrole(ctx, action: str = None, target: str = None, role: discord.Role = None):
    """
    ,massrole add @everyone @Role
    ,massrole add bots @Role
    ,massrole remove @everyone @Role
    """
    if not action or not target or not role:
        return await ctx.reply("Usage: `,massrole add/remove @everyone/@bots @Role`")

    msg = await ctx.reply("Processing... this may take a while.")
    count = 0
    is_bots = target.lower() == "bots"
    members = [m for m in ctx.guild.members if m.bot == is_bots] if is_bots else ctx.guild.members

    for member in members:
        try:
            if action.lower() == "add" and role not in member.roles:
                await member.add_roles(role)
                count += 1
            elif action.lower() == "remove" and role in member.roles:
                await member.remove_roles(role)
                count += 1
            await asyncio.sleep(0.4)   # rate limit safety
        except:
            pass

    await msg.edit(content=None, embed=discord.Embed(
        description=f"{action.capitalize()}d **{role.name}** for {count} member(s).",
        color=0x2B2D31
    ))

# ============================================================
#                         JAIL SYSTEM
# ============================================================
@bot.command(name="jailsetup")
@commands.has_permissions(administrator=True)
async def jail_setup(ctx):
    """Creates a #jail channel and Jailed role."""
    # Create role
    jailed_role = discord.utils.get(ctx.guild.roles, name="Jailed")
    if not jailed_role:
        jailed_role = await ctx.guild.create_role(name="Jailed", color=discord.Color.dark_gray())

    # Lock every channel for Jailed role
    for ch in ctx.guild.channels:
        try:
            await ch.set_permissions(jailed_role, view_channel=False, send_messages=False)
        except:
            pass

    # Create jail channel visible only to jailed
    jail_channel = discord.utils.get(ctx.guild.channels, name="jail")
    if not jail_channel:
        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            jailed_role: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            ctx.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        jail_channel = await ctx.guild.create_text_channel("jail", overwrites=overwrites)

    await settings_col.update_one(
        {"_id": str(ctx.guild.id)},
        {"$set": {"jail_role_id": jailed_role.id, "jail_channel_id": jail_channel.id}},
        upsert=True
    )
    await ctx.reply(f"Jail system ready. Role: {jailed_role.mention} | Channel: {jail_channel.mention}")

@bot.command(name="jail")
@ctx_mod_or_owner()
async def jail_member(ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
    if not member:
        return await ctx.reply("Mention a member to jail.")
    gs = await settings_col.find_one({"_id": str(ctx.guild.id)})
    if not gs or not gs.get("jail_role_id"):
        return await ctx.reply("Jail system not configured. Run `,jailsetup` first.")
    jailed_role = ctx.guild.get_role(gs["jail_role_id"])
    if not jailed_role:
        return await ctx.reply("Jailed role not found. Run `,jailsetup` again.")

    # Save roles and assign jailed
    old_roles = [r.id for r in member.roles if r != ctx.guild.default_role]
    await jail_col.update_one(
        {"guild_id": str(ctx.guild.id), "user_id": str(member.id)},
        {"$set": {"old_roles": old_roles}},
        upsert=True
    )
    await member.edit(roles=[jailed_role], reason=reason)
    embed = discord.Embed(description=f"**{member}** jailed. Reason: {reason}", color=0x2B2D31)
    await ctx.reply(embed=embed)
    await _log_event(ctx.guild, "jail", f"{member} jailed by {ctx.author}. Reason: {reason}")

@bot.command(name="unjail")
@ctx_mod_or_owner()
async def unjail_member(ctx, member: discord.Member = None):
    if not member:
        return await ctx.reply("Mention a member to unjail.")
    gs = await settings_col.find_one({"_id": str(ctx.guild.id)})
    doc = await jail_col.find_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
    if not doc:
        return await ctx.reply(f"**{member}** is not jailed.")

    old_role_ids = doc.get("old_roles", [])
    roles = [ctx.guild.get_role(rid) for rid in old_role_ids if ctx.guild.get_role(rid)]
    await member.edit(roles=roles)
    await jail_col.delete_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
    await ctx.reply(f"**{member}** has been released from jail.")

# ============================================================
#                  SERVER SETUP WIZARD (Quick)
# ============================================================
@bot.command(name="quicksetup")
@commands.has_permissions(administrator=True)
async def quick_setup(ctx):
    """Interactive server setup: creates channels, roles, categories."""
    msg = await ctx.reply("Starting server setup... (this may take a moment)")
    guild = ctx.guild
    created = []

    # Roles
    roles_to_make = ["Member", "Moderator", "Admin", "Muted"]
    for rname in roles_to_make:
        if not discord.utils.get(guild.roles, name=rname):
            await guild.create_role(name=rname)
            created.append(f"Role: `{rname}`")
        await asyncio.sleep(0.3)

    # Categories & channels
    cats = {
        "INFORMATION": ["rules", "announcements", "roles"],
        "GENERAL":     ["general", "off-topic", "media"],
        "MODERATION":  ["mod-logs", "mod-chat"],
    }
    for cat_name, ch_names in cats.items():
        cat = discord.utils.get(guild.categories, name=cat_name)
        if not cat:
            cat = await guild.create_category(cat_name)
            created.append(f"Category: `{cat_name}`")
        for ch_name in ch_names:
            if not discord.utils.get(guild.channels, name=ch_name):
                await guild.create_text_channel(ch_name, category=cat)
                created.append(f"Channel: `#{ch_name}`")
            await asyncio.sleep(0.3)

    lines = "\n".join(created) or "Everything already existed."
    embed = discord.Embed(
        title="Quick Setup Complete",
        description=lines,
        color=0x2B2D31
    )
    embed.set_footer(text="Configure welcome/bye/logs/tickets separately.")
    await msg.edit(content=None, embed=embed)

# ============================================================
#                     ROLE MANAGEMENT
# ============================================================
@bot.group(name="role", invoke_without_command=True)
@ctx_mod_or_owner()
async def role_group(ctx):
    await ctx.reply("Usage: `,role add @user @role` | `,role remove @user @role`")

@role_group.command(name="add")
@ctx_mod_or_owner()
async def role_add(ctx, member: discord.Member = None, role: discord.Role = None):
    if not member or not role:
        return await ctx.reply("Usage: `,role add @user @role`")
    if ctx.author.id != BOT_OWNER_ID and ctx.author.top_role <= role:
        return await ctx.reply("You cannot assign a role equal to or higher than yours.")
    if ctx.guild.me.top_role <= role:
        return await ctx.reply("My role is too low to assign that role.")
    await member.add_roles(role)
    await ctx.reply(f"Added {role.mention} to {member.mention}.")

@role_group.command(name="remove")
@ctx_mod_or_owner()
async def role_remove(ctx, member: discord.Member = None, role: discord.Role = None):
    if not member or not role:
        return await ctx.reply("Usage: `,role remove @user @role`")
    if ctx.author.id != BOT_OWNER_ID and ctx.author.top_role <= role:
        return await ctx.reply("You cannot remove a role equal to or higher than yours.")
    await member.remove_roles(role)
    await ctx.reply(f"Removed {role.mention} from {member.mention}.")

# ============================================================
#                    WELCOME / BYE  (opt-in)
# ============================================================
@bot.group(name="welcome", invoke_without_command=True)
@commands.has_permissions(administrator=True)
async def welcome_group(ctx):
    await ctx.reply("Sub-commands: `,welcome set #channel` | `,welcome enable` | `,welcome disable`")

@welcome_group.command(name="set")
@commands.has_permissions(administrator=True)
async def welcome_set(ctx, channel: discord.TextChannel = None):
    if not channel:
        return await ctx.reply("Mention a channel.")
    await update_server_data(ctx.guild.id, "welcome_channel", channel.id)
    await ctx.reply(f"Welcome channel set to {channel.mention}.")

@welcome_group.command(name="enable")
@commands.has_permissions(administrator=True)
async def welcome_enable(ctx):
    await update_server_data(ctx.guild.id, "welcome_enabled", True)
    await ctx.reply("Welcome messages enabled.")

@welcome_group.command(name="disable")
@commands.has_permissions(administrator=True)
async def welcome_disable(ctx):
    await update_server_data(ctx.guild.id, "welcome_enabled", False)
    await ctx.reply("Welcome messages disabled.")

@bot.command(name="setbye")
@commands.has_permissions(administrator=True)
async def setbye(ctx, channel: discord.TextChannel = None):
    if not channel:
        return await ctx.reply("Mention a channel.")
    await update_server_data(ctx.guild.id, "bye_channel", channel.id)
    await update_server_data(ctx.guild.id, "bye_enabled", True)
    await ctx.reply(f"Bye channel set to {channel.mention}.")

@bot.event
async def on_member_join(member):
    data = await get_server_data(member.guild.id)
    if not data.get("welcome_enabled"):
        return
    channel_id = data.get("welcome_channel")
    channel = bot.get_channel(channel_id) if channel_id else member.guild.system_channel
    if not channel:
        return
    embed = discord.Embed(
        description=f"Welcome to **{member.guild.name}**, {member.mention}!",
        color=0x2B2D31
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    await channel.send(embed=embed)
    await _log_event(member.guild, "member_join", f"{member} joined the server.")

@bot.event
async def on_member_remove(member):
    data = await get_server_data(member.guild.id)
    if not data.get("bye_enabled"):
        return
    channel_id = data.get("bye_channel")
    channel = bot.get_channel(channel_id) if channel_id else None
    if not channel:
        return
    embed = discord.Embed(
        description=f"**{member.name}** has left the server. Members: {member.guild.member_count}",
        color=0x2B2D31
    )
    await channel.send(embed=embed)
    await _log_event(member.guild, "member_leave", f"{member} left the server.")

# ============================================================
#                      LOGGING SYSTEM
# ============================================================
async def _log_event(guild, event_type: str, description: str):
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
            timestamp=datetime.now(timezone.utc)
        )
        await channel.send(embed=embed)
    except:
        pass

@bot.group(name="logs", invoke_without_command=True)
@commands.has_permissions(administrator=True)
async def logs_group(ctx):
    await ctx.reply("Usage: `,logs set #channel` | `,logs disable`")

@logs_group.command(name="set")
@commands.has_permissions(administrator=True)
async def logs_set(ctx, channel: discord.TextChannel = None):
    if not channel:
        return await ctx.reply("Mention a channel.")
    await logs_col.update_one(
        {"guild_id": str(ctx.guild.id)},
        {"$set": {"channel_id": str(channel.id)}},
        upsert=True
    )
    await ctx.reply(f"Log channel set to {channel.mention}.")

@logs_group.command(name="disable")
@commands.has_permissions(administrator=True)
async def logs_disable(ctx):
    await logs_col.delete_one({"guild_id": str(ctx.guild.id)})
    await ctx.reply("Logging disabled.")

@bot.event
async def on_message_delete(message):
    if message.author.bot or not message.guild:
        return
    await _log_event(message.guild, "message_delete",
                     f"**{message.author}** deleted in {message.channel.mention}:\n> {message.content[:500]}")

@bot.event
async def on_message_edit(before, after):
    if before.author.bot or not before.guild or before.content == after.content:
        return
    await _log_event(before.guild, "message_edit",
                     f"**{before.author}** edited in {before.channel.mention}:\n"
                     f"Before: {before.content[:300]}\nAfter: {after.content[:300]}")

@bot.event
async def on_member_ban(guild, user):
    await _log_event(guild, "member_ban", f"**{user}** was banned.")

@bot.event
async def on_member_unban(guild, user):
    await _log_event(guild, "member_unban", f"**{user}** was unbanned.")

# ============================================================
#                    ANNOUNCEMENT / GIVEAWAY
# ============================================================
@bot.command(name="announce")
@ctx_mod_or_owner()
async def announce(ctx, channel: discord.TextChannel = None, *, content: str = None):
    if not content:
        return await ctx.reply("Usage: `,announce #channel Your announcement here`\nOr `,announce Your message` for current channel.")
    target = channel or ctx.channel
    embed = discord.Embed(description=content, color=0x2B2D31, timestamp=datetime.now())
    embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
    embed.set_footer(text=f"Announced by {ctx.author.display_name}")
    await target.send(embed=embed)
    if channel:
        await ctx.message.delete()
    await _log_event(ctx.guild, "announcement", f"{ctx.author} announced in {target.mention}.")

@bot.command(name="giveaway")
@ctx_mod_or_owner()
async def giveaway(ctx, duration_minutes: int = None, winners: int = 1, *, prize: str = None):
    if not duration_minutes or not prize:
        return await ctx.reply("Usage: `,giveaway <minutes> <winners> <prize>`\nExample: `,giveaway 60 1 Nitro Classic`")

    end_time = datetime.now() + timedelta(minutes=duration_minutes)
    ts = int(end_time.timestamp())
    embed = discord.Embed(
        title=f"Giveaway — {prize}",
        description=(
            f"React with 🎉 to enter.\n"
            f"Winners: **{winners}**\n"
            f"Ends: <t:{ts}:R>"
        ),
        color=0x2B2D31
    )
    embed.set_footer(text=f"Hosted by {ctx.author}")
    await ctx.message.delete()
    msg = await ctx.channel.send(embed=embed)
    await msg.add_reaction("🎉")

    await asyncio.sleep(duration_minutes * 60)

    try:
        msg = await ctx.channel.fetch_message(msg.id)
        reaction = next((r for r in msg.reactions if str(r.emoji) == "🎉"), None)
        if not reaction:
            return await ctx.channel.send(f"Giveaway for **{prize}** ended with no entries.")
        users = [u async for u in reaction.users() if not u.bot]
        if len(users) < winners:
            return await ctx.channel.send(f"Not enough entries for **{prize}** giveaway.")
        chosen = random.sample(users, winners)
        mentions = ", ".join(w.mention for w in chosen)
        await ctx.channel.send(
            embed=discord.Embed(
                title="Giveaway Ended",
                description=f"Prize: **{prize}**\nWinner(s): {mentions}",
                color=0xffd700
            )
        )
    except Exception as e:
        print(f"[Giveaway] {e}")

# ============================================================
#                       AFK SYSTEM
# ============================================================
@bot.command(name="afk")
async def afk(ctx, *, reason: str = "Away from keyboard"):
    await afk_col.update_one(
        {"user_id": ctx.author.id, "guild_id": ctx.guild.id},
        {"$set": {"reason": reason, "time": datetime.now(timezone.utc)}},
        upsert=True
    )
    try:
        if ctx.guild.me.guild_permissions.manage_nicknames and not ctx.author.display_name.startswith("[AFK]"):
            await ctx.author.edit(nick=f"[AFK] {ctx.author.display_name[:25]}")
    except:
        pass
    embed = discord.Embed(description=f"{ctx.author.mention}, AFK status set.\nReason: `{reason}`", color=0x2B2D31)
    await ctx.reply(embed=embed)

# ============================================================
#                     STICKY MESSAGES
# ============================================================
@bot.command(name="sticky")
@ctx_mod_or_owner()
async def sticky(ctx, *, text: str = None):
    if not text:
        return await ctx.reply("Usage: `,sticky Your message here`")
    old = await sticky_col.find_one({"channel_id": ctx.channel.id})
    if old:
        try:
            om = await ctx.channel.fetch_message(old["message_id"])
            await om.delete()
        except:
            pass
    embed = discord.Embed(description=text.replace("\\n", "\n"), color=0x2B2D31)
    embed.set_footer(text="Sticky Message")
    msg = await ctx.channel.send(embed=embed)
    await sticky_col.update_one(
        {"channel_id": ctx.channel.id},
        {"$set": {"message_id": msg.id, "content": text}},
        upsert=True
    )
    await ctx.message.delete()

@bot.command(name="unsticky")
@ctx_mod_or_owner()
async def unsticky(ctx):
    data = await sticky_col.find_one({"channel_id": ctx.channel.id})
    if not data:
        return await ctx.reply("No sticky message in this channel.")
    try:
        om = await ctx.channel.fetch_message(data["message_id"])
        await om.delete()
    except:
        pass
    await sticky_col.delete_one({"channel_id": ctx.channel.id})
    await ctx.reply("Sticky message removed.", delete_after=4)

# ============================================================
#                   AUTOMOD / ANTI-INVITE
# ============================================================
@bot.group(name="automod", invoke_without_command=True)
@commands.has_permissions(administrator=True)
async def automod_group(ctx):
    await ctx.reply("Usage: `,automod invite on/off`")

@automod_group.command(name="invite")
@commands.has_permissions(administrator=True)
async def automod_invite(ctx, status: str = None):
    if not status or status.lower() not in ("on", "off"):
        return await ctx.reply("Usage: `,automod invite on` or `,automod invite off`")
    state = status.lower() == "on"
    await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$set": {"invite_block": state}}, upsert=True)
    await ctx.reply(f"Anti-invite blocker turned **{status.upper()}**.")

# ============================================================
#                  REACTION ROLES
# ============================================================
@bot.command(name="reactionrole", aliases=["rr"])
@commands.has_permissions(administrator=True)
async def reaction_role(ctx, action: str = None, message_link: str = None, emoji: str = None, role: discord.Role = None):
    if not action or action.lower() != "add" or not all([message_link, emoji, role]):
        return await ctx.reply("Usage: `,reactionrole add <message_link> <emoji> @role`")
    try:
        parts = message_link.strip().split("/")
        channel_id = int(parts[-2])
        message_id = int(parts[-1])
    except:
        return await ctx.reply("Invalid message link.")
    try:
        ch = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        target_msg = await ch.fetch_message(message_id)
        await target_msg.add_reaction(emoji)
    except Exception as e:
        return await ctx.reply(f"Error: {e}")
    await reaction_roles_col.update_one(
        {"message_id": str(message_id), "emoji": str(emoji)},
        {"$set": {"channel_id": str(channel_id), "guild_id": str(ctx.guild.id), "role_id": str(role.id)}},
        upsert=True
    )
    await ctx.reply(f"Reaction role set: {emoji} → {role.mention}")

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return
    doc = await reaction_roles_col.find_one({"message_id": str(payload.message_id), "emoji": str(payload.emoji)})
    if doc:
        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        role = guild.get_role(int(doc["role_id"]))
        member = payload.member or await guild.fetch_member(payload.user_id)
        if role and member:
            await member.add_roles(role, reason="Reaction Role")

@bot.event
async def on_raw_reaction_remove(payload):
    doc = await reaction_roles_col.find_one({"message_id": str(payload.message_id), "emoji": str(payload.emoji)})
    if doc:
        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        role = guild.get_role(int(doc["role_id"]))
        try:
            member = await guild.fetch_member(payload.user_id)
        except:
            return
        if role and member:
            await member.remove_roles(role, reason="Reaction Role removed")

# ============================================================
#                  BUTTON ROLES (premium feature)
# ============================================================
class ButtonRolesView(discord.ui.View):
    def __init__(self, roles_data=None):
        super().__init__(timeout=None)
        if roles_data:
            for item in roles_data:
                self.add_item(ButtonRoleItem(
                    label=item["label"],
                    role_id=item["role_id"],
                    custom_id=f"btnrole_{item['role_id']}"
                ))

class ButtonRoleItem(discord.ui.Button):
    def __init__(self, label, role_id, custom_id):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, custom_id=custom_id)
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(int(self.role_id))
        if not role:
            return await interaction.response.send_message("Role not found.", ephemeral=True)
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role)
            await interaction.response.send_message(f"Removed **{role.name}**.", ephemeral=True)
        else:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"Added **{role.name}**.", ephemeral=True)

@bot.command(name="buttonrole", aliases=["br"])
@commands.has_permissions(administrator=True)
@ctx_premium_required()
async def button_role(ctx, *, args: str = None):
    """
    ,buttonrole @role1 Label1 | @role2 Label2
    Creates a button role panel.
    """
    if not args:
        return await ctx.reply("Usage: `,buttonrole @role Label | @role2 Label2`")
    entries = [e.strip() for e in args.split("|")]
    roles_data = []
    for entry in entries:
        parts = entry.split()
        if not parts:
            continue
        role_mention = parts[0]
        label = " ".join(parts[1:]) or "Role"
        match = re.search(r"\d+", role_mention)
        if not match:
            continue
        role = ctx.guild.get_role(int(match.group()))
        if role:
            roles_data.append({"role_id": str(role.id), "label": label})

    if not roles_data:
        return await ctx.reply("No valid roles found.")

    view = ButtonRolesView(roles_data)
    embed = discord.Embed(title="Role Selection", description="Click a button to assign/remove a role.", color=0x2B2D31)
    await ctx.send(embed=embed, view=view)
    await ctx.message.delete()

# ============================================================
#                      BOOSTER ROLES
# ============================================================
@bot.command(name="boosterrole")
@commands.has_permissions(administrator=True)
async def booster_role_setup(ctx, role: discord.Role = None):
    """Set the role to give boosters."""
    if not role:
        return await ctx.reply("Mention a role to give to boosters.")
    await booster_roles_col.update_one(
        {"guild_id": str(ctx.guild.id)},
        {"$set": {"role_id": str(role.id)}},
        upsert=True
    )
    await ctx.reply(f"Booster reward role set to {role.mention}.")

@bot.event
async def on_member_update(before, after):
    # Booster check
    was_boosting = before.premium_since is None
    is_boosting  = after.premium_since is not None
    if not was_boosting and is_boosting:
        doc = await booster_roles_col.find_one({"guild_id": str(after.guild.id)})
        if doc:
            role = after.guild.get_role(int(doc["role_id"]))
            if role:
                await after.add_roles(role, reason="Server Boost reward")

# ============================================================
#                    VOICEMASTER (Premium)
# ============================================================
@bot.command(name="vcsetup")
@commands.has_permissions(administrator=True)
@ctx_premium_required()
async def vc_setup(ctx):
    """Creates a 'Join to Create' voice channel."""
    cat = await ctx.guild.create_category("Voice Channels")
    vc  = await ctx.guild.create_voice_channel("Join to Create", category=cat)
    await voicemaster_col.update_one(
        {"guild_id": str(ctx.guild.id)},
        {"$set": {"create_channel_id": str(vc.id), "category_id": str(cat.id)}},
        upsert=True
    )
    await ctx.reply(f"VoiceMaster ready. Join {vc.mention} to create a private voice channel.")

@bot.event
async def on_voice_state_update(member, before, after):
    # VoiceMaster
    doc = await voicemaster_col.find_one({"guild_id": str(member.guild.id)})
    if doc and after.channel and str(after.channel.id) == doc["create_channel_id"]:
        cat = member.guild.get_channel(int(doc["category_id"]))
        new_vc = await member.guild.create_voice_channel(
            name=f"{member.display_name}'s VC",
            category=cat,
            user_limit=10
        )
        await member.move_to(new_vc)
        await voicemaster_col.update_one(
            {"guild_id": str(member.guild.id)},
            {"$push": {"temp_channels": str(new_vc.id)}}
        )
    # Delete empty temp VCs
    if doc and before.channel and before.channel != after.channel:
        temp_ids = doc.get("temp_channels", [])
        if str(before.channel.id) in temp_ids and len(before.channel.members) == 0:
            await before.channel.delete()
            await voicemaster_col.update_one(
                {"guild_id": str(member.guild.id)},
                {"$pull": {"temp_channels": str(before.channel.id)}}
            )

# ============================================================
#                   BUMP REMINDER (Premium)
# ============================================================
@bot.command(name="bumpreminder")
@commands.has_permissions(administrator=True)
@ctx_premium_required()
async def bump_reminder_cmd(ctx, status: str = "on"):
    state = status.lower() == "on"
    await bump_col.update_one(
        {"guild_id": str(ctx.guild.id)},
        {"$set": {"enabled": state, "channel_id": str(ctx.channel.id)}},
        upsert=True
    )
    await ctx.reply(f"Bump reminder {'enabled' if state else 'disabled'} in {ctx.channel.mention}.")

@bot.event
async def on_message(message):
    if message.author.bot:
        # Check if DISBOARD bump message
        if message.author.id == 302050872383242240:  # DISBOARD bot ID
            if message.embeds and "Bump done" in (message.embeds[0].description or ""):
                doc = await bump_col.find_one({"guild_id": str(message.guild.id)})
                if doc and doc.get("enabled") and doc.get("channel_id"):
                    channel = bot.get_channel(int(doc["channel_id"]))
                    if channel:
                        await asyncio.sleep(7200)  # 2 hours
                        await channel.send("It's time to bump the server! Use `/bump` on DISBOARD.")
        return

    await _handle_message(message)

async def _handle_message(message):
    """Central message handler."""
    if not message.guild:
        await bot.process_commands(message)
        return

    # Anti-invite check
    gs = await settings_col.find_one({"_id": str(message.guild.id)})
    if gs and gs.get("invite_block"):
        content_low = message.content.lower().replace(" ", "")
        if ("discord.gg/" in content_low or "discord.com/invite/" in content_low):
            if not message.author.guild_permissions.administrator and message.author.id != BOT_OWNER_ID:
                try:
                    await message.delete()
                    await message.channel.send(
                        embed=discord.Embed(description=f"{message.author.mention}, invite links are not allowed.", color=0xff0000),
                        delete_after=5
                    )
                    return
                except:
                    pass

    # AFK return check
    user_afk = await afk_col.find_one({"user_id": message.author.id, "guild_id": message.guild.id})
    if user_afk:
        away_time = ""
        if "time" in user_afk:
            afk_time = user_afk["time"]
            if afk_time.tzinfo is None:
                afk_time = afk_time.replace(tzinfo=timezone.utc)
            mins = int((datetime.now(timezone.utc) - afk_time).total_seconds() / 60)
            if mins > 0:
                away_time = f" ({mins}m away)"
        await afk_col.delete_one({"_id": user_afk["_id"]})
        try:
            if message.guild.me.guild_permissions.manage_nicknames:
                await message.author.edit(nick=message.author.display_name.replace("[AFK] ", ""))
        except:
            pass
        await message.channel.send(
            embed=discord.Embed(description=f"Welcome back, {message.author.mention}{away_time}!", color=0x2B2D31),
            delete_after=6
        )

    # AFK ping check
    if message.mentions:
        for mentioned in message.mentions:
            if mentioned.id == message.author.id:
                continue
            target_afk = await afk_col.find_one({"user_id": mentioned.id, "guild_id": message.guild.id})
            if target_afk:
                reason   = target_afk.get("reason", "Away from keyboard")
                afk_time = target_afk.get("time")
                time_str = f" (<t:{int(afk_time.timestamp())}:R>)" if afk_time else ""
                await message.reply(
                    embed=discord.Embed(
                        description=f"**{mentioned.name}** is AFK{time_str}.\nReason: `{reason}`",
                        color=0x2B2D31
                    )
                )

    # Sticky message move
    sticky_data = await sticky_col.find_one({"channel_id": message.channel.id})
    if sticky_data:
        chan_id = message.channel.id
        sticky_counter[chan_id] = sticky_counter.get(chan_id, 0) + 1
        if sticky_counter[chan_id] >= 1:
            sticky_counter[chan_id] = 0
            try:
                old = await message.channel.fetch_message(sticky_data["message_id"])
                await old.delete()
                embed = discord.Embed(description=sticky_data["content"], color=0x2B2D31)
                embed.set_footer(text="Sticky Message")
                new_sticky = await message.channel.send(embed=embed)
                await sticky_col.update_one({"channel_id": chan_id}, {"$set": {"message_id": new_sticky.id}})
            except:
                pass

    # XP / level system
    await _handle_xp(message)

    # Maintenance mode guard
    if maintenance_mode:
        if message.author.id == BOT_OWNER_ID:
            await bot.process_commands(message)
        elif bot.user.mentioned_in(message):
            await message.reply("Happy is currently under maintenance. Back soon.")
        return

    # Command prefix check
    if message.content.startswith(('/', '$')):
        return

    if len(message.content) < 2 or "http" in message.content.lower():
        await bot.process_commands(message)
        return

    # Heart reaction
    greetings = ["good morning", "gm", "good night", "gn", "happy birthday", "hbd", "hello", "hi", "welcome"]
    if any(word in message.content.lower().split() for word in greetings):
        try:
            await asyncio.sleep(random.uniform(0.2, 0.8))
            await message.add_reaction("💖")
        except:
            pass

    # AI Chat
    IST = pytz.timezone('Asia/Kolkata')
    dt_now = datetime.now(IST)
    readable_time = dt_now.strftime("%I:%M %p")
    readable_date = dt_now.strftime("%d %B %Y")

    channel_id   = message.channel.id
    current_time = time.time()
    is_mentioned = bot.user.mentioned_in(message)
    is_reply_to_bot = False

    if message.reference and message.reference.message_id:
        try:
            replied = await message.channel.fetch_message(message.reference.message_id)
            if replied.author.id == bot.user.id:
                is_reply_to_bot = True
        except:
            pass

    is_session_active = (channel_id in active_sessions and
                         current_time - active_sessions[channel_id] < SESSION_TIMEOUT)

    # Premium gate for AI chat
    can_use_ai = (
        message.author.id == BOT_OWNER_ID or
        await is_premium_user(message.author.id) or
        await is_premium_server(message.guild.id)
    )

    if ai_enabled and can_use_ai and (is_mentioned or is_reply_to_bot or is_session_active):
        active_sessions[channel_id] = current_time
        uid = message.author.id
        clean_prompt = message.content.replace(f'<@!{bot.user.id}>', '').replace(f'<@{bot.user.id}>', '').strip()

        if clean_prompt:
            if uid not in user_memories:
                user_memories[uid] = []

            instruction = f"""You are Happy, an Indian guy.
1. Language: Natural Hinglish (Mix of Hindi/English). No forced slangs.
2. Rule: Give logical, helpful, and sensible answers only.
3. Style: Keep it very short (1 line). Chat like a normal person on discord.
4. Persona: Friendly but not stupid. If a question is serious, answer it simply.
5. No AI behavior: Don't say "As an AI" or "I'm here to help."
Emojis: Use rarely (1-2 max). No bot-like sparkles.
Current Date: {readable_date}
Current Time: {readable_time}"""

            messages_to_send = [{"role": "system", "content": instruction}]
            for hist in user_memories[uid][-6:]:
                messages_to_send.append(hist)
            messages_to_send.append({"role": "user", "content": clean_prompt})

            try:
                await asyncio.sleep(random.uniform(1.0, 2.0))
                async with message.channel.typing():
                    typing_wait = min(random.uniform(2.0, 4.5) + len(clean_prompt) / 10, 7.0)
                    await asyncio.sleep(typing_wait)
                    chat = groq_client.chat.completions.create(
                        messages=messages_to_send,
                        model="llama-3.3-70b-versatile",
                        max_tokens=100,
                        temperature=0.7
                    )
                    reply = chat.choices[0].message.content
                    user_memories[uid].append({"role": "user", "content": clean_prompt})
                    user_memories[uid].append({"role": "assistant", "content": reply})
                    await message.reply(reply)
            except Exception as e:
                print(f"[Groq] {e}")

    elif ai_enabled and not can_use_ai and is_mentioned:
        await message.reply("AI chat is a **Happy Premium** feature. Ask the server owner to activate it.")

    # Global call relay
    server_id = message.guild.id
    if server_id in active_calls:
        data = active_calls[server_id]
        if message.channel.id == data.get('my_channel') and not is_mentioned:
            target_ch = bot.get_channel(data.get('partner_channel'))
            if target_ch:
                try:
                    await target_ch.send(f"**{message.author.name}**: {message.content}")
                except:
                    pass

    await bot.process_commands(message)

# ============================================================
#               LEVELING SYSTEM (XP)
# ============================================================
async def _handle_xp(message):
    uid = str(message.author.id)
    gid = str(message.guild.id)
    doc = await levels_col.find_one({"guild_id": gid, "user_id": uid})
    xp    = (doc.get("xp", 0) if doc else 0) + random.randint(5, 15)
    level = doc.get("level", 0) if doc else 0
    required = (level + 1) * 100
    leveled_up = False
    if xp >= required:
        xp = 0
        level += 1
        leveled_up = True
    await levels_col.update_one(
        {"guild_id": gid, "user_id": uid},
        {"$set": {"xp": xp, "level": level}},
        upsert=True
    )
    if leveled_up:
        await message.channel.send(
            embed=discord.Embed(
                description=f"{message.author.mention} reached **Level {level}**!",
                color=0x2B2D31
            ),
            delete_after=10
        )

@bot.command(name="level", aliases=["rank", "xp"])
async def level_cmd(ctx, member: discord.Member = None):
    member = member or ctx.author
    doc = await levels_col.find_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
    if not doc:
        return await ctx.reply(f"**{member}** has not earned any XP yet.")
    level = doc.get("level", 0)
    xp    = doc.get("xp", 0)
    nxt   = (level + 1) * 100
    bar_filled = round((xp / nxt) * 10)
    bar = "█" * bar_filled + "░" * (10 - bar_filled)
    embed = discord.Embed(title=f"Level — {member.display_name}", color=0x2B2D31)
    embed.add_field(name="Level", value=f"**{level}**", inline=True)
    embed.add_field(name="XP",    value=f"**{xp}/{nxt}**", inline=True)
    embed.add_field(name="Progress", value=f"`{bar}`", inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.reply(embed=embed)

@bot.command(name="leaderboard", aliases=["lb"])
async def leaderboard(ctx):
    cursor = levels_col.find({"guild_id": str(ctx.guild.id)}).sort("level", -1).limit(10)
    docs = await cursor.to_list(length=10)
    if not docs:
        return await ctx.reply("No level data yet.")
    lines = []
    for i, doc in enumerate(docs, 1):
        member = ctx.guild.get_member(int(doc["user_id"]))
        name   = member.display_name if member else f"Unknown ({doc['user_id']})"
        lines.append(f"`{i}.` **{name}** — Level {doc.get('level', 0)} ({doc.get('xp', 0)} XP)")
    embed = discord.Embed(title="Leaderboard", description="\n".join(lines), color=0x2B2D31)
    await ctx.reply(embed=embed)

# ============================================================
#                      BIRTHDAY SYSTEM
# ============================================================
@bot.group(name="birthday", aliases=["bday"], invoke_without_command=True)
async def birthday_group(ctx, member: discord.Member = None):
    member = member or ctx.author
    doc = await birthdays_col.find_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
    if not doc:
        return await ctx.reply(f"No birthday set for **{member}**. Use `,birthday set DD/MM`.")
    embed = discord.Embed(
        description=f"**{member.display_name}'s** birthday: **{doc['date']}**",
        color=0x2B2D31
    )
    await ctx.reply(embed=embed)

@birthday_group.command(name="set")
async def birthday_set(ctx, date: str = None):
    if not date:
        return await ctx.reply("Usage: `,birthday set DD/MM`  (e.g. `,birthday set 25/12`)")
    try:
        day, month = map(int, date.split("/"))
        if not (1 <= day <= 31 and 1 <= month <= 12):
            raise ValueError
    except:
        return await ctx.reply("Invalid date format. Use DD/MM (e.g. 25/12).")
    await birthdays_col.update_one(
        {"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)},
        {"$set": {"date": date, "day": day, "month": month}},
        upsert=True
    )
    await ctx.reply(f"Birthday set to **{date}**.")

@tasks.loop(hours=24)
async def birthday_check():
    await bot.wait_until_ready()
    now = datetime.now(timezone.utc)
    cursor = birthdays_col.find({"day": now.day, "month": now.month})
    async for doc in cursor:
        guild = bot.get_guild(int(doc.get("guild_id", 0))) if doc.get("guild_id") else None
        if not guild:
            continue
        member = guild.get_member(int(doc["user_id"]))
        if not member:
            continue
        gs = await settings_col.find_one({"_id": str(guild.id)})
        channel_id = gs.get("welcome_channel") if gs else None
        channel = bot.get_channel(channel_id) if channel_id else guild.system_channel
        if channel:
            await channel.send(
                embed=discord.Embed(
                    description=f"Happy Birthday, {member.mention}! Hope you have a great day.",
                    color=0x2B2D31
                )
            )

# ============================================================
#                     COUNTERS
# ============================================================
@bot.command(name="counter")
@commands.has_permissions(administrator=True)
async def counter_cmd(ctx, action: str = None, name: str = None, channel: discord.VoiceChannel = None):
    """
    ,counter create members #vc-channel
    ,counter create bots #vc-channel
    """
    if not action or action.lower() != "create" or not name or not channel:
        return await ctx.reply("Usage: `,counter create members/bots/channels #voice-channel`")
    await counters_col.update_one(
        {"guild_id": str(ctx.guild.id), "type": name.lower()},
        {"$set": {"channel_id": str(channel.id)}},
        upsert=True
    )
    await _update_counters(ctx.guild)
    await ctx.reply(f"Counter `{name}` linked to {channel.mention}.")

async def _update_counters(guild):
    cursor = counters_col.find({"guild_id": str(guild.id)})
    async for doc in cursor:
        ch = guild.get_channel(int(doc["channel_id"]))
        if not ch:
            continue
        ctype = doc["type"]
        if ctype == "members":
            await ch.edit(name=f"Members: {guild.member_count}")
        elif ctype == "bots":
            bots = sum(1 for m in guild.members if m.bot)
            await ch.edit(name=f"Bots: {bots}")
        elif ctype == "channels":
            await ch.edit(name=f"Channels: {len(guild.channels)}")

# ============================================================
#                  SERVER STATUS (Premium)
# ============================================================
@bot.command(name="setstatus")
@ctx_premium_required()
@commands.has_permissions(administrator=True)
async def set_server_status(ctx, *, status: str = None):
    if not status:
        await server_status_col.delete_one({"guild_id": str(ctx.guild.id)})
        return await ctx.reply("Custom bot status for your server removed.")
    await server_status_col.update_one(
        {"guild_id": str(ctx.guild.id)},
        {"$set": {"status": status}},
        upsert=True
    )
    await ctx.reply(f"Bot will now show status from your server's pool: `{status}`")

# ============================================================
#                  GLOBAL CALL (Premium)
# ============================================================
@bot.command(name="call")
@ctx_premium_required()
@commands.has_permissions(administrator=True)
async def call_cmd(ctx):
    global waiting_list
    server_id  = ctx.guild.id
    channel_id = ctx.channel.id

    if server_id in active_calls:
        return await ctx.reply("Already on a call. Use `,hangup` first.")
    if any(d["server_id"] == server_id for d in waiting_list):
        return await ctx.reply("Already in the waiting queue.")

    if waiting_list:
        partner = waiting_list.pop(0)
        p_sid = partner["server_id"]
        p_cid = partner["channel_id"]
        active_calls[server_id] = {"partner_channel": p_cid, "my_channel": channel_id}
        active_calls[p_sid]     = {"partner_channel": channel_id, "my_channel": p_cid}
        embed = discord.Embed(description="Call connected. You can now chat with the other server.", color=0x2B2D31)
        await ctx.send(embed=embed)
        pch = bot.get_channel(p_cid)
        if pch:
            await pch.send(embed=discord.Embed(description="Call connected. Partner server joined.", color=0x2B2D31))
    else:
        waiting_list.append({"server_id": server_id, "channel_id": channel_id})
        await ctx.reply("Waiting for another server to join...")

@bot.command(name="hangup")
@commands.has_permissions(administrator=True)
async def hangup_cmd(ctx):
    global waiting_list
    server_id = ctx.guild.id
    in_waiting = next((d for d in waiting_list if d["server_id"] == server_id), None)
    if in_waiting:
        waiting_list.remove(in_waiting)
        return await ctx.reply("Removed from the waiting queue.")
    if server_id in active_calls:
        data = active_calls[server_id]
        p_cid = data.get("partner_channel")
        del active_calls[server_id]
        # Find and remove partner too
        for sid, d in list(active_calls.items()):
            if d.get("partner_channel") == ctx.channel.id or d.get("my_channel") == p_cid:
                del active_calls[sid]
        await ctx.reply("Call ended.")
        if p_cid:
            pch = bot.get_channel(p_cid)
            if pch:
                await pch.send("The other server has ended the call.")
    else:
        await ctx.reply("No active call found.")

# ============================================================
#                    UTILITY COMMANDS
# ============================================================
@bot.command(name="userinfo", aliases=["whois"])
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(title=f"User Info — {member}", color=0x2B2D31)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID",          value=member.id,                                         inline=True)
    embed.add_field(name="Joined",      value=member.joined_at.strftime("%d %b %Y") if member.joined_at else "Unknown", inline=True)
    embed.add_field(name="Created",     value=member.created_at.strftime("%d %b %Y"),             inline=True)
    embed.add_field(name="Top Role",    value=member.top_role.mention,                            inline=True)
    embed.add_field(name="Bot",         value="Yes" if member.bot else "No",                      inline=True)
    embed.add_field(name="Roles",       value=str(len(member.roles) - 1),                         inline=True)
    await ctx.reply(embed=embed)

@bot.command(name="avatar", aliases=["av"])
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(title=f"{member}'s Avatar", color=0x2B2D31)
    embed.set_image(url=member.display_avatar.url)
    await ctx.reply(embed=embed)

@bot.command(name="serverinfo", aliases=["si"])
async def serverinfo(ctx):
    g = ctx.guild
    embed = discord.Embed(title=g.name, color=0x2B2D31)
    embed.add_field(name="Owner",       value=g.owner.mention if g.owner else "Unknown", inline=True)
    embed.add_field(name="Members",     value=g.member_count,                            inline=True)
    embed.add_field(name="Channels",    value=len(g.channels),                           inline=True)
    embed.add_field(name="Roles",       value=len(g.roles),                              inline=True)
    embed.add_field(name="Boost Level", value=f"Level {g.premium_tier}",                 inline=True)
    embed.add_field(name="ID",          value=g.id,                                      inline=True)
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    await ctx.reply(embed=embed)

@bot.command(name="ping")
async def ping(ctx):
    lat = round(bot.latency * 1000)
    embed = discord.Embed(description=f"Latency: **{lat}ms**", color=0x2B2D31)
    await ctx.reply(embed=embed)

@bot.command(name="membercount", aliases=["mc"])
async def membercount(ctx):
    g = ctx.guild
    bots   = sum(1 for m in g.members if m.bot)
    humans = g.member_count - bots
    embed = discord.Embed(title=f"{g.name} — Member Count", color=0x2B2D31)
    embed.add_field(name="Total",  value=g.member_count, inline=True)
    embed.add_field(name="Humans", value=humans,          inline=True)
    embed.add_field(name="Bots",   value=bots,            inline=True)
    await ctx.reply(embed=embed)

@bot.command(name="shrug")
async def shrug(ctx, *, message: str = None):
    try:
        await ctx.message.delete()
    except:
        pass
    await ctx.send(f"{message} ¯\\_(ツ)_/¯" if message else "¯\\_(ツ)_/¯")

# ============================================================
#                     FUN COMMANDS
# ============================================================
@bot.command(name="ship", aliases=["love", "match"])
async def ship(ctx, user1: discord.Member = None, user2: discord.Member = None):
    if not user1 or not user2:
        return await ctx.reply("Usage: `,ship @user1 @user2`")
    if user1.id == user2.id:
        return await ctx.reply("Cannot ship someone with themselves.")
    random.seed(user1.id + user2.id)
    pct = random.randint(0, 100)
    random.seed()
    if pct < 20:   verdict = "Terrible match."
    elif pct < 45: verdict = "Friendzone incoming."
    elif pct < 75: verdict = "There's something there."
    elif pct < 90: verdict = "Great match!"
    else:          verdict = "Soulmates."
    bar = "🟥" * round(pct / 10) + "⬛" * (10 - round(pct / 10))
    embed = discord.Embed(title="Matchmaker", color=0x2B2D31)
    embed.add_field(name=f"{pct}% — {user1.name} + {user2.name}", value=f"{bar}\n{verdict}", inline=False)
    await ctx.reply(embed=embed)

@bot.command(name="hot", aliases=["hotness"])
async def hot(ctx, member: discord.Member = None):
    member = member or ctx.author
    random.seed(member.id)
    pct = random.randint(0, 100)
    random.seed()
    if pct < 20:   verdict = "Zero hotness."
    elif pct < 50: verdict = "Pretty average."
    elif pct < 80: verdict = "Looking good."
    else:          verdict = "Extreme heat warning."
    bar = "🔥" * round(pct / 10) + "⬛" * (10 - round(pct / 10))
    embed = discord.Embed(title="Hotness Meter", color=0x2B2D31)
    embed.add_field(name=f"{pct}% — {member.display_name}", value=f"{bar}\n{verdict}", inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.reply(embed=embed)

# ============================================================
#                   ROLEPLAY COMMANDS
# ============================================================
RP_ACTIONS = {
    "hug":    ("{author} hugs {target}.",               "🤗"),
    "pat":    ("{author} pats {target} on the head.",   "🙂"),
    "slap":   ("{author} slaps {target}.",              "👋"),
    "kiss":   ("{author} gives {target} a kiss.",       "💋"),
    "poke":   ("{author} pokes {target}.",              "👉"),
    "highfive": ("{author} high-fives {target}.",       "✋"),
    "bonk":   ("{author} bonks {target} on the head.",  "🔨"),
    "cuddle": ("{author} cuddles {target}.",            "🫂"),
}

for action_name, (template, emoji) in RP_ACTIONS.items():
    async def rp_cmd(ctx, target: discord.Member = None, _template=template, _emoji=emoji):
        target = target or ctx.author
        text = _template.format(author=ctx.author.display_name, target=target.display_name)
        await ctx.reply(f"{_emoji} {text}")
    rp_cmd.__name__ = action_name
    bot.command(name=action_name)(rp_cmd)

# ============================================================
#                   URBAN DICTIONARY
# ============================================================
@bot.command(name="urban", aliases=["ud"])
async def urban(ctx, *, word: str = None):
    if not word:
        return await ctx.reply("Usage: `,urban <word>`")
    url = f"https://api.urbandictionary.com/v0/define?term={urllib.parse.quote(word)}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            if r.status != 200:
                return await ctx.reply("Could not reach Urban Dictionary API.")
            data = await r.json()
    if not data.get("list"):
        return await ctx.reply(f"No definition found for `{word}`.")
    top = data["list"][0]
    definition = top["definition"].replace("[", "").replace("]", "")[:1000]
    example    = top["example"].replace("[", "").replace("]", "")[:500]
    embed = discord.Embed(title=top["word"], url=top["permalink"], color=0x2B2D31)
    embed.add_field(name="Definition", value=definition, inline=False)
    if example:
        embed.add_field(name="Example", value=f"*{example}*", inline=False)
    embed.set_footer(text=f"👍 {top['thumbs_up']} | 👎 {top['thumbs_down']} | by {top['author']}")
    await ctx.reply(embed=embed)

# ============================================================
#                     TRANSLATE
# ============================================================
@bot.command(name="translate", aliases=["tr"])
async def translate(ctx, lang: str = None, *, text: str = None):
    if not lang or not text:
        return await ctx.reply("Usage: `,translate <language> <text>`\nExamples: `,translate hindi Hello` or `,translate en नमस्ते`")
    lang_map = {
        "english": "en", "hindi": "hi", "french": "fr", "spanish": "es",
        "german": "de", "japanese": "ja", "russian": "ru", "arabic": "ar"
    }
    tl = lang_map.get(lang.lower(), lang.lower()[:2])
    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl={tl}&dt=t&q={urllib.parse.quote(text)}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            if r.status != 200:
                return await ctx.reply("Translation service unavailable.")
            result = await r.json()
    translated = "".join([s[0] for s in result[0] if s[0]])
    embed = discord.Embed(title="Translation", color=0x2B2D31)
    embed.add_field(name="Original",   value=f"```{text[:500]}```",        inline=False)
    embed.add_field(name="Translated", value=f"```{translated[:500]}```",  inline=False)
    await ctx.reply(embed=embed)

# ============================================================
#                    PROFILE SYSTEM
# ============================================================
@bot.group(name="profile", invoke_without_command=True)
async def profile_group(ctx, member: discord.Member = None):
    member = member or ctx.author
    doc = await profiles_col.find_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
    bio  = doc.get("bio", "Not set.") if doc else "Not set."
    loc  = doc.get("location", "Unknown") if doc else "Unknown"
    embed = discord.Embed(title=f"{member.display_name}", color=0x2B2D31)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Bio",      value=f"*{bio}*",                           inline=False)
    embed.add_field(name="Location", value=loc,                                   inline=True)
    embed.add_field(name="Joined",   value=member.joined_at.strftime("%d %b %Y") if member.joined_at else "Unknown", inline=True)
    embed.add_field(name="Roles",    value=str(len(member.roles) - 1),            inline=True)
    embed.set_footer(text=f"ID: {member.id}")
    await ctx.reply(embed=embed)

@profile_group.command(name="bio")
async def profile_bio(ctx, *, text: str = None):
    if not text or len(text) > 150:
        return await ctx.reply("Provide a bio under 150 characters.")
    await profiles_col.update_one(
        {"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)},
        {"$set": {"bio": text}}, upsert=True
    )
    await ctx.reply("Bio updated.")

@profile_group.command(name="location", aliases=["loc"])
async def profile_location(ctx, *, city: str = None):
    if not city:
        return await ctx.reply("Provide a location.")
    await profiles_col.update_one(
        {"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)},
        {"$set": {"location": city}}, upsert=True
    )
    await ctx.reply(f"Location set to `{city}`.")

# ============================================================
#                  TICKET SYSTEM (Multi-purpose)
# ============================================================
TICKET_TYPES = {
    "help":       "General Help",
    "report":     "Report a User",
    "staff":      "Join the Staff",
    "event":      "Server Event",
}

class TicketSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        options = [
            discord.SelectOption(label=label, value=key, description=f"Open a ticket: {label}")
            for key, label in TICKET_TYPES.items()
        ]
        select = discord.ui.Select(
            placeholder="Select ticket type...",
            options=options,
            custom_id="ticket_type_select"
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        await _create_ticket(interaction, interaction.data["values"][0])

class TicketCreateView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.success, emoji="📩", custom_id="create_ticket_btn")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Select the type of ticket:",
            view=TicketSelectView(),
            ephemeral=True
        )

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="close_ticket_btn")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = await tickets_col.find_one({"channel_id": str(interaction.channel.id), "active": True})
        if not ticket:
            return await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)
        await interaction.response.send_message("Closing ticket in 5 seconds...")
        await asyncio.sleep(5)
        await tickets_col.delete_one({"channel_id": str(interaction.channel.id)})
        await interaction.channel.delete()

async def _create_ticket(interaction: discord.Interaction, ticket_type: str):
    guild  = interaction.guild
    member = interaction.user
    gdata  = await tickets_col.find_one({"_id": str(guild.id)}) or {}
    existing = await tickets_col.find_one({"guild_id": str(guild.id), "owner_id": str(member.id), "active": True})
    if existing:
        ch = guild.get_channel(int(existing["channel_id"]))
        if ch:
            return await interaction.followup.send(f"You already have a ticket: {ch.mention}", ephemeral=True)

    count = gdata.get("ticket_count", 0) + 1
    await tickets_col.update_one({"_id": str(guild.id)}, {"$set": {"ticket_count": count}}, upsert=True)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        member:             discord.PermissionOverwrite(read_messages=True, send_messages=True),
        guild.me:           discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }
    if gdata.get("staff_role_id"):
        staff = guild.get_role(int(gdata["staff_role_id"]))
        if staff:
            overwrites[staff] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    label = TICKET_TYPES.get(ticket_type, ticket_type.title())
    ch = await guild.create_text_channel(
        name=f"{ticket_type}-{count:04d}",
        overwrites=overwrites,
        topic=f"{label} | {member} (ID: {member.id})"
    )
    await tickets_col.insert_one({"guild_id": str(guild.id), "channel_id": str(ch.id), "owner_id": str(member.id), "active": True, "type": ticket_type})

    embed = discord.Embed(
        title=f"Ticket #{count:04d} — {label}",
        description=f"Welcome {member.mention}. Describe your issue and staff will assist you.\n\nUse the button below or `,ticket close` to close.",
        color=0x2B2D31
    )
    await ch.send(content=member.mention, embed=embed, view=TicketCloseView())
    try:
        await interaction.followup.send(f"Ticket opened: {ch.mention}", ephemeral=True)
    except:
        await interaction.response.send_message(f"Ticket opened: {ch.mention}", ephemeral=True)

@bot.group(name="ticket", invoke_without_command=True)
async def ticket_group(ctx):
    await ctx.reply("Sub-commands: `setup`, `close`, `add @user`, `remove @user`, `staffrole @role`")

@ticket_group.command(name="setup")
@commands.has_permissions(administrator=True)
async def ticket_setup(ctx):
    embed = discord.Embed(
        title="Support Tickets",
        description="Click the button below to open a support ticket.",
        color=0x2B2D31
    )
    embed.set_footer(text="Please only open tickets for valid reasons.")
    await ctx.send(embed=embed, view=TicketCreateView())
    await ctx.message.delete()

@ticket_group.command(name="close")
@ctx_mod_or_owner()
async def ticket_close(ctx):
    ticket = await tickets_col.find_one({"channel_id": str(ctx.channel.id), "active": True})
    if not ticket:
        return await ctx.reply("This is not an active ticket channel.")
    await ctx.reply("Closing in 5 seconds...")
    await asyncio.sleep(5)
    await tickets_col.delete_one({"channel_id": str(ctx.channel.id)})
    await ctx.channel.delete()

@ticket_group.command(name="add")
@ctx_mod_or_owner()
async def ticket_add(ctx, member: discord.Member = None):
    if not member:
        return await ctx.reply("Mention a member.")
    ticket = await tickets_col.find_one({"channel_id": str(ctx.channel.id), "active": True})
    if not ticket:
        return await ctx.reply("Not a ticket channel.")
    await ctx.channel.set_permissions(member, read_messages=True, send_messages=True)
    await ctx.reply(f"Added {member.mention} to this ticket.")

@ticket_group.command(name="remove")
@ctx_mod_or_owner()
async def ticket_remove(ctx, member: discord.Member = None):
    if not member:
        return await ctx.reply("Mention a member.")
    await ctx.channel.set_permissions(member, overwrite=None)
    await ctx.reply(f"Removed {member.mention} from this ticket.")

@ticket_group.command(name="staffrole")
@commands.has_permissions(administrator=True)
async def ticket_staffrole(ctx, role: discord.Role = None):
    if not role:
        return await ctx.reply("Mention a role.")
    await tickets_col.update_one({"_id": str(ctx.guild.id)}, {"$set": {"staff_role_id": str(role.id)}}, upsert=True)
    await ctx.reply(f"Staff role set to {role.mention}. They will have access to all new tickets.")

# ============================================================
#                  EMBED BUILDER
# ============================================================
@bot.group(name="embed", invoke_without_command=True)
@commands.has_permissions(administrator=True)
async def embed_group(ctx):
    await ctx.reply("Steps: `create` → `title` → `description` → `color` → `thumbnail` → `send`\nUsage: `,embed <subcommand>`")

@embed_group.command(name="create")
@commands.has_permissions(administrator=True)
async def emb_create(ctx):
    await embed_col.update_one(
        {"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)},
        {"$set": {"title": None, "description": "Default description.", "color": "2B2D31", "thumbnail": None}},
        upsert=True
    )
    await ctx.reply("Draft created. Use `,embed title/description/color/thumbnail/send` to customize.")

@embed_group.command(name="title")
@commands.has_permissions(administrator=True)
async def emb_title(ctx, *, text: str = None):
    if not text:
        return await ctx.reply("Provide a title.")
    draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
    if not draft:
        return await ctx.reply("No draft found. Run `,embed create` first.")
    await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"title": text}})
    await ctx.reply(f"Title set to `{text}`.")

@embed_group.command(name="description", aliases=["desc"])
@commands.has_permissions(administrator=True)
async def emb_desc(ctx, *, text: str = None):
    if not text:
        return await ctx.reply("Provide a description.")
    draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
    if not draft:
        return await ctx.reply("No draft found. Run `,embed create` first.")
    await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"description": text}})
    await ctx.reply("Description updated.")

@embed_group.command(name="color")
@commands.has_permissions(administrator=True)
async def emb_color(ctx, hex_code: str = None):
    if not hex_code:
        return await ctx.reply("Provide a hex code (e.g. #FF0000).")
    clean = hex_code.replace("#", "").strip()
    try:
        int(clean, 16)
    except:
        return await ctx.reply("Invalid hex code.")
    draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
    if not draft:
        return await ctx.reply("No draft found. Run `,embed create` first.")
    await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"color": clean}})
    await ctx.reply(f"Color set to `#{clean}`.")

@embed_group.command(name="thumbnail")
@commands.has_permissions(administrator=True)
async def emb_thumb(ctx, url: str = None):
    if not url or not url.startswith("http"):
        return await ctx.reply("Provide a valid image URL.")
    draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
    if not draft:
        return await ctx.reply("No draft found. Run `,embed create` first.")
    await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"thumbnail": url}})
    await ctx.reply("Thumbnail set.")

@embed_group.command(name="send")
@commands.has_permissions(administrator=True)
async def emb_send(ctx, channel: discord.TextChannel = None):
    channel = channel or ctx.channel
    draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
    if not draft:
        return await ctx.reply("No draft found. Run `,embed create` first.")
    try:
        color_int = int(draft.get("color", "2B2D31"), 16)
        final = discord.Embed(title=draft.get("title"), description=draft.get("description"), color=color_int)
        if draft.get("thumbnail"):
            final.set_thumbnail(url=draft["thumbnail"])
        await channel.send(embed=final)
        await embed_col.delete_one({"_id": draft["_id"]})
        await ctx.reply(f"Embed sent to {channel.mention}.")
    except Exception as e:
        await ctx.reply(f"Error: {e}")

# ============================================================
#                    MIMIC / ECHO
# ============================================================
@bot.command(name="mimic")
@ctx_mod_or_owner()
async def mimic(ctx, member: discord.Member = None, *, message: str = None):
    if not member or not message:
        return await ctx.reply("Usage: `,mimic @user message`")
    webhooks = await ctx.channel.webhooks()
    wh = discord.utils.get(webhooks, name="HappyMimic")
    if not wh:
        wh = await ctx.channel.create_webhook(name="HappyMimic")
    await wh.send(content=message, username=member.display_name, avatar_url=member.display_avatar.url)
    await ctx.message.delete()

@bot.command(name="echo")
@ctx_mod_or_owner()
async def echo(ctx, channel: discord.TextChannel = None, *, message: str = None):
    if not message:
        return await ctx.reply("Usage: `,echo #channel message` or `,echo message`")
    target = channel or ctx.channel
    await target.send(message)
    await ctx.message.delete()

# ============================================================
#                    MUTE SETUP
# ============================================================
@bot.command(name="setupmute")
@commands.has_permissions(administrator=True)
async def setup_mute(ctx):
    msg = await ctx.reply("Setting up mute roles...")
    guild = ctx.guild
    roles = {
        "Muted":          discord.Color.dark_grey(),
        "Image Muted":    discord.Color.blue(),
        "Reaction Muted": discord.Color.orange(),
    }
    created_roles = {}
    for name, color in roles.items():
        role = discord.utils.get(guild.roles, name=name) or await guild.create_role(name=name, color=color)
        created_roles[name] = role

    text_count = 0
    for ch in guild.channels:
        try:
            if isinstance(ch, discord.TextChannel):
                await ch.set_permissions(created_roles["Muted"],          send_messages=False, add_reactions=False)
                await ch.set_permissions(created_roles["Image Muted"],    attach_files=False,  embed_links=False)
                await ch.set_permissions(created_roles["Reaction Muted"], add_reactions=False)
                text_count += 1
            elif isinstance(ch, discord.VoiceChannel):
                await ch.set_permissions(created_roles["Muted"], speak=False, send_messages=False)
        except:
            pass

    await msg.edit(content=None, embed=discord.Embed(
        title="Mute System Ready",
        description=f"Roles created/verified. {text_count} channels configured.",
        color=0x2B2D31
    ))

# ============================================================
#                     AI MODE TOGGLE
# ============================================================
@bot.command(name="aimode")
@ctx_is_owner()
async def ai_mode(ctx, status: str = None):
    global ai_enabled
    if not status:
        return await ctx.reply(f"AI is currently **{'ON' if ai_enabled else 'OFF'}**.")
    ai_enabled = status.lower() in ("on", "true", "1")
    await ctx.reply(f"AI chat set to **{'ON' if ai_enabled else 'OFF'}**.")

@bot.command(name="maintenance")
@ctx_is_owner()
async def maintenance(ctx, status: str = None):
    global maintenance_mode
    if not status:
        return await ctx.reply(f"Maintenance mode: **{'ON' if maintenance_mode else 'OFF'}**.")
    maintenance_mode = status.lower() in ("on", "true", "1")
    await ctx.reply(f"Maintenance mode: **{'ON' if maintenance_mode else 'OFF'}**.")

# ============================================================
#                       HELP COMMAND
# ============================================================
@bot.command(name="help", aliases=["h"])
async def help_cmd(ctx, category: str = None):
    categories = {
        "prefix":      "prefix, prefix set, prefix remove, prefix self (Premium)",
        "mod":         "kick, ban, unban, mute, unmute, warn, warnings, clearwarns, softban, nickname, jail, unjail",
        "lock":        "lock, unlock, lockdown, unlockdown, vclock, vcunlock",
        "purge":       "purge [amount/bots/@user/links]",
        "roles":       "role add/remove, massrole, boosterrole, reactionrole, buttonrole (Premium)",
        "welcome":     "welcome set/enable/disable, setbye",
        "logs":        "logs set, logs disable",
        "tickets":     "ticket setup/close/add/remove/staffrole",
        "utility":     "userinfo, avatar, serverinfo, ping, membercount, shrug, translate, urban",
        "fun":         "ship, hot, hug, pat, slap, kiss, poke, highfive, bonk, cuddle",
        "profile":     "profile, profile bio, profile location",
        "levels":      "level, leaderboard",
        "birthday":    "birthday, birthday set",
        "embed":       "embed create/title/description/color/thumbnail/send",
        "premium":     "call, hangup, vcsetup, buttonrole, bumpreminder, setstatus, prefix self",
        "admin":       "settings, premiumrole, quicksetup, setupmute, jailsetup, counter, automod, announce, giveaway",
        "sticky":      "sticky, unsticky",
        "ai":          "@Happy or reply to Happy (Premium only)",
        "owner":       "premium add/remove/list, aimode, maintenance",
    }

    if category and category.lower() in categories:
        embed = discord.Embed(
            title=f"Help — {category.title()}",
            description=categories[category.lower()],
            color=0x2B2D31
        )
        await ctx.reply(embed=embed)
        return

    embed = discord.Embed(title="Happy — Command Help", color=0x2B2D31)
    embed.description = "Use `,help <category>` for details.\n"
    for cat, cmds in categories.items():
        embed.add_field(name=cat.title(), value=f"`,help {cat}`", inline=True)
    embed.set_footer(text="Premium features require Happy Premium. Contact the bot owner.")
    await ctx.reply(embed=embed)

# ============================================================
#                     VOICE LOCK (prefix)
# ============================================================
@bot.command(name="vclock", aliases=["vlock"])
@ctx_mod_or_owner()
async def vclock(ctx, channel: discord.VoiceChannel = None):
    if not channel:
        if ctx.author.voice:
            channel = ctx.author.voice.channel
        else:
            return await ctx.reply("Mention a voice channel or join one.")
    ow = channel.overwrites_for(ctx.guild.default_role)
    ow.connect = False
    await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
    await ctx.reply(f"**{channel.name}** locked.")

@bot.command(name="vcunlock", aliases=["vunlock"])
@ctx_mod_or_owner()
async def vcunlock(ctx, channel: discord.VoiceChannel = None):
    if not channel:
        if ctx.author.voice:
            channel = ctx.author.voice.channel
        else:
            return await ctx.reply("Mention a voice channel or join one.")
    ow = channel.overwrites_for(ctx.guild.default_role)
    ow.connect = None
    await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
    await ctx.reply(f"**{channel.name}** unlocked.")

# ============================================================
#              SLASH COMMANDS (key ones)
# ============================================================

@bot.tree.command(name="ping", description="Check bot latency")
async def slash_ping(interaction: discord.Interaction):
    lat = round(bot.latency * 1000)
    await interaction.response.send_message(
        embed=discord.Embed(description=f"Latency: **{lat}ms**", color=0x2B2D31),
        ephemeral=True
    )

@bot.tree.command(name="userinfo", description="Get info about a user")
async def slash_userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"{member}", color=0x2B2D31)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID",       value=member.id,                                                               inline=True)
    embed.add_field(name="Joined",   value=member.joined_at.strftime("%d %b %Y") if member.joined_at else "N/A",    inline=True)
    embed.add_field(name="Top Role", value=member.top_role.mention,                                                 inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="avatar", description="View a user's avatar")
async def slash_avatar(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"{member}'s Avatar", color=0x2B2D31)
    embed.set_image(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="kick", description="Kick a member")
@is_mod_or_owner()
async def slash_kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if interaction.user.id != BOT_OWNER_ID and interaction.user.top_role <= member.top_role:
        return await interaction.response.send_message("Cannot kick someone with equal or higher role.", ephemeral=True)
    await member.kick(reason=reason)
    await interaction.response.send_message(f"**{member}** kicked. Reason: {reason}", ephemeral=True)

@bot.tree.command(name="ban", description="Ban a member")
@is_mod_or_owner()
async def slash_ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await member.ban(reason=reason)
    await interaction.response.send_message(f"**{member}** banned.", ephemeral=True)

@bot.tree.command(name="warn", description="Warn a member")
@is_mod_or_owner()
async def slash_warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await interaction.response.defer()
    sid = str(interaction.guild.id)
    uid = str(member.id)
    doc = await warns_col.find_one({"server_id": sid, "user_id": uid})
    count = (doc["count"] + 1) if doc else 1
    await warns_col.update_one({"server_id": sid, "user_id": uid}, {"$set": {"count": count}}, upsert=True)
    embed = discord.Embed(title="Warning", color=0xff0000)
    embed.add_field(name="Member",   value=member.mention, inline=True)
    embed.add_field(name="Warnings", value=count,          inline=True)
    embed.add_field(name="Reason",   value=reason,         inline=False)
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="mute", description="Timeout a member")
@is_mod_or_owner()
async def slash_mute(interaction: discord.Interaction, member: discord.Member, minutes: int = 10, reason: str = "No reason"):
    await member.timeout(timedelta(minutes=minutes), reason=reason)
    await interaction.response.send_message(f"**{member}** muted for {minutes} min.", ephemeral=True)

@bot.tree.command(name="clear", description="Delete messages in bulk")
@is_mod_or_owner()
async def slash_clear(interaction: discord.Interaction, amount: int):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"Deleted {len(deleted)} messages.", ephemeral=True)

@bot.tree.command(name="announce", description="Send an announcement")
@is_mod_or_owner()
async def slash_announce(
    interaction: discord.Interaction,
    title: str,
    description: str,
    channel: discord.TextChannel = None,
    ping_everyone: bool = False
):
    target = channel or interaction.channel
    embed = discord.Embed(title=f"📢 {title}", description=description, color=0x2B2D31, timestamp=datetime.now())
    embed.set_footer(text=f"Announced by {interaction.user.display_name}")
    await target.send(content="@everyone" if ping_everyone else None, embed=embed)
    await interaction.response.send_message(f"Announcement sent to {target.mention}.", ephemeral=True)

@bot.tree.command(name="afk", description="Set your AFK status")
async def slash_afk(interaction: discord.Interaction, reason: str = "Away from keyboard"):
    await afk_col.update_one(
        {"user_id": interaction.user.id, "guild_id": interaction.guild.id},
        {"$set": {"reason": reason, "time": datetime.now(timezone.utc)}},
        upsert=True
    )
    await interaction.response.send_message(f"AFK status set. Reason: `{reason}`", ephemeral=True)

@bot.tree.command(name="level", description="Check your level or someone else's")
async def slash_level(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    doc = await levels_col.find_one({"guild_id": str(interaction.guild.id), "user_id": str(member.id)})
    if not doc:
        return await interaction.response.send_message(f"**{member}** has no XP yet.", ephemeral=True)
    lvl = doc.get("level", 0)
    xp  = doc.get("xp", 0)
    nxt = (lvl + 1) * 100
    bar = "█" * round((xp / nxt) * 10) + "░" * (10 - round((xp / nxt) * 10))
    embed = discord.Embed(title=f"Level — {member.display_name}", color=0x2B2D31)
    embed.add_field(name="Level",    value=lvl,            inline=True)
    embed.add_field(name="XP",       value=f"{xp}/{nxt}",  inline=True)
    embed.add_field(name="Progress", value=f"`{bar}`",      inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="help", description="View all Happy commands")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(title="Happy — Help", description="Use `,help` in chat for detailed command list by category.", color=0x2B2D31)
    embed.add_field(name="Prefix Commands",  value="Use `,help` for full command list",        inline=False)
    embed.add_field(name="Slash Commands",   value="Use `/` and browse commands directly",     inline=False)
    embed.add_field(name="Premium Features", value="AI Chat, Global Call, VoiceMaster, Button Roles, Bump Reminder", inline=False)
    embed.set_footer(text="Contact the bot owner to activate Premium.")
    await interaction.response.send_message(embed=embed)

# ============================================================
#                 START BACKGROUND TASKS
# ============================================================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    bot.add_view(TicketCreateView())
    bot.add_view(TicketCloseView())
    bot.add_view(ButtonRolesView())
    if not change_status.is_running():
        change_status.start()
    if not birthday_check.is_running():
        birthday_check.start()
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Sync error: {e}")

# ============================================================
#                         RUN
# ============================================================
if __name__ == "__main__":
    Thread(target=run).start()
    bot.run(TOKEN)
