"""
cogs/utility.py — userinfo, avatar, serverinfo, ping, translate, urban, profile,
                   afk, sticky, embed builder, mimic, echo, birthday, level.
"""

import discord
from discord.ext import commands
from discord import app_commands
import aiohttp, asyncio, urllib.parse, datetime
from datetime import timezone

from utils.db import (
    afk_col, sticky_col, profiles_col, embed_col,
    birthdays_col, settings_col
)
from utils.helpers import BOT_OWNER_ID, ctx_mod, ctx_admin


class Utility(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Ping ──────────────────────────────────────────────────────────────────
    @commands.command()
    async def ping(self, ctx):
        """Check bot latency."""
        lat = round(self.bot.latency * 1000)
        embed = discord.Embed(description=f"Latency: **{lat}ms**", color=0x2B2D31)
        await ctx.reply(embed=embed)

    @app_commands.command(name="ping", description="Check bot latency")
    async def slash_ping(self, interaction: discord.Interaction):
        lat = round(self.bot.latency * 1000)
        await interaction.response.send_message(
            embed=discord.Embed(description=f"Latency: **{lat}ms**", color=0x2B2D31), ephemeral=True
        )

    # ── Userinfo ──────────────────────────────────────────────────────────────
    @commands.command(aliases=["whois"])
    async def userinfo(self, ctx, member: discord.Member = None):
        """View detailed info about a member."""
        member = member or ctx.author
        embed  = discord.Embed(title=str(member), color=0x2B2D31)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID",          value=member.id,                                                                 inline=True)
        embed.add_field(name="Joined",      value=member.joined_at.strftime("%d %b %Y") if member.joined_at else "Unknown",  inline=True)
        embed.add_field(name="Created",     value=member.created_at.strftime("%d %b %Y"),                                    inline=True)
        embed.add_field(name="Top Role",    value=member.top_role.mention,                                                   inline=True)
        embed.add_field(name="Bot",         value="Yes" if member.bot else "No",                                             inline=True)
        embed.add_field(name="Roles",       value=str(len(member.roles) - 1),                                                inline=True)
        status = str(member.status).title() if hasattr(member, 'status') else "Unknown"
        embed.add_field(name="Status",      value=status, inline=True)
        await ctx.reply(embed=embed)

    @app_commands.command(name="userinfo", description="Get information about a user")
    async def slash_userinfo(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        embed = discord.Embed(title=str(member), color=0x2B2D31)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID",       value=member.id,                                                                inline=True)
        embed.add_field(name="Joined",   value=member.joined_at.strftime("%d %b %Y") if member.joined_at else "N/A",    inline=True)
        embed.add_field(name="Top Role", value=member.top_role.mention,                                                  inline=True)
        await interaction.response.send_message(embed=embed)

    # ── Avatar ────────────────────────────────────────────────────────────────
    @commands.command(aliases=["av"])
    async def avatar(self, ctx, member: discord.Member = None):
        """View someone's avatar in full size."""
        member = member or ctx.author
        embed  = discord.Embed(title=f"{member.display_name}'s Avatar", color=0x2B2D31)
        embed.set_image(url=member.display_avatar.url)
        embed.add_field(name="Download", value=f"[Click here]({member.display_avatar.url})")
        await ctx.reply(embed=embed)

    @app_commands.command(name="avatar", description="View a user's avatar")
    async def slash_avatar(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        embed  = discord.Embed(title=f"{member.display_name}'s Avatar", color=0x2B2D31)
        embed.set_image(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    # ── Server info ───────────────────────────────────────────────────────────
    @commands.command(aliases=["si", "server"])
    async def serverinfo(self, ctx):
        """View server statistics."""
        g = ctx.guild
        embed = discord.Embed(title=g.name, color=0x2B2D31)
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        embed.add_field(name="Owner",       value=g.owner.mention if g.owner else "Unknown", inline=True)
        embed.add_field(name="Members",     value=g.member_count,                            inline=True)
        embed.add_field(name="Channels",    value=len(g.channels),                           inline=True)
        embed.add_field(name="Roles",       value=len(g.roles),                              inline=True)
        embed.add_field(name="Boost Level", value=f"Level {g.premium_tier}",                 inline=True)
        embed.add_field(name="Boosters",    value=g.premium_subscription_count,              inline=True)
        embed.add_field(name="Created",     value=g.created_at.strftime("%d %b %Y"),         inline=True)
        embed.add_field(name="ID",          value=g.id,                                      inline=True)
        await ctx.reply(embed=embed)

    # ── Membercount ───────────────────────────────────────────────────────────
    @commands.command(aliases=["mc"])
    async def membercount(self, ctx):
        """View member count breakdown."""
        g      = ctx.guild
        bots   = sum(1 for m in g.members if m.bot)
        humans = g.member_count - bots
        embed  = discord.Embed(title=f"{g.name} — Members", color=0x2B2D31)
        embed.add_field(name="Total",  value=g.member_count, inline=True)
        embed.add_field(name="Humans", value=humans,          inline=True)
        embed.add_field(name="Bots",   value=bots,            inline=True)
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        await ctx.reply(embed=embed)

    # ── AFK ───────────────────────────────────────────────────────────────────
    @commands.command()
    async def afk(self, ctx, *, reason: str = "Away from keyboard"):
        """Set your AFK status. Bot will notify people who ping you."""
        await afk_col.update_one(
            {"user_id": ctx.author.id, "guild_id": ctx.guild.id},
            {"$set": {"reason": reason, "time": datetime.datetime.now(timezone.utc)}},
            upsert=True
        )
        try:
            if ctx.guild.me.guild_permissions.manage_nicknames and \
               not ctx.author.display_name.startswith("[AFK]"):
                await ctx.author.edit(nick=f"[AFK] {ctx.author.display_name[:25]}")
        except:
            pass
        embed = discord.Embed(
            description=f"{ctx.author.mention} is now AFK.\nReason: `{reason}`",
            color=0x2B2D31
        )
        await ctx.reply(embed=embed)

    @app_commands.command(name="afk", description="Set your AFK status")
    async def slash_afk(self, interaction: discord.Interaction, reason: str = "Away from keyboard"):
        await afk_col.update_one(
            {"user_id": interaction.user.id, "guild_id": interaction.guild.id},
            {"$set": {"reason": reason, "time": datetime.datetime.now(timezone.utc)}},
            upsert=True
        )
        await interaction.response.send_message(f"AFK status set. Reason: `{reason}`", ephemeral=True)

    # ── Sticky ────────────────────────────────────────────────────────────────
    @commands.command()
    @ctx_mod()
    async def sticky(self, ctx, *, text: str = None):
        """Pin a sticky message that re-appears after every new message."""
        if not text:
            return await ctx.reply("Usage: `,sticky Your message`")
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
        try:
            await ctx.message.delete()
        except:
            pass

    @commands.command()
    @ctx_mod()
    async def unsticky(self, ctx):
        """Remove the sticky message from this channel."""
        data = await sticky_col.find_one({"channel_id": ctx.channel.id})
        if not data:
            return await ctx.reply("No sticky message set in this channel.")
        try:
            om = await ctx.channel.fetch_message(data["message_id"])
            await om.delete()
        except:
            pass
        await sticky_col.delete_one({"channel_id": ctx.channel.id})
        await ctx.reply("Sticky message removed.", delete_after=4)

    # ── Translate ─────────────────────────────────────────────────────────────
    LANG_MAP = {
        "english": "en", "hindi": "hi", "french": "fr", "spanish": "es",
        "german": "de", "japanese": "ja", "russian": "ru", "arabic": "ar",
        "portuguese": "pt", "italian": "it", "korean": "ko", "chinese": "zh",
    }

    @commands.command(aliases=["tr"])
    async def translate(self, ctx, lang: str = None, *, text: str = None):
        """Translate text into any language."""
        if not lang or not text:
            return await ctx.reply(
                "Usage: `,translate <language> <text>`\n"
                "Example: `,translate hindi Hello everyone`"
            )
        tl  = self.LANG_MAP.get(lang.lower(), lang.lower()[:2])
        url = (f"https://translate.googleapis.com/translate_a/single"
               f"?client=gtx&sl=auto&tl={tl}&dt=t&q={urllib.parse.quote(text)}")
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as r:
                if r.status != 200:
                    return await ctx.reply("Translation service unavailable. Try again later.")
                result = await r.json()
        try:
            translated  = "".join(s[0] for s in result[0] if s[0])
            detected    = result[2] if len(result) > 2 else "?"
            embed = discord.Embed(title="Translation", color=0x2B2D31)
            embed.add_field(name=f"Original ({detected.upper()})", value=f"```{text[:400]}```",       inline=False)
            embed.add_field(name=f"Translated ({tl.upper()})",     value=f"```{translated[:400]}```", inline=False)
            await ctx.reply(embed=embed)
        except:
            await ctx.reply("Could not parse the translation response.")

    # ── Urban Dictionary ──────────────────────────────────────────────────────
    @commands.command(aliases=["ud"])
    async def urban(self, ctx, *, word: str = None):
        """Look up a word on Urban Dictionary."""
        if not word:
            return await ctx.reply("Usage: `,urban <word>`")
        url = f"https://api.urbandictionary.com/v0/define?term={urllib.parse.quote(word)}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as r:
                if r.status != 200:
                    return await ctx.reply("Could not reach Urban Dictionary.")
                data = await r.json()
        if not data.get("list"):
            return await ctx.reply(f"No definition found for `{word}`.")
        top        = data["list"][0]
        definition = top["definition"].replace("[","").replace("]","")[:1000]
        example    = top["example"].replace("[","").replace("]","")[:500]
        embed = discord.Embed(title=top["word"], url=top["permalink"], color=0x2B2D31)
        embed.add_field(name="Definition", value=definition,           inline=False)
        if example:
            embed.add_field(name="Example", value=f"*{example}*",     inline=False)
        embed.set_footer(text=f"👍 {top['thumbs_up']} | 👎 {top['thumbs_down']} | by {top['author']}")
        await ctx.reply(embed=embed)

    # ── Profile ───────────────────────────────────────────────────────────────
    @commands.group(aliases=["card"], invoke_without_command=True)
    async def profile(self, ctx, member: discord.Member = None):
        """View your or someone else's profile card."""
        member = member or ctx.author
        doc    = await profiles_col.find_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
        bio    = doc.get("bio",      "Not set. Use `,profile bio <text>`") if doc else "Not set."
        loc    = doc.get("location", "Unknown") if doc else "Unknown"
        lvl_doc = await levels_col.find_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
        level   = lvl_doc.get("level", 0) if lvl_doc else 0
        xp      = lvl_doc.get("xp",    0) if lvl_doc else 0

        embed = discord.Embed(color=0x2B2D31)
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Bio",      value=f"*{bio}*",                                              inline=False)
        embed.add_field(name="Location", value=loc,                                                      inline=True)
        embed.add_field(name="Level",    value=f"**{level}** ({xp} XP)",                                inline=True)
        embed.add_field(name="Roles",    value=str(len(member.roles) - 1),                              inline=True)
        embed.add_field(name="Joined",   value=member.joined_at.strftime("%d %b %Y") if member.joined_at else "?", inline=True)
        embed.set_footer(text=f"ID: {member.id}")
        await ctx.reply(embed=embed)

    @profile.command(name="bio")
    async def profile_bio(self, ctx, *, text: str = None):
        """Set your profile bio."""
        if not text:
            return await ctx.reply("Usage: `,profile bio <your bio>`")
        if len(text) > 150:
            return await ctx.reply("Bio must be 150 characters or fewer.")
        await profiles_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)},
            {"$set": {"bio": text}}, upsert=True
        )
        await ctx.reply("Bio updated.")

    @profile.command(name="location", aliases=["loc"])
    async def profile_location(self, ctx, *, city: str = None):
        """Set your profile location."""
        if not city:
            return await ctx.reply("Usage: `,profile location <city>`")
        if len(city) > 40:
            return await ctx.reply("Location must be 40 characters or fewer.")
        await profiles_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)},
            {"$set": {"location": city}}, upsert=True
        )
        await ctx.reply(f"Location set to `{city}`.")

    # ── Birthday ──────────────────────────────────────────────────────────────
    @commands.group(aliases=["bday"], invoke_without_command=True)
    async def birthday(self, ctx, member: discord.Member = None):
        """View a member's birthday."""
        member = member or ctx.author
        doc    = await birthdays_col.find_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
        if not doc:
            return await ctx.reply(f"No birthday set for **{member}**. Use `,birthday set DD/MM`.")
        await ctx.reply(embed=discord.Embed(
            description=f"**{member.display_name}'s** birthday: **{doc['date']}**",
            color=0x2B2D31
        ))

    @birthday.command(name="channel")
    @commands.has_permissions(manage_guild=True)
    async def birthday_channel(self, ctx, channel: discord.TextChannel = None):
        """
        Set the channel where birthday wishes are announced.
        Leave blank to view current. Use 'remove' to clear.
        """
        if channel is None:
            gs  = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
            cid = gs.get("birthday_channel")
            if cid:
                ch = ctx.guild.get_channel(int(cid))
                return await ctx.reply(embed=discord.Embed(
                    description=(f"Birthday announcements go to {ch.mention if ch else f'<#{cid}>'}."
                                 "\nUse `,birthday channel remove` to clear."),
                    color=0x2B2D31
                ))
            else:
                return await ctx.reply(embed=discord.Embed(
                    description=("No birthday channel set. Birthdays go to the welcome channel or system channel by default.\nUse `,birthday channel #channel` to set one."),
                    color=0x2B2D31
                ))

        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"birthday_channel": str(channel.id)}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(
            description=f"Birthday announcements will now go to {channel.mention}.",
            color=0x2B2D31
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

    @birthday.command(name="set")
    async def birthday_set(self, ctx, date: str = None):
        """Set your birthday in DD/MM format."""
        if not date:
            return await ctx.reply("Usage: `,birthday set DD/MM`  (e.g. `,birthday set 25/12`)")
        try:
            day, month = map(int, date.split("/"))
            if not (1 <= day <= 31 and 1 <= month <= 12):
                raise ValueError
        except:
            return await ctx.reply("Invalid format. Use DD/MM, e.g. `25/12`.")
        await birthdays_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)},
            {"$set": {"date": date, "day": day, "month": month}},
            upsert=True
        )
        await ctx.reply(f"Birthday set to **{date}**.")

    # ── Mimic / Echo ──────────────────────────────────────────────────────────
    @commands.command()
    @ctx_mod()
    async def mimic(self, ctx, member: discord.Member = None, *, message: str = None):
        """Send a message as another member using a webhook."""
        if not member or not message:
            return await ctx.reply("Usage: `,mimic @user <message>`")
        webhooks = await ctx.channel.webhooks()
        wh = discord.utils.get(webhooks, name="HappyMimic")
        if not wh:
            wh = await ctx.channel.create_webhook(name="HappyMimic")
        await wh.send(content=message, username=member.display_name, avatar_url=member.display_avatar.url)
        try:
            await ctx.message.delete()
        except:
            pass

    @commands.command()
    @ctx_mod()
    async def echo(self, ctx, channel: discord.TextChannel = None, *, message: str = None):
        """Send a message as the bot."""
        if not message:
            return await ctx.reply("Usage: `,echo [#channel] <message>`")
        target = channel or ctx.channel
        await target.send(message)
        try:
            await ctx.message.delete()
        except:
            pass

    # ── Embed builder ─────────────────────────────────────────────────────────
    @commands.group(name="embed", invoke_without_command=True)
    @commands.has_permissions(manage_messages=True)
    async def embed_group(self, ctx):
        """Interactive embed builder. Steps: create → title → desc → color → thumbnail → send"""
        embed = discord.Embed(title="Embed Builder", color=0x2B2D31, description=(
            "**Step-by-step:**\n"
            "1. `,embed create` — start a new draft\n"
            "2. `,embed title <text>`\n"
            "3. `,embed description <text>`\n"
            "4. `,embed color #hex`\n"
            "5. `,embed thumbnail <url>`\n"
            "6. `,embed send [#channel]`"
        ))
        await ctx.reply(embed=embed)

    @embed_group.command(name="create")
    @commands.has_permissions(manage_messages=True)
    async def emb_create(self, ctx):
        await embed_col.update_one(
            {"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)},
            {"$set": {"title": None, "description": "Default description.", "color": "2B2D31", "thumbnail": None}},
            upsert=True
        )
        await ctx.reply("Draft created. Use `,embed title/description/color/thumbnail/send` to customize.")

    @embed_group.command(name="title")
    @commands.has_permissions(manage_messages=True)
    async def emb_title(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply("Provide a title.")
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("No draft. Run `,embed create` first.")
        await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"title": text}})
        await ctx.reply(f"Title set to `{text}`.")

    @embed_group.command(name="description", aliases=["desc"])
    @commands.has_permissions(manage_messages=True)
    async def emb_desc(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply("Provide a description.")
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("No draft. Run `,embed create` first.")
        await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"description": text}})
        await ctx.reply("Description updated.")

    @embed_group.command(name="color")
    @commands.has_permissions(manage_messages=True)
    async def emb_color(self, ctx, hex_code: str = None):
        if not hex_code:
            return await ctx.reply("Provide a hex code, e.g. `#FF0000`.")
        clean = hex_code.replace("#","").strip()
        try:
            int(clean, 16)
        except:
            return await ctx.reply("Invalid hex code.")
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("No draft. Run `,embed create` first.")
        await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"color": clean}})
        await ctx.reply(f"Color set to `#{clean}`.")

    @embed_group.command(name="thumbnail")
    @commands.has_permissions(manage_messages=True)
    async def emb_thumbnail(self, ctx, url: str = None):
        if not url or not url.startswith("http"):
            return await ctx.reply("Provide a valid image URL.")
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("No draft. Run `,embed create` first.")
        await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"thumbnail": url}})
        await ctx.reply("Thumbnail set.")

    @embed_group.command(name="send")
    @commands.has_permissions(manage_messages=True)
    async def emb_send(self, ctx, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        draft   = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("No draft. Run `,embed create` first.")
        try:
            color   = int(draft.get("color","2B2D31"), 16)
            final   = discord.Embed(title=draft.get("title"), description=draft.get("description"), color=color)
            if draft.get("thumbnail"):
                final.set_thumbnail(url=draft["thumbnail"])
            await channel.send(embed=final)
            await embed_col.delete_one({"_id": draft["_id"]})
            await ctx.reply(f"Embed sent to {channel.mention}.")
        except Exception as e:
            await ctx.reply(f"Error: {e}")


async def setup(bot):
    await bot.add_cog(Utility(bot))