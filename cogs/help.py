import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

from utils.db import settings_col
from utils.helpers import BOT_OWNER_ID, is_premium_user, is_premium_server

EMOJI_HOME       = "<:home:1522637327149568100>"
EMOJI_OWNER      = "<:owner:1522644329510862848>"
EMOJI_DISBOARD   = "<:disboard:1522643098599948389>"
EMOJI_BDAY_CAKE  = "<a:birthdaycake:1522641563153334423>"
EMOJI_BDAY_WORK  = "<a:appyworkbirthday:1522641672968736961>"
EMOJI_TADA       = "<a:tada:1522638851250720969>"
EMOJI_BOT        = "<:bot:1522522753125716098>"
EMOJI_COMMUNITY  = "<:communitymembers:1522522379090137128>"
EMOJI_MEMBER     = "<:member:1522522354176233594>"
EMOJI_DOT_GREEN  = "<:dotgreen:1522520298539319399>"
EMOJI_DOT_BLACK  = "<:dotblack:1522520296450424853>"
EMOJI_DOT_YELLOW = "<:dotyellow:1522520275944734851>"
EMOJI_DOT_RED    = "<:dotred:1522520224304336927>"
EMOJI_SPARKLE    = "<:sparkle:1522515167995367435>"
EMOJI_HEART      = "<a:bounceheart:1522493780798734358>"
EMOJI_GHOST      = "<a:ghosty:1522505832288354334>"
EMOJI_GIVEAWAYS  = "<:giveaways:1522646168801382410>"

PERM_ORDER  = {"everyone": 0, "mod": 1, "admin": 2, "owner": 3}
PERM_LABELS = {"everyone": "Member", "mod": "Moderator", "admin": "Admin", "owner": "Bot Owner"}
PERM_COLORS = {"everyone": 0x5865F2, "mod": 0x57F287, "admin": 0xED4245, "owner": 0xF0C040}
PERM_BADGE  = {"mod": f"{EMOJI_DOT_GREEN} Mod", "admin": f"{EMOJI_DOT_RED} Admin", "owner": f"{EMOJI_OWNER} Owner", "everyone": ""}

def _user_level(member: discord.Member, owner_id: int) -> str:
    if member.id == owner_id:
        return "owner"
    if member.guild_permissions.administrator:
        return "admin"
    p = member.guild_permissions
    if p.manage_messages or p.kick_members or p.manage_roles or p.ban_members:
        return "mod"
    return "everyone"


REGISTRY: dict[str, list[tuple]] = {
    "fun": [
        ("ship @user1 @user2",       "Check love compatibility between two members",   "everyone", False),
        ("hot [@user]",              "Check someone's hotness level",                  "everyone", False),
        ("8ball <question>",         "Ask the magic 8-ball a question",                "everyone", False),
        ("coinflip <amount> [h/t]",  "Gamble HC on a coinflip",                        "everyone", False),
        ("dice [sides]",             "Roll a dice — default d6",                       "everyone", False),
        ("wouldyourather",           "Random would-you-rather with vote buttons",      "everyone", False),
        ("roast [@user]",            "Send a gentle roast",                            "everyone", False),
        ("praise [@user]",           "Send a compliment",                              "everyone", False),
        ("shrug [text]",             "Post a shrug  ¯_(ツ)_/¯",                       "everyone", False),
    ],
    "roleplay": [
        ("hug [@user]",    "Hug someone",                   "everyone", False),
        ("pat [@user]",    "Pat someone on the head",        "everyone", False),
        ("slap [@user]",   "Slap someone",                   "everyone", False),
        ("kiss [@user]",   "Give someone a kiss",            "everyone", False),
        ("poke [@user]",   "Poke someone repeatedly",        "everyone", False),
        ("highfive [@user]","High-five someone",             "everyone", False),
        ("bonk [@user]",   "Bonk someone",                   "everyone", False),
        ("cuddle [@user]", "Cuddle up with someone",         "everyone", False),
        ("boop [@user]",   "Boop someone's nose",            "everyone", False),
        ("wave [@user]",   "Wave at someone",                "everyone", False),
        ("stare [@user]",  "Stare intensely at someone",     "everyone", False),
    ],
    "utility": [
        ("ping",                    "Check bot latency",                              "everyone", False),
        ("userinfo [@user]",        "View detailed info about a member",              "everyone", False),
        ("avatar [@user]",          "View a member's full-size avatar",               "everyone", False),
        ("serverinfo",              "View server stats — members, roles, boosts",     "everyone", False),
        ("membercount",             "Total, human, and bot count",                    "everyone", False),
        ("translate <lang> <text>", "Translate text to any language",                 "everyone", False),
        ("urban <word>",            "Urban Dictionary lookup",                        "everyone", False),
        ("afk [reason]",            "Go AFK — bot notifies anyone who pings you",    "everyone", False),
        ("mimic @user <msg>",       "Send a message as another member via webhook",   "mod",      False),
        ("echo [#channel] <msg>",   "Send a message as the bot",                      "mod",      False),
    ],
    "profile": [
        ("profile [@user]",         "View a profile card",                            "everyone", False),
        ("profile bio <text>",      "Set your bio (max 150 chars)",                   "everyone", False),
        ("profile location <city>", "Set your location",                              "everyone", False),
        ("profile clear",           "Clear your bio and location",                    "everyone", False),
        ("birthday [@user]",        "View a member's birthday",                       "everyone", False),
        ("birthday set DD/MM",      "Set your birthday — bot wishes you on the day",  "everyone", False),
        ("birthday remove",         "Remove your birthday",                           "everyone", False),
        ("birthday channel #ch",    "Set the birthday announcement channel",          "admin",    False),
    ],
    "levels": [
        ("level [@user]",       "View XP, level, and progress bar",               "everyone", False),
        ("leaderboard",         "Top 10 most active members by level",            "everyone", False),
        ("setlevel @user <n>",  "Manually set a member's level",                  "admin",    False),
        ("resetxp [@user]",     "Reset XP for a member or the whole server",      "admin",    False),
    ],
    "mod": [
        ("kick @user [reason]",           "Kick a member",                          "mod",   False),
        ("ban @user [reason]",            "Permanently ban a member",               "mod",   False),
        ("tempban @user <dur> [reason]",  "Temporarily ban — auto-unbans",          "mod",   False),
        ("unban <user_id> [reason]",      "Unban a user by ID",                     "mod",   False),
        ("mute @user [dur] [reason]",     "Timeout a member (max 28d)",             "mod",   False),
        ("unmute @user",                  "Remove a member's timeout",              "mod",   False),
        ("warn @user [reason]",           "Warn a member — tracked in DB",          "mod",   False),
        ("warnings [@user]",              "View warning count for a member",        "mod",   False),
        ("clearwarns @user",              "Clear all warnings for a member",        "mod",   False),
        ("softban @user [reason]",        "Ban + unban — clears 7d of messages",   "mod",   False),
        ("nickname @user [name]",         "Change or reset a member's nickname",    "mod",   False),
        ("note @user",                    "View mod notes for a member",            "mod",   False),
        ("note add @user <text>",         "Add a mod note",                         "mod",   False),
        ("note clear @user",              "Clear all mod notes",                    "mod",   False),
        ("cases @user [limit]",           "View case history for a member",         "mod",   False),
        ("case <number>",                 "View a specific case by number",         "mod",   False),
        ("purge <amount>",                "Delete recent messages",                 "mod",   False),
        ("purge bots/links/images <n>",   "Filtered bulk delete",                   "mod",   False),
        ("lock [#channel]",               "Lock a channel",                         "mod",   False),
        ("unlock [#channel]",             "Unlock a channel",                       "mod",   False),
        ("vclock / vcunlock [#vc]",       "Lock or unlock a voice channel",         "mod",   False),
        ("lockdown [reason]",             "Lock ALL channels — emergency",          "admin", False),
        ("unlockdown",                    "Lift the server lockdown",               "admin", False),
        ("jail @user [reason]",           "Strip roles, move to jail channel",      "mod",   False),
        ("unjail @user",                  "Release from jail, restore roles",       "mod",   False),
        ("sticky <text>",                 "Pin a sticky message that re-appears",   "mod",   False),
        ("unsticky",                      "Remove sticky from this channel",        "mod",   False),
        ("slowmode <seconds> [#ch]",      "Set channel slowmode (0 to disable)",    "mod",   False),
        ("topic [text]",                  "Set or clear the channel topic",         "mod",   False),
        ("rename <name>",                 "Rename the current channel",             "mod",   False),
    ],
    "antispam": [
        ("antispam",                   "View anti-spam config",                    "admin", False),
        ("antispam enable",            "Enable the anti-spam system",              "admin", False),
        ("antispam disable",           "Disable the anti-spam system",             "admin", False),
        ("antispam set <n> <action>",  "Set threshold + action: mute/kick/ban",   "admin", False),
    ],
    "roles": [
        ("role add @user @role",              "Add a role to a member",             "admin", False),
        ("role remove @user @role",           "Remove a role from a member",        "admin", False),
        ("massrole add everyone @role",       "Give a role to all members",         "admin", False),
        ("massrole add bots @role",           "Give a role to all bots",            "admin", False),
        ("massrole remove everyone @role",    "Remove a role from all members",     "admin", False),
        ("reactionrole add <link> <e> @role", "Set up a reaction role",             "admin", False),
        ("buttonrole @role Label | ...",      "Create a button role panel",         "admin", True),
        ("boosterrole @role",                 "Reward role for server boosters",    "admin", False),
    ],
    "tickets": [
        ("ticket setup",           "Send the ticket creation panel",          "admin", False),
        ("ticket staffrole @role", "Set which role can see all tickets",      "admin", False),
        ("ticket close",           "Close and delete this ticket channel",    "mod",   False),
        ("ticket add @user",       "Add a user to this ticket",               "mod",   False),
        ("ticket remove @user",    "Remove a user from this ticket",          "mod",   False),
    ],
    "setup": [
        ("quicksetup",                 "Auto-create channels, categories, roles",          "admin", False),
        ("jailsetup",                  "Create Jailed role + private jail channel",        "admin", False),
        ("setupmute",                  "Create Muted / Image Muted / Reaction Muted roles","admin", False),
        ("welcome set #channel",       "Set the welcome message channel",                  "admin", False),
        ("welcome enable/disable",     "Toggle welcome messages on or off",                "admin", False),
        ("welcome message <text>",     "Custom welcome message — supports variables",      "admin", False),
        ("welcome resetmsg",           "Revert welcome message to default",               "admin", False),
        ("welcome test",               "Preview the welcome message",                      "admin", False),
        ("bye set #channel",           "Set the bye message channel",                      "admin", False),
        ("bye enable/disable",         "Toggle bye messages on or off",                    "admin", False),
        ("bye message <text>",         "Custom goodbye message — supports variables",      "admin", False),
        ("bye resetmsg",               "Revert goodbye message to default",               "admin", False),
        ("bye test",                   "Preview the bye message",                          "admin", False),
        ("logs set #channel",          "Set the moderation log channel",                   "admin", False),
        ("logs disable",               "Disable logging",                                  "admin", False),
        ("automod invite on/off",      "Block Discord invite links server-wide",           "admin", False),
        ("counter create <type> #vc",  "Live member/bot/channel counter in a VC name",     "admin", False),
    ],
    "admin": [
        ("settings",                        "Full server configuration dashboard",          "admin", False),
        ("prefix",                          "View current prefix",                          "everyone", False),
        ("prefix set <symbol>",             "Change server prefix (max 3 chars)",           "admin", False),
        ("prefix remove",                   "Reset prefix to default ,",                   "admin", False),
        ("prefix self <symbol>",            "Personal prefix across all servers",           "everyone", True),
        ("prefix selfremove",               "Remove your personal prefix",                  "everyone", False),
        ("premiumrole @role",               "Role that grants premium AI access",           "admin", False),
        ("disable <command>",               "Disable a command for this server",            "admin", False),
        ("enable <command>",                "Re-enable a disabled command",                 "admin", False),
        ("announce [#ch] <msg>",            "Send an announcement embed",                   "mod",   False),
        ("giveaway <dur> <n> <prize>",      "Start a giveaway — 30m/2h/1d",               "mod",   False),
        ("giveaway ... --msgs <n>",         "Require n messages to enter",                  "mod",   False),
        ("giveaway ... --invites <n>",      "Require n invites to enter",                   "mod",   False),
        ("giveaway end <msg_id>",           "End a giveaway early",                         "mod",   False),
        ("giveaway reroll <msg_id>",        "Reroll winners for an ended giveaway",         "mod",   False),
        ("embed create",                    "Start a new embed draft",                      "admin", False),
        ("embed title/desc/color/image",    "Edit embed draft fields",                      "admin", False),
        ("embed send [#channel]",           "Send the finished embed",                      "admin", False),
        ("togglelevels on/off",             "Toggle level-up announcements",                "admin", False),
        ("togglereactions on/off",          "Toggle auto heart reactions",                  "admin", False),
    ],
    "premium": [
        ("mypremium",                "Check your premium status and features",      "everyone", False),
        ("call",                     "Connect this channel to another server live", "admin",    True),
        ("callinfo",                 "View current call status and queue position", "everyone", False),
        ("hangup",                   "End the cross-server call or leave queue",    "mod",      False),
        ("vcsetup create",           "Create the Join to Create trigger channel",   "admin",    True),
        ("vcsetup limit <n>",        "Set default user limit for temp VCs",         "admin",    True),
        ("vcsetup name <template>",  "Set VC name template — {user} = name",       "admin",    True),
        ("vcsetup remove",           "Remove VoiceMaster and all temp VCs",         "admin",    True),
        ("bumpreminder on/off",      "DISBOARD bump reminder — pings 2h after bump","admin",   True),
        ("bumpreminder on @role",    "Bump reminder with role ping",                "admin",    True),
        ("bumppingrole @role",       "Set the role pinged on bump reminder",        "admin",    True),
        ("setstatus <text>",         "Add custom text to bot rotating status",      "admin",    True),
        ("color",                    "Pick an accent color role for yourself",      "everyone", True),
        ("color setup",              "Create the default color role palette",       "admin",    True),
        ("serverpolish",             "One-click aesthetic server setup",            "admin",    True),
    ],
    "economy": [
        ("balance [@user]",           "View wallet + bank balance",                 "everyone", False),
        ("daily",                     "Claim daily reward — 150-350 HC, 24h",      "everyone", False),
        ("work",                      "Work for HC — boosted by server activity",   "everyone", False),
        ("deposit <amount/all>",      "Move HC from wallet to bank",                "everyone", False),
        ("withdraw <amount/all>",     "Move HC from bank to wallet",                "everyone", False),
        ("transfer @user <amount>",   "Send HC to another user",                    "everyone", False),
        ("coinflip <amount> [h/t]",   "Gamble with a coinflip — 50/50",            "everyone", False),
        ("slots <amount>",            "Spin the slot machine — match 3 to win",     "everyone", False),
        ("rob @user",                 "Try to rob someone — 45% success rate",      "everyone", False),
        ("richlist",                  "Top 10 richest HC holders globally",         "everyone", False),
        ("serverstatus",              "Server activity and work multiplier",         "everyone", False),
        ("trade offer @user <o> <w>", "Send a trade offer",                         "everyone", False),
        ("trade accept/decline <id>", "Accept or decline a pending trade",          "everyone", False),
        ("trade cancel <id>",         "Cancel your own trade",                      "everyone", False),
        ("trade list",                "See all your pending trades",                 "everyone", False),
        ("invest in <type> <amount>", "Start investing (Safe, Market, Crypto, Business)","everyone", False),
        ("invest check",              "See your active investment details",          "everyone", False),
        ("invest collect",            "Collect matured investment returns",         "everyone", False),
        ("invest withdraw",           "Exit investment early (10% penalty)",        "everyone", False),
        ("givecash @user <n>",        "Give HC to a user",                          "owner",    False),
        ("takecash @user <n>",        "Remove HC from a user",                      "owner",    False),
        ("resetcash @user",           "Reset a user's balance",                     "owner",    False),
    ],
    "emojis": [
        ("emoji steal <emojis>",      "Download & recreate custom emojis in the server", "admin", False),
        ("emoji search <name>",       "Search active servers via Dropdown browser", "everyone", False),
        ("emoji addid <id> <name>",   "Add a custom emoji directly by raw ID",      "admin",    False),
        ("emoji remove",              "Remove a custom emoji from the server",      "admin",    False),
        ("emoji rename",              "Rename a custom emoji in the server",        "admin",    False),
        ("emoji list",                "Check static/animated slot usage dashboard",  "everyone", False),
    ],
    "games": [
        ("numguess start [max]",   "Start a number guessing game (1–100)",      "everyone", False),
        ("numguess stop",          "End the current number guess game",         "mod",      False),
        ("counting setup #channel","Set up the counting channel",               "mod",      False),
        ("counting stats",         "View counting high score",                   "everyone", False),
        ("counting reset",         "Reset the count to 0",                       "mod",      False),
        ("wordguess start",        "Start a word guessing game (Hangman)",       "everyone", False),
        ("wordguess stop",         "End the current word guess game",            "mod",      False),
    ],
    "extraperm": [
        ("extraperm setup",               "Create all 10 extra perm roles in one go",     "admin",    True),
        ("extraperm",                     "Dashboard — all roles, counts, who has them",  "admin",    True),
        ("extraperm give @member <role>", "Assign an extra perm role to a member",        "admin",    True),
        ("extraperm take @member <role>", "Remove an extra perm role from a member",      "admin",    True),
        ("extraperm list [@member]",      "See which extra perm roles a member has",      "everyone", True),
        ("extraperm info [rolename]",     "What a role grants and who has it",            "everyone", True),
        ("extraperm teardown",            "Delete all created extra perm roles",          "admin",    True),
    ],
    "levelroles": [
        ("levelroles setup",                 "Create Lvl 1-100 roles and 7 perm roles",  "admin",    True),
        ("levelroles",                       "Level roles dashboard",                     "admin",    True),
        ("levelroles managechannel #ch",     "Toggle channel for auto permission mgmt",   "admin",    True),
        ("levelroles sync",                  "Re-apply all roles and perms to members",   "admin",    True),
        ("levelroles info <level>",          "Show role and permissions for a level",     "everyone", True),
        ("levelroles grant @member @role",   "Give a member an extra perm role",          "admin",    True),
        ("levelroles revoke @member @role",  "Remove an extra perm role from a member",   "admin",    True),
        ("levelroles grants [@member]",      "List extra roles a member has",             "everyone", True),
        ("levelroles teardown",              "Delete all 107 created roles",              "admin",    True),
    ],
    "owner": [
        ("premium add server <id>",         "Activate premium for a server",             "owner", False),
        ("premium add user <id>",           "Activate premium for a user",               "owner", False),
        ("premium remove server/user <id>", "Remove premium access",                     "owner", False),
        ("premium list",                    "List all active premium entries",            "owner", False),
        ("aimode on/off",                   "Toggle AI chat globally",                   "owner", False),
        ("aiblock <guild_id>",              "Block AI for a specific server",            "owner", False),
        ("maintenance on/off",              "Toggle maintenance mode",                   "owner", False),
        ("botstatus set <act> <text>",      "Set always-on owner status override",       "owner", False),
        ("botstatus set24h <act> <text>",   "Set 24h owner status override",             "owner", False),
        ("botstatus reset",                 "Remove owner status override",              "owner", False),
        ("serverlist",                      "List all servers the bot is in",            "owner", False),
        ("leaveguild <guild_id>",           "Force the bot to leave a server",           "owner", False),
        ("dm @user <message>",              "Send a DM to any user as the bot",          "owner", False),
        ("sync [guild_id]",                 "Sync slash commands",                       "owner", False),
    ],
    "tracker": [
        ("invites [@user]",       "How many people a user has invited",           "everyone", False),
        ("inviteleaderboard",     "Top 10 inviters in the server",                "everyone", False),
        ("inviteinfo <code>",     "Detailed info about a specific invite link",   "everyone", False),
        ("invitelog #channel",    "Set the invite join/leave log channel",        "admin",    False),
        ("invitelogdisable",      "Disable invite logging",                       "admin",    False),
        ("invitereset [@user]",   "Reset invite count for a user or all",         "admin",    False),
        ("messages [@user]",      "Total messages sent by a user + rank",         "everyone", False),
        ("msgleaderboard",        "Top 10 most active chatters",                  "everyone", False),
        ("msgstats",              "Server-wide message totals and top chatter",   "everyone", False),
        ("msgreset [@user]",      "Reset message count for a user or all",        "admin",    False),
    ],
}

ALL_COMMANDS = [
    (cat, syn, desc, perm, prem)
    for cat, entries in REGISTRY.items()
    for syn, desc, perm, prem in entries
]

CAT_META: dict[str, tuple[str, str, str]] = {
    "fun":        ("Fun",          EMOJI_TADA,      "Ship, 8ball, dice, roast, praise"),
    "roleplay":   ("Roleplay",     EMOJI_HEART,     "Hug, pat, kiss, bonk, cuddle and more"),
    "utility":    ("Utility",      EMOJI_SPARKLE,   "Ping, userinfo, avatar, AFK, translate"),
    "profile":    ("Profile",      EMOJI_BDAY_CAKE, "Profile card, bio, location, birthday"),
    "levels":     ("Levels",       EMOJI_DOT_YELLOW,"XP system, level, leaderboard"),
    "mod":        ("Moderation",   EMOJI_DOT_RED,   "Kick, ban, mute, warn, lock, purge, jail"),
    "antispam":   ("Anti-Spam",    EMOJI_DOT_BLACK, "Auto-detect and punish spam floods"),
    "roles":      ("Roles",        EMOJI_COMMUNITY, "Reaction roles, button roles, boosters"),
    "tickets":    ("Tickets",      EMOJI_MEMBER,    "Support ticket system"),
    "setup":      ("Server Setup", EMOJI_HOME,      "Welcome, bye, logs, automod, counters"),
    "admin":      ("Admin",        EMOJI_GIVEAWAYS, "Prefix, giveaway, announce, embeds"),
    "premium":    ("Premium",      EMOJI_DISBOARD,  "AI chat, VoiceMaster, bump reminder"),
    "economy":    ("Economy",      EMOJI_DOT_GREEN, "Balance, daily, work, slots, trade, invest"),
    "emojis":     ("Custom Emojis",EMOJI_BOT,       "Steal, search, addid, list dashboard"),
    "games":      ("Games",        EMOJI_GHOST,     "Number guess, counting, word guess"),
    "extraperm":  ("Extra Perms",  EMOJI_SPARKLE,   "Gif, React, Media, Ext perm roles"),
    "levelroles": ("Level Roles",  EMOJI_BDAY_WORK, "Auto roles Lvl 1-100 with permissions"),
    "owner":      ("Owner",        EMOJI_OWNER,     "Premium mgmt, AI toggle, server tools"),
    "tracker":    ("Tracker",      EMOJI_COMMUNITY, "Invite tracker, message counter"),
}

CAT_ORDER = list(CAT_META.keys())


def _build_home_embed(bot: discord.Client, pfx: str, user_level: str,
                      is_prem: bool, color: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"{EMOJI_HOME} Happy Help",
        color=color,
        description=(
            f"**Prefix:** `{pfx}`  •  **Slash:** `/help`\n"
            f"**Role:** {PERM_LABELS[user_level]}"
            + (f"  •  {EMOJI_SPARKLE} Premium" if is_prem else "") + "\n\n"
            f"Use the dropdown below to jump to a category.\n"
            f"Use the arrows to page through everything.\n"
            f"Type `{pfx}help <word>` to search commands."
        )
    )
    embed.set_thumbnail(url=bot.user.display_avatar.url)

    member_cats = []
    staff_cats  = []
    prem_cats   = []

    for key, (label, emoji, desc) in CAT_META.items():
        entries = REGISTRY[key]
        accessible = [
            e for e in entries
            if PERM_ORDER[e[2]] <= PERM_ORDER[user_level]
            and (not e[3] or is_prem)
        ]
        if not accessible:
            continue

        all_prem = all(e[3] for e in entries)
        min_perm = min(PERM_ORDER[e[2]] for e in accessible)

        entry = f"{emoji} **{label}** `{len(accessible)}`"
        if all_prem:
            prem_cats.append(entry)
        elif min_perm >= 1:
            staff_cats.append(entry)
        else:
            member_cats.append(entry)

    if member_cats:
        embed.add_field(name="For Everyone", value="\n".join(member_cats), inline=True)
    if staff_cats:
        embed.add_field(name="Staff Only", value="\n".join(staff_cats), inline=True)
    if prem_cats:
        embed.add_field(name=f"{EMOJI_SPARKLE} Premium", value="\n".join(prem_cats), inline=True)

    embed.set_footer(
        text="Premium active — mention Happy to start AI chat" if is_prem
        else "Tip: ,mypremium to see premium features"
    )
    return embed


def _build_category_embed(cat: str, pfx: str, user_level: str,
                           is_prem: bool, page: int, per_page: int) -> tuple[discord.Embed, int]:
    label, emoji, desc = CAT_META[cat]
    entries = REGISTRY[cat]
    color   = PERM_COLORS[user_level]

    visible = [
        (syn, d, perm, prem)
        for syn, d, perm, prem in entries
        if PERM_ORDER[perm] <= PERM_ORDER[user_level]
        and (not prem or is_prem)
    ]
    locked = [
        (syn, d, perm, prem)
        for syn, d, perm, prem in entries
        if PERM_ORDER[perm] > PERM_ORDER[user_level] or (prem and not is_prem)
    ]

    total_pages = max(1, (len(visible) + per_page - 1) // per_page)
    page        = max(0, min(page, total_pages - 1))
    chunk       = visible[page * per_page:(page + 1) * per_page]

    page_info = f"  ·  Page {page+1}/{total_pages}" if total_pages > 1 else ""
    embed = discord.Embed(
        title=f"{emoji} {label}",
        description=f"{desc}{page_info}",
        color=color
    )

    if not visible:
        embed.description = f"{desc}\n\nNothing available at your permission level."
        if locked:
            embed.set_footer(text=f"{EMOJI_DOT_RED} {len(locked)} command(s) need higher permissions or Premium")
        return embed, 1

    lines = []
    for syn, d, perm, prem in chunk:
        badges = []
        if prem:
            badges.append(EMOJI_SPARKLE)
        if perm in ("mod", "admin", "owner"):
            badges.append(PERM_BADGE[perm])
        badge_str = "  " + "  ".join(badges) if badges else ""
        lines.append(f"`{pfx}{syn}`{badge_str}\n{d}")

    embed.add_field(name="Commands", value="\n\n".join(lines), inline=False)

    if locked:
        lock_lines = []
        for syn, d, perm, prem in locked[:5]:
            reason = "Premium" if (prem and not is_prem) else PERM_LABELS[perm]
            lock_lines.append(f"`{pfx}{syn}` — needs {reason}")
        if len(locked) > 5:
            lock_lines.append(f"…and {len(locked)-5} more")
        embed.add_field(name=f"{EMOJI_DOT_RED} Locked  ({len(locked)})", value="\n".join(lock_lines), inline=False)

    footer_parts = [f"{len(visible)} commands"]
    if total_pages > 1:
        footer_parts.append(f"page {page+1} of {total_pages}")
    embed.set_footer(text="  ·  ".join(footer_parts))

    return embed, total_pages


def _build_search_embed(query: str, pfx: str, user_level: str, is_prem: bool) -> discord.Embed:
    results = [
        (cat, syn, desc, perm, prem)
        for cat, syn, desc, perm, prem in ALL_COMMANDS
        if query in syn.lower() or query in desc.lower()
    ]

    color = PERM_COLORS[user_level]
    embed = discord.Embed(title=f"Search — {query}", color=color)

    if not results:
        embed.description = f"No commands found for `{query}`.\nTry `{pfx}help` to browse all categories."
        return embed

    accessible   = []
    inaccessible = []
    for cat, syn, desc, perm, prem in results[:20]:
        can = PERM_ORDER[perm] <= PERM_ORDER[user_level] and (not prem or is_prem)
        cat_label = CAT_META[cat][0]
        if can:
            badge = f" {EMOJI_SPARKLE}" if prem else ""
            accessible.append(f"`{pfx}{syn}`{badge}  —  {desc}  `#{cat_label}`")
        else:
            reason = "Premium" if (prem and not is_prem) else PERM_LABELS[perm]
            inaccessible.append(f"~~`{pfx}{syn}`~~  —  needs {reason}")

    lines = accessible + inaccessible
    embed.description = "\n".join(lines)
    if len(results) > 20:
        embed.description += f"\n\n…and {len(results)-20} more. Browse with `{pfx}help <category>`."
    embed.set_footer(text=f"{len(results)} result(s) found")
    return embed


class HelpView(discord.ui.View):
    PER_PAGE = 7

    def __init__(self, bot, author_id: int, pfx: str,
                 user_level: str, is_prem: bool, start_cat: Optional[str] = None):
        super().__init__(timeout=120)
        self.bot         = bot
        self.author_id   = author_id
        self.pfx         = pfx
        self.user_level  = user_level
        self.is_prem     = is_prem
        self.color       = PERM_COLORS[user_level]
        self.current_cat: Optional[str] = start_cat
        self.cat_page: int = 0
        self.message: Optional[discord.Message] = None
        self._rebuild_components()

    def _rebuild_components(self):
        self.clear_items()
        self.add_item(CategorySelect(self.user_level, self.is_prem, self.current_cat))

        on_home = self.current_cat is None

        home_btn = discord.ui.Button(
            label="Home",
            emoji=EMOJI_HOME,
            style=discord.ButtonStyle.secondary,
            custom_id="help_home",
            row=1,
            disabled=on_home
        )
        home_btn.callback = self._home_callback
        self.add_item(home_btn)

        prev_btn = discord.ui.Button(
            label="Prev",
            style=discord.ButtonStyle.primary,
            custom_id="help_prev",
            row=1,
            disabled=self._prev_disabled()
        )
        prev_btn.callback = self._prev_callback
        self.add_item(prev_btn)

        next_btn = discord.ui.Button(
            label="Next",
            style=discord.ButtonStyle.primary,
            custom_id="help_next",
            row=1,
            disabled=self._next_disabled()
        )
        next_btn.callback = self._next_callback
        self.add_item(next_btn)

        close_btn = discord.ui.Button(
            label="Close",
            style=discord.ButtonStyle.danger,
            custom_id="help_close",
            row=1
        )
        close_btn.callback = self._close_callback
        self.add_item(close_btn)

    def _prev_disabled(self) -> bool:
        if self.current_cat is None:
            return True
        if self.cat_page > 0:
            return False
        return CAT_ORDER.index(self.current_cat) == 0

    def _next_disabled(self) -> bool:
        if self.current_cat is None:
            return len(CAT_ORDER) == 0
        idx = CAT_ORDER.index(self.current_cat)
        _, total = _build_category_embed(
            self.current_cat, self.pfx, self.user_level,
            self.is_prem, self.cat_page, self.PER_PAGE
        )
        if self.cat_page < total - 1:
            return False
        return idx == len(CAT_ORDER) - 1

    def build_embed(self) -> discord.Embed:
        if self.current_cat is None:
            return _build_home_embed(self.bot, self.pfx, self.user_level, self.is_prem, self.color)
        embed, _ = _build_category_embed(
            self.current_cat, self.pfx, self.user_level,
            self.is_prem, self.cat_page, self.PER_PAGE
        )
        return embed

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This help menu isn't yours. Type `,help` to open your own.",
                ephemeral=True
            )
            return False
        return True

    async def _update(self, interaction: discord.Interaction):
        self._rebuild_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _home_callback(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self.current_cat = None
        self.cat_page    = 0
        await self._update(interaction)

    async def _prev_callback(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        if self.current_cat is None:
            return
        if self.cat_page > 0:
            self.cat_page -= 1
        else:
            idx = CAT_ORDER.index(self.current_cat)
            if idx > 0:
                self.current_cat = CAT_ORDER[idx - 1]
                _, total = _build_category_embed(
                    self.current_cat, self.pfx, self.user_level,
                    self.is_prem, 0, self.PER_PAGE
                )
                self.cat_page = total - 1
        await self._update(interaction)

    async def _next_callback(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        if self.current_cat is None:
            self.current_cat = CAT_ORDER[0]
            self.cat_page    = 0
        else:
            _, total = _build_category_embed(
                self.current_cat, self.pfx, self.user_level,
                self.is_prem, self.cat_page, self.PER_PAGE
            )
            if self.cat_page < total - 1:
                self.cat_page += 1
            else:
                idx = CAT_ORDER.index(self.current_cat)
                if idx < len(CAT_ORDER) - 1:
                    self.current_cat = CAT_ORDER[idx + 1]
                    self.cat_page    = 0
        await self._update(interaction)

    async def _close_callback(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        await interaction.response.edit_message(
            embed=discord.Embed(description="Help menu closed.", color=0x2B2D31),
            view=None
        )
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except:
                pass


class CategorySelect(discord.ui.Select):
    def __init__(self, user_level: str, is_prem: bool, current_cat: Optional[str]):
        options = []
        for key, (label, emoji, desc) in CAT_META.items():
            entries = REGISTRY[key]
            accessible = [
                e for e in entries
                if PERM_ORDER[e[2]] <= PERM_ORDER[user_level]
                and (not e[3] or is_prem)
            ]
            if not accessible:
                continue
            options.append(discord.SelectOption(
                label=label,
                value=key,
                emoji=emoji,
                description=f"{len(accessible)} commands  —  {desc[:45]}",
                default=(key == current_cat)
            ))

        if not options:
            options.append(discord.SelectOption(label="No categories available", value="__none__"))

        super().__init__(placeholder="Jump to a category...", options=options[:25], row=0)
        self.user_level = user_level
        self.is_prem    = is_prem

    async def callback(self, interaction: discord.Interaction):
        view: HelpView = self.view
        if not await view._guard(interaction):
            return
        val = self.values[0]
        if val == "__none__":
            return await interaction.response.defer()
        view.current_cat = val
        view.cat_page    = 0
        view._rebuild_components()
        await interaction.response.edit_message(embed=view.build_embed(), view=view)


class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _get_prefix(self, ctx) -> str:
        try:
            data = await settings_col.find_one({"_id": str(ctx.guild.id)})
            return data.get("prefix", ",") if data else ","
        except:
            return ","

    async def _get_context(self, ctx_or_interaction):
        if hasattr(ctx_or_interaction, "author"):
            member = ctx_or_interaction.author
            guild  = ctx_or_interaction.guild
            pfx    = await self._get_prefix(ctx_or_interaction)
        else:
            member = ctx_or_interaction.user
            guild  = ctx_or_interaction.guild
            try:
                data = await settings_col.find_one({"_id": str(guild.id)})
                pfx  = data.get("prefix", ",") if data else ","
            except:
                pfx = ","

        user_level = _user_level(member, BOT_OWNER_ID)
        is_prem    = (
            member.id == BOT_OWNER_ID
            or await is_premium_user(member.id)
            or await is_premium_server(guild.id)
        )
        return pfx, user_level, is_prem

    @commands.command(aliases=["h", "commands", "cmds"])
    async def help(self, ctx, *, query: str = None):
        pfx, user_level, is_prem = await self._get_context(ctx)

        if query and query.lower() not in REGISTRY:
            embed = _build_search_embed(query.lower(), pfx, user_level, is_prem)
            await ctx.reply(embed=embed)
            return

        start_cat = query.lower() if query and query.lower() in REGISTRY else None
        view = HelpView(
            bot=self.bot,
            author_id=ctx.author.id,
            pfx=pfx,
            user_level=user_level,
            is_prem=is_prem,
            start_cat=start_cat
        )
        view.message = await ctx.reply(embed=view.build_embed(), view=view)

    @app_commands.command(name="help", description="View Happy's command list")
    @app_commands.describe(
        category="Jump to a specific category",
        search="Search for a command by name or description"
    )
    @app_commands.choices(category=[
        app_commands.Choice(name="Fun",          value="fun"),
        app_commands.Choice(name="Roleplay",     value="roleplay"),
        app_commands.Choice(name="Utility",      value="utility"),
        app_commands.Choice(name="Profile",      value="profile"),
        app_commands.Choice(name="Levels",       value="levels"),
        app_commands.Choice(name="Moderation",   value="mod"),
        app_commands.Choice(name="Anti-Spam",    value="antispam"),
        app_commands.Choice(name="Roles",        value="roles"),
        app_commands.Choice(name="Tickets",      value="tickets"),
        app_commands.Choice(name="Server Setup", value="setup"),
        app_commands.Choice(name="Admin",        value="admin"),
        app_commands.Choice(name="Premium",      value="premium"),
        app_commands.Choice(name="Economy",      value="economy"),
        app_commands.Choice(name="Custom Emojis",value="emojis"),
        app_commands.Choice(name="Games",        value="games"),
    ])
    async def slash_help(
        self,
        interaction: discord.Interaction,
        category: str = None,
        search: str = None
    ):
        await interaction.response.defer(ephemeral=True)
        pfx, user_level, is_prem = await self._get_context(interaction)

        if search:
            embed = _build_search_embed(search.lower(), pfx, user_level, is_prem)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        start_cat = category if category and category in REGISTRY else None
        view = HelpView(
            bot=self.bot,
            author_id=interaction.user.id,
            pfx=pfx,
            user_level=user_level,
            is_prem=is_prem,
            start_cat=start_cat
        )
        view.message = await interaction.followup.send(embed=view.build_embed(), view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Help(bot))