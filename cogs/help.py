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
PERM_BADGE  = {
    "everyone": "",
    "mod": f"{EMOJI_DOT_GREEN} Mod",
    "admin": f"{EMOJI_DOT_RED} Admin",
    "owner": f"{EMOJI_OWNER} Owner",
}

DEFAULT_PREFIX = ","
MAX_QUERY_LENGTH = 40
PER_PAGE = 6
VIEW_TIMEOUT = 120


def _user_level(member: discord.Member, owner_id: int) -> str:
    if member.id == owner_id:
        return "owner"
    perms = member.guild_permissions
    if perms.administrator:
        return "admin"
    if perms.manage_messages or perms.kick_members or perms.ban_members or perms.moderate_members:
        return "mod"
    return "everyone"


REGISTRY: dict[str, list[tuple]] = {
    "fun": [
        ("ship @user1 @user2",      "See how well two people match",              "everyone", False),
        ("hot [@user]",             "See how hot someone is",                     "everyone", False),
        ("8ball <question>",        "Ask the magic 8-ball anything",              "everyone", False),
        ("coinflip <amount> [h/t]", "Bet your coins on a coin toss",              "everyone", False),
        ("dice [sides]",            "Roll a dice. Default is 6 sides",            "everyone", False),
        ("wouldyourather",          "A fun question with vote buttons",           "everyone", False),
        ("roast [@user]",           "Send a light, friendly roast",               "everyone", False),
        ("praise [@user]",          "Send a nice compliment",                     "everyone", False),
        ("shrug [text]",            "Post a shrug  ¯\\_(ツ)_/¯",                  "everyone", False),
    ],
    "roleplay": [
        ("hug [@user]",      "Give someone a hug",           "everyone", False),
        ("pat [@user]",      "Pat someone on the head",      "everyone", False),
        ("slap [@user]",     "Slap someone",                 "everyone", False),
        ("kiss [@user]",     "Give someone a kiss",          "everyone", False),
        ("poke [@user]",     "Poke someone",                 "everyone", False),
        ("highfive [@user]", "Give someone a high-five",     "everyone", False),
        ("bonk [@user]",     "Bonk someone",                 "everyone", False),
        ("cuddle [@user]",   "Cuddle with someone",          "everyone", False),
        ("boop [@user]",     "Boop someone's nose",          "everyone", False),
        ("wave [@user]",     "Wave at someone",              "everyone", False),
        ("stare [@user]",    "Stare at someone",             "everyone", False),
    ],
    "utility": [
        ("ping",                    "See how fast the bot replies",              "everyone", False),
        ("userinfo [@user]",        "See details about a member",                "everyone", False),
        ("avatar [@user]",          "See a member's profile picture",            "everyone", False),
        ("serverinfo",              "See details about this server",             "everyone", False),
        ("membercount",             "See how many people and bots are here",     "everyone", False),
        ("translate <lang> <text>", "Turn text into another language",           "everyone", False),
        ("urban <word>",            "Look up a word on Urban Dictionary",        "everyone", False),
        ("afk [reason]",            "Go away. The bot tells people who ping you","everyone", False),
        ("mimic @user <message>",   "Send a message that looks like it is from another member", "mod", False),
        ("echo [#channel] <message>","Make the bot send a message",              "mod",      False),
    ],
    "profile": [
        ("profile [@user]",         "See a profile card",                        "everyone", False),
        ("profile bio <text>",      "Write your bio (up to 150 letters)",        "everyone", False),
        ("profile location <city>", "Set your city",                             "everyone", False),
        ("profile clear",           "Delete your bio and city",                  "everyone", False),
        ("birthday [@user]",        "See someone's birthday",                    "everyone", False),
        ("birthday set DD/MM",      "Set your birthday. The bot wishes you",     "everyone", False),
        ("birthday list",           "See upcoming birthdays",                    "everyone", False),
        ("birthday remove",         "Delete your birthday",                      "everyone", False),
        ("birthday channel #ch",    "Choose where birthday wishes are posted",   "admin",    False),
        ("birthday channelremove",  "Stop posting birthday wishes",              "admin",    False),
    ],
    "levels": [
        ("level [@user]",      "See your level and progress",              "everyone", False),
        ("leaderboard",        "See the top 10 most active members",       "everyone", False),
        ("setlevel @user <n>", "Set a member's level by hand",             "admin",    False),
        ("resetxp [@user]",    "Reset points for one member or everyone",  "admin",    False),
    ],
    "mod": [
        ("kick @user [reason]",          "Remove a member from the server",            "mod",   False),
        ("ban @user [reason]",           "Ban a member for good",                      "mod",   False),
        ("tempban @user <time> [reason]","Ban a member for a set time",                "mod",   False),
        ("unban <user_id> [reason]",     "Unban someone using their ID",               "mod",   False),
        ("mute @user [time] [reason]",   "Stop a member from chatting (up to 28 days)","mod",   False),
        ("unmute @user",                 "Let a muted member chat again",              "mod",   False),
        ("warn @user [reason]",          "Give a member a warning",                    "mod",   False),
        ("warnings [@user]",             "See a member's warnings",                    "mod",   False),
        ("clearwarns @user",             "Delete all warnings for a member",           "mod",   False),
        ("softban @user [reason]",       "Ban then unban to wipe their recent messages","mod",  False),
        ("nickname @user [name]",        "Change or reset a member's nickname",        "mod",   False),
        ("note @user",                   "See private notes about a member",           "mod",   False),
        ("note add @user <text>",        "Add a private note",                         "mod",   False),
        ("note clear @user",             "Delete all notes for a member",              "mod",   False),
        ("cases @user [limit]",          "See past actions taken on a member",         "mod",   False),
        ("case <number>",                "See one case by its number",                 "mod",   False),
        ("purge <amount>",               "Delete recent messages",                     "mod",   False),
        ("purge bots/links/images <n>",  "Delete only certain kinds of messages",      "mod",   False),
        ("lock [#channel]",              "Stop people from chatting in a channel",     "mod",   False),
        ("unlock [#channel]",            "Let people chat in a channel again",         "mod",   False),
        ("vclock / vcunlock [#vc]",      "Lock or unlock a voice channel",             "mod",   False),
        ("lockdown [reason]",            "Lock every channel in an emergency",         "admin", False),
        ("unlockdown",                   "End the lockdown",                           "admin", False),
        ("jail @user [reason]",          "Take a member's roles and send them to jail","mod",   False),
        ("unjail @user",                 "Free a member and give their roles back",    "mod",   False),
        ("sticky <text>",                "Keep a message at the bottom of a channel",  "mod",   False),
        ("unsticky",                     "Remove the sticky message here",             "mod",   False),
        ("slowmode <seconds> [#ch]",     "Set a wait time between messages (0 = off)", "mod",   False),
        ("topic [text]",                 "Set or clear the channel topic",             "mod",   False),
        ("rename <name>",                "Rename this channel",                        "mod",   False),
    ],
    "antispam": [
        ("antispam",                  "See the anti-spam settings",               "admin", False),
        ("antispam enable",           "Turn anti-spam on",                        "admin", False),
        ("antispam disable",          "Turn anti-spam off",                       "admin", False),
        ("antispam set <n> <action>", "Choose the limit and the action: mute, kick or ban", "admin", False),
    ],
    "roles": [
        ("role add @user @role",              "Give a role to a member",             "admin", False),
        ("role remove @user @role",           "Take a role from a member",           "admin", False),
        ("massrole add everyone @role",       "Give a role to all members",          "admin", False),
        ("massrole add bots @role",           "Give a role to all bots",             "admin", False),
        ("massrole remove everyone @role",    "Take a role from all members",        "admin", False),
        ("reactionrole add <link> <e> @role", "Give a role when people react",       "admin", False),
        ("buttonrole @role Label | ...",      "Make a panel where buttons give roles","admin", True),
        ("boosterrole @role",                 "Choose a reward role for boosters",   "admin", False),
    ],
    "tickets": [
        ("ticket setup",           "Post the panel for opening tickets",     "admin", False),
        ("ticket staffrole @role", "Choose who can see all tickets",         "admin", False),
        ("ticket close",           "Close and delete this ticket",           "mod",   False),
        ("ticket add @user",       "Let a person into this ticket",          "mod",   False),
        ("ticket remove @user",    "Remove a person from this ticket",       "mod",   False),
    ],
    "setup": [
        ("quicksetup",                "Create the basic channels and roles for you",   "admin", False),
        ("jailsetup",                 "Create the jail role and jail channel",         "admin", False),
        ("setupmute",                 "Create the mute roles",                         "admin", False),
        ("welcome set #channel",      "Choose the welcome channel",                    "admin", False),
        ("welcome removechannel",     "Stop using a welcome channel",                  "admin", False),
        ("welcome enable/disable",    "Turn welcome messages on or off",               "admin", False),
        ("welcome message <text>",    "Write your own welcome message",                "admin", False),
        ("welcome gif <link>",        "Add a picture or GIF to the welcome message",   "admin", False),
        ("welcome test",              "See how the welcome message looks",             "admin", False),
        ("bye set #channel",          "Choose the goodbye channel",                    "admin", False),
        ("bye removechannel",         "Stop using a goodbye channel",                  "admin", False),
        ("bye enable/disable",        "Turn goodbye messages on or off",               "admin", False),
        ("bye message <text>",        "Write your own goodbye message",                "admin", False),
        ("bye gif <link>",            "Add a picture or GIF to the goodbye message",   "admin", False),
        ("bye test",                  "See how the goodbye message looks",             "admin", False),
        ("boost set #channel",        "Choose where boost thank-you messages go",      "admin", False),
        ("boost message <text>",      "Write your own boost message",                  "admin", False),
        ("boost test",                "See how the boost message looks",               "admin", False),
        ("joindm message <text>",     "Write a private message sent to new members",   "admin", False),
        ("joindm disable",            "Stop sending the private welcome",              "admin", False),
        ("joindm test",               "See how the private welcome looks",             "admin", False),
        ("poj start",                 "Ping new members in chosen channels, then delete the ping", "admin", False),
        ("poj stop",                  "Turn off those pings",                          "admin", False),
        ("poj list",                  "See the channels used for those pings",         "admin", False),
        ("variables",                 "See the words you can use in your messages",    "admin", False),
        ("logs set #channel",         "Choose where moderation logs go",               "admin", False),
        ("logs disable",              "Turn logs off",                                 "admin", False),
        ("automod invite on/off",     "Block Discord invite links",                    "admin", False),
        ("counter create <type> #vc", "Show a live count in a voice channel name",     "admin", False),
    ],
    "admin": [
        ("settings",                       "See all server settings in one place",      "admin",    False),
        ("prefix",                         "See the current prefix",                    "everyone", False),
        ("prefix set <symbol>",            "Change the server prefix (up to 3 letters)","admin",    False),
        ("prefix remove",                  "Go back to the default prefix  ,",          "admin",    False),
        ("prefix self <symbol>",           "Pick your own prefix in every server",      "everyone", True),
        ("prefix selfremove",              "Remove your own prefix",                    "everyone", False),
        ("premiumrole @role",              "Choose which role gets premium AI chat",    "admin",    False),
        ("disable <command>",              "Turn off a command in this server",         "admin",    False),
        ("enable <command>",               "Turn a command back on",                    "admin",    False),
        ("announce [#ch] <message>",       "Post an announcement",                      "mod",      False),
        ("giveaway <time> <winners> <prize>","Start a giveaway. Time looks like 30m, 2h, 1d","mod", False),
        ("giveaway ... --msgs <n>",        "Members need n messages to enter",          "mod",      False),
        ("giveaway ... --invites <n>",     "Members need n invites to enter",           "mod",      False),
        ("giveaway end <message_id>",      "End a giveaway now",                        "mod",      False),
        ("giveaway reroll <message_id>",   "Pick new winners",                          "mod",      False),
        ("embed create",                   "Start building a custom message card",      "admin",    False),
        ("embed title/desc/color/image",   "Change parts of your message card",         "admin",    False),
        ("embed footer/thumbnail",         "Add a footer or a small picture",           "admin",    False),
        ("embed preview",                  "See your message card before sending",      "admin",    False),
        ("embed send [#channel]",          "Send your message card",                    "admin",    False),
        ("embed discard",                  "Throw away your message card",              "admin",    False),
        ("togglelevels on/off",            "Turn level-up messages on or off",          "admin",    False),
        ("togglereactions on/off",         "Turn automatic heart reactions on or off",  "admin",    False),
    ],
    "premium": [
        ("mypremium",               "See your premium status",                       "everyone", False),
        ("call",                    "Talk live with another server",                 "admin",    True),
        ("callinfo",                "See your call status",                          "everyone", False),
        ("hangup",                  "End the call or leave the waiting line",        "mod",      False),
        ("vcsetup create",          "Make a channel that creates private voice rooms","admin",   True),
        ("vcsetup limit <n>",       "Set how many people fit in a private room",     "admin",    True),
        ("vcsetup name <template>", "Set the room name. {user} becomes the person's name", "admin", True),
        ("vcsetup remove",          "Remove private voice rooms",                    "admin",    True),
        ("bumpreminder on/off",     "Get a reminder 2 hours after a DISBOARD bump",  "admin",    True),
        ("bumpreminder on @role",   "Ping a role for the bump reminder",             "admin",    True),
        ("bumppingrole @role",      "Choose the role to ping for bumps",             "admin",    True),
        ("setstatus <text>",        "Add your own text to the bot's status",         "admin",    True),
        ("color",                   "Pick a color role for yourself",                "everyone", True),
        ("color setup",             "Create the color roles",                        "admin",    True),
        ("serverpolish",            "Make your server look nice in one click",       "admin",    True),
    ],
    "economy": [
        ("balance [@user]",            "See your wallet and bank coins",             "everyone", False),
        ("daily",                      "Get your daily reward (150 to 350 coins)",   "everyone", False),
        ("work",                       "Work to earn coins",                         "everyone", False),
        ("deposit <amount/all>",       "Move coins from wallet to bank",             "everyone", False),
        ("withdraw <amount/all>",      "Move coins from bank to wallet",             "everyone", False),
        ("transfer @user <amount>",    "Send coins to someone",                      "everyone", False),
        ("coinflip <amount> [h/t]",    "Bet on a coin toss. 50/50 chance",           "everyone", False),
        ("slots <amount>",             "Spin the slots. Match 3 to win",             "everyone", False),
        ("rob @user",                  "Try to steal coins. 45% chance to work",     "everyone", False),
        ("richlist",                   "See the 10 richest people",                  "everyone", False),
        ("serverstatus",               "See how active the server is",               "everyone", False),
        ("trade offer @user <o> <w>",  "Offer a trade to someone",                   "everyone", False),
        ("trade accept/decline <id>",  "Accept or say no to a trade",                "everyone", False),
        ("trade cancel <id>",          "Cancel a trade you made",                    "everyone", False),
        ("trade list",                 "See your open trades",                       "everyone", False),
        ("invest in <type> <amount>",  "Invest coins: Safe, Market, Crypto or Business", "everyone", False),
        ("invest check",               "See your investment",                        "everyone", False),
        ("invest collect",             "Take your coins when the investment is ready","everyone", False),
        ("invest withdraw",            "Take your coins out early (10% fee)",        "everyone", False),
        ("givecash @user <n>",         "Give coins to a user",                       "owner",    False),
        ("takecash @user <n>",         "Take coins from a user",                     "owner",    False),
        ("resetcash @user",            "Reset a user's coins",                       "owner",    False),
    ],
    "emojis": [
        ("emoji steal <emojis>",     "Copy emojis from other servers into this one", "admin",    False),
        ("emoji search <name>",      "Search for emojis and pick from a list",       "everyone", False),
        ("emoji addid <id> <name>",  "Add an emoji using its ID",                    "admin",    False),
        ("emoji remove",             "Delete an emoji from this server",             "admin",    False),
        ("emoji rename",             "Rename an emoji in this server",               "admin",    False),
        ("emoji list",               "See how many emoji slots are used",            "everyone", False),
    ],
    "games": [
        ("guessword / gw",         "Starts Guess the Word. One player gets a secret word and the others try to guess it", "everyone", False),
        ("guesstime <seconds>",    "Starts a timed Guess the Number game from 1 to 1,000. Pick 1 to 300 seconds",      "everyone", False),
        ("guess",                  "Starts Guess the Number with a range of 1 to 100,000",                              "everyone", False),
        ("endgame",                "Ends active Guess the Number games. It does not end Guess the Word",                "everyone", False),
        ("wordchain / wc",         "Starts Word Chain. Each new word must begin with the last letter of the one before","everyone", False),
        ("mafia / ma",             "Starts Mafia, including its extra roles",                                           "everyone", False),
        ("guessagent / gta",       "Shows the Guess the Agent guide. Agent Black gets the real word, Agent Red gets a similar word and Agent White gets no word. Players describe their words, discuss and vote", "everyone", False),
        ("finalnight / fn",        "Starts Final Night. Each player gets three rooms. Assassins can only target civilians who share a room with them", "everyone", False),
        ("stats",                  "Shows server and global stats",                                                     "everyone", False),
        ("counting setup #channel","Choose the counting channel",                                                       "mod",      False),
        ("counting stats",         "See the best count so far",                                                         "everyone", False),
        ("counting reset",         "Start counting again from 0",                                                       "mod",      False),
    ],
    "extraperm": [
        ("extraperm setup",                "Create all 10 extra permission roles",    "admin",    True),
        ("extraperm",                      "See all roles and who has them",          "admin",    True),
        ("extraperm give @member <role>",  "Give a member an extra permission role",  "admin",    True),
        ("extraperm take @member <role>",  "Take an extra permission role away",      "admin",    True),
        ("extraperm list [@member]",       "See which of these roles a member has",   "everyone", True),
        ("extraperm info [rolename]",      "See what a role allows",                  "everyone", True),
        ("extraperm teardown",             "Delete all these roles",                  "admin",    True),
    ],
    "levelroles": [
        ("levelroles setup",                "Create level roles 1 to 100 and 7 permission roles", "admin", True),
        ("levelroles",                      "See the level roles overview",            "admin",    True),
        ("levelroles managechannel #ch",    "Choose channels where permissions are handled for you", "admin", True),
        ("levelroles sync",                 "Fix roles for all members",               "admin",    True),
        ("levelroles info <level>",         "See what a level gives you",              "everyone", True),
        ("levelroles grant @member @role",  "Give a member an extra role",             "admin",    True),
        ("levelroles revoke @member @role", "Take an extra role from a member",        "admin",    True),
        ("levelroles grants [@member]",     "See a member's extra roles",              "everyone", True),
        ("levelroles teardown",             "Delete all 107 created roles",            "admin",    True),
    ],
    "owner": [
        ("premium add server <id>",          "Give a server premium",                  "owner", False),
        ("premium add user <id>",            "Give a user premium",                    "owner", False),
        ("premium remove server/user <id>",  "Take premium away",                      "owner", False),
        ("premium list",                     "See everyone with premium",              "owner", False),
        ("aimode on/off",                    "Turn AI chat on or off everywhere",      "owner", False),
        ("aiblock <guild_id>",               "Block AI chat in one server",            "owner", False),
        ("maintenance on/off",               "Turn maintenance mode on or off",        "owner", False),
        ("botstatus set <act> <text>",       "Set a permanent bot status",             "owner", False),
        ("botstatus set24h <act> <text>",    "Set a bot status for 24 hours",          "owner", False),
        ("botstatus reset",                  "Remove the custom bot status",           "owner", False),
        ("serverlist",                       "See every server the bot is in",         "owner", False),
        ("leaveguild <guild_id>",            "Make the bot leave a server",            "owner", False),
        ("dm @user <message>",               "Send a private message as the bot",      "owner", False),
        ("sync [guild_id]",                  "Update the slash commands",              "owner", False),
        ("setavatar <url>",                  "Change the bot's picture",               "owner", False),
        ("setbanner <url>",                  "Change the bot's banner",                "owner", False),
        ("setbotname <name>",                "Change the bot's name",                  "owner", False),
        ("blacklist add user/server <id>",   "Block a user or server from the bot",    "owner", False),
        ("blacklist remove <id>",            "Unblock a user or server",               "owner", False),
        ("reload/load/unload <cog>",         "Update bot parts without restarting",    "owner", False),
        ("cogs",                             "See all loaded bot parts",               "owner", False),
        ("botinfo",                          "See bot servers, speed and uptime",      "owner", False),
        ("broadcast <message>",              "Message every server owner",             "owner", False),
        ("shutdown",                         "Turn the bot off safely",                "owner", False),
    ],
    "tracker": [
        ("invites [@user]",       "See how many people someone invited",     "everyone", False),
        ("inviteleaderboard",     "See the top 10 inviters",                 "everyone", False),
        ("inviteinfo <code>",     "See details about an invite link",        "everyone", False),
        ("invitelog #channel",    "Choose where invite logs go",             "admin",    False),
        ("invitelogdisable",      "Turn invite logs off",                    "admin",    False),
        ("invitereset [@user]",   "Reset invite counts",                     "admin",    False),
        ("messages [@user]",      "See how many messages someone sent",      "everyone", False),
        ("msgleaderboard",        "See the top 10 chatters",                 "everyone", False),
        ("msgstats",              "See message totals for the server",       "everyone", False),
        ("msgreset [@user]",      "Reset message counts",                    "admin",    False),
    ],
}

CAT_META: dict[str, tuple[str, str, str]] = {
    "fun":        ("Fun",           EMOJI_TADA,       "Games and jokes to enjoy with friends"),
    "roleplay":   ("Roleplay",      EMOJI_HEART,      "Hugs, pats, kisses and more"),
    "utility":    ("Utility",       EMOJI_SPARKLE,    "Handy everyday tools"),
    "profile":    ("Profile",       EMOJI_BDAY_CAKE,  "Your profile card and birthday"),
    "levels":     ("Levels",        EMOJI_DOT_YELLOW, "Earn points by chatting"),
    "mod":        ("Moderation",    EMOJI_DOT_RED,    "Keep the server safe and tidy"),
    "antispam":   ("Anti-Spam",     EMOJI_DOT_BLACK,  "Stop spam automatically"),
    "roles":      ("Roles",         EMOJI_COMMUNITY,  "Give out roles in easy ways"),
    "tickets":    ("Tickets",       EMOJI_MEMBER,     "Help members one by one"),
    "setup":      ("Server Setup",  EMOJI_HOME,       "Welcome, goodbye, logs and more"),
    "admin":      ("Admin",         EMOJI_GIVEAWAYS,  "Prefix, giveaways and announcements"),
    "premium":    ("Premium",       EMOJI_DISBOARD,   "Extra features for premium"),
    "economy":    ("Economy",       EMOJI_DOT_GREEN,  "Earn, save, trade and invest coins"),
    "emojis":     ("Custom Emojis", EMOJI_BOT,        "Add and manage emojis"),
    "games":      ("Games",         EMOJI_GHOST,      "Number, word and party games. Most work as / commands too"),
    "extraperm":  ("Extra Perms",   EMOJI_SPARKLE,    "Roles that unlock extra powers"),
    "levelroles": ("Level Roles",   EMOJI_BDAY_WORK,  "Roles that grow as members level up"),
    "owner":      ("Owner",         EMOJI_OWNER,      "Only for the bot owner"),
    "tracker":    ("Tracker",       EMOJI_COMMUNITY,  "Count invites and messages"),
}

ALL_COMMANDS = [
    (cat, syn, desc, perm, prem)
    for cat, entries in REGISTRY.items()
    for syn, desc, perm, prem in entries
]


def _clean_prefix(value) -> str:
    if not isinstance(value, str):
        return DEFAULT_PREFIX
    value = value.replace("`", "").strip()[:3]
    return value or DEFAULT_PREFIX


def _allowed(perm: str, user_level: str) -> bool:
    return PERM_ORDER.get(perm, 99) <= PERM_ORDER[user_level]


def _split_entries(cat: str, user_level: str, is_prem: bool) -> tuple[list, list]:
    ready, premium_locked = [], []
    for entry in REGISTRY.get(cat, []):
        syn, desc, perm, prem = entry
        if not _allowed(perm, user_level):
            continue
        if prem and not is_prem:
            premium_locked.append(entry)
        else:
            ready.append(entry)
    return ready, premium_locked


def _available_cats(user_level: str, is_prem: bool) -> list[str]:
    cats = []
    for key in CAT_META:
        ready, premium_locked = _split_entries(key, user_level, is_prem)
        if ready or premium_locked:
            cats.append(key)
    return cats


def _page_count(cat: str, user_level: str, is_prem: bool) -> int:
    ready, _ = _split_entries(cat, user_level, is_prem)
    return max(1, (len(ready) + PER_PAGE - 1) // PER_PAGE)


def _safe_text(text: str) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text))


def _build_home_embed(bot: discord.Client, pfx: str, user_level: str,
                      is_prem: bool, cats: list[str]) -> discord.Embed:
    status = PERM_LABELS[user_level] + (f"  ·  {EMOJI_SPARKLE} Premium" if is_prem else "")
    embed = discord.Embed(
        title=f"{EMOJI_HOME}  Happy Help",
        description=(
            f"Welcome! Here is everything you can do.\n\n"
            f"**Your prefix**  `{pfx}`\n"
            f"**Slash**  `/help`\n"
            f"**Your role**  {status}\n\n"
            f"Pick a category from the menu below.\n"
            f"Looking for something? Type `{pfx}help <word>`."
        ),
        color=PERM_COLORS[user_level]
    )
    if bot.user:
        embed.set_thumbnail(url=bot.user.display_avatar.url)

    everyone_cats, staff_cats, premium_cats = [], [], []
    for key in cats:
        label, emoji, _ = CAT_META[key]
        ready, premium_locked = _split_entries(key, user_level, is_prem)
        count = len(ready)
        entries = REGISTRY[key]
        line = f"{emoji} **{label}**  `{count}`"
        if all(e[3] for e in entries):
            premium_cats.append(line)
        elif min(PERM_ORDER[e[2]] for e in ready or premium_locked) >= 1:
            staff_cats.append(line)
        else:
            everyone_cats.append(line)

    if everyone_cats:
        embed.add_field(name="For Everyone", value="\n".join(everyone_cats), inline=True)
    if staff_cats:
        embed.add_field(name="For Staff", value="\n".join(staff_cats), inline=True)
    if premium_cats:
        embed.add_field(name=f"{EMOJI_SPARKLE} Premium", value="\n".join(premium_cats), inline=True)

    embed.set_footer(
        text="Premium is on. Mention Happy to chat with AI." if is_prem
        else f"Tip: type {pfx}mypremium to see what premium gives you"
    )
    return embed


def _build_category_embed(cat: str, pfx: str, user_level: str,
                          is_prem: bool, page: int) -> discord.Embed:
    label, emoji, desc = CAT_META[cat]
    ready, premium_locked = _split_entries(cat, user_level, is_prem)

    total_pages = max(1, (len(ready) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    chunk = ready[page * PER_PAGE:(page + 1) * PER_PAGE]

    embed = discord.Embed(title=f"{emoji}  {label}", color=PERM_COLORS[user_level])

    if not ready:
        embed.description = f"{desc}\n\nYou need premium to use the commands here."
    else:
        blocks = []
        for syn, d, perm, prem in chunk:
            badges = []
            if prem:
                badges.append(EMOJI_SPARKLE)
            if PERM_BADGE[perm]:
                badges.append(PERM_BADGE[perm])
            badge_text = "  " + "  ".join(badges) if badges else ""
            blocks.append(f"**`{pfx}{syn}`**{badge_text}\n{d}")
        embed.description = f"*{desc}*\n\n" + "\n\n".join(blocks)

    if premium_locked:
        lines = [f"`{pfx}{syn}`" for syn, _, _, _ in premium_locked[:5]]
        if len(premium_locked) > 5:
            lines.append(f"and {len(premium_locked) - 5} more")
        embed.add_field(
            name=f"{EMOJI_SPARKLE} Needs Premium",
            value="\n".join(lines),
            inline=False
        )

    footer = f"{len(ready)} commands"
    if total_pages > 1:
        footer += f"  ·  Page {page + 1} of {total_pages}"
    embed.set_footer(text=footer)
    return embed


def _build_search_embed(query: str, pfx: str, user_level: str, is_prem: bool) -> discord.Embed:
    shown = _safe_text(query)
    embed = discord.Embed(title=f"Search: {shown}", color=PERM_COLORS[user_level])

    if len(query) < 2:
        embed.description = "Please type at least 2 letters to search."
        return embed

    ready_lines, locked_lines = [], []
    for cat, syn, desc, perm, prem in ALL_COMMANDS:
        if not _allowed(perm, user_level):
            continue
        label = CAT_META[cat][0]
        if query not in syn.lower() and query not in desc.lower() and query not in label.lower():
            continue
        if prem and not is_prem:
            locked_lines.append(f"{EMOJI_SPARKLE} `{pfx}{syn}`  needs Premium")
        else:
            badge = f" {EMOJI_SPARKLE}" if prem else ""
            ready_lines.append(f"`{pfx}{syn}`{badge}\n{desc}  ·  *{label}*")

    total = len(ready_lines) + len(locked_lines)
    if total == 0:
        embed.description = f"Nothing found for **{shown}**.\nType `{pfx}help` to browse everything."
        return embed

    parts = ready_lines[:8] + locked_lines[:4]
    text = "\n\n".join(parts)
    if len(text) > 3800:
        text = text[:3800].rsplit("\n", 1)[0]
    shown_count = len(parts)
    if total > shown_count:
        text += f"\n\nand {total - shown_count} more. Try a longer word or open a category."
    embed.description = text
    embed.set_footer(text=f"{total} found")
    return embed


class CategorySelect(discord.ui.Select):
    def __init__(self, cats: list[str], user_level: str, is_prem: bool, current_cat: Optional[str]):
        options = []
        for key in cats[:25]:
            label, emoji, desc = CAT_META[key]
            ready, premium_locked = _split_entries(key, user_level, is_prem)
            count = len(ready) if ready else len(premium_locked)
            text = f"{count} commands  ·  {desc}"
            options.append(discord.SelectOption(
                label=label,
                value=key,
                emoji=emoji,
                description=text[:100],
                default=(key == current_cat)
            ))

        if not options:
            options.append(discord.SelectOption(label="Nothing to show", value="__none__"))

        super().__init__(placeholder="Pick a category", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        view: HelpView = self.view
        value = self.values[0]
        if value not in view.cats:
            return await interaction.response.defer()
        view.current_cat = value
        view.cat_page = 0
        await view.refresh(interaction)


class HelpView(discord.ui.View):
    def __init__(self, bot, author_id: int, pfx: str, user_level: str,
                 is_prem: bool, start_cat: Optional[str] = None):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.bot = bot
        self.author_id = author_id
        self.pfx = pfx
        self.user_level = user_level
        self.is_prem = is_prem
        self.cats = _available_cats(user_level, is_prem)
        self.current_cat: Optional[str] = start_cat if start_cat in self.cats else None
        self.cat_page = 0
        self.message: Optional[discord.Message] = None
        self._build_components()

    def _build_components(self):
        self.clear_items()
        self.add_item(CategorySelect(self.cats, self.user_level, self.is_prem, self.current_cat))

        home_btn = discord.ui.Button(
            label="Home", emoji=EMOJI_HOME, style=discord.ButtonStyle.secondary,
            row=1, disabled=self.current_cat is None
        )
        home_btn.callback = self._home_callback
        self.add_item(home_btn)

        back_btn = discord.ui.Button(
            label="Back", style=discord.ButtonStyle.secondary,
            row=1, disabled=self._back_disabled()
        )
        back_btn.callback = self._back_callback
        self.add_item(back_btn)

        next_btn = discord.ui.Button(
            label="Next", style=discord.ButtonStyle.primary,
            row=1, disabled=self._next_disabled()
        )
        next_btn.callback = self._next_callback
        self.add_item(next_btn)

        close_btn = discord.ui.Button(label="Close", style=discord.ButtonStyle.danger, row=1)
        close_btn.callback = self._close_callback
        self.add_item(close_btn)

    def _back_disabled(self) -> bool:
        if self.current_cat is None:
            return True
        return self.cat_page == 0 and self.cats.index(self.current_cat) == 0

    def _next_disabled(self) -> bool:
        if not self.cats:
            return True
        if self.current_cat is None:
            return False
        pages = _page_count(self.current_cat, self.user_level, self.is_prem)
        return self.cat_page >= pages - 1 and self.cats.index(self.current_cat) == len(self.cats) - 1

    def build_embed(self) -> discord.Embed:
        if self.current_cat is None:
            return _build_home_embed(self.bot, self.pfx, self.user_level, self.is_prem, self.cats)
        return _build_category_embed(
            self.current_cat, self.pfx, self.user_level, self.is_prem, self.cat_page
        )

    async def refresh(self, interaction: discord.Interaction):
        self._build_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                f"This menu belongs to someone else. Type `{self.pfx}help` to open your own.",
                ephemeral=True
            )
            return False
        return True

    async def _home_callback(self, interaction: discord.Interaction):
        self.current_cat = None
        self.cat_page = 0
        await self.refresh(interaction)

    async def _back_callback(self, interaction: discord.Interaction):
        if self.current_cat is None:
            return await interaction.response.defer()
        if self.cat_page > 0:
            self.cat_page -= 1
        else:
            index = self.cats.index(self.current_cat)
            if index > 0:
                self.current_cat = self.cats[index - 1]
                self.cat_page = _page_count(self.current_cat, self.user_level, self.is_prem) - 1
        await self.refresh(interaction)

    async def _next_callback(self, interaction: discord.Interaction):
        if not self.cats:
            return await interaction.response.defer()
        if self.current_cat is None:
            self.current_cat = self.cats[0]
            self.cat_page = 0
        else:
            pages = _page_count(self.current_cat, self.user_level, self.is_prem)
            if self.cat_page < pages - 1:
                self.cat_page += 1
            else:
                index = self.cats.index(self.current_cat)
                if index < len(self.cats) - 1:
                    self.current_cat = self.cats[index + 1]
                    self.cat_page = 0
        await self.refresh(interaction)

    async def _close_callback(self, interaction: discord.Interaction):
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
            except discord.HTTPException:
                pass


class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _get_prefix(self, guild_id: int) -> str:
        try:
            data = await settings_col.find_one({"_id": str(guild_id)})
        except Exception:
            return DEFAULT_PREFIX
        return _clean_prefix(data.get("prefix", DEFAULT_PREFIX)) if data else DEFAULT_PREFIX

    async def _get_context(self, member: discord.Member, guild: discord.Guild):
        pfx = await self._get_prefix(guild.id)
        user_level = _user_level(member, BOT_OWNER_ID)
        is_prem = member.id == BOT_OWNER_ID
        if not is_prem:
            try:
                is_prem = bool(await is_premium_user(member.id) or await is_premium_server(guild.id))
            except Exception:
                is_prem = False
        return pfx, user_level, is_prem

    @commands.command(aliases=["h", "commands", "cmds"])
    @commands.guild_only()
    @commands.cooldown(2, 10, commands.BucketType.user)
    async def help(self, ctx: commands.Context, *, query: str = None):
        pfx, user_level, is_prem = await self._get_context(ctx.author, ctx.guild)
        cats = _available_cats(user_level, is_prem)
        clean = (query or "").strip().lower()[:MAX_QUERY_LENGTH]

        start_cat = None
        if clean:
            for key in cats:
                if clean == key or clean == CAT_META[key][0].lower():
                    start_cat = key
                    break
            if start_cat is None:
                embed = _build_search_embed(clean, pfx, user_level, is_prem)
                await ctx.reply(embed=embed, mention_author=False,
                                allowed_mentions=discord.AllowedMentions.none())
                return

        view = HelpView(self.bot, ctx.author.id, pfx, user_level, is_prem, start_cat)
        view.message = await ctx.reply(
            embed=view.build_embed(), view=view, mention_author=False,
            allowed_mentions=discord.AllowedMentions.none()
        )

    @help.error
    async def help_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                f"Please wait {error.retry_after:.0f} seconds and try again.",
                mention_author=False, delete_after=5
            )
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.reply("Help only works inside a server.", mention_author=False)
        else:
            raise error

    @app_commands.command(name="help", description="See everything Happy can do")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(2, 10.0)
    @app_commands.describe(
        category="Jump straight to a category",
        search="Search for a command by name or by what it does"
    )
    @app_commands.choices(category=[
        app_commands.Choice(name="Fun",           value="fun"),
        app_commands.Choice(name="Roleplay",      value="roleplay"),
        app_commands.Choice(name="Utility",       value="utility"),
        app_commands.Choice(name="Profile",       value="profile"),
        app_commands.Choice(name="Levels",        value="levels"),
        app_commands.Choice(name="Moderation",    value="mod"),
        app_commands.Choice(name="Anti-Spam",     value="antispam"),
        app_commands.Choice(name="Roles",         value="roles"),
        app_commands.Choice(name="Tickets",       value="tickets"),
        app_commands.Choice(name="Server Setup",  value="setup"),
        app_commands.Choice(name="Admin",         value="admin"),
        app_commands.Choice(name="Premium",       value="premium"),
        app_commands.Choice(name="Economy",       value="economy"),
        app_commands.Choice(name="Custom Emojis", value="emojis"),
        app_commands.Choice(name="Games",         value="games"),
        app_commands.Choice(name="Extra Perms",   value="extraperm"),
        app_commands.Choice(name="Level Roles",   value="levelroles"),
        app_commands.Choice(name="Tracker",       value="tracker"),
    ])
    async def slash_help(self, interaction: discord.Interaction,
                         category: Optional[str] = None,
                         search: Optional[str] = None):
        await interaction.response.defer(ephemeral=True)
        pfx, user_level, is_prem = await self._get_context(interaction.user, interaction.guild)

        if search:
            clean = search.strip().lower()[:MAX_QUERY_LENGTH]
            embed = _build_search_embed(clean, pfx, user_level, is_prem)
            await interaction.followup.send(
                embed=embed, ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none()
            )
            return

        view = HelpView(self.bot, interaction.user.id, pfx, user_level, is_prem, category)
        view.message = await interaction.followup.send(
            embed=view.build_embed(), view=view, ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none()
        )

    @slash_help.error
    async def slash_help_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"Please wait {error.retry_after:.0f} seconds and try again."
        else:
            message = "Something went wrong. Please try again."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Help(bot))