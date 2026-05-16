"""
cogs/help.py — Permission-aware help system.
Shows only commands the user can actually use.
"""

import discord
from discord.ext import commands
from discord import app_commands

from utils.helpers import (
    BOT_OWNER_ID, is_premium_user, is_premium_server
)

# ── Command registry ──────────────────────────────────────────────────────────
# Each entry: (command, description, min_permission, is_premium)
# min_permission: "everyone" | "mod" | "admin" | "owner"

COMMANDS = [
    # ── Everyone ──────────────────────────────────────────────────────────────
    ("ping",             "Check bot latency",                               "everyone", False),
    ("userinfo [@user]", "View detailed info about a member",               "everyone", False),
    ("avatar [@user]",   "View a member's avatar",                          "everyone", False),
    ("serverinfo",       "View server statistics",                          "everyone", False),
    ("membercount",      "View member/bot count",                           "everyone", False),
    ("level [@user]",    "Check XP and level",                              "everyone", False),
    ("leaderboard",      "Top 10 members by level",                         "everyone", False),
    ("profile [@user]",  "View profile card",                               "everyone", False),
    ("profile bio",      "Set your bio",                                    "everyone", False),
    ("profile location", "Set your location",                               "everyone", False),
    ("birthday [@user]", "View a member's birthday",                        "everyone", False),
    ("birthday set DD/MM","Set your birthday",                              "everyone", False),
    ("afk [reason]",     "Set AFK status",                                  "everyone", False),
    ("ship @u1 @u2",     "Love compatibility check",                        "everyone", False),
    ("hot [@user]",      "Hotness meter",                                   "everyone", False),
    ("8ball <question>", "Ask the magic 8-ball",                            "everyone", False),
    ("coinflip",         "Flip a coin",                                     "everyone", False),
    ("dice [sides]",     "Roll a dice",                                     "everyone", False),
    ("wouldyourather",   "Random would-you-rather question",                "everyone", False),
    ("hug/pat/slap/kiss","Roleplay actions",                                "everyone", False),
    ("boop/wave/stare",  "More roleplay actions",                           "everyone", False),
    ("bonk/cuddle/highfive","Even more roleplay actions",                   "everyone", False),
    ("roast [@user]",    "Gently roast someone",                            "everyone", False),
    ("praise [@user]",   "Send a compliment",                               "everyone", False),
    ("translate <lang>", "Translate text to any language",                  "everyone", False),
    ("urban <word>",     "Urban Dictionary lookup",                         "everyone", False),
    ("shrug [text]",     "Send a shrug",                                    "everyone", False),
    ("prefix",           "View prefix info",                                "everyone", False),
    ("prefix self",      "Personal prefix across all servers",              "everyone", True),

    # ── Moderator ─────────────────────────────────────────────────────────────
    ("kick @user",       "Kick a member",                                   "mod", False),
    ("ban @user",        "Ban a member",                                    "mod", False),
    ("unban <id>",       "Unban a user by ID",                              "mod", False),
    ("mute @user [min]", "Timeout a member",                                "mod", False),
    ("unmute @user",     "Remove timeout",                                  "mod", False),
    ("warn @user",       "Warn a member",                                   "mod", False),
    ("warnings [@user]", "View warnings for a member",                      "mod", False),
    ("clearwarns @user", "Clear all warnings for a member",                 "mod", False),
    ("softban @user",    "Ban + unban (clear messages)",                    "mod", False),
    ("nickname @user",   "Change or reset a nickname",                      "mod", False),
    ("lock [#channel]",  "Lock a channel",                                  "mod", False),
    ("unlock [#channel]","Unlock a channel",                                "mod", False),
    ("vclock [#vc]",     "Lock a voice channel",                            "mod", False),
    ("vcunlock [#vc]",   "Unlock a voice channel",                          "mod", False),
    ("purge <amount>",   "Delete messages (also: bots, @user, links, images)", "mod", False),
    ("jail @user",       "Put a member in jail",                            "mod", False),
    ("unjail @user",     "Release a member from jail",                      "mod", False),
    ("sticky <text>",    "Set a sticky message",                            "mod", False),
    ("unsticky",         "Remove sticky message",                           "mod", False),
    ("announce <msg>",   "Send an announcement",                            "mod", False),
    ("giveaway <min> <w>","Start a giveaway",                              "mod", False),
    ("mimic @user <msg>","Send message as another member",                  "mod", False),
    ("echo [#ch] <msg>", "Send a message as the bot",                       "mod", False),
    ("ticket close",     "Close the current ticket",                        "mod", False),
    ("ticket add/remove","Add or remove users from a ticket",               "mod", False),
    ("hangup",           "End a global call",                               "mod", False),
    ("call",             "Connect to another server (global call)",         "mod", True),

    # ── Admin ─────────────────────────────────────────────────────────────────
    ("settings",         "View server configuration dashboard",             "admin", False),
    ("prefix set",       "Change the server prefix",                        "admin", False),
    ("prefix remove",    "Reset server prefix",                             "admin", False),
    ("welcome set/enable/disable","Configure welcome messages",             "admin", False),
    ("bye set/enable/disable",   "Configure bye messages",                  "admin", False),
    ("logs set/disable", "Configure log channel",                           "admin", False),
    ("automod invite on/off","Toggle anti-invite blocker",                  "admin", False),
    ("role add/remove",  "Add or remove roles from members",                "admin", False),
    ("massrole add/remove","Give or take a role from all members",          "admin", False),
    ("reactionrole add", "Set up a reaction role",                          "admin", False),
    ("buttonrole",       "Create button role panel",                        "admin", True),
    ("boosterrole",      "Set reward role for boosters",                    "admin", False),
    ("ticket setup",     "Send ticket creation panel",                      "admin", False),
    ("ticket staffrole", "Set staff role for ticket access",                "admin", False),
    ("jailsetup",        "Create jail role and channel",                    "admin", False),
    ("setupmute",        "Create Muted/Image/Reaction Muted roles",         "admin", False),
    ("quicksetup",       "Auto-create channels, roles, categories",         "admin", False),
    ("embed create/send","Interactive embed builder",                       "admin", False),
    ("counter create",   "Live member/bot/channel counter in VC",           "admin", False),
    ("lockdown",         "Lock ALL channels (emergency)",                   "admin", False),
    ("unlockdown",       "Lift server lockdown",                            "admin", False),
    ("premiumrole",      "Set the Premium Members role",                    "admin", False),
    ("setstatus <text>", "Custom bot status for your server",               "admin", True),
    ("bumpreminder on/off","DISBOARD bump reminder",                       "admin", True),
    ("vcsetup",          "Set up VoiceMaster temp VCs",                     "admin", True),
    ("setlevel @user",   "Manually set a member's level",                   "admin", False),
    ("resetxp [@user]",  "Reset XP for member or server",                  "admin", False),

    # ── Owner ─────────────────────────────────────────────────────────────────
    ("premium add/remove/list","Manage premium servers and users",          "owner", False),
    ("aimode on/off",    "Toggle AI chat globally",                         "owner", False),
    ("maintenance on/off","Toggle maintenance mode",                        "owner", False),
]

PERM_ORDER = {"everyone": 0, "mod": 1, "admin": 2, "owner": 3}

PERM_LABELS = {
    "everyone": "Everyone",
    "mod":      "Moderator",
    "admin":    "Administrator",
    "owner":    "Bot Owner",
}


def _user_level(author: discord.Member, guild: discord.Guild, owner_id: int) -> str:
    if author.id == owner_id:
        return "owner"
    if author.guild_permissions.administrator:
        return "admin"
    p = author.guild_permissions
    if p.manage_messages or p.kick_members or p.manage_roles:
        return "mod"
    return "everyone"


class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(aliases=["h", "commands"])
    async def help(self, ctx, *, category: str = None):
        """
        Smart permission-aware help.
        Usage: `,help` | `,help mod` | `,help admin` | `,help fun` | `,help premium`
        """
        user_level = _user_level(ctx.author, ctx.guild, BOT_OWNER_ID)
        is_prem    = await is_premium_user(ctx.author.id) or await is_premium_server(ctx.guild.id)

        # ── Category filters ──────────────────────────────────────────────────
        CATEGORY_MAP = {
            "fun":       ["ship", "hot", "8ball", "coinflip", "dice", "wouldyourather",
                          "hug", "pat", "slap", "kiss", "poke", "highfive", "bonk",
                          "cuddle", "boop", "wave", "stare", "roast", "praise", "shrug"],
            "utility":   ["ping", "userinfo", "avatar", "serverinfo", "membercount",
                          "translate", "urban", "shrug", "level", "leaderboard"],
            "profile":   ["profile", "birthday", "afk"],
            "mod":       ["kick", "ban", "unban", "mute", "unmute", "warn", "warnings",
                          "clearwarns", "softban", "nickname", "lock", "unlock", "vclock",
                          "vcunlock", "purge", "jail", "unjail", "sticky", "unsticky",
                          "announce", "giveaway", "mimic", "echo", "lockdown", "unlockdown"],
            "roles":     ["role", "massrole", "reactionrole", "buttonrole", "boosterrole"],
            "tickets":   ["ticket"],
            "embed":     ["embed"],
            "premium":   ["call", "hangup", "vcsetup", "buttonrole", "bumpreminder",
                          "setstatus", "prefix self"],
            "admin":     ["settings", "prefix", "welcome", "bye", "logs", "automod",
                          "quicksetup", "jailsetup", "setupmute", "counter", "premiumrole"],
            "levels":    ["level", "leaderboard", "setlevel", "resetxp"],
            "owner":     ["premium", "aimode", "maintenance"],
        }

        if category and category.lower() in CATEGORY_MAP:
            cat    = category.lower()
            keys   = CATEGORY_MAP[cat]
            # Filter commands available to this user
            cmds   = [
                (cmd, desc, perm, prem)
                for cmd, desc, perm, prem in COMMANDS
                if cmd.split()[0] in keys
                and PERM_ORDER[perm] <= PERM_ORDER[user_level]
                and (not prem or is_prem or ctx.author.id == BOT_OWNER_ID)
            ]
            embed = discord.Embed(
                title=f"Help — {cat.title()}",
                color=0x2B2D31
            )
            if not cmds:
                embed.description = "No commands available for your permission level in this category."
            else:
                lines = []
                for cmd, desc, perm, prem in cmds:
                    tag = " `Premium`" if prem else ""
                    lines.append(f"`,{cmd}` — {desc}{tag}")
                embed.description = "\n".join(lines)
            embed.set_footer(text=f"Prefix: , | Your level: {PERM_LABELS[user_level]}")
            return await ctx.reply(embed=embed)

        # ── Main help menu ────────────────────────────────────────────────────
        embed = discord.Embed(
            title="Happy — Help",
            color=0x2B2D31
        )
        embed.description = (
            f"Your permission level: **{PERM_LABELS[user_level]}**"
            + (" · **Premium**" if (is_prem or ctx.author.id == BOT_OWNER_ID) else "")
            + "\n\nUse `,help <category>` for a detailed list.\n"
        )

        # Show only categories relevant to the user's level
        categories_to_show = [
            ("fun",      "Ship, 8ball, roleplay, dice, coinflip"),
            ("utility",  "Userinfo, avatar, ping, translate, urban"),
            ("profile",  "Profile card, birthday, AFK"),
            ("levels",   "XP, level, leaderboard"),
            ("tickets",  "Support ticket system"),
            ("embed",    "Interactive embed builder"),
        ]
        if PERM_ORDER[user_level] >= PERM_ORDER["mod"]:
            categories_to_show += [
                ("mod",   "Kick, ban, mute, warn, lock, purge, jail"),
                ("roles", "Reaction roles, button roles, booster roles"),
            ]
        if PERM_ORDER[user_level] >= PERM_ORDER["admin"]:
            categories_to_show += [
                ("admin", "Settings, prefix, welcome, automod, counters"),
            ]
        if is_prem or ctx.author.id == BOT_OWNER_ID:
            categories_to_show += [
                ("premium", "Call, VoiceMaster, bump reminder, custom status, personal prefix"),
            ]
        if user_level == "owner":
            categories_to_show += [
                ("owner", "Premium management, AI mode, maintenance"),
            ]

        for cat_name, cat_desc in categories_to_show:
            embed.add_field(
                name=f"`,help {cat_name}`",
                value=cat_desc,
                inline=False
            )

        embed.add_field(
            name="AI Chat",
            value=(
                "@mention Happy or reply to start a conversation. **(Premium only)**"
                if not (is_prem or ctx.author.id == BOT_OWNER_ID)
                else "@mention Happy or reply to chat. **Active for you.**"
            ),
            inline=False
        )

        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text=f"Default prefix: , | Server prefix may differ | Happy Premium for extra features")
        await ctx.reply(embed=embed)

    # ── Slash help ─────────────────────────────────────────────────────────────
    @app_commands.command(name="help", description="View Happy's command list")
    async def slash_help(self, interaction: discord.Interaction):
        user_level = _user_level(interaction.user, interaction.guild, BOT_OWNER_ID)
        is_prem    = await is_premium_user(interaction.user.id) or await is_premium_server(interaction.guild.id)

        embed = discord.Embed(
            title="Happy — Help",
            description=(
                f"Your level: **{PERM_LABELS[user_level]}**"
                + (" · **Premium**" if (is_prem or interaction.user.id == BOT_OWNER_ID) else "")
                + "\n\nUse `,help <category>` for a detailed list of commands.\n\n"
                "**Categories:** `fun` `utility` `profile` `levels` `mod` `admin` `roles` `tickets` `embed` `premium`"
            ),
            color=0x2B2D31
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text="Default prefix: , | Slash commands also available")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Help(bot))
