"""
cogs/utility.py — userinfo, avatar, serverinfo, ping, translate, urban, profile,
                   afk, sticky, embed builder, mimic, echo, birthday.

FIXES vs original:
  - Added levels_col to DB imports (was missing → NameError on ,profile)
  - profile command now shows global level/XP correctly
  - translate: content-type header added so Google API doesn't reject the request
  - translate: added more languages to LANG_MAP
  - urban: graceful handling of empty definition/example fields
  - afk: store user_id/guild_id as str for consistency with rest of bot
  - sticky: store channel_id as str for consistency
  - embed builder: added image + footer field support
  - userinfo: added account age in days, boosting status
  - serverinfo: added online member count, emojis count
  - birthday: slash command added
  - All DB writes use str() for IDs consistently
"""

import discord
from discord.ext import commands
from discord import app_commands
import aiohttp, asyncio, urllib.parse, datetime
from datetime import timezone

from utils.db import (
    afk_col, sticky_col, profiles_col, embed_col,
    birthdays_col, settings_col, levels_col          # ← FIXED: levels_col was missing
)
from utils.helpers import BOT_OWNER_ID, ctx_mod, ctx_admin


class Utility(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ══════════════════════════════════════════════════════════════════════════
    #  PING
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    async def ping(self, ctx):
        """Check bot latency."""
        lat   = round(self.bot.latency * 1000)
        color = 0x57F287 if lat < 100 else (0xFEE75C if lat < 200 else 0xED4245)
        embed = discord.Embed(
            description=f"<a:ghosty:1522505832288354334> Pong! Latency: **{lat}ms**",
            color=color
        )
        await ctx.reply(embed=embed)

    @app_commands.command(name="ping", description="Check bot latency")
    async def slash_ping(self, interaction: discord.Interaction):
        lat   = round(self.bot.latency * 1000)
        color = 0x57F287 if lat < 100 else (0xFEE75C if lat < 200 else 0xED4245)
        await interaction.response.send_message(
            embed=discord.Embed(description=f"<a:ghosty:1522505832288354334> Pong! Latency: **{lat}ms**", color=color),
            ephemeral=True
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  USERINFO
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["whois", "ui"])
    async def userinfo(self, ctx, member: discord.Member = None):
        """View detailed info about a member."""
        member = member or ctx.author

        # Account age
        now       = datetime.datetime.now(timezone.utc)
        created   = member.created_at.replace(tzinfo=timezone.utc)
        age_days  = (now - created).days

        # Roles (skip @everyone)
        roles = [r.mention for r in reversed(member.roles) if r != ctx.guild.default_role]
        roles_str = " ".join(roles[:10]) + (f" +{len(roles)-10} more" if len(roles) > 10 else "") if roles else "None"

        # Status
        status_emoji = {
            discord.Status.online:    "<:dotgreen:1522520298539319399> Online",
            discord.Status.idle:      "<:dotyellow:1522520275944734851> Idle",
            discord.Status.dnd:       "<:dotred:1522520224304336927> Do Not Disturb",
            discord.Status.offline:   "<:dotblack:1522520296450424853> Offline",
        }
        status_str = status_emoji.get(member.status, "⚫ Offline")

        embed = discord.Embed(
            title=str(member),
            color=member.color if member.color != discord.Color.default() else 0x2B2D31
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID",          value=f"`{member.id}`",                                                              inline=True)
        embed.add_field(name="Status",      value=status_str,                                                                     inline=True)
        embed.add_field(name="Bot",         value="Yes ✅" if member.bot else "No",                                              inline=True)
        embed.add_field(name="Joined",      value=f"{member.joined_at.strftime('%d %b %Y') if member.joined_at else 'Unknown'}",  inline=True)
        embed.add_field(name="Created",     value=f"{created.strftime('%d %b %Y')} ({age_days}d ago)",                           inline=True)
        embed.add_field(name="Top Role",    value=member.top_role.mention,                                                        inline=True)
        if member.premium_since:
            embed.add_field(name="Boosting Since", value=member.premium_since.strftime("%d %b %Y"), inline=True)
        embed.add_field(name=f"Roles ({len(roles)})", value=roles_str, inline=False)
        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.reply(embed=embed)

    @app_commands.command(name="userinfo", description="Get information about a user")
    async def slash_userinfo(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        now    = datetime.datetime.now(timezone.utc)
        age    = (now - member.created_at.replace(tzinfo=timezone.utc)).days
        embed  = discord.Embed(title=str(member), color=0x2B2D31)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID",       value=f"`{member.id}`",                                                               inline=True)
        embed.add_field(name="Joined",   value=member.joined_at.strftime("%d %b %Y") if member.joined_at else "N/A",           inline=True)
        embed.add_field(name="Created",  value=f"{member.created_at.strftime('%d %b %Y')} ({age}d ago)",                       inline=True)
        embed.add_field(name="Top Role", value=member.top_role.mention,                                                         inline=True)
        await interaction.response.send_message(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  AVATAR
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["av", "pfp"])
    async def avatar(self, ctx, member: discord.Member = None):
        """View someone's avatar in full size."""
        member = member or ctx.author
        embed  = discord.Embed(title=f"{member.display_name}'s Avatar", color=0x2B2D31)
        embed.set_image(url=member.display_avatar.url)
        embed.add_field(
            name="Links",
            value=f"[PNG]({member.display_avatar.with_format('png').url}) · "
                  f"[JPG]({member.display_avatar.with_format('jpg').url}) · "
                  f"[WEBP]({member.display_avatar.with_format('webp').url})"
        )
        await ctx.reply(embed=embed)

    @app_commands.command(name="avatar", description="View a user's avatar")
    async def slash_avatar(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        embed  = discord.Embed(title=f"{member.display_name}'s Avatar", color=0x2B2D31)
        embed.set_image(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  SERVER INFO
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["si", "server", "guildinfo"])
    async def serverinfo(self, ctx):
        """View server statistics."""
        g      = ctx.guild
        bots   = sum(1 for m in g.members if m.bot)
        humans = g.member_count - bots
        online = sum(1 for m in g.members if m.status != discord.Status.offline and not m.bot)

        embed = discord.Embed(
            title=g.name,
            description=g.description or "",
            color=0x2B2D31,
            timestamp=datetime.datetime.now(timezone.utc)
        )
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        if g.banner:
            embed.set_image(url=g.banner.url)

        embed.add_field(name="Owner",        value=g.owner.mention if g.owner else "Unknown", inline=True)
        embed.add_field(name="Members",      value=f"{humans} humans · {bots} bots",          inline=True)
        embed.add_field(name="Online",       value=f"{online}",                               inline=True)
        embed.add_field(name="Channels",     value=str(len(g.channels)),                      inline=True)
        embed.add_field(name="Roles",        value=str(len(g.roles)),                         inline=True)
        embed.add_field(name="Emojis",       value=str(len(g.emojis)),                        inline=True)
        embed.add_field(name="Boost Level",  value=f"Level {g.premium_tier}",                 inline=True)
        embed.add_field(name="Boosters",     value=str(g.premium_subscription_count),         inline=True)
        embed.add_field(name="Created",      value=f"<t:{int(g.created_at.timestamp())}:D>",  inline=True)
        embed.set_footer(text=f"ID: {g.id}")
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  MEMBERCOUNT
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["mc"])
    async def membercount(self, ctx):
        """View member count breakdown."""
        g      = ctx.guild
        bots   = sum(1 for m in g.members if m.bot)
        humans = g.member_count - bots
        online = sum(1 for m in g.members if m.status != discord.Status.offline and not m.bot)
        embed  = discord.Embed(title=f"{g.name} — Members", color=0x2B2D31)
        embed.add_field(name="<:253261communitymembers:1522522379090137128> Total",   value=str(g.member_count), inline=True)
        embed.add_field(name="<:82382member:1522522354176233594> Humans",  value=str(humans),          inline=True)
        embed.add_field(name="<:bot:1522522753125716098> Bots",    value=str(bots),            inline=True)
        embed.add_field(name="<:dotgreen:1522520298539319399> Online",  value=str(online),          inline=True)
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  AFK
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    async def afk(self, ctx, *, reason: str = "Away from keyboard"):
        """Set your AFK status. Bot will notify people who ping you."""
        if len(reason) > 200:
            return await ctx.reply("AFK reason must be 200 characters or fewer.")

        # FIXED: use str() for IDs — consistent with rest of bot
        await afk_col.update_one(
            {"user_id": str(ctx.author.id), "guild_id": str(ctx.guild.id)},
            {"$set": {"reason": reason, "time": datetime.datetime.now(timezone.utc)}},
            upsert=True
        )
        try:
            if (ctx.guild.me.guild_permissions.manage_nicknames
                    and not ctx.author.display_name.startswith("[AFK]")
                    and ctx.author != ctx.guild.owner):
                await ctx.author.edit(nick=f"[AFK] {ctx.author.display_name[:25]}")
        except:
            pass
        embed = discord.Embed(
            description=f"💤 {ctx.author.mention} is now AFK.\nReason: `{reason}`",
            color=0x2B2D31
        )
        await ctx.reply(embed=embed)

    @app_commands.command(name="afk", description="Set your AFK status")
    async def slash_afk(self, interaction: discord.Interaction, reason: str = "Away from keyboard"):
        await afk_col.update_one(
            {"user_id": str(interaction.user.id), "guild_id": str(interaction.guild.id)},
            {"$set": {"reason": reason, "time": datetime.datetime.now(timezone.utc)}},
            upsert=True
        )
        await interaction.response.send_message(
            embed=discord.Embed(description=f"💤 AFK set. Reason: `{reason}`", color=0x2B2D31),
            ephemeral=True
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  STICKY
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @ctx_mod()
    async def sticky(self, ctx, *, text: str = None):
        """Pin a sticky message that re-appears after every new message."""
        if not text:
            return await ctx.reply("Usage: `,sticky Your message here`")
        if len(text) > 1900:
            return await ctx.reply("Sticky message must be under 1900 characters.")

        # Delete old sticky if exists
        old = await sticky_col.find_one({"channel_id": str(ctx.channel.id)})
        if old:
            try:
                om = await ctx.channel.fetch_message(int(old["message_id"]))
                await om.delete()
            except:
                pass

        embed = discord.Embed(description=text.replace("\\n", "\n"), color=0x2B2D31)
        embed.set_footer(text="📌 Sticky Message")
        msg = await ctx.channel.send(embed=embed)

        # FIXED: store as str for consistency
        await sticky_col.update_one(
            {"channel_id": str(ctx.channel.id)},
            {"$set": {"message_id": str(msg.id), "content": text}},
            upsert=True
        )
        try:
            await ctx.message.delete()
        except:
            pass

    @commands.command()
    @ctx_mod()
    async def unsticky(self, ctx):
        """Remove the sticky message from this channel."""
        data = await sticky_col.find_one({"channel_id": str(ctx.channel.id)})
        if not data:
            return await ctx.reply("No sticky message in this channel.")
        try:
            om = await ctx.channel.fetch_message(int(data["message_id"]))
            await om.delete()
        except:
            pass
        await sticky_col.delete_one({"channel_id": str(ctx.channel.id)})
        await ctx.reply("Sticky message removed.", delete_after=4)

    # ══════════════════════════════════════════════════════════════════════════
    #  TRANSLATE
    # ══════════════════════════════════════════════════════════════════════════

    # FIXED: expanded language map
    LANG_MAP = {
        "english":    "en", "hindi":      "hi", "french":     "fr",
        "spanish":    "es", "german":     "de", "japanese":   "ja",
        "russian":    "ru", "arabic":     "ar", "portuguese": "pt",
        "italian":    "it", "korean":     "ko", "chinese":    "zh",
        "turkish":    "tr", "dutch":      "nl", "polish":     "pl",
        "swedish":    "sv", "norwegian":  "no", "danish":     "da",
        "finnish":    "fi", "greek":      "el", "hebrew":     "iw",
        "thai":       "th", "vietnamese": "vi", "indonesian": "id",
        "malay":      "ms", "urdu":       "ur", "bengali":    "bn",
        "punjabi":    "pa", "tamil":      "ta", "telugu":     "te",
        "marathi":    "mr", "gujarati":   "gu", "kannada":    "kn",
    }

    @commands.command(aliases=["tr"])
    async def translate(self, ctx, lang: str = None, *, text: str = None):
        """Translate text into any language."""
        if not lang or not text:
            return await ctx.reply(
                "Usage: `,translate <language> <text>`\n"
                "Example: `,translate hindi Hello everyone`\n"
                "Supports: english, hindi, french, spanish, german, japanese, korean, arabic + more"
            )
        if len(text) > 500:
            return await ctx.reply("Text must be 500 characters or fewer.")

        tl  = self.LANG_MAP.get(lang.lower(), lang.lower()[:5])
        url = (
            f"https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl=auto&tl={tl}&dt=t&q={urllib.parse.quote(text)}"
        )

        try:
            # FIXED: content-type header prevents occasional 400 errors
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers={"Content-Type": "application/json"}) as r:
                    if r.status != 200:
                        return await ctx.reply(
                            f"Translation service returned an error (status {r.status}). Try again later."
                        )
                    result = await r.json(content_type=None)

            translated = "".join(s[0] for s in result[0] if s and s[0])
            detected   = result[2] if len(result) > 2 else "?"

            if not translated.strip():
                return await ctx.reply("Got an empty translation. Try again.")

            embed = discord.Embed(title="🌐 Translation", color=0x2B2D31)
            embed.add_field(
                name=f"Original ({str(detected).upper()})",
                value=f"```{text[:400]}```",
                inline=False
            )
            embed.add_field(
                name=f"Translated ({tl.upper()})",
                value=f"```{translated[:400]}```",
                inline=False
            )
            embed.set_footer(text="Powered by Google Translate")
            await ctx.reply(embed=embed)

        except Exception as e:
            await ctx.reply(f"Translation failed: `{e}`")

    # ══════════════════════════════════════════════════════════════════════════
    #  URBAN DICTIONARY
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["ud"])
    async def urban(self, ctx, *, word: str = None):
        """Look up a word on Urban Dictionary."""
        if not word:
            return await ctx.reply("Usage: `,urban <word>`")

        url = f"https://api.urbandictionary.com/v0/define?term={urllib.parse.quote(word)}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status != 200:
                        return await ctx.reply("Could not reach Urban Dictionary right now.")
                    data = await r.json()
        except asyncio.TimeoutError:
            return await ctx.reply("Urban Dictionary took too long to respond.")
        except Exception as e:
            return await ctx.reply(f"Error: `{e}`")

        if not data.get("list"):
            return await ctx.reply(f"No definition found for **{word}**.")

        top = data["list"][0]

        # FIXED: clean brackets and guard empty strings
        definition = top.get("definition", "").replace("[", "").replace("]", "").strip()
        example    = top.get("example",    "").replace("[", "").replace("]", "").strip()

        if not definition:
            return await ctx.reply(f"Definition for **{word}** was empty.")

        embed = discord.Embed(
            title=top.get("word", word),
            url=top.get("permalink", ""),
            color=0x2B2D31
        )
        embed.add_field(name="Definition", value=definition[:1024],                    inline=False)
        if example:
            embed.add_field(name="Example", value=f"*{example[:512]}*",               inline=False)
        embed.set_footer(
            text=f"👍 {top.get('thumbs_up', 0)} · 👎 {top.get('thumbs_down', 0)} · by {top.get('author', '?')}"
        )
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  PROFILE
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(aliases=["card"], invoke_without_command=True)
    async def profile(self, ctx, member: discord.Member = None):
        """View your or someone else's profile card."""
        member = member or ctx.author

        # Profile data
        doc = await profiles_col.find_one({
            "guild_id": str(ctx.guild.id),
            "user_id":  str(member.id)
        })
        bio = doc.get("bio",      "Not set. Use `,profile bio <text>`") if doc else "Not set."
        loc = doc.get("location", "Not set") if doc else "Not set"

        # FIXED: levels_col is now imported — this no longer raises NameError
        lvl_doc = await levels_col.find_one({
            "guild_id": str(ctx.guild.id),
            "user_id":  str(member.id)
        })
        level = lvl_doc.get("level", 0) if lvl_doc else 0
        xp    = lvl_doc.get("xp",    0) if lvl_doc else 0
        nxt   = (level + 1) * 100
        pct   = int((xp / nxt) * 10) if nxt else 0
        bar   = "█" * pct + "░" * (10 - pct)

        # Birthday
        bday_doc = await birthdays_col.find_one({
            "guild_id": str(ctx.guild.id),
            "user_id":  str(member.id)
        })
        bday = bday_doc.get("date", "Not set") if bday_doc else "Not set"

        embed = discord.Embed(color=member.color if member.color != discord.Color.default() else 0x2B2D31)
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="📝 Bio",       value=f"*{bio}*",                   inline=False)
        embed.add_field(name="📍 Location",  value=loc,                           inline=True)
        embed.add_field(name="🎂 Birthday",  value=bday,                          inline=True)
        embed.add_field(name="⭐ Level",     value=f"**{level}** ({xp}/{nxt} XP)", inline=True)
        embed.add_field(name="📊 Progress",  value=f"`{bar}`",                    inline=True)
        embed.add_field(name="🏷️ Roles",    value=str(len(member.roles) - 1),    inline=True)
        embed.add_field(
            name="📅 Joined",
            value=member.joined_at.strftime("%d %b %Y") if member.joined_at else "?",
            inline=True
        )
        embed.set_footer(text=f"ID: {member.id}")
        await ctx.reply(embed=embed)

    @profile.command(name="bio")
    async def profile_bio(self, ctx, *, text: str = None):
        """Set your profile bio (max 150 chars)."""
        if not text:
            return await ctx.reply("Usage: `,profile bio <your bio>`")
        if len(text) > 150:
            return await ctx.reply("Bio must be 150 characters or fewer.")
        await profiles_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)},
            {"$set": {"bio": text}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(description="✅ Bio updated.", color=0x57F287))

    @profile.command(name="location", aliases=["loc"])
    async def profile_location(self, ctx, *, city: str = None):
        """Set your profile location (max 40 chars)."""
        if not city:
            return await ctx.reply("Usage: `,profile location <city>`")
        if len(city) > 40:
            return await ctx.reply("Location must be 40 characters or fewer.")
        await profiles_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)},
            {"$set": {"location": city}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(description=f"✅ Location set to `{city}`.", color=0x57F287))

    @profile.command(name="clear")
    async def profile_clear(self, ctx):
        """Clear your profile bio and location."""
        await profiles_col.delete_one({"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)})
        await ctx.reply(embed=discord.Embed(description="✅ Profile cleared.", color=0x57F287))

    # ══════════════════════════════════════════════════════════════════════════
    #  BIRTHDAY
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(aliases=["bday"], invoke_without_command=True)
    async def birthday(self, ctx, member: discord.Member = None):
        """View a member's birthday."""
        member = member or ctx.author
        doc    = await birthdays_col.find_one({
            "guild_id": str(ctx.guild.id),
            "user_id":  str(member.id)
        })
        if not doc:
            return await ctx.reply(
                f"No birthday set for **{member.display_name}**.\n"
                f"Use `,birthday set DD/MM` to set yours."
            )
        await ctx.reply(embed=discord.Embed(
            description=f"🎂 **{member.display_name}'s** birthday: **{doc['date']}**",
            color=0x2B2D31
        ))

    @birthday.command(name="set")
    async def birthday_set(self, ctx, date: str = None):
        """Set your birthday — format DD/MM (e.g. 25/12)."""
        if not date:
            return await ctx.reply("Usage: `,birthday set DD/MM`  e.g. `,birthday set 25/12`")
        try:
            day, month = map(int, date.strip().split("/"))
            if not (1 <= day <= 31 and 1 <= month <= 12):
                raise ValueError
        except:
            return await ctx.reply("Invalid format. Use DD/MM, e.g. `25/12`.")
        await birthdays_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)},
            {"$set": {"date": date, "day": day, "month": month}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(
            description=f"🎂 Birthday set to **{date}**. Happy will wish you on the day!",
            color=0x57F287
        ))

    @birthday.command(name="remove")
    async def birthday_remove(self, ctx):
        """Remove your birthday."""
        result = await birthdays_col.delete_one({
            "guild_id": str(ctx.guild.id),
            "user_id":  str(ctx.author.id)
        })
        if result.deleted_count:
            await ctx.reply("Birthday removed.")
        else:
            await ctx.reply("No birthday was set.")

    @birthday.command(name="channel")
    @commands.has_permissions(manage_guild=True)
    async def birthday_channel(self, ctx, channel: discord.TextChannel = None):
        """Set the channel for birthday announcements."""
        if channel is None:
            gs  = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
            cid = gs.get("birthday_channel")
            if cid:
                ch = ctx.guild.get_channel(int(cid))
                return await ctx.reply(embed=discord.Embed(
                    description=(
                        f"Birthday announcements go to {ch.mention if ch else f'<#{cid}>'}.\n"
                        "Use `,birthday channel remove` to clear."
                    ),
                    color=0x2B2D31
                ))
            return await ctx.reply(embed=discord.Embed(
                description=(
                    "No birthday channel set.\n"
                    "Falls back to welcome channel or system channel.\n"
                    "Use `,birthday channel #channel` to set one."
                ),
                color=0x2B2D31
            ))

        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"birthday_channel": str(channel.id)}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(
            description=f"🎂 Birthday announcements will go to {channel.mention}.",
            color=0x57F287
        ))

    @birthday.command(name="channelremove", aliases=["removechannel"])
    @commands.has_permissions(manage_guild=True)
    async def birthday_channel_remove(self, ctx):
        """Remove the dedicated birthday channel."""
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$unset": {"birthday_channel": ""}},
            upsert=True
        )
        await ctx.reply("Birthday channel removed. Will fall back to welcome/system channel.")

    # Birthday slash command
    @app_commands.command(name="birthday", description="View or set your birthday")
    @app_commands.describe(member="Member to check (leave blank for yourself)")
    async def slash_birthday(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        doc    = await birthdays_col.find_one({
            "guild_id": str(interaction.guild.id),
            "user_id":  str(member.id)
        })
        if not doc:
            await interaction.response.send_message(
                f"No birthday set for **{member.display_name}**.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description=f"🎂 **{member.display_name}'s** birthday: **{doc['date']}**",
                    color=0x2B2D31
                )
            )

    # ══════════════════════════════════════════════════════════════════════════
    #  MIMIC / ECHO
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @ctx_mod()
    async def mimic(self, ctx, member: discord.Member = None, *, message: str = None):
        """Send a message as another member using a webhook."""
        if not member or not message:
            return await ctx.reply("Usage: `,mimic @user <message>`")
        if len(message) > 2000:
            return await ctx.reply("Message too long (max 2000 chars).")
        try:
            webhooks = await ctx.channel.webhooks()
            wh = discord.utils.get(webhooks, name="HappyMimic")
            if not wh:
                wh = await ctx.channel.create_webhook(name="HappyMimic")
            await wh.send(
                content=message,
                username=member.display_name,
                avatar_url=member.display_avatar.url
            )
            try:
                await ctx.message.delete()
            except:
                pass
        except discord.Forbidden:
            await ctx.reply("Missing Manage Webhooks permission.")

    @commands.command()
    @ctx_mod()
    async def echo(self, ctx, channel: discord.TextChannel = None, *, message: str = None):
        """Send a message as the bot in any channel."""
        if not message:
            return await ctx.reply("Usage: `,echo [#channel] <message>`")
        target = channel or ctx.channel
        await target.send(message)
        try:
            await ctx.message.delete()
        except:
            pass

    # ══════════════════════════════════════════════════════════════════════════
    #  EMBED BUILDER
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(name="embed", invoke_without_command=True)
    @commands.has_permissions(manage_messages=True)
    async def embed_group(self, ctx):
        """Interactive embed builder."""
        embed = discord.Embed(title="Embed Builder", color=0x2B2D31, description=(
            "**Steps:**\n"
            "1. `,embed create` — start a draft\n"
            "2. `,embed title <text>`\n"
            "3. `,embed description <text>`\n"
            "4. `,embed color #hex`\n"
            "5. `,embed thumbnail <url>`\n"
            "6. `,embed image <url>`\n"
            "7. `,embed footer <text>`\n"
            "8. `,embed send [#channel]`\n"
            "9. `,embed preview` — preview before sending"
        ))
        await ctx.reply(embed=embed)

    @embed_group.command(name="create")
    @commands.has_permissions(manage_messages=True)
    async def emb_create(self, ctx):
        await embed_col.update_one(
            {"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)},
            {"$set": {
                "title": None, "description": "Edit this description.",
                "color": "2B2D31", "thumbnail": None,
                "image": None, "footer": None
            }},
            upsert=True
        )
        await ctx.reply("Draft created. Use `,embed <field>` to customize, then `,embed send`.")

    @embed_group.command(name="title")
    @commands.has_permissions(manage_messages=True)
    async def emb_title(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply("Provide a title.")
        if len(text) > 256:
            return await ctx.reply("Title must be 256 characters or fewer.")
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("No draft. Run `,embed create` first.")
        await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"title": text}})
        await ctx.reply(f"Title set to `{text[:50]}`.")

    @embed_group.command(name="description", aliases=["desc"])
    @commands.has_permissions(manage_messages=True)
    async def emb_desc(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply("Provide a description.")
        if len(text) > 4000:
            return await ctx.reply("Description must be 4000 characters or fewer.")
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("No draft. Run `,embed create` first.")
        await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"description": text}})
        await ctx.reply("Description updated.")

    @embed_group.command(name="color", aliases=["colour"])
    @commands.has_permissions(manage_messages=True)
    async def emb_color(self, ctx, hex_code: str = None):
        if not hex_code:
            return await ctx.reply("Provide a hex code e.g. `#FF0000`.")
        clean = hex_code.replace("#", "").strip()
        try:
            int(clean, 16)
        except:
            return await ctx.reply("Invalid hex code. Example: `#FF5500`")
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("No draft. Run `,embed create` first.")
        await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"color": clean}})
        await ctx.reply(embed=discord.Embed(description=f"Color set to `#{clean}`.", color=int(clean, 16)))

    @embed_group.command(name="thumbnail")
    @commands.has_permissions(manage_messages=True)
    async def emb_thumbnail(self, ctx, url: str = None):
        if not url or not url.startswith("http"):
            return await ctx.reply("Provide a valid image URL starting with http.")
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("No draft. Run `,embed create` first.")
        await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"thumbnail": url}})
        await ctx.reply("Thumbnail set.")

    @embed_group.command(name="image")
    @commands.has_permissions(manage_messages=True)
    async def emb_image(self, ctx, url: str = None):
        """Set a large image at the bottom of the embed."""
        if not url or not url.startswith("http"):
            return await ctx.reply("Provide a valid image URL.")
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("No draft. Run `,embed create` first.")
        await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"image": url}})
        await ctx.reply("Image set.")

    @embed_group.command(name="footer")
    @commands.has_permissions(manage_messages=True)
    async def emb_footer(self, ctx, *, text: str = None):
        """Set the footer text of the embed."""
        if not text:
            return await ctx.reply("Provide footer text.")
        if len(text) > 2048:
            return await ctx.reply("Footer must be 2048 characters or fewer.")
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("No draft. Run `,embed create` first.")
        await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"footer": text}})
        await ctx.reply("Footer set.")

    @embed_group.command(name="preview")
    @commands.has_permissions(manage_messages=True)
    async def emb_preview(self, ctx):
        """Preview your embed draft before sending."""
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("No draft. Run `,embed create` first.")
        try:
            color = int(draft.get("color", "2B2D31"), 16)
            final = discord.Embed(
                title=draft.get("title"),
                description=draft.get("description"),
                color=color
            )
            if draft.get("thumbnail"):
                final.set_thumbnail(url=draft["thumbnail"])
            if draft.get("image"):
                final.set_image(url=draft["image"])
            if draft.get("footer"):
                final.set_footer(text=draft["footer"])
            await ctx.reply(content="**Preview:**", embed=final)
        except Exception as e:
            await ctx.reply(f"Preview error: `{e}`")

    @embed_group.command(name="send")
    @commands.has_permissions(manage_messages=True)
    async def emb_send(self, ctx, channel: discord.TextChannel = None):
        """Send your embed draft to a channel."""
        channel = channel or ctx.channel
        draft   = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("No draft. Run `,embed create` first.")
        try:
            color = int(draft.get("color", "2B2D31"), 16)
            final = discord.Embed(
                title=draft.get("title"),
                description=draft.get("description"),
                color=color
            )
            if draft.get("thumbnail"):
                final.set_thumbnail(url=draft["thumbnail"])
            if draft.get("image"):
                final.set_image(url=draft["image"])
            if draft.get("footer"):
                final.set_footer(text=draft["footer"])
            await channel.send(embed=final)
            await embed_col.delete_one({"_id": draft["_id"]})
            await ctx.reply(f"✅ Embed sent to {channel.mention}.")
        except Exception as e:
            await ctx.reply(f"Error sending embed: `{e}`")

    @embed_group.command(name="discard")
    @commands.has_permissions(manage_messages=True)
    async def emb_discard(self, ctx):
        """Discard your current embed draft."""
        result = await embed_col.delete_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if result.deleted_count:
            await ctx.reply("Draft discarded.")
        else:
            await ctx.reply("No active draft to discard.")


async def setup(bot):
    await bot.add_cog(Utility(bot))