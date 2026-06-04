"""
cogs/help.py — Permission-aware, category-based help system.
Shows only commands the user can actually run based on their role.
Dynamically fetches the server prefix.
"""

import discord
from discord.ext import commands
from discord import app_commands

from utils.db import settings_col
from utils.helpers import BOT_OWNER_ID, is_premium_user, is_premium_server

# ── Permission levels ─────────────────────────────────────────────────────────
PERM_ORDER  = {"everyone": 0, "mod": 1, "admin": 2, "owner": 3}
PERM_LABELS = {
    "everyone": "Member",
    "mod":      "Moderator",
    "admin":    "Administrator",
    "owner":    "Bot Owner",
}
PERM_COLORS = {
    "everyone": 0x2B2D31,
    "mod":      0x5865F2,
    "admin":    0xED4245,
    "owner":    0xffd700,
}


def _user_level(member: discord.Member, owner_id: int) -> str:
    if member.id == owner_id:
        return "owner"
    if member.guild_permissions.administrator:
        return "admin"
    p = member.guild_permissions
    if p.manage_messages or p.kick_members or p.manage_roles or p.ban_members:
        return "mod"
    return "everyone"


# ══════════════════════════════════════════════════════════════════════════════
#  COMPLETE COMMAND REGISTRY
#  Format: (syntax, description, permission_level, is_premium)
# ══════════════════════════════════════════════════════════════════════════════

# category → list of (syntax, description, perm, premium)
REGISTRY = {

    # ── FUN ───────────────────────────────────────────────────────────────────
    "fun": [
        ("ship @user1 @user2",  "Love compatibility between two members",        "everyone", False),
        ("hot [@user]",         "Hotness meter (consistent per user)",           "everyone", False),
        ("8ball <question>",    "Ask the magic 8-ball",                          "everyone", False),
        ("coinflip",            "Flip a coin",                                   "everyone", False),
        ("dice [sides]",        "Roll a dice — default d6, up to d1000",         "everyone", False),
        ("wouldyourather",      "Random would-you-rather question with vote",    "everyone", False),
        ("roast [@user]",       "Send a gentle roast",                           "everyone", False),
        ("praise [@user]",      "Send a compliment",                             "everyone", False),
        ("shrug [text]",        "Post a shrug — ¯\\_(ツ)_/¯",                   "everyone", False),
    ],

    # ── ROLEPLAY ──────────────────────────────────────────────────────────────
    "roleplay": [
        ("hug [@user]",         "Hug someone",                                   "everyone", False),
        ("pat [@user]",         "Pat someone on the head",                       "everyone", False),
        ("slap [@user]",        "Slap someone",                                  "everyone", False),
        ("kiss [@user]",        "Give someone a kiss",                           "everyone", False),
        ("poke [@user]",        "Poke someone repeatedly",                       "everyone", False),
        ("highfive [@user]",    "High-five someone",                             "everyone", False),
        ("bonk [@user]",        "Bonk someone with the hammer",                  "everyone", False),
        ("cuddle [@user]",      "Cuddle up with someone",                        "everyone", False),
        ("boop [@user]",        "Boop someone's nose",                           "everyone", False),
        ("wave [@user]",        "Wave at someone",                               "everyone", False),
        ("stare [@user]",       "Stare intensely at someone",                    "everyone", False),
    ],

    # ── UTILITY ───────────────────────────────────────────────────────────────
    "utility": [
        ("ping",                "Check bot latency",                             "everyone", False),
        ("userinfo [@user]",    "View detailed info about a member",             "everyone", False),
        ("avatar [@user]",      "View a member's avatar in full size",           "everyone", False),
        ("serverinfo",          "View server stats (members, roles, boosts)",    "everyone", False),
        ("membercount",         "Member, human, and bot count",                  "everyone", False),
        ("translate <lang> <text>","Translate text to any language",             "everyone", False),
        ("urban <word>",        "Urban Dictionary definition lookup",            "everyone", False),
        ("afk [reason]",        "Set AFK status — bot notifies when you're pinged","everyone", False),
        ("mimic @user <msg>",   "Send a message as another member via webhook",  "mod",      False),
        ("echo [#channel] <msg>","Send a message as the bot",                   "mod",      False),
    ],

    # ── PROFILE ───────────────────────────────────────────────────────────────
    "profile": [
        ("profile [@user]",     "View your or someone's profile card",           "everyone", False),
        ("profile bio <text>",  "Set your profile bio (max 150 chars)",          "everyone", False),
        ("profile location <city>","Set your location on your profile",          "everyone", False),
        ("birthday [@user]",    "View a member's birthday",                      "everyone", False),
        ("birthday set DD/MM",  "Set your birthday — bot wishes you on the day", "everyone", False),
    ],

    # ── LEVELS ────────────────────────────────────────────────────────────────
    "levels": [
        ("level [@user]",       "View XP, level, and progress bar",              "everyone", False),
        ("leaderboard",         "Top 10 most active members by level",           "everyone", False),
        ("setlevel @user <n>",  "Manually set a member's level",                 "admin",    False),
        ("resetxp [@user]",     "Reset XP for one member or the whole server",   "admin",    False),
    ],

    # ── MODERATION ────────────────────────────────────────────────────────────
    "mod": [
        ("kick @user [reason]",         "Kick a member — DMs them first",               "mod",   False),
        ("ban @user [reason]",          "Permanently ban a member",                     "mod",   False),
        ("tempban @user <dur> [reason]","Temporarily ban — auto-unbans (10m/2h/7d)",    "mod",   False),
        ("unban <user_id> [reason]",    "Unban a user by ID",                           "mod",   False),
        ("mute @user [dur] [reason]",   "Timeout a member (10m/2h/7d, max 28d)",        "mod",   False),
        ("unmute @user",                "Remove a member's timeout",                    "mod",   False),
        ("warn @user [reason]",         "Warn a member — tracked in database",          "mod",   False),
        ("warnings [@user]",            "View warning count for a member",              "mod",   False),
        ("clearwarns @user",            "Clear all warnings for a member",              "mod",   False),
        ("softban @user [reason]",      "Ban + unban — clears 7 days of messages",      "mod",   False),
        ("nickname @user [name]",       "Change or reset a member's nickname",          "mod",   False),
        ("note @user",                  "View private mod notes for a member",          "mod",   False),
        ("note add @user <text>",       "Add a private mod note",                       "mod",   False),
        ("note clear @user",            "Clear all mod notes for a member",             "mod",   False),
        ("cases @user [limit]",         "View case history for a member",               "mod",   False),
        ("case <number>",               "View a specific case by number",               "mod",   False),
        ("purge <amount>",              "Delete recent messages",                        "mod",   False),
        ("purge bots <amount>",         "Delete bot messages only",                     "mod",   False),
        ("purge @user <amount>",        "Delete a specific user's messages",            "mod",   False),
        ("purge links <amount>",        "Delete messages containing links",             "mod",   False),
        ("purge images <amount>",       "Delete messages with attachments",             "mod",   False),
        ("purge embeds <amount>",       "Delete messages with embeds",                  "mod",   False),
        ("lock [#channel] [reason]",    "Lock a text/voice/thread/forum channel",       "mod",   False),
        ("unlock [#channel]",           "Unlock a channel",                             "mod",   False),
        ("vclock [#vc]",                "Lock a voice channel (prevents joins)",        "mod",   False),
        ("vcunlock [#vc]",              "Unlock a voice channel",                       "mod",   False),
        ("lockdown [reason]",           "Lock ALL channels — emergency use",            "admin", False),
        ("unlockdown",                  "Lift the server lockdown",                     "admin", False),
        ("jail @user [reason]",         "Strip roles and move member to jail channel",  "mod",   False),
        ("unjail @user",                "Release member from jail and restore roles",   "mod",   False),
        ("sticky <text>",               "Pin a sticky message that re-appears",         "mod",   False),
        ("unsticky",                    "Remove sticky message from this channel",      "mod",   False),
        ("slowmode <seconds> [#ch]",    "Set channel slowmode (0 to disable)",          "mod",   False),
        ("topic [text]",                "Set or clear the channel topic",               "mod",   False),
        ("rename <name>",               "Rename the current channel",                   "mod",   False),
    ],

    # ── ANTISPAM ──────────────────────────────────────────────────────────────
    "antispam": [
        ("antispam",                    "View anti-spam configuration",                 "admin", False),
        ("antispam enable",             "Enable the anti-spam system",                  "admin", False),
        ("antispam disable",            "Disable the anti-spam system",                 "admin", False),
        ("antispam set <n> <action>",   "Set threshold + action (mute/kick/ban)",       "admin", False),
    ],

    # ── ROLES ─────────────────────────────────────────────────────────────────
    "roles": [
        ("role add @user @role",        "Add a role to a member",                       "admin", False),
        ("role remove @user @role",     "Remove a role from a member",                  "admin", False),
        ("massrole add everyone @role", "Give a role to all members",                   "admin", False),
        ("massrole add bots @role",     "Give a role to all bots",                      "admin", False),
        ("massrole remove everyone @role","Remove a role from all members",             "admin", False),
        ("reactionrole add <link> <emoji> @role","Set up a reaction role",             "admin", False),
        ("buttonrole @role Label | ...", "Create a button role panel",                 "admin", True),
        ("boosterrole @role",           "Set a reward role for server boosters",        "admin", False),
    ],

    # ── TICKETS ───────────────────────────────────────────────────────────────
    "tickets": [
        ("ticket setup",                "Send the ticket creation panel",               "admin", False),
        ("ticket staffrole @role",      "Set which role can see all tickets",           "admin", False),
        ("ticket close",                "Close and delete this ticket channel",         "mod",   False),
        ("ticket add @user",            "Add a user to this ticket",                    "mod",   False),
        ("ticket remove @user",         "Remove a user from this ticket",               "mod",   False),
    ],

    # ── SERVER SETUP ──────────────────────────────────────────────────────────
    "setup": [
        ("quicksetup",                  "Auto-create channels, categories, and roles",  "admin", False),
        ("jailsetup",                   "Create Jailed role + private jail channel",    "admin", False),
        ("setupmute",                   "Create Muted/Image Muted/Reaction Muted roles","admin", False),
        ("welcome set #channel",        "Set the welcome message channel",              "admin", False),
        ("welcome enable/disable",      "Turn welcome messages on or off",              "admin", False),
        ("welcome test",                "Preview the welcome message",                  "admin", False),
        ("bye set #channel",            "Set the bye message channel",                  "admin", False),
        ("bye enable/disable",          "Turn bye messages on or off",                  "admin", False),
        ("bye test",                    "Preview the bye message",                      "admin", False),
        ("logs set #channel",           "Set the moderation log channel",               "admin", False),
        ("logs disable",                "Disable logging",                              "admin", False),
        ("automod invite on/off",       "Block Discord invite links server-wide",       "admin", False),
        ("counter create <type> #vc",   "Live counter in a VC name (members/bots/channels)","admin", False),
    ],

    # ── ADMIN ─────────────────────────────────────────────────────────────────
    "admin": [
        ("settings",                    "Full server configuration dashboard",          "admin", False),
        ("prefix",                      "View prefix info",                             "everyone", False),
        ("prefix set <symbol>",         "Change server prefix (max 3 chars)",           "admin", False),
        ("prefix remove",               "Reset server prefix to default ,",             "admin", False),
        ("prefix self <symbol>",        "Personal prefix across all servers",           "everyone", True),
        ("prefix selfremove",           "Remove your personal prefix",                  "everyone", False),
        ("premiumrole @role",           "Set the role that grants premium AI access",   "admin", False),
        ("disable <command>",           "Disable a command for this server",            "admin", False),
        ("enable <command>",            "Re-enable a disabled command",                 "admin", False),
        ("announce [#ch] <message>",    "Send an announcement embed (add --ping for @everyone)","mod",False),
        ("giveaway <dur> <winners> <prize>","Start a giveaway (30m/2h/1d)",           "mod",   False),
        ("giveaway end <msg_id>",       "End a giveaway early",                         "mod",   False),
        ("giveaway reroll <msg_id>",    "Reroll winners for an ended giveaway",         "mod",   False),
        ("embed create",                "Start a new embed draft",                      "admin", False),
        ("embed title/description/color/thumbnail","Edit your embed draft fields",      "admin", False),
        ("embed send [#channel]",       "Send the finished embed",                      "admin", False),
    ],

    # ── PREMIUM ───────────────────────────────────────────────────────────────
    "premium": [
        ("mypremium",                   "Check your premium status and unlocked features",   "everyone", False),
        ("call",                        "Connect this channel to another server live",        "admin",    True),
        ("callinfo",                    "View current call status and queue position",        "everyone", False),
        ("hangup",                      "End the cross-server call or leave the queue",       "mod",      False),
        ("vcsetup",                     "View VoiceMaster config and status",                 "admin",    True),
        ("vcsetup create",              "Create the 'Join to Create' trigger channel",        "admin",    True),
        ("vcsetup limit <n>",           "Set default user limit for temp VCs (0 = unlimited)","admin",   True),
        ("vcsetup name <template>",     "Set VC name template — use {user} for member name",  "admin",    True),
        ("vcsetup remove",              "Remove VoiceMaster and delete all temp VCs",         "admin",    True),
        ("bumpreminder on/off",         "DISBOARD bump reminder — pings 2h after bump",       "admin",    True),
        ("setstatus <text>",            "Add custom text to bot's rotating status pool",      "admin",    True),
        ("prefix self <symbol>",        "Personal prefix that works in all servers",          "everyone", True),
        ("prefix selfremove",           "Remove your personal prefix",                        "everyone", False),
    ],


    # ── GAMES ─────────────────────────────────────────────────────────────────
    "games": [
        ("numguess start [max]",        "Start a number guessing game (default 1-100)",      "everyone", False),
        ("numguess stop",               "End the current number guess game",                 "mod",      False),
        ("counting setup #channel",     "Set up the counting channel",                       "mod",      False),
        ("counting reset",              "Reset the count back to 0",                         "mod",      False),
        ("counting stats",              "View counting high score",                          "everyone", False),
        ("wordguess start",             "Start a word guessing game (hangman-style)",        "everyone", False),
        ("wordguess stop",              "End the current word guess game",                   "mod",      False),
    ],

    # ── ECONOMY ───────────────────────────────────────────────────────────────
    "economy": [
        ("balance [@user]",             "Check your global HC balance (wallet + bank)",      "everyone", False),
        ("daily",                       "Claim 150-350 HC every 24 hours",                   "everyone", False),
        ("work",                        "Earn 50-150 HC every hour (activity boosts pay)",   "everyone", False),
        ("deposit <amount/all>",        "Move HC from wallet to bank",                       "everyone", False),
        ("withdraw <amount/all>",       "Move HC from bank to wallet",                       "everyone", False),
        ("transfer @user <amount>",     "Send HC to another user (global)",                  "everyone", False),
        ("coinflip <amount> [h/t]",     "Gamble with a coinflip — 50/50",                   "everyone", False),
        ("slots <amount>",              "Spin the slot machine — match 3 for jackpot",       "everyone", False),
        ("rob @user",                   "Attempt to rob someone's wallet (45% success)",     "everyone", False),
        ("richlist",                    "Top 10 richest HC holders globally",                "everyone", False),
        ("trade offer @user <off> <want>","Send a trade offer (locks your HC)",              "everyone", False),
        ("trade accept/decline <id>",   "Accept or decline a trade offer",                   "everyone", False),
        ("trade cancel <id>",           "Cancel your own pending trade",                     "everyone", False),
        ("trade list",                  "View your pending trades",                          "everyone", False),
        ("serverstatus",                "Check server activity and work earnings multiplier","everyone", False),
        ("givecash @user <n>",          "Give HC to a user — Bot Owner only",               "owner",    False),
        ("takecash @user <n>",          "Remove HC from a user — Bot Owner only",           "owner",    False),
        ("resetcash @user",             "Reset a user's balance — Bot Owner only",          "owner",    False),
    ],

    # ── INVEST ────────────────────────────────────────────────────────────────
    "invest": [
        ("market",                      "View all stocks with current prices and trends",    "everyone", False),
        ("stock <SYMBOL>",              "Detailed stock info — price, chart, 24h high/low",  "everyone", False),
        ("buy <SYMBOL> <qty>",          "Buy shares of a stock using HC",                   "everyone", False),
        ("sell <SYMBOL> <qty/all>",     "Sell shares — shows profit or loss",               "everyone", False),
        ("portfolio [@user]",           "View holdings, value, and total P&L",              "everyone", False),
        ("investlist",                  "Top 10 investors by portfolio value",               "everyone", False),
        ("setprice <SYMBOL> <price>",   "Manually set a stock price",                       "owner",    False),
        ("settrend <SYMBOL> bull/bear", "Force a stock trend direction",                    "owner",    False),
        ("addstock <SYMBOL> <price> <name>","Add a new custom stock to the market",         "owner",    False),
    ],

    # ── ECONOMY ───────────────────────────────────────────────────────────────
    "economy": [
        ("balance [@user]",             "Check your global HC balance (wallet + bank)",     "everyone", False),
        ("daily",                       "Claim daily reward — 150 to 350 HC (24h cooldown)","everyone", False),
        ("work",                        "Work for HC — boosted by server activity (1h cd)",  "everyone", False),
        ("deposit <amount/all>",        "Move HC from wallet to bank",                       "everyone", False),
        ("withdraw <amount/all>",       "Move HC from bank to wallet",                       "everyone", False),
        ("transfer @user <amount>",     "Send HC to someone globally",                       "everyone", False),
        ("coinflip <amount> [h/t]",     "Bet HC on a coin flip — alias: cf",                "everyone", False),
        ("slots <amount>",              "Spin the slot machine — match 3 for jackpot",       "everyone", False),
        ("rob @user",                   "Try to rob someone — 45% success, fine on fail",    "everyone", False),
        ("richlist",                    "Top 10 richest HC holders globally",                "everyone", False),
        ("trade offer @user <off> <want>","Send a trade offer — locks your HC instantly",   "everyone", False),
        ("trade accept/decline <id>",   "Accept or decline a pending trade",                 "everyone", False),
        ("trade cancel <id>",           "Cancel your own pending trade offer",               "everyone", False),
        ("trade list",                  "See all your pending trades",                       "everyone", False),
        ("givecash @user <amount>",     "Give HC to a user — Owner only",                   "owner",    False),
        ("takecash @user <amount>",     "Remove HC from a user — Owner only",               "owner",    False),
        ("resetcash @user",             "Reset a user's full balance — Owner only",          "owner",    False),
    ],

    # ── INVEST ────────────────────────────────────────────────────────────────
    "invest": [
        ("market",                      "View all stocks with prices and trends",            "everyone", False),
        ("stock <SYMBOL>",              "Detailed stock info — price, chart, high/low",      "everyone", False),
        ("buy <SYMBOL> <qty>",          "Buy shares of a stock with HC",                     "everyone", False),
        ("sell <SYMBOL> <qty/all>",     "Sell shares — shows exact profit/loss",             "everyone", False),
        ("portfolio [@user]",           "View holdings, value, and total P&L",               "everyone", False),
        ("investlist",                  "Top 10 investors by portfolio value",               "everyone", False),
        ("serverstatus",                "Server activity level and work multiplier",         "everyone", False),
        ("setprice <SYMBOL> <price>",   "Manually set a stock price — Owner only",           "owner",    False),
        ("settrend <SYMBOL> bull/bear", "Set a stock trend — Owner only",                   "owner",    False),
        ("addstock <SYM> <price> <name>","Add a custom stock — Owner only",                 "owner",    False),
    ],

    # ── GAMES ─────────────────────────────────────────────────────────────────
    "games": [
        ("numguess start [max]",        "Start a number guessing game (default 1-100)",      "everyone", False),
        ("numguess stop",               "End the current number guess game",                 "mod",      False),
        ("counting setup #channel",     "Set up the counting channel",                       "mod",      False),
        ("counting reset",              "Reset the count to 0",                              "mod",      False),
        ("counting stats",              "View current count and high score",                 "everyone", False),
        ("wordguess start",             "Start a word guessing game (Hangman-style)",        "everyone", False),
        ("wordguess stop",              "End the current word guess game",                   "mod",      False),
    ],

    # ── ECONOMY ───────────────────────────────────────────────────────────────
    "economy": [
        ("balance [@user]",          "View global wallet + bank balance",                "everyone", False),
        ("daily",                    "Claim daily reward (150–350 HC, resets 24h)",     "everyone", False),
        ("work",                     "Work for HC — boosted by server activity",         "everyone", False),
        ("deposit <amount/all>",     "Move HC from wallet to bank",                     "everyone", False),
        ("withdraw <amount/all>",    "Move HC from bank to wallet",                     "everyone", False),
        ("transfer @user <amount>",  "Send HC to another user (global)",                "everyone", False),
        ("coinflip <amount> [h/t]",  "Gamble with a coinflip — 50/50",                 "everyone", False),
        ("slots <amount>",           "Spin the slot machine — match 3 for jackpot",     "everyone", False),
        ("rob @user",                "Try to rob someone — 45% success rate",           "everyone", False),
        ("richlist",                 "Top 10 richest HC holders globally",              "everyone", False),
        ("serverstatus",             "Server activity level and work multiplier",        "everyone", False),
        ("trade offer @user <o> <w>","Send a trade offer (offer HC, want HC)",         "everyone", False),
        ("trade accept/decline <id>","Accept or decline a trade",                       "everyone", False),
        ("trade cancel <id>",        "Cancel your own pending trade",                   "everyone", False),
        ("trade list",               "See all your pending trades",                     "everyone", False),
        ("givecash @user <n>",       "Give HC to a user — Bot Owner only",             "owner",    False),
        ("takecash @user <n>",       "Remove HC from a user — Bot Owner only",         "owner",    False),
        ("resetcash @user",          "Reset a user balance — Bot Owner only",          "owner",    False),
    ],

    # ── INVEST ────────────────────────────────────────────────────────────────
    "invest": [
        ("market",                   "View all stocks with live prices and trends",     "everyone", False),
        ("stock <SYMBOL>",           "Detailed stock info + mini price chart",          "everyone", False),
        ("buy <SYMBOL> <qty>",       "Buy shares of a stock using HC",                 "everyone", False),
        ("sell <SYMBOL> <qty/all>",  "Sell shares and see profit/loss",                "everyone", False),
        ("portfolio [@user]",        "Your holdings, current value, total P&L",        "everyone", False),
        ("investlist",               "Top 10 investors by portfolio value",            "everyone", False),
        ("setprice <SYM> <price>",   "Manually set a stock price — Owner only",        "owner",    False),
        ("settrend <SYM> bull/bear",  "Set a stock trend — Owner only",                "owner",    False),
        ("addstock <SYM> <p> <name>","Add a new stock to the market — Owner only",    "owner",    False),
    ],

    # ── GAMES ─────────────────────────────────────────────────────────────────
    "games": [
        ("numguess start [max]",     "Start a number guessing game",                   "everyone", False),
        ("numguess stop",            "End the current number guess game",              "mod",      False),
        ("counting setup #channel",  "Set up the counting channel",                    "mod",      False),
        ("counting stats",           "View counting high score",                        "everyone", False),
        ("counting reset",           "Reset the count to 0",                           "mod",      False),
        ("wordguess start",          "Start a word guessing game (Hangman-style)",     "everyone", False),
        ("wordguess stop",           "End the current word guess game",                "mod",      False),
    ],
    # ── OWNER ─────────────────────────────────────────────────────────────────
    "owner": [
        ("premium add server <id>",     "Activate premium for a server",                "owner", False),
        ("premium add user <id>",       "Activate premium for a user",                  "owner", False),
        ("premium remove server/user <id>","Remove premium access",                    "owner", False),
        ("premium list",                "List all active premium entries",               "owner", False),
        ("aimode on/off",               "Toggle AI chat globally across all servers",   "owner", False),
        ("maintenance on/off",          "Toggle maintenance mode",                       "owner", False),
        ("botstatus <type> <text>",     "Override bot status (watching/playing/listening)","owner",False),
        ("serverlist",                  "List all servers the bot is in",               "owner", False),
        ("leaveguild <guild_id>",       "Force the bot to leave a server",              "owner", False),
        ("dm @user <message>",          "Send a DM to any user as the bot",             "owner", False),
        ("sync [guild_id]",             "Sync slash commands globally or to one guild", "owner", False),
    ],
}

# Flat list for search
ALL_COMMANDS = [
    (cat, syn, desc, perm, prem)
    for cat, entries in REGISTRY.items()
    for syn, desc, perm, prem in entries
]

# ── Category metadata ──────────────────────────────────────────────────────────
CAT_META = {
    "fun":      ("Fun",         "Ship, 8ball, dice, coinflip, roast, praise"),
    "roleplay": ("Roleplay",    "Hug, pat, slap, kiss, bonk, cuddle, wave, stare..."),
    "utility":  ("Utility",     "Ping, userinfo, avatar, translate, urban, AFK"),
    "profile":  ("Profile",     "Profile card, bio, location, birthday"),
    "levels":   ("Levels",      "XP system, level, leaderboard"),
    "mod":      ("Moderation",  "Kick, ban, mute, warn, lock, purge, jail, slowmode"),
    "antispam": ("Anti-Spam",   "Auto-detect and punish spam floods"),
    "roles":    ("Roles",       "Reaction roles, button roles, booster reward roles"),
    "tickets":  ("Tickets",     "Multi-purpose support ticket system"),
    "setup":    ("Server Setup","Welcome, bye, logs, automod, counters, quicksetup"),
    "admin":    ("Admin",       "Settings, prefix, announcements, giveaways, embeds"),
    "premium":  ("Premium",     "Global call, VoiceMaster, bump reminder, custom status"),
    "economy":  ("Economy",     "Balance, daily, work, slots, rob, trade, richlist"),
    "invest":   ("Invest",      "Stock market, buy/sell, portfolio, P&L tracking"),
    "games":    ("Games",       "Number guess, counting channel, word guess (Hangman)"),
    "owner":    ("Owner",       "Premium management, AI toggle, server tools"),
}


class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _get_prefix(self, ctx) -> str:
        """Fetch the actual server prefix."""
        try:
            data = await settings_col.find_one({"_id": str(ctx.guild.id)})
            return data.get("prefix", ",") if data else ","
        except:
            return ","

    # ══════════════════════════════════════════════════════════════════════════
    #  MAIN HELP COMMAND
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["h", "commands", "cmds"])
    async def help(self, ctx, *, query: str = None):
        """
        Permission-aware help system.
        Usage:
          ,help                — main menu
          ,help <category>     — category command list
          ,help <command>      — search for a specific command
        Categories: fun roleplay utility profile levels mod antispam
                    roles tickets setup admin premium owner
        """
        pfx        = await self._get_prefix(ctx)
        user_level = _user_level(ctx.author, BOT_OWNER_ID)
        is_prem    = (
            ctx.author.id == BOT_OWNER_ID or
            await is_premium_user(ctx.author.id) or
            await is_premium_server(ctx.guild.id)
        )

        # ── Category view ─────────────────────────────────────────────────────
        if query and query.lower() in REGISTRY:
            await self._send_category(ctx, query.lower(), pfx, user_level, is_prem)
            return

        # ── Command search ────────────────────────────────────────────────────
        if query:
            await self._send_search(ctx, query.lower(), pfx, user_level, is_prem)
            return

        # ── Main menu ─────────────────────────────────────────────────────────
        await self._send_main(ctx, pfx, user_level, is_prem)

    # ── Main menu embed ────────────────────────────────────────────────────────
    async def _send_main(self, ctx, pfx, user_level, is_prem):
        color = PERM_COLORS[user_level]
        embed = discord.Embed(
            title="Happy — Command Help",
            color=color
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        # Status line
        prem_tag = " · **Premium**" if is_prem else ""
        embed.description = (
            f"Your level: **{PERM_LABELS[user_level]}**{prem_tag}\n"
            f"Prefix: `{pfx}` · Mention: {self.bot.user.mention}\n\n"
            f"Use `{pfx}help <category>` to see commands.\n"
            f"Use `{pfx}help <command>` to search.\n"
        )

        # Show categories filtered by permission
        shown = []
        for cat, (label, desc) in CAT_META.items():
            # Always show if user has enough perm for at least one command in category
            entries = REGISTRY[cat]
            accessible = [
                e for e in entries
                if PERM_ORDER[e[2]] <= PERM_ORDER[user_level]
                and (not e[3] or is_prem)
            ]
            if not accessible:
                continue
            shown.append((cat, label, desc, len(accessible)))

        # Two-column layout using inline fields
        for cat, label, desc, count in shown:
            embed.add_field(
                name=f"`{pfx}help {cat}` — {label}",
                value=f"{desc}\n*{count} command(s)*",
                inline=True
            )

        # Pad to even number for Discord 2-col alignment
        if len(shown) % 2 == 1:
            embed.add_field(name="\u200b", value="\u200b", inline=True)

        # AI chat notice
        if is_prem:
            ai_val = f"Mention {self.bot.user.mention} or reply to chat. **Active for you.**"
        else:
            ai_val = f"Mention {self.bot.user.mention} — **Premium only**. Contact the bot owner."
        embed.add_field(name="AI Chat", value=ai_val, inline=False)

        embed.set_footer(text=f"Happy Premium unlocks AI chat, global call, VoiceMaster & more.")
        await ctx.reply(embed=embed)

    # ── Category embed ─────────────────────────────────────────────────────────
    async def _send_category(self, ctx, cat, pfx, user_level, is_prem):
        label, desc = CAT_META[cat]
        entries     = REGISTRY[cat]
        color       = PERM_COLORS.get(user_level, 0x2B2D31)

        # Filter by permission and premium
        visible = [
            (syn, d, perm, prem)
            for syn, d, perm, prem in entries
            if PERM_ORDER[perm] <= PERM_ORDER[user_level]
            and (not prem or is_prem)
        ]

        embed = discord.Embed(
            title=f"Help — {label}",
            color=color
        )

        if not visible:
            embed.description = (
                "No commands available for your permission level in this category.\n"
                + ("Some commands require **Premium**." if any(e[3] for e in entries) else "")
            )
        else:
            lines = []
            for syn, d, perm, prem in visible:
                prem_tag = " `✦ Premium`" if prem else ""
                perm_tag = ""
                if perm == "mod":
                    perm_tag = " `mod`"
                elif perm == "admin":
                    perm_tag = " `admin`"
                elif perm == "owner":
                    perm_tag = " `owner`"
                lines.append(f"`{pfx}{syn}`{prem_tag}{perm_tag}\n{d}")
            embed.description = "\n\n".join(lines)

        # Show locked commands if any hidden
        locked = [e for e in entries if PERM_ORDER[e[2]] > PERM_ORDER[user_level] or (e[3] and not is_prem)]
        if locked:
            lock_lines = []
            for syn, d, perm, prem in locked:
                reason = "Premium" if (prem and not is_prem) else PERM_LABELS[perm]
                lock_lines.append(f"`{pfx}{syn}` — *requires {reason}*")
            embed.add_field(
                name=f"Locked ({len(locked)})",
                value="\n".join(lock_lines[:8]) + ("..." if len(lock_lines) > 8 else ""),
                inline=False
            )

        embed.set_footer(
            text=f"Your level: {PERM_LABELS[user_level]} · {pfx}help for full menu"
        )
        await ctx.reply(embed=embed)

    # ── Search embed ───────────────────────────────────────────────────────────
    async def _send_search(self, ctx, query, pfx, user_level, is_prem):
        results = [
            (cat, syn, desc, perm, prem)
            for cat, syn, desc, perm, prem in ALL_COMMANDS
            if query in syn.lower() or query in desc.lower()
        ]

        embed = discord.Embed(
            title=f"Search — \"{query}\"",
            color=PERM_COLORS[user_level]
        )

        if not results:
            embed.description = (
                f"No commands found matching `{query}`.\n\n"
                f"Try `{pfx}help` to browse all categories."
            )
            return await ctx.reply(embed=embed)

        lines = []
        for cat, syn, desc, perm, prem in results[:12]:  # cap at 12
            accessible = (
                PERM_ORDER[perm] <= PERM_ORDER[user_level]
                and (not prem or is_prem)
            )
            if accessible:
                prem_tag = " `✦`" if prem else ""
                lines.append(f"`{pfx}{syn}`{prem_tag} — {desc}")
            else:
                reason = "Premium" if (prem and not is_prem) else PERM_LABELS[perm]
                lines.append(f"~~`{pfx}{syn}`~~ — *requires {reason}*")

        embed.description = "\n".join(lines)
        if len(results) > 12:
            embed.description += f"\n\n*...and {len(results)-12} more. Use `{pfx}help <category>` to browse.*"

        embed.set_footer(text=f"{pfx}help <category> for full lists")
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  SLASH HELP
    # ══════════════════════════════════════════════════════════════════════════

    @app_commands.command(name="help", description="View Happy's command list")
    @app_commands.describe(category="Browse a specific category (optional)")
    @app_commands.choices(category=[
        app_commands.Choice(name="Fun & Games",     value="fun"),
        app_commands.Choice(name="Roleplay",        value="roleplay"),
        app_commands.Choice(name="Utility",         value="utility"),
        app_commands.Choice(name="Profile",         value="profile"),
        app_commands.Choice(name="Levels & XP",     value="levels"),
        app_commands.Choice(name="Moderation",      value="mod"),
        app_commands.Choice(name="Anti-Spam",       value="antispam"),
        app_commands.Choice(name="Roles",           value="roles"),
        app_commands.Choice(name="Tickets",         value="tickets"),
        app_commands.Choice(name="Server Setup",    value="setup"),
        app_commands.Choice(name="Admin",           value="admin"),
        app_commands.Choice(name="Premium",         value="premium"),
    ])
    async def slash_help(
        self,
        interaction: discord.Interaction,
        category: str = None
    ):
        await interaction.response.defer(ephemeral=True)

        user_level = _user_level(interaction.user, BOT_OWNER_ID)
        is_prem    = (
            interaction.user.id == BOT_OWNER_ID or
            await is_premium_user(interaction.user.id) or
            await is_premium_server(interaction.guild.id)
        )

        # Get prefix
        try:
            data = await settings_col.find_one({"_id": str(interaction.guild.id)})
            pfx  = data.get("prefix", ",") if data else ","
        except:
            pfx = ","

        if category and category in REGISTRY:
            embed = discord.Embed(
                title=f"Help — {CAT_META[category][0]}",
                color=PERM_COLORS[user_level]
            )
            entries = REGISTRY[category]
            visible = [
                e for e in entries
                if PERM_ORDER[e[2]] <= PERM_ORDER[user_level]
                and (not e[3] or is_prem)
            ]
            if visible:
                lines = []
                for syn, d, perm, prem in visible:
                    prem_tag = " `✦`" if prem else ""
                    lines.append(f"`{pfx}{syn}`{prem_tag} — {d}")
                embed.description = "\n".join(lines)
            else:
                embed.description = "No accessible commands in this category for your permission level."
        else:
            prem_tag = " · **Premium**" if is_prem else ""
            embed = discord.Embed(
                title="Happy — Help",
                description=(
                    f"Level: **{PERM_LABELS[user_level]}**{prem_tag}\n"
                    f"Prefix: `{pfx}`\n\n"
                    "Use `/help category:` to browse a specific category.\n"
                    f"Use `{pfx}help <name>` in chat to search for a command.\n\n"
                    "**Categories:** fun · roleplay · utility · profile · levels · "
                    "mod · antispam · roles · tickets · setup · admin · premium"
                ),
                color=PERM_COLORS[user_level]
            )
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        embed.set_footer(text=f"Happy Premium · {pfx}help for prefix commands")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Help(bot))