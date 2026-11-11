import asyncio
import logging
from typing import NamedTuple, Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import secui
from utils.db import settings_col
from utils.helpers import BOT_OWNER_ID, is_premium_server, is_premium_user

log = logging.getLogger(__name__)

EMOJI_HOME = "<:home:1522637327149568100>"
EMOJI_OWNER = "<:owner:1522644329510862848>"
EMOJI_BDAY_CAKE = "<a:birthdaycake:1522641563153334423>"
EMOJI_BDAY_WORK = "<a:appyworkbirthday:1522641672968736961>"
EMOJI_SLOT = "<:slotmachine:1528208618179133471>"
EMOJI_BOT = "<:bot:1522522753125716098>"
EMOJI_COMMUNITY = "<:communitymembers:1522522379090137128>"
EMOJI_MEMBER = "<:member:1522522354176233594>"
EMOJI_DOT_GREEN = "<:dotgreen:1522520298539319399>"
EMOJI_DOT_YELLOW = "<:dotyellow:1522520275944734851>"
EMOJI_DOT_RED = "<:dotred:1522520224304336927>"
EMOJI_SPARKLE = "<:sparkle:1522515167995367435>"
EMOJI_HEART = "<a:bounceheart:1522493780798734358>"
EMOJI_GHOST = "<a:ghosty:1522505832288354334>"
EMOJI_GIVEAWAYS = "<:giveaways:1522646168801382410>"
EMOJI_INVEST = "<:investment:1528209880748527800>"
EMOJI_PREMIUM = "<:premium:1569047367565709322>"
EMOJI_SECURITY = "<:shield:1569120046121226402>"
EMOJI_STAFF = "<:staff:1569122067222102158>"
LOCK = "🔒"

BADGE_TRUSTED = "🔐 Trusted"

LEVEL_RANK = {"everyone": 0, "mod": 1, "admin": 2, "owner": 3}
LEVEL_LABEL = {"everyone": "Member", "mod": "Moderator", "admin": "Admin", "owner": "Bot Owner"}
PERM_BADGE = {
    "everyone": "",
    "mod": f"{EMOJI_DOT_GREEN} Mod",
    "admin": f"{EMOJI_DOT_RED} Admin",
    "trusted": BADGE_TRUSTED,
    "owner": f"{EMOJI_OWNER} Owner",
}
ACCENT = {
    "everyone": 0x5865F2,
    "mod": 0x57F287,
    "admin": 0xED4245,
    "trusted": 0x8B7CF6,
    "owner": 0xF0C040,
}
CLOSED_COLOR = 0x2B2D31

TIER_NOTE = {
    "server": "Needs Premium on this server",
    "user": "Needs Premium on your account",
    "any": "Needs Premium",
    "both": "Needs Premium on your account and this server",
}

DEFAULT_PREFIX = ","
MAX_QUERY_INPUT = 200
MAX_QUERY_LENGTH = 40
MAX_QUERY_WORDS = 5
PER_PAGE = 6
SEARCH_LIMIT = 8
VIEW_TIMEOUT = 120
RAW_STARTS = ("/", "Apps ", "@")
NO_PING = discord.AllowedMentions.none()


class Entry(NamedTuple):
    syntax: str
    desc: str
    perm: str = "everyone"
    tier: str = ""


class Viewer:
    __slots__ = ("level", "trusted", "user_premium", "server_premium")

    def __init__(self, level: str, trusted: bool, user_premium: bool, server_premium: bool):
        self.level = level
        self.trusted = trusted
        self.user_premium = user_premium
        self.server_premium = server_premium

    def can_see(self, perm: str) -> bool:
        if self.level == "owner":
            return True
        if perm == "trusted":
            return self.trusted
        rank = LEVEL_RANK.get(perm)
        if rank is None:
            return False
        return rank <= LEVEL_RANK.get(self.level, 0)

    def has_tier(self, tier: str) -> bool:
        if not tier:
            return True
        if tier == "server":
            return self.server_premium
        if tier == "user":
            return self.user_premium
        if tier == "any":
            return self.user_premium or self.server_premium
        if tier == "both":
            return self.user_premium and self.server_premium
        return False


_RAW_REGISTRY = {
    "fun": [
        ("ship @user1 @user2", "See how well two people match"),
        ("hot [@user]", "See how hot someone is"),
        ("8ball <question>", "Ask the magic 8-ball anything"),
        ("dice [sides]", "Roll a dice. Default is 6 sides"),
        ("wouldyourather", "A fun question with vote buttons"),
        ("roast [@user]", "Send a light, friendly roast"),
        ("praise [@user]", "Send a nice compliment"),
        ("shrug [text]", "Send a shrug"),
    ],
    "roleplay": [
        ("hug [@user]", "Give someone a hug"),
        ("pat [@user]", "Pat someone on the head"),
        ("slap [@user]", "Slap someone"),
        ("kiss [@user]", "Give someone a kiss"),
        ("poke [@user]", "Poke someone"),
        ("highfive [@user]", "Give someone a high-five"),
        ("bonk [@user]", "Bonk someone"),
        ("cuddle [@user]", "Cuddle with someone"),
        ("boop [@user]", "Boop someone's nose"),
        ("wave [@user]", "Wave at someone"),
        ("stare [@user]", "Stare at someone"),
    ],
    "utility": [
        ("ping", "See how fast the bot replies"),
        ("userinfo [@user]", "See details about a member"),
        ("avatar [@user]", "See a member's profile picture"),
        ("serverinfo", "See details about this server"),
        ("servercard", "See a neat card about this server"),
        ("membercount", "See how many people and bots are here"),
        ("translate <lang> <text>", "Turn text into another language"),
        ("urban <word>", "Look up a word on Urban Dictionary"),
        ("afk [reason]", "Go away. The bot tells people who ping you"),
        ("mimic @user <message>", "Send a message that looks like it is from another member", "mod"),
        ("echo [#channel] <message>", "Make the bot send a message", "mod"),
    ],
    "profile": [
        ("profile [@user]", "See a profile card"),
        ("profile bio <text>", "Write your bio (up to 150 letters)"),
        ("profile location <city>", "Set your city"),
        ("profile clear", "Delete your bio and city"),
        ("birthday [@user]", "See someone's birthday"),
        ("birthday set DD/MM", "Set your birthday. The bot wishes you"),
        ("birthday list", "See upcoming birthdays"),
        ("birthday remove", "Delete your birthday"),
        ("birthday channel #ch", "Choose where birthday wishes are posted", "admin"),
        ("birthday channelremove", "Stop posting birthday wishes", "admin"),
    ],
    "levels": [
        ("level [@user]", "See your level and progress"),
        ("leaderboard", "See the top 10 most active members"),
        ("setlevel @user <n>", "Set a member's level by hand", "admin"),
        ("resetxp [@user]", "Reset points for one member or everyone", "admin"),
    ],
    "economy": [
        ("balance [@user]", "See the HC in your wallet and bank"),
        ("daily", "Collect 150 to 350 HC once a day"),
        ("work", "Work to earn HC. You can work once an hour"),
        ("serverstatus", "See how busy the server is. A busy server pays more"),
        ("deposit <amount/all>", "Move HC from your wallet to your bank"),
        ("withdraw <amount/all>", "Move HC from your bank to your wallet"),
        ("transfer @user <amount>", "Send HC to someone. Not allowed while you have a loan"),
        ("shop", "See the items you can buy"),
        ("buy <item> [amount]", "Buy an item. Up to 20 at a time"),
        ("inventory [@user]", "See the items someone owns"),
        ("use <item>", "Use an item you own"),
        ("gift @user <item> [amount]", "Give an item to someone"),
        ("loan", "See the loan rules or your current loan"),
        ("loan take <amount>", "Borrow 100 to 10,000 HC. You pay back only what you took"),
        ("loan pay", "Pay back your loan from your wallet"),
        ("coinflip <amount> [h/t]", "Bet at least 10 HC on a coin toss. 50/50 chance"),
        ("slots <amount>", "Spin the slots. Match 3 to win"),
        ("rob @user", "Try to steal HC. 45% chance. If you get caught, you pay a fine"),
        ("richlist", "See the 10 richest people"),
        ("trade offer @user <o> <w>", "Offer a trade to someone"),
        ("trade accept/decline <id>", "Accept or say no to a trade"),
        ("trade cancel <id>", "Cancel a trade you made"),
        ("trade list", "See your open trades"),
        ("invest in <type> <amount>", "Invest HC: Safe, Market, Crypto or Business"),
        ("invest check", "See your investment"),
        ("invest collect", "Take your HC when the investment is ready"),
        ("invest withdraw", "Take your HC out early (10% fee)"),
        ("givecash @user <n>", "Give HC to a user", "owner"),
        ("takecash @user <n>", "Take HC from a user", "owner"),
        ("resetcash @user", "Reset a user's HC", "owner"),
    ],
    "games": [
        ("guessword / gw", "Start Guess the Word. One player gets a secret word and the others try to guess it"),
        ("guesstime <seconds>", "Start a timed Guess the Number game from 1 to 1,000. Pick 1 to 300 seconds"),
        ("guess", "Start Guess the Number with a range of 1 to 100,000"),
        ("endgame", "End active Guess the Number games. It does not end Guess the Word"),
        ("wordchain / wc", "Start Word Chain. Each new word must begin with the last letter of the one before"),
        ("mafia / ma", "Start Mafia, with extra roles"),
        ("guessagent / gta", "See the Guess the Agent guide. Agent Black gets the real word, Agent Red gets a similar word and Agent White gets none. Players describe their words, talk and vote"),
        ("finalnight / fn", "Start Final Night. Each player gets three rooms. Assassins can only target civilians who share a room with them"),
        ("stats", "See server and global stats"),
        ("counting setup #channel", "Choose the counting channel", "mod"),
        ("counting stats", "See the best count so far"),
        ("counting reset", "Start counting again from 0", "mod"),
    ],
    "tracker": [
        ("invites [@user]", "See how many people someone invited"),
        ("inviteleaderboard", "See the top 10 inviters"),
        ("inviteinfo <code>", "See details about an invite link"),
        ("invitelog #channel", "Choose where invite logs go", "admin"),
        ("invitelogdisable", "Turn invite logs off", "admin"),
        ("invitereset [@user]", "Reset invite counts", "admin"),
        ("messages [@user]", "See how many messages someone sent"),
        ("msgleaderboard", "See the top 10 chatters"),
        ("msgstats", "See message totals for the server"),
        ("msgreset [@user]", "Reset message counts", "admin"),
    ],
    "mod": [
        ("kick <@user/id> [reason]", "Remove a member from the server", "mod"),
        ("ban <@user/id> [reason]", "Ban someone, even if they already left", "mod"),
        ("massban <id> <id> ... [reason]", "Ban up to 50 people at once using their IDs", "mod"),
        ("tempban <@user/id> <time> [reason]", "Ban someone for a set time. Time looks like 30m, 2h or 7d", "mod"),
        ("unban <user_id> [reason]", "Unban someone using their ID", "mod"),
        ("mute <@user/id> [time] [reason]", "Stop a member from chatting. Default is 10 minutes, up to 28 days", "mod"),
        ("unmute <@user/id>", "Let a muted member chat again", "mod"),
        ("softban <@user/id> [reason]", "Ban then unban, to wipe their recent messages", "mod"),
        ("warn <@user/id> [reason]", "Give a member a warning", "mod"),
        ("warnings [@user]", "See a member's warnings", "mod"),
        ("clearwarns <@user/id>", "Delete all warnings for a member", "mod"),
        ("nickname <@user/id> [name]", "Change or reset a member's nickname", "mod"),
        ("note <@user>", "See private notes about a member", "mod"),
        ("note add <@user/id> <text>", "Add a private note", "mod"),
        ("note clear <@user/id>", "Delete all notes for a member", "mod"),
        ("cases <@user/id> [limit]", "See past actions taken on a member", "mod"),
        ("case <number>", "See one case by its number", "mod"),
        ("purge <amount>", "Delete recent messages", "mod"),
        ("purge bots/links/images/embeds <n>", "Delete only one kind of message", "mod"),
        ("purge @user <n>", "Delete recent messages from one member", "mod"),
        ("lock [#channel] [reason]", "Stop people from chatting in a channel", "mod"),
        ("unlock [#channel] [reason]", "Let people chat in a channel again", "mod"),
        ("vclock / vcunlock [#vc]", "Lock or unlock a voice channel", "mod"),
        ("lockdown [reason]", "Lock every channel in an emergency", "admin"),
        ("unlockdown", "End the lockdown", "admin"),
        ("jail <@user/id> [reason]", "Take a member's roles and send them to jail", "mod"),
        ("unjail <@user/id>", "Free a member and give their roles back", "mod"),
        ("sticky <text>", "Keep a message at the bottom of a channel", "mod"),
        ("unsticky", "Remove the sticky message here", "mod"),
        ("slowmode <seconds> [#ch]", "Set a wait time between messages (0 = off)", "mod"),
        ("topic [text]", "Set or clear the channel topic", "mod"),
        ("rename <name>", "Rename this channel", "mod"),
    ],
    "automod": [
        ("automod", "Open the Auto-Mod panel with buttons", "admin"),
        ("automod enable / disable", "Turn Auto-Mod on or off", "admin"),
        ("automod logs #channel", "Choose where Auto-Mod alerts go", "admin"),
        ("automod punishment <timeout/kick/ban>", "Choose what happens after repeated rule breaks", "admin"),
        ("automod limit <msgs> <repeats> <pings>", "Set the spam limits. Example: 5 3 6", "admin"),
        ("automod ignore #channel / @role", "Skip a channel, category or role. Run it again to undo", "admin"),
        ("automod words", "See the list of blocked words", "admin"),
        ("automod words add <word>", "Block a word or phrase. End with * to catch longer words", "admin"),
        ("automod words remove <word>", "Unblock a word", "admin"),
        ("automod words clear", "Empty the blocked word list", "admin"),
        ("automod phishing", "See the scam link protection", "admin"),
        ("automod phishing block <site>", "Block a website that is not safe", "admin"),
        ("automod phishing unblock <site>", "Unblock a website you blocked", "admin"),
        ("automod phishing safe <site>", "Always allow a website", "admin"),
        ("automod phishing unsafe <site>", "Stop always allowing a website", "admin"),
        ("automod phishing action <ban/kick/timeout>", "Choose what happens to people who post scam links", "admin"),
    ],
    "antinuke": [
        ("antinuke", "Open the Anti-Nuke panel with buttons", "trusted"),
        ("antinuke enable / disable", "Turn Anti-Nuke on or off", "trusted"),
        ("antinuke whitelist", "See the trusted people", "trusted"),
        ("antinuke whitelist add @member", "Make someone trusted. They are never stopped", "trusted"),
        ("antinuke whitelist remove @member", "Take trust away from someone", "trusted"),
        ("antinuke punishment <strip/kick/ban>", "Choose what happens to someone who breaks the limits", "trusted"),
        ("antinuke logs #channel", "Choose where alerts go", "trusted"),
        ("antinuke limit <action> <1-20>", "Set how many times someone can do an action in 15 seconds", "trusted"),
        ("antinuke bots", "See the bots you approved", "trusted"),
        ("antinuke bots allow <bot id>", "Approve a bot so it can be added", "trusted"),
        ("antinuke bots deny <bot id>", "Take approval away from a bot", "trusted"),
    ],
    "antiraid": [
        ("antiraid", "Open the Anti-Raid panel with buttons", "trusted"),
        ("antiraid enable / disable", "Turn Anti-Raid on or off", "trusted"),
        ("antiraid action <ban/kick/timeout>", "Choose what happens to raiders", "trusted"),
        ("antiraid limit <joins> <seconds>", "Set when a raid starts. Example: 6 joins in 10 seconds", "trusted"),
        ("antiraid age <days>", "Kick new accounts younger than this. Use 0 for off", "trusted"),
        ("antiraid duration <minutes>", "Set how long raid mode lasts", "trusted"),
        ("antiraid logs #channel", "Choose where raid alerts go", "trusted"),
        ("antiraid start", "Start raid mode right now", "trusted"),
        ("antiraid stop", "End raid mode and go back to normal", "trusted"),
    ],
    "fakeperms": [
        ("fakeperms", "See which roles have fake permissions", "trusted"),
        ("fakeperms grant @role <permission>", "Let a role use bot commands without real permission", "trusted"),
        ("fakeperms revoke @role <permission>", "Take a fake permission away", "trusted"),
        ("fakeperms reset @role", "Remove all fake permissions from a role", "trusted"),
    ],
    "roles": [
        ("role add @user @role", "Give a role to a member", "admin"),
        ("role remove @user @role", "Take a role from a member", "admin"),
        ("massrole add <everyone/bots> @role", "Give a role to all members or all bots", "admin"),
        ("massrole remove <everyone/bots> @role", "Take a role from all members or all bots", "admin"),
        ("reactionrole add <link> <e> @role", "Give a role when people react", "admin"),
        ("buttonrole @role Label | ...", "Make a panel where buttons give roles", "admin", "any"),
        ("boosterrole @role", "Choose a reward role for boosters", "admin"),
    ],
    "tickets": [
        ("ticket setup", "Post the panel for opening tickets", "admin"),
        ("ticket staffrole @role", "Choose who can see all tickets", "admin"),
        ("ticket close", "Close and delete this ticket", "mod"),
        ("ticket add @user", "Let a person into this ticket", "mod"),
        ("ticket remove @user", "Remove a person from this ticket", "mod"),
    ],
    "setup": [
        ("quicksetup", "Create the basic channels and roles for you", "admin"),
        ("jailsetup", "Create the jail role and jail channel", "admin"),
        ("setupmute", "Create the mute roles", "admin"),
        ("welcome set #channel", "Choose the welcome channel", "admin"),
        ("welcome removechannel", "Stop using a welcome channel", "admin"),
        ("welcome enable/disable", "Turn welcome messages on or off", "admin"),
        ("welcome message <text>", "Write your own welcome message", "admin"),
        ("welcome gif <link>", "Add a picture or GIF to the welcome message", "admin"),
        ("welcome test", "See how the welcome message looks", "admin"),
        ("bye set #channel", "Choose the goodbye channel", "admin"),
        ("bye removechannel", "Stop using a goodbye channel", "admin"),
        ("bye enable/disable", "Turn goodbye messages on or off", "admin"),
        ("bye message <text>", "Write your own goodbye message", "admin"),
        ("bye gif <link>", "Add a picture or GIF to the goodbye message", "admin"),
        ("bye test", "See how the goodbye message looks", "admin"),
        ("boost set #channel", "Choose where boost thank-you messages go", "admin"),
        ("boost message <text>", "Write your own boost message", "admin"),
        ("boost test", "See how the boost message looks", "admin"),
        ("joindm message <text>", "Write a private message sent to new members", "admin"),
        ("joindm disable", "Stop sending the private welcome", "admin"),
        ("joindm test", "See how the private welcome looks", "admin"),
        ("poj start", "Ping new members in chosen channels, then delete the ping", "admin"),
        ("poj stop", "Turn off those pings", "admin"),
        ("poj list", "See the channels used for those pings", "admin"),
        ("variables", "See the words you can use in your messages", "admin"),
        ("logs set #channel", "Choose where moderation logs go", "admin"),
        ("logs disable", "Turn logs off", "admin"),
        ("counter create <type> #vc", "Show a live count in a voice channel name", "admin"),
    ],
    "admin": [
        ("settings", "See all server settings in one place", "admin"),
        ("prefix", "See the symbol used before commands"),
        ("prefix set <symbol>", "Change the server's command symbol (up to 3 letters)", "admin"),
        ("prefix remove", "Go back to the default symbol  ,", "admin"),
        ("prefix self <symbol>", "Pick your own command symbol in every server", "everyone", "any"),
        ("prefix selfremove", "Remove your own command symbol"),
        ("premiumrole @role", "Choose which role gets premium AI chat", "admin"),
        ("disable <command>", "Turn off a command in this server", "admin"),
        ("enable <command>", "Turn a command back on", "admin"),
        ("announce [#ch] <message>", "Post an announcement", "mod"),
        ("giveaway <time> <winners> <prize>", "Start a giveaway. Time looks like 30m, 2h, 1d", "mod"),
        ("giveaway ... --msgs <n>", "Members need n messages to enter", "mod"),
        ("giveaway ... --invites <n>", "Members need n invites to enter", "mod"),
        ("giveaway end <message_id>", "End a giveaway now", "mod"),
        ("giveaway reroll <message_id>", "Pick new winners", "mod"),
        ("embed create", "Start building a custom message card", "admin"),
        ("embed title/desc/color/image", "Change parts of your message card", "admin"),
        ("embed footer/thumbnail", "Add a footer or a small picture", "admin"),
        ("embed preview", "See your message card before sending", "admin"),
        ("embed send [#channel]", "Send your message card", "admin"),
        ("embed discard", "Throw away your message card", "admin"),
        ("togglelevels on/off", "Turn level-up messages on or off", "admin"),
        ("togglereactions on/off", "Turn automatic heart reactions on or off", "admin"),
    ],
    "emojis": [
        ("emoji steal", "Add emojis, emoji links or a sticker. Reply to a message, or paste them after the command", "admin"),
        ("emoji rename <emoji> <name>", "Change the name of an emoji", "admin"),
        ("emoji remove <emoji>", "Delete an emoji from this server", "admin"),
        ("emoji list", "See all emojis and how much space is left", "admin"),
        ("Apps ▸ Steal Emojis & Stickers", "Press and hold any message, choose Apps, then add its emojis and stickers. Works everywhere once I am on your profile"),
        ("/sendemoji <text>", "Send a message with any emoji, even without Nitro. Write names like :fire:"),
        ("/emojisearch <name>", "Find emojis from your other servers and add them here", "admin"),
    ],
    "ai": [
        ("@Happy <message>", "Chat with me. Mention me or reply to my message", "everyone", "both"),
        ("aiserver", "See the AI chat settings for this server", "admin"),
        ("aiserver on/off", "Turn AI chat on or off in this server", "admin"),
        ("aichannel", "See the channels where I chat", "admin"),
        ("aichannel add #channel", "Let me chat in a channel. You can add up to 5", "admin"),
        ("aichannel remove #channel", "Stop me from chatting in a channel", "admin"),
        ("aichannel reset", "Clear the list. I go back to channels named happy, bot or commands", "admin"),
    ],
    "premium": [
        ("perks", "See your Premium status and what it gives you"),
        ("badge [on/off]", "Show or hide the Premium badge before your name", "everyone", "user"),
        ("color [name]", "Pick a color role for your name", "everyone", "server"),
        ("/catch <link>", "Save a message as a picture and get it in your DMs. Costs HC, half price with Premium"),
        ("Apps ▸ Catch", "Press and hold a message, choose Apps, then Catch. Add me to your profile first"),
        ("call", "Talk live with another server", "admin", "server"),
        ("callinfo", "See your call status"),
        ("hangup", "End the call or leave the waiting line", "admin"),
        ("vcsetup", "See your private voice room settings", "admin", "server"),
        ("vcsetup create", "Make a channel that creates private voice rooms", "admin", "server"),
        ("vcsetup limit <0-99>", "Set how many people fit in a private room. Use 0 for no limit", "admin", "server"),
        ("vcsetup name <name>", "Set the room name. {user} becomes the person's name", "admin", "server"),
        ("vcsetup remove", "Remove private voice rooms", "admin", "server"),
        ("bumpreminder on/off", "Get a reminder 2 hours after a DISBOARD bump", "admin", "server"),
        ("bumpreminder on @role", "Ping a role for the bump reminder", "admin", "server"),
        ("bumppingrole @role", "Choose the role to ping for bumps", "admin", "server"),
        ("setstatus <text>", "Add your own text to the bot's status", "admin", "server"),
        ("setstatus remove", "Remove your server's status text", "admin", "server"),
    ],
    "studio": [
        ("studio", "See all Server Studio settings in one place", "admin"),
        ("studio channel #channel <mode>", "Choose what a channel is for: media, chat, links, embeds or off", "admin", "server"),
        ("studio autoreact add #channel <emoji> [type]", "React to messages in a channel. Type can be all, media, images, videos, links or text", "admin", "server"),
        ("studio autoreact remove #channel [emoji]", "Stop reacting in a channel", "admin"),
        ("studio autopin #channel", "Pin new messages in a channel by themselves. Run it again to turn it off", "admin", "server"),
        ("studio autorole @role", "Give new members a role. Run it with no role to turn it off", "admin", "server"),
        ("studio milestone #channel", "Choose where member milestones are posted", "admin", "server"),
        ("studio badge on/off", "Show a badge before the names of Premium members", "admin"),
        ("studio badge symbol <symbol>", "Pick the badge symbol", "admin"),
        ("studio badge sync", "Update all names right now", "admin"),
        ("studio colors", "Post the menu where members pick a color", "admin", "server"),
        ("studio colors setup", "Create the color roles", "admin", "server"),
        ("studio colors add <name> <hex>", "Add your own color. Example: Ocean #1E90FF", "admin", "server"),
        ("studio colors remove <name>", "Remove a color", "admin"),
        ("glowup", "Make your server look nice in one click", "admin", "server"),
    ],
    "extraperm": [
        ("extraperm setup", "Create all 10 extra permission roles", "admin", "any"),
        ("extraperm", "See all roles and who has them", "admin", "any"),
        ("extraperm give @member <role>", "Give a member an extra permission role", "admin", "any"),
        ("extraperm take @member <role>", "Take an extra permission role away", "admin", "any"),
        ("extraperm list [@member]", "See which of these roles a member has", "everyone", "any"),
        ("extraperm info [rolename]", "See what a role allows", "everyone", "any"),
        ("extraperm teardown", "Delete all these roles", "admin", "any"),
    ],
    "levelroles": [
        ("levelroles setup", "Create level roles 1 to 100 and 7 permission roles", "admin", "any"),
        ("levelroles", "See the level roles overview", "admin", "any"),
        ("levelroles managechannel #ch", "Choose channels where permissions are handled for you", "admin", "any"),
        ("levelroles sync", "Fix roles for all members", "admin", "any"),
        ("levelroles info <level>", "See what a level gives you", "everyone", "any"),
        ("levelroles grant @member @role", "Give a member an extra role", "admin", "any"),
        ("levelroles revoke @member @role", "Take an extra role from a member", "admin", "any"),
        ("levelroles grants [@member]", "See a member's extra roles", "everyone", "any"),
        ("levelroles teardown", "Delete all 107 created roles", "admin", "any"),
    ],
    "owner": [
        ("premium", "See everyone who has Premium", "owner"),
        ("premium add server <id>", "Give a server Premium", "owner"),
        ("premium add user <id>", "Give a user Premium", "owner"),
        ("premium remove server/user <id>", "Take Premium away", "owner"),
        ("premium info <server id>", "See Premium details for one server", "owner"),
        ("aimode on/off", "Turn AI chat on or off everywhere", "owner"),
        ("aiblock <server id> [remove]", "Block AI chat in one server. Add remove to unblock", "owner"),
        ("maintenance on/off", "Turn maintenance mode on or off", "owner"),
        ("botstatus set <act> <text>", "Set a permanent bot status", "owner"),
        ("botstatus set24h <act> <text>", "Set a bot status for 24 hours", "owner"),
        ("botstatus reset", "Remove the custom bot status", "owner"),
        ("serverlist", "See every server the bot is in", "owner"),
        ("leaveguild <server id>", "Make the bot leave a server", "owner"),
        ("dm @user <message>", "Send a private message as the bot", "owner"),
        ("sync [server id]", "Update the / commands", "owner"),
        ("setavatar <url>", "Change the bot's picture", "owner"),
        ("setbanner <url>", "Change the bot's banner", "owner"),
        ("setbotname <name>", "Change the bot's name", "owner"),
        ("blacklist add user/server <id>", "Block a user or server from the bot", "owner"),
        ("blacklist remove <id>", "Unblock a user or server", "owner"),
        ("reload/load/unload <name>", "Update parts of the bot without restarting", "owner"),
        ("cogs", "See all loaded parts of the bot", "owner"),
        ("botinfo", "See bot servers, speed and uptime", "owner"),
        ("broadcast <message>", "Message every server owner", "owner"),
        ("shutdown", "Turn the bot off safely", "owner"),
    ],
}

REGISTRY: dict[str, list[Entry]] = {
    cat: [Entry(*row) for row in rows] for cat, rows in _RAW_REGISTRY.items()
}

CAT_META: dict[str, tuple[str, str, str, str]] = {
    "fun": ("Fun", EMOJI_SLOT, "Games and jokes to enjoy with friends", "everyone"),
    "roleplay": ("Roleplay", EMOJI_HEART, "Hugs, pats, kisses and more", "everyone"),
    "utility": ("Utility", EMOJI_SPARKLE, "Handy everyday tools", "everyone"),
    "profile": ("Profile", EMOJI_BDAY_CAKE, "Your profile card and birthday", "everyone"),
    "levels": ("Levels", EMOJI_DOT_YELLOW, "Earn points by chatting", "everyone"),
    "economy": ("Economy", EMOJI_INVEST, "Earn, save, borrow, trade and invest HC", "everyone"),
    "games": ("Games", EMOJI_GHOST, "Number, word and party games. Most also work with /", "everyone"),
    "tracker": ("Tracker", EMOJI_COMMUNITY, "Count invites and messages", "everyone"),
    "mod": ("Moderation", EMOJI_DOT_RED, "Keep the server safe and tidy. Kick, ban, mute, warn, lock and clear also work with /", "staff"),
    "roles": ("Roles", EMOJI_COMMUNITY, "Give out roles in easy ways", "staff"),
    "tickets": ("Tickets", EMOJI_MEMBER, "Help members one by one", "staff"),
    "setup": ("Server Setup", EMOJI_HOME, "Welcome, goodbye, logs and more", "staff"),
    "admin": ("Admin", EMOJI_GIVEAWAYS, "Command symbol, giveaways and announcements", "staff"),
    "emojis": ("Custom Emojis", EMOJI_BOT, "Add and manage emojis and stickers. Each one costs a small amount of HC", "staff"),
    "ai": ("AI Chat", "💬", "Chat with me and choose where I talk", "staff"),
    "automod": ("Auto-Mod", "🤖", "Stops spam, scam links and bad words for you", "security"),
    "antinuke": ("Anti-Nuke", "🛡️", "Stops anyone from wrecking your server", "security"),
    "antiraid": ("Anti-Raid", "🚨", "Stops sudden floods of new members", "security"),
    "fakeperms": ("Fake Perms", "🎭", "Let staff use bot commands without real permissions", "security"),
    "premium": ("Premium", EMOJI_PREMIUM, "Perks you unlock with Premium", "premium"),
    "studio": ("Server Studio", "🎨", "Make your server look and feel special", "premium"),
    "extraperm": ("Extra Perms", EMOJI_SPARKLE, "Roles that unlock extra powers", "premium"),
    "levelroles": ("Level Roles", EMOJI_BDAY_WORK, "Roles that grow as members level up", "premium"),
    "owner": ("Owner", EMOJI_OWNER, "Only for the bot owner", "owner"),
}

GROUPS = (
    ("everyone", f"{EMOJI_COMMUNITY} Everyone"),
    ("staff", f"{EMOJI_STAFF} Staff"),
    ("security", f"{EMOJI_SECURITY} Security"),
    ("premium", f"{EMOJI_PREMIUM} Premium"),
    ("owner", f"{EMOJI_OWNER} Owner"),
)

CAT_ALIASES = {
    "moderation": "mod",
    "auto-mod": "automod",
    "anti-nuke": "antinuke",
    "nuke": "antinuke",
    "anti-raid": "antiraid",
    "raid": "antiraid",
    "fake": "fakeperms",
    "fake perms": "fakeperms",
    "emoji": "emojis",
    "emotes": "emojis",
    "money": "economy",
    "hc": "economy",
    "aichat": "ai",
    "ai chat": "ai",
    "server studio": "studio",
    "perks": "premium",
}

ALL_COMMANDS = [(cat, entry) for cat, rows in REGISTRY.items() for entry in rows]

SLASH_CHOICES = [
    app_commands.Choice(name=meta[0], value=key)
    for key, meta in CAT_META.items()
    if key != "owner"
]


def _clean_prefix(value) -> str:
    if not isinstance(value, str):
        return DEFAULT_PREFIX
    value = "".join(ch for ch in value if ch.isprintable() and ch not in "`\\")
    return value.strip()[:3] or DEFAULT_PREFIX


def _clean_query(value: Optional[str]) -> str:
    return " ".join((value or "")[:MAX_QUERY_INPUT].lower().split())[:MAX_QUERY_LENGTH]


def _safe_text(text: str) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text))


def _user_level(member: discord.Member) -> str:
    if member.id == BOT_OWNER_ID:
        return "owner"
    perms = member.guild_permissions
    if perms.administrator:
        return "admin"
    if perms.manage_messages or perms.kick_members or perms.ban_members or perms.moderate_members:
        return "mod"
    return "everyone"


def _accent(viewer: Viewer) -> int:
    if viewer.level == "owner":
        return ACCENT["owner"]
    if viewer.trusted:
        return ACCENT["trusted"]
    return ACCENT.get(viewer.level, ACCENT["everyone"])


def _status(viewer: Viewer) -> str:
    parts = [LEVEL_LABEL.get(viewer.level, "Member")]
    if viewer.trusted and viewer.level != "owner":
        parts.append(BADGE_TRUSTED)
    if viewer.user_premium:
        parts.append(f"{EMOJI_PREMIUM} Premium member")
    if viewer.server_premium:
        parts.append("Premium server")
    return "  ·  ".join(parts)


def _show(pfx: str, syntax: str) -> str:
    return syntax if syntax.startswith(RAW_STARTS) else f"{pfx}{syntax}"


def _visible(cat: str, viewer: Viewer) -> list[Entry]:
    return [entry for entry in REGISTRY.get(cat, ()) if viewer.can_see(entry.perm)]


def _available_cats(viewer: Viewer) -> list[str]:
    return [key for key in CAT_META if _visible(key, viewer)]


def _page_count(cat: str, viewer: Viewer) -> int:
    total = len(_visible(cat, viewer))
    return max(1, -(-total // PER_PAGE))


def _find_cat(word: str, cats: list[str]) -> Optional[str]:
    word = CAT_ALIASES.get(word, word)
    for key in cats:
        if word == key or word == CAT_META[key][0].lower():
            return key
    return None


def _block(pfx: str, entry: Entry, viewer: Viewer, label: Optional[str] = None) -> str:
    tags = []
    badge = PERM_BADGE.get(entry.perm, "")
    if badge:
        tags.append(badge)
    if entry.tier:
        tags.append(f"{EMOJI_SPARKLE} Premium")
    head = f"**`{_show(pfx, entry.syntax)}`**"
    if tags:
        head += "  " + "  ".join(tags)
    detail = f"> {entry.desc}"
    if label:
        detail += f"  ·  *{label}*"
    lines = [head, detail]
    if not viewer.has_tier(entry.tier):
        lines.append(f"> {LOCK} *{TIER_NOTE.get(entry.tier, 'Needs Premium')}*")
    return "\n".join(lines)


def _home_embed(bot: discord.Client, pfx: str, viewer: Viewer, cats: list[str]) -> discord.Embed:
    embed = discord.Embed(
        title=f"{EMOJI_HOME}  Happy Help",
        description=(
            "Everything I can do is listed here.\n"
            "Pick a topic from the menu below.\n\n"
            f"**Command symbol**  `{pfx}`\n"
            f"**You are**  {_status(viewer)}\n"
            f"**Search**  `{pfx}help <word>`"
        ),
        color=_accent(viewer),
    )
    if bot.user:
        embed.set_thumbnail(url=bot.user.display_avatar.url)

    for group, title in GROUPS:
        lines = [
            f"{CAT_META[key][1]} **{CAT_META[key][0]}**  `{len(_visible(key, viewer))}`"
            for key in cats
            if CAT_META[key][3] == group
        ]
        if lines:
            embed.add_field(name=title, value="\n".join(lines), inline=True)

    if viewer.user_premium or viewer.server_premium:
        embed.set_footer(text=f"Premium is active. Type {pfx}perks to see your perks.")
    else:
        embed.set_footer(text=f"Type {pfx}perks to see what Premium gives you.")
    return embed


def _category_embed(cat: str, pfx: str, viewer: Viewer, page: int) -> discord.Embed:
    label, emoji, desc, _ = CAT_META[cat]
    entries = _visible(cat, viewer)
    pages = _page_count(cat, viewer)
    page = max(0, min(page, pages - 1))
    chunk = entries[page * PER_PAGE:(page + 1) * PER_PAGE]

    embed = discord.Embed(title=f"{emoji}  {label}", color=_accent(viewer))
    blocks = [_block(pfx, entry, viewer) for entry in chunk]
    body = "\n\n".join(blocks) if blocks else "Nothing here for you yet."
    embed.description = f"*{desc}*\n\n{body}"[:4000]

    footer = f"{len(entries)} commands"
    if pages > 1:
        footer += f"  ·  Page {page + 1} of {pages}"
    embed.set_footer(text=footer)
    return embed


def _search_embed(query: str, pfx: str, viewer: Viewer) -> discord.Embed:
    shown = _safe_text(query)
    embed = discord.Embed(title=f"{EMOJI_HOME}  Search", color=_accent(viewer))

    words = query.split()[:MAX_QUERY_WORDS]
    if len(query) < 2 or not words:
        embed.description = "Please type at least 2 letters to search."
        return embed

    hits = []
    for cat, entry in ALL_COMMANDS:
        if not viewer.can_see(entry.perm):
            continue
        label = CAT_META[cat][0]
        text = f"{entry.syntax} {entry.desc} {label}".lower()
        if all(word in text for word in words):
            rank = 0 if query in entry.syntax.lower() else 1
            hits.append((rank, label, entry))
    hits.sort(key=lambda hit: hit[0])

    if not hits:
        embed.description = f"Nothing found for **{shown}**.\nType `{pfx}help` to see everything."
        return embed

    parts, size = [], 0
    for _, label, entry in hits[:SEARCH_LIMIT]:
        block = _block(pfx, entry, viewer, label)
        if size + len(block) + 2 > 3400:
            break
        parts.append(block)
        size += len(block) + 2

    text = "\n\n".join(parts)
    extra = len(hits) - len(parts)
    if extra > 0:
        text += f"\n\n*And {extra} more. Try a longer word or open a topic.*"
    embed.description = f"Results for **{shown}**\n\n{text}"
    embed.set_footer(text=f"{len(hits)} found")
    return embed


class CategorySelect(discord.ui.Select):
    def __init__(self, cats: list[str], viewer: Viewer, current: Optional[str]):
        options = []
        for key in cats[:25]:
            label, emoji, desc, _ = CAT_META[key]
            count = len(_visible(key, viewer))
            options.append(discord.SelectOption(
                label=label,
                value=key,
                emoji=emoji,
                description=f"{count} commands  ·  {desc}"[:100],
                default=(key == current),
            ))
        if not options:
            options.append(discord.SelectOption(label="Nothing to show", value="__none__"))
        super().__init__(placeholder="Pick a topic", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        view: "HelpView" = self.view
        value = self.values[0]
        if value not in view.cats:
            return await interaction.response.defer()
        view.current_cat = value
        view.page = 0
        await view.refresh(interaction)


class HelpView(discord.ui.View):
    def __init__(self, bot, registry: dict, key: tuple, pfx: str, viewer: Viewer, start_cat: Optional[str] = None):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.bot = bot
        self.registry = registry
        self.key = key
        self.user_id = key[1]
        self.pfx = pfx
        self.viewer = viewer
        self.cats = _available_cats(viewer)
        self.current_cat: Optional[str] = start_cat if start_cat in self.cats else None
        self.page = 0
        self.message: Optional[discord.Message] = None
        self.select = CategorySelect(self.cats, viewer, self.current_cat)
        self.add_item(self.select)
        self.sync()

    def release(self):
        if self.registry.get(self.key) is self:
            del self.registry[self.key]

    def sync(self):
        for option in self.select.options:
            option.default = option.value == self.current_cat
        at_home = self.current_cat is None
        self.home_button.disabled = at_home
        self.back_button.disabled = at_home
        self.next_button.disabled = not self.cats or self.at_end()
        if at_home:
            self.counter_button.label = "Menu"
        else:
            self.counter_button.label = f"{self.page + 1} / {_page_count(self.current_cat, self.viewer)}"

    def at_end(self) -> bool:
        if self.current_cat is None:
            return False
        last_page = self.page >= _page_count(self.current_cat, self.viewer) - 1
        return last_page and self.cats.index(self.current_cat) == len(self.cats) - 1

    def build_embed(self) -> discord.Embed:
        if self.current_cat is None:
            return _home_embed(self.bot, self.pfx, self.viewer, self.cats)
        return _category_embed(self.current_cat, self.pfx, self.viewer, self.page)

    async def refresh(self, interaction: discord.Interaction):
        self.sync()
        try:
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        except discord.HTTPException:
            log.warning("Help menu could not be updated", exc_info=True)

    async def disable(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                f"This menu belongs to someone else. Type `{self.pfx}help` to open your own.",
                ephemeral=True,
                allowed_mentions=NO_PING,
            )
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        log.error("Help menu error", exc_info=error)
        text = "Something went wrong. Please open the help menu again."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass

    async def on_timeout(self):
        self.release()
        await self.disable()

    @discord.ui.button(label="Home", emoji=EMOJI_HOME, style=discord.ButtonStyle.secondary, row=1)
    async def home_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_cat = None
        self.page = 0
        await self.refresh(interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_cat is None:
            return await interaction.response.defer()
        if self.page > 0:
            self.page -= 1
        else:
            index = self.cats.index(self.current_cat)
            if index == 0:
                self.current_cat = None
            else:
                self.current_cat = self.cats[index - 1]
                self.page = _page_count(self.current_cat, self.viewer) - 1
        await self.refresh(interaction)

    @discord.ui.button(label="Menu", style=discord.ButtonStyle.secondary, row=1, disabled=True)
    async def counter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=1)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.cats:
            return await interaction.response.defer()
        if self.current_cat is None:
            self.current_cat = self.cats[0]
            self.page = 0
        elif self.page < _page_count(self.current_cat, self.viewer) - 1:
            self.page += 1
        else:
            index = self.cats.index(self.current_cat)
            if index < len(self.cats) - 1:
                self.current_cat = self.cats[index + 1]
                self.page = 0
        await self.refresh(interaction)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=1)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.release()
        self.stop()
        try:
            await interaction.response.edit_message(
                embed=discord.Embed(description="Help menu closed.", color=CLOSED_COLOR),
                view=None,
            )
        except discord.HTTPException:
            pass


async def _flag(awaitable) -> bool:
    try:
        return bool(await awaitable)
    except Exception:
        log.warning("Help lookup failed", exc_info=True)
        return False


class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.open_views: dict[tuple, HelpView] = {}

    async def cog_unload(self):
        views = list(self.open_views.values())
        self.open_views.clear()
        for view in views:
            view.stop()
            await view.disable()

    async def _get_prefix(self, guild_id: int) -> str:
        try:
            data = await settings_col.find_one({"_id": str(guild_id)})
        except Exception:
            log.warning("Help could not read the prefix", exc_info=True)
            return DEFAULT_PREFIX
        if not isinstance(data, dict):
            return DEFAULT_PREFIX
        return _clean_prefix(data.get("prefix", DEFAULT_PREFIX))

    async def _get_context(self, member: discord.Member, guild: discord.Guild) -> tuple[str, Viewer]:
        pfx = await self._get_prefix(guild.id)
        level = _user_level(member)
        if level == "owner":
            return pfx, Viewer(level, True, True, True)
        trusted, user_premium, server_premium = await asyncio.gather(
            _flag(secui.is_trusted(self.bot, guild, member.id)),
            _flag(is_premium_user(member.id)),
            _flag(is_premium_server(guild.id)),
        )
        return pfx, Viewer(level, trusted, user_premium, server_premium)

    async def _start_view(self, guild_id: int, user_id: int, pfx: str, viewer: Viewer, category: Optional[str]) -> HelpView:
        key = (guild_id, user_id)
        old = self.open_views.pop(key, None)
        if old is not None:
            old.stop()
            await old.disable()
        view = HelpView(self.bot, self.open_views, key, pfx, viewer, category)
        self.open_views[key] = view
        return view

    @commands.command(name="help", aliases=["h", "commands", "cmds"])
    @commands.guild_only()
    @commands.cooldown(2, 10, commands.BucketType.user)
    async def help(self, ctx: commands.Context, *, query: str = None):
        if not ctx.channel.permissions_for(ctx.guild.me).embed_links:
            return await ctx.reply(
                "I need the Embed Links permission to show the help menu.",
                mention_author=False, allowed_mentions=NO_PING,
            )

        pfx, viewer = await self._get_context(ctx.author, ctx.guild)
        cats = _available_cats(viewer)
        clean = _clean_query(query)

        start_cat = None
        if clean:
            start_cat = _find_cat(clean, cats)
            if start_cat is None:
                return await ctx.reply(
                    embed=_search_embed(clean, pfx, viewer),
                    mention_author=False, allowed_mentions=NO_PING,
                )

        view = await self._start_view(ctx.guild.id, ctx.author.id, pfx, viewer, start_cat)
        try:
            view.message = await ctx.reply(
                embed=view.build_embed(), view=view,
                mention_author=False, allowed_mentions=NO_PING,
            )
        except discord.HTTPException:
            view.release()
            raise

    @help.error
    async def help_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                f"Please wait {error.retry_after:.0f} seconds and try again.",
                mention_author=False, delete_after=5, allowed_mentions=NO_PING,
            )
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.reply("Help only works inside a server.", mention_author=False, allowed_mentions=NO_PING)
        else:
            raise error

    @app_commands.command(name="help", description="See everything Happy can do")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(2, 10.0)
    @app_commands.describe(
        category="Jump straight to a topic",
        search="Search for a command by name or by what it does",
    )
    @app_commands.choices(category=SLASH_CHOICES)
    async def slash_help(self, interaction: discord.Interaction,
                         category: Optional[str] = None,
                         search: Optional[str] = None):
        await interaction.response.defer(ephemeral=True)

        member = interaction.user
        if interaction.guild is None or not isinstance(member, discord.Member):
            return await interaction.followup.send("Help only works inside a server.", ephemeral=True)

        pfx, viewer = await self._get_context(member, interaction.guild)

        if search:
            return await interaction.followup.send(
                embed=_search_embed(_clean_query(search), pfx, viewer),
                ephemeral=True, allowed_mentions=NO_PING,
            )

        view = await self._start_view(interaction.guild.id, member.id, pfx, viewer, category)
        try:
            view.message = await interaction.followup.send(
                embed=view.build_embed(), view=view,
                ephemeral=True, allowed_mentions=NO_PING,
            )
        except discord.HTTPException:
            view.release()
            raise

    @slash_help.error
    async def slash_help_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"Please wait {error.retry_after:.0f} seconds and try again."
        else:
            log.error("Help command error", exc_info=error)
            message = "Something went wrong. Please try again in a moment."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Help(bot))