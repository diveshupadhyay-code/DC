import discord
from discord.ext import commands, tasks
from discord import app_commands, ui
import aiohttp, asyncio, urllib.parse, datetime
from datetime import timezone

from utils.db import (
    afk_col, sticky_col, profiles_col, embed_col,
    birthdays_col, settings_col, levels_col
)
from utils.helpers import BOT_OWNER_ID, ctx_mod, ctx_admin


def parse_embed_variables(text: str, ctx: commands.Context) -> str:
    if not text:
        return text
    replacements = {
        "{user}": ctx.author.mention,
        "{username}": ctx.author.name,
        "{user_id}": str(ctx.author.id),
        "{user_avatar}": ctx.author.display_avatar.url,
        "{server}": ctx.guild.name,
        "{server_id}": str(ctx.guild.id),
        "{server_icon}": ctx.guild.icon.url if ctx.guild.icon else "",
        "{members}": str(ctx.guild.member_count),
    }
    for key, val in replacements.items():
        text = text.replace(key, val)
    return text


class EmbedEditModal(ui.Modal, title="Edit Embed Configuration"):
    embed_title = ui.TextInput(
        label="Title",
        style=discord.TextStyle.short,
        placeholder="Enter embed title...",
        required=False,
        max_length=256
    )
    embed_desc = ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        placeholder="Enter embed description... (Variables: {user}, {server}, etc.)",
        required=False,
        max_length=4000
    )
    embed_color = ui.TextInput(
        label="Color (Hex Code)",
        style=discord.TextStyle.short,
        placeholder="2B2D31 or #FF0000",
        required=False,
        max_length=7
    )
    embed_thumb = ui.TextInput(
        label="Thumbnail URL ({server_icon}, {user_avatar}, URL)",
        style=discord.TextStyle.short,
        placeholder="https://... or {server_icon}",
        required=False,
        max_length=1000
    )
    embed_image = ui.TextInput(
        label="Image URL",
        style=discord.TextStyle.short,
        placeholder="https://...",
        required=False,
        max_length=1000
    )

    def __init__(self, cog, ctx, draft_data):
        super().__init__()
        self.cog = cog
        self.ctx = ctx
        self.draft_data = draft_data

        if draft_data.get("title"):
            self.embed_title.default = draft_data["title"]
        if draft_data.get("description"):
            self.embed_desc.default = draft_data["description"]
        if draft_data.get("color"):
            self.embed_color.default = draft_data["color"]
        if draft_data.get("thumbnail"):
            self.embed_thumb.default = draft_data["thumbnail"]
        if draft_data.get("image"):
            self.embed_image.default = draft_data["image"]

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("❌ This session is not for you.", ephemeral=True)

        clean_color = self.embed_color.value.replace("#", "").strip() if self.embed_color.value else "2B2D31"
        try:
            int(clean_color, 16)
        except ValueError:
            return await interaction.response.send_message("❌ Invalid hex color code provided.", ephemeral=True)

        update_data = {
            "title": self.embed_title.value or None,
            "description": self.embed_desc.value or None,
            "color": clean_color,
            "thumbnail": self.embed_thumb.value or None,
            "image": self.embed_image.value or None,
        }

        await embed_col.update_one(
            {"guild_id": str(self.ctx.guild.id), "author_id": str(self.ctx.author.id)},
            {"$set": update_data},
            upsert=True
        )

        preview_embed = await self.cog.build_embed_from_data(self.ctx, {**self.draft_data, **update_data})
        view = EmbedPreviewView(self.cog, self.ctx)
        
        await interaction.response.edit_message(content="✨ **Interactive Embed Builder**", embed=preview_embed, view=view)


class EmbedPreviewView(ui.View):
    def __init__(self, cog, ctx):
        super().__init__(timeout=180)
        self.cog = cog
        self.ctx = ctx

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ You cannot control this session.", ephemeral=True)
            return False
        return True

    @ui.button(label="Edit", style=discord.ButtonStyle.primary, emoji="✏️")
    async def edit_button(self, interaction: discord.Interaction, button: ui.Button):
        draft = await embed_col.find_one({"guild_id": str(self.ctx.guild.id), "author_id": str(self.ctx.author.id)}) or {}
        modal = EmbedEditModal(self.cog, self.ctx, draft)
        await interaction.response.send_modal(modal)

    @ui.button(label="Send", style=discord.ButtonStyle.success, emoji="🚀")
    async def send_button(self, interaction: discord.Interaction, button: ui.Button):
        draft = await embed_col.find_one({"guild_id": str(self.ctx.guild.id), "author_id": str(self.ctx.author.id)})
        if not draft:
            return await interaction.response.send_message("❌ No embed draft found to send.", ephemeral=True)

        final_embed = await self.cog.build_embed_from_data(self.ctx, draft)
        await self.ctx.channel.send(embed=final_embed)
        await embed_col.delete_one({"_id": draft["_id"]})
        
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="✅ **Embed successfully dispatched!**", view=self)

    @ui.button(label="Discard", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def discard_button(self, interaction: discord.Interaction, button: ui.Button):
        await embed_col.delete_one({"guild_id": str(self.ctx.guild.id), "author_id": str(self.ctx.author.id)})
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="🗑️ **Embed draft discarded.**", embed=None, view=self)


class Utility(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.birthday_check.start()

    def cog_unload(self):
        self.birthday_check.cancel()

    async def build_embed_from_data(self, ctx: commands.Context, draft: dict) -> discord.Embed:
        title = parse_embed_variables(draft.get("title"), ctx)
        description = parse_embed_variables(draft.get("description"), ctx)
        
        try:
            color = int(draft.get("color", "2B2D31"), 16)
        except ValueError:
            color = 0x2B2D31

        embed = discord.Embed(
            title=title,
            description=description,
            color=color
        )

        if draft.get("thumbnail"):
            parsed_thumb = parse_embed_variables(draft["thumbnail"], ctx)
            if parsed_thumb.startswith("http://") or parsed_thumb.startswith("https://"):
                embed.set_thumbnail(url=parsed_thumb)

        if draft.get("image"):
            parsed_img = parse_embed_variables(draft["image"], ctx)
            if parsed_img.startswith("http://") or parsed_img.startswith("https://"):
                embed.set_image(url=parsed_img)

        if draft.get("footer"):
            parsed_footer = parse_embed_variables(draft["footer"], ctx)
            embed.set_footer(text=parsed_footer)

        return embed

    @tasks.loop(hours=24)
    async def birthday_check(self):
        await self.bot.wait_until_ready()
        now = datetime.datetime.now(timezone.utc)
        current_day = now.day
        current_month = now.month

        cursor = birthdays_col.find({"day": current_day, "month": current_month})
        async for doc in cursor:
            guild_id = doc.get("guild_id")
            user_id = doc.get("user_id")

            try:
                guild = self.bot.get_guild(int(guild_id))
                if not guild:
                    continue

                member = guild.get_member(int(user_id))
                if not member:
                    continue

                settings = await settings_col.find_one({"_id": str(guild_id)}) or {}
                channel_id = settings.get("birthday_channel")

                channel = guild.get_channel(int(channel_id)) if channel_id else guild.system_channel

                if channel and channel.permissions_for(guild.me).send_messages:
                    embed = discord.Embed(
                        description=f"🎉 **Happy Birthday {member.mention}!** Wishing you a brilliant day! 🎂",
                        color=0x57F287
                    )
                    embed.set_thumbnail(url=member.display_avatar.url)
                    await channel.send(embed=embed)
            except Exception:
                continue

    @commands.command()
    async def ping(self, ctx):
        lat = round(self.bot.latency * 1000)
        color = 0x57F287 if lat < 100 else (0xFEE75C if lat < 200 else 0xED4245)
        embed = discord.Embed(
            description=f"⚡ **System Latency:** `{lat}ms`",
            color=color
        )
        await ctx.reply(embed=embed)

    @app_commands.command(name="ping", description="Check bot latency")
    async def slash_ping(self, interaction: discord.Interaction):
        lat = round(self.bot.latency * 1000)
        color = 0x57F287 if lat < 100 else (0xFEE75C if lat < 200 else 0xED4245)
        embed = discord.Embed(
            description=f"⚡ **System Latency:** `{lat}ms`",
            color=color
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.command(aliases=["whois", "ui"])
    async def userinfo(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        now = datetime.datetime.now(timezone.utc)
        created = member.created_at.replace(tzinfo=timezone.utc)
        age_days = (now - created).days

        roles = [r.mention for r in reversed(member.roles) if r != ctx.guild.default_role]
        roles_str = " ".join(roles[:10]) + (f" `+{len(roles)-10} more`" if len(roles) > 10 else "") if roles else "None"

        status_emoji = {
            discord.Status.online: "🟢 Online",
            discord.Status.idle: "🟡 Idle",
            discord.Status.dnd: "🔴 Do Not Disturb",
            discord.Status.offline: "⚫ Offline",
        }
        status_str = status_emoji.get(member.status, "⚫ Offline")

        embed = discord.Embed(color=member.color if member.color != discord.Color.default() else 0x2B2D31)
        embed.set_author(name=f"{member.name}", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)

        embed.add_field(name="User ID", value=f"`{member.id}`", inline=True)
        embed.add_field(name="Status", value=status_str, inline=True)
        embed.add_field(name="Bot Account", value="`Yes`" if member.bot else "`No`", inline=True)

        embed.add_field(name="Joined Server", value=f"<t:{int(member.joined_at.timestamp())}:D>" if member.joined_at else "`Unknown`", inline=True)
        embed.add_field(name="Created Account", value=f"<t:{int(created.timestamp())}:D> ({age_days}d ago)", inline=True)
        embed.add_field(name="Top Role", value=member.top_role.mention, inline=True)

        if member.premium_since:
            embed.add_field(name="Server Boosting", value=f"<t:{int(member.premium_since.timestamp())}:D>", inline=True)

        embed.add_field(name=f"Roles [{len(roles)}]", value=roles_str, inline=False)
        embed.set_footer(text=f"Requested by {ctx.author.name}")
        await ctx.reply(embed=embed)

    @app_commands.command(name="userinfo", description="Get information about a user")
    async def slash_userinfo(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        now = datetime.datetime.now(timezone.utc)
        created = member.created_at.replace(tzinfo=timezone.utc)
        age = (now - created).days

        embed = discord.Embed(color=0x2B2D31)
        embed.set_author(name=f"{member.name}", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)

        embed.add_field(name="User ID", value=f"`{member.id}`", inline=True)
        embed.add_field(name="Joined Server", value=f"<t:{int(member.joined_at.timestamp())}:D>" if member.joined_at else "`N/A`", inline=True)
        embed.add_field(name="Created Account", value=f"<t:{int(created.timestamp())}:D> ({age}d ago)", inline=True)
        embed.add_field(name="Top Role", value=member.top_role.mention, inline=True)

        await interaction.response.send_message(embed=embed)

    @commands.command(aliases=["av", "pfp"])
    async def avatar(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        embed = discord.Embed(color=0x2B2D31)
        embed.set_author(name=f"{member.name}'s Avatar", icon_url=member.display_avatar.url)
        embed.set_image(url=member.display_avatar.url)

        png = member.display_avatar.with_format('png').url
        jpg = member.display_avatar.with_format('jpg').url
        webp = member.display_avatar.with_format('webp').url
        embed.description = f"🔗 [PNG]({png}) · [JPG]({jpg}) · [WEBP]({webp})"

        await ctx.reply(embed=embed)

    @app_commands.command(name="avatar", description="View a user's avatar")
    async def slash_avatar(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        embed = discord.Embed(color=0x2B2D31)
        embed.set_author(name=f"{member.name}'s Avatar", icon_url=member.display_avatar.url)
        embed.set_image(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @commands.command(aliases=["si", "server", "guildinfo"])
    async def serverinfo(self, ctx):
        g = ctx.guild
        bots = sum(1 for m in g.members if m.bot)
        humans = g.member_count - bots
        online = sum(1 for m in g.members if m.status != discord.Status.offline and not m.bot)

        embed = discord.Embed(
            title=g.name,
            description=g.description or "No description available.",
            color=0x2B2D31,
            timestamp=datetime.datetime.now(timezone.utc)
        )
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        if g.banner:
            embed.set_image(url=g.banner.url)

        embed.add_field(name="Owner", value=g.owner.mention if g.owner else "Unknown", inline=True)
        embed.add_field(name="Members", value=f"`{humans}` Humans · `{bots}` Bots", inline=True)
        embed.add_field(name="Online", value=f"`{online}`", inline=True)
        embed.add_field(name="Channels", value=f"`{len(g.channels)}`", inline=True)
        embed.add_field(name="Roles", value=f"`{len(g.roles)}`", inline=True)
        embed.add_field(name="Emojis", value=f"`{len(g.emojis)}`", inline=True)
        embed.add_field(name="Tier", value=f"Level `{g.premium_tier}`", inline=True)
        embed.add_field(name="Boosters", value=f"`{g.premium_subscription_count}`", inline=True)
        embed.add_field(name="Created On", value=f"<t:{int(g.created_at.timestamp())}:D>", inline=True)
        embed.set_footer(text=f"Server ID: {g.id}")
        await ctx.reply(embed=embed)

    @commands.command(aliases=["mc"])
    async def membercount(self, ctx):
        g = ctx.guild
        bots = sum(1 for m in g.members if m.bot)
        humans = g.member_count - bots
        online = sum(1 for m in g.members if m.status != discord.Status.offline and not m.bot)

        embed = discord.Embed(color=0x2B2D31)
        embed.set_author(name=f"{g.name} Metrics", icon_url=g.icon.url if g.icon else None)
        embed.add_field(name="Total Members", value=f"`{g.member_count}`", inline=True)
        embed.add_field(name="Humans", value=f"`{humans}`", inline=True)
        embed.add_field(name="Bots", value=f"`{bots}`", inline=True)
        embed.add_field(name="Online Humans", value=f"`{online}`", inline=True)
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        await ctx.reply(embed=embed)

    @commands.command()
    async def afk(self, ctx, *, reason: str = "Away from keyboard"):
        if len(reason) > 200:
            return await ctx.reply("❌ Reason must not exceed 200 characters.")

        await afk_col.update_one(
            {"user_id": str(ctx.author.id), "guild_id": str(ctx.guild.id)},
            {"$set": {"reason": reason, "time": datetime.datetime.now(timezone.utc)}},
            upsert=True
        )
        try:
            if (ctx.guild.me.guild_permissions.manage_nicknames
                    and not ctx.author.display_name.startswith("[AFK]")
                    and ctx.author != ctx.guild.owner
                    and ctx.guild.me.top_role > ctx.author.top_role):
                await ctx.author.edit(nick=f"[AFK] {ctx.author.display_name[:25]}")
        except Exception:
            pass

        embed = discord.Embed(
            description=f"💤 {ctx.author.mention} is now AFK.\n**Reason:** `{reason}`",
            color=0x2B2D31
        )
        await ctx.reply(embed=embed)

    @app_commands.command(name="afk", description="Set your AFK status")
    async def slash_afk(self, interaction: discord.Interaction, reason: str = "Away from keyboard"):
        if len(reason) > 200:
            return await interaction.response.send_message("❌ Reason must not exceed 200 characters.", ephemeral=True)

        await afk_col.update_one(
            {"user_id": str(interaction.user.id), "guild_id": str(interaction.guild.id)},
            {"$set": {"reason": reason, "time": datetime.datetime.now(timezone.utc)}},
            upsert=True
        )
        embed = discord.Embed(description=f"💤 AFK set.\n**Reason:** `{reason}`", color=0x2B2D31)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.command()
    @ctx_mod()
    async def sticky(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply("❌ Usage: `,sticky <message>`")
        if len(text) > 1900:
            return await ctx.reply("❌ Sticky message must be under 1900 characters.")

        old = await sticky_col.find_one({"channel_id": str(ctx.channel.id)})
        if old:
            try:
                om = await ctx.channel.fetch_message(int(old["message_id"]))
                await om.delete()
            except Exception:
                pass

        embed = discord.Embed(description=text.replace("\\n", "\n"), color=0x2B2D31)
        embed.set_footer(text="📌 Sticky Message")
        msg = await ctx.channel.send(embed=embed)

        await sticky_col.update_one(
            {"channel_id": str(ctx.channel.id)},
            {"$set": {"message_id": str(msg.id), "content": text}},
            upsert=True
        )
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @commands.command()
    @ctx_mod()
    async def unsticky(self, ctx):
        data = await sticky_col.find_one({"channel_id": str(ctx.channel.id)})
        if not data:
            return await ctx.reply("❌ No active sticky message found in this channel.")
        try:
            om = await ctx.channel.fetch_message(int(data["message_id"]))
            await om.delete()
        except Exception:
            pass
        await sticky_col.delete_one({"channel_id": str(ctx.channel.id)})
        await ctx.reply("✅ Sticky message removed.", delete_after=4)

    LANG_MAP = {
        "english": "en", "hindi": "hi", "french": "fr",
        "spanish": "es", "german": "de", "japanese": "ja",
        "russian": "ru", "arabic": "ar", "portuguese": "pt",
        "italian": "it", "korean": "ko", "chinese": "zh",
        "turkish": "tr", "dutch": "nl", "polish": "pl",
        "swedish": "sv", "norwegian": "no", "danish": "da",
        "finnish": "fi", "greek": "el", "hebrew": "iw",
        "thai": "th", "vietnamese": "vi", "indonesian": "id",
        "malay": "ms", "urdu": "ur", "bengali": "bn",
        "punjabi": "pa", "tamil": "ta", "telugu": "te",
        "marathi": "mr", "gujarati": "gu", "kannada": "kn",
    }

    @commands.command(aliases=["tr"])
    async def translate(self, ctx, target_lang: str = "english", *, text_to_translate: str = None):
        target_lang = target_lang.lower()

        if ctx.message.reference and not text_to_translate:
            try:
                referenced_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                text_to_translate = referenced_msg.content
                if not referenced_msg.content and referenced_msg.embeds:
                    text_to_translate = referenced_msg.embeds[0].description or referenced_msg.embeds[0].title
            except Exception:
                return await ctx.reply("❌ Could not retrieve referenced message.")

            if target_lang not in self.LANG_MAP and target_lang not in self.LANG_MAP.values():
                if text_to_translate:
                    text_to_translate = f"{target_lang} {text_to_translate}"
                else:
                    text_to_translate = target_lang
                target_lang = "english"
        else:
            if not text_to_translate:
                return await ctx.reply(
                    "❌ Usage:\n"
                    "`,translate [language] <text>`\n"
                    "Or reply to a message with `,translate`"
                )

        if not text_to_translate or not text_to_translate.strip():
            return await ctx.reply("❌ No content found to translate.")

        if len(text_to_translate) > 1000:
            return await ctx.reply("❌ Input exceeds 1000 character limit.")

        resolved_lang_code = self.LANG_MAP.get(target_lang, target_lang[:5])
        api_url = (
            f"https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl=auto&tl={resolved_lang_code}&dt=t&q={urllib.parse.quote(text_to_translate)}"
        )

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, headers=headers) as response:
                    if response.status != 200:
                        return await ctx.reply("❌ Service unavailable. Try again later.")
                    response_data = await response.json(content_type=None)

            translated_segments = "".join(segment[0] for segment in response_data[0] if segment and segment[0])
            detected_language = response_data[2] if len(response_data) > 2 else "Unknown"

            if not translated_segments.strip():
                return await ctx.reply("❌ Empty translation response.")

            embed = discord.Embed(color=0x2B2D31)
            embed.set_author(name="Google Translate", icon_url="https://upload.wikimedia.org/wikipedia/commons/d/d7/Google_Translate_logo.svg")
            embed.add_field(
                name=f"Source ({str(detected_language).upper()})",
                value=f"```text\n{text_to_translate[:900]}```",
                inline=False
            )
            embed.add_field(
                name=f"Output ({resolved_lang_code.upper()})",
                value=f"```text\n{translated_segments[:900]}```",
                inline=False
            )
            await ctx.reply(embed=embed)

        except Exception as error_log:
            await ctx.reply(f"❌ Translation error: `{error_log}`")

    @commands.command(aliases=["ud"])
    async def urban(self, ctx, *, word: str = None):
        if not word:
            return await ctx.reply("❌ Usage: `,urban <term>`")

        url = f"https://api.urbandictionary.com/v0/define?term={urllib.parse.quote(word)}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status != 200:
                        return await ctx.reply("❌ Urban Dictionary API unreachable.")
                    data = await r.json()
        except asyncio.TimeoutError:
            return await ctx.reply("❌ Connection timed out.")
        except Exception as e:
            return await ctx.reply(f"❌ Error: `{e}`")

        if not data.get("list"):
            return await ctx.reply(f"❌ No definitions found for **{word}**.")

        top = data["list"][0]
        definition = top.get("definition", "").replace("[", "").replace("]", "").strip()
        example = top.get("example", "").replace("[", "").replace("]", "").strip()

        embed = discord.Embed(
            title=top.get("word", word),
            url=top.get("permalink", ""),
            color=0x2B2D31
        )
        embed.add_field(name="Definition", value=definition[:1024] or "None", inline=False)
        if example:
            embed.add_field(name="Example", value=f"*{example[:512]}*", inline=False)
        embed.set_footer(
            text=f"👍 {top.get('thumbs_up', 0)} · 👎 {top.get('thumbs_down', 0)} · Author: {top.get('author', 'Anonymous')}"
        )
        await ctx.reply(embed=embed)

    @commands.group(aliases=["card"], invoke_without_command=True)
    async def profile(self, ctx, member: discord.Member = None):
        member = member or ctx.author

        doc = await profiles_col.find_one({
            "guild_id": str(ctx.guild.id),
            "user_id": str(member.id)
        })
        bio = doc.get("bio", "Not set. Use `,profile bio <text>`") if doc else "Not set."
        loc = doc.get("location", "Not set") if doc else "Not set"

        lvl_doc = await levels_col.find_one({
            "guild_id": str(ctx.guild.id),
            "user_id": str(member.id)
        })
        level = lvl_doc.get("level", 0) if lvl_doc else 0
        xp = lvl_doc.get("xp", 0) if lvl_doc else 0
        nxt = (level + 1) * 100
        pct = int((xp / nxt) * 10) if nxt else 0
        bar = "█" * pct + "░" * (10 - pct)

        bday_doc = await birthdays_col.find_one({
            "guild_id": str(ctx.guild.id),
            "user_id": str(member.id)
        })
        bday = bday_doc.get("date", "Not set") if bday_doc else "Not set"

        embed = discord.Embed(color=member.color if member.color != discord.Color.default() else 0x2B2D31)
        embed.set_author(name=f"{member.name}'s Profile", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Bio", value=f"*{bio}*", inline=False)
        embed.add_field(name="Location", value=f"`{loc}`", inline=True)
        embed.add_field(name="Birthday", value=f"`{bday}`", inline=True)
        embed.add_field(name="Level", value=f"`{level}` ({xp}/{nxt} XP)", inline=True)
        embed.add_field(name="Progress", value=f"`{bar}`", inline=True)
        embed.add_field(name="Roles", value=f"`{len(member.roles) - 1}`", inline=True)
        embed.add_field(
            name="Joined",
            value=f"<t:{int(member.joined_at.timestamp())}:D>" if member.joined_at else "`Unknown`",
            inline=True
        )
        embed.set_footer(text=f"User ID: {member.id}")
        await ctx.reply(embed=embed)

    @profile.command(name="bio")
    async def profile_bio(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply("❌ Usage: `,profile bio <text>`")
        if len(text) > 150:
            return await ctx.reply("❌ Bio limit is 150 characters.")
        await profiles_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)},
            {"$set": {"bio": text}},
            upsert=True
        )
        embed = discord.Embed(description="✅ Bio updated successfully.", color=0x57F287)
        await ctx.reply(embed=embed)

    @profile.command(name="location", aliases=["loc"])
    async def profile_location(self, ctx, *, city: str = None):
        if not city:
            return await ctx.reply("❌ Usage: `,profile location <city>`")
        if len(city) > 40:
            return await ctx.reply("❌ Location limit is 40 characters.")
        await profiles_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)},
            {"$set": {"location": city}},
            upsert=True
        )
        embed = discord.Embed(description=f"✅ Location updated to `{city}`.", color=0x57F287)
        await ctx.reply(embed=embed)

    @profile.command(name="clear")
    async def profile_clear(self, ctx):
        await profiles_col.delete_one({"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)})
        embed = discord.Embed(description="✅ Profile cleared.", color=0x57F287)
        await ctx.reply(embed=embed)

    @commands.group(aliases=["bday"], invoke_without_command=True)
    async def birthday(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        doc = await birthdays_col.find_one({
            "guild_id": str(ctx.guild.id),
            "user_id": str(member.id)
        })
        if not doc:
            return await ctx.reply(f"❌ No birthday record set for **{member.name}**.")
        
        embed = discord.Embed(
            description=f"🎂 **{member.name}'s** birthday is set to **{doc['date']}**",
            color=0x2B2D31
        )
        await ctx.reply(embed=embed)

    @birthday.command(name="list")
    async def birthday_list(self, ctx):
        cursor = birthdays_col.find({"guild_id": str(ctx.guild.id)})
        birthdays = []
        async for doc in cursor:
            birthdays.append(doc)

        if not birthdays:
            return await ctx.reply("❌ No birthdays registered in this server.")

        birthdays.sort(key=lambda x: (x.get("month", 0), x.get("day", 0)))

        formatted_list = []
        for doc in birthdays:
            member = ctx.guild.get_member(int(doc["user_id"]))
            name = member.mention if member else f"User ID `{doc['user_id']}`"
            formatted_list.append(f"• {name} — **{doc['date']}**")

        embed = discord.Embed(
            title=f"🎂 Server Birthdays ({len(birthdays)})",
            description="\n".join(formatted_list[:25]),
            color=0x2B2D31
        )
        if len(formatted_list) > 25:
            embed.set_footer(text=f"Showing 25 of {len(formatted_list)} records.")
        await ctx.reply(embed=embed)

    @birthday.command(name="set")
    async def birthday_set(self, ctx, date: str = None):
        if not date:
            return await ctx.reply("❌ Usage: `,birthday set DD/MM` (e.g. `,birthday set 25/12`)")
        try:
            day, month = map(int, date.strip().split("/"))
            if not (1 <= day <= 31 and 1 <= month <= 12):
                raise ValueError
        except Exception:
            return await ctx.reply("❌ Invalid format. Use DD/MM.")

        await birthdays_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)},
            {"$set": {"date": date, "day": day, "month": month}},
            upsert=True
        )
        embed = discord.Embed(
            description=f"🎂 Birthday recorded as **{date}**.",
            color=0x57F287
        )
        await ctx.reply(embed=embed)

    @birthday.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def birthday_add(self, ctx, member: discord.Member = None, date: str = None):
        if not member or not date:
            return await ctx.reply("❌ Usage: `,birthday add @member DD/MM`")
        try:
            day, month = map(int, date.strip().split("/"))
            if not (1 <= day <= 31 and 1 <= month <= 12):
                raise ValueError
        except Exception:
            return await ctx.reply("❌ Invalid format. Use DD/MM.")

        await birthdays_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(member.id)},
            {"$set": {"date": date, "day": day, "month": month}},
            upsert=True
        )
        embed = discord.Embed(
            description=f"🎂 Birthday for {member.mention} set to **{date}**.",
            color=0x57F287
        )
        await ctx.reply(embed=embed)

    @birthday.command(name="remove")
    async def birthday_remove(self, ctx):
        result = await birthdays_col.delete_one({
            "guild_id": str(ctx.guild.id),
            "user_id": str(ctx.author.id)
        })
        if result.deleted_count:
            await ctx.reply("✅ Birthday removed.")
        else:
            await ctx.reply("❌ No birthday entry found.")

    @birthday.command(name="channel")
    @commands.has_permissions(manage_guild=True)
    async def birthday_channel(self, ctx, channel: discord.TextChannel = None):
        if channel is None:
            gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
            cid = gs.get("birthday_channel")
            if cid:
                ch = ctx.guild.get_channel(int(cid))
                return await ctx.reply(embed=discord.Embed(
                    description=f"Announcements active in {ch.mention if ch else f'<#{cid}>'}.",
                    color=0x2B2D31
                ))
            return await ctx.reply(embed=discord.Embed(
                description="No specific channel designated. Defaulting to system channel.",
                color=0x2B2D31
            ))

        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"birthday_channel": str(channel.id)}},
            upsert=True
        )
        embed = discord.Embed(
            description=f"🎂 Birthday notifications directed to {channel.mention}.",
            color=0x57F287
        )
        await ctx.reply(embed=embed)

    @birthday.command(name="channelremove", aliases=["removechannel"])
    @commands.has_permissions(manage_guild=True)
    async def birthday_channel_remove(self, ctx):
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$unset": {"birthday_channel": ""}},
            upsert=True
        )
        await ctx.reply("✅ Birthday channel preference removed.")

    @app_commands.command(name="birthday", description="View or set your birthday")
    @app_commands.describe(member="Member to check")
    async def slash_birthday(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        doc = await birthdays_col.find_one({
            "guild_id": str(interaction.guild.id),
            "user_id": str(member.id)
        })
        if not doc:
            await interaction.response.send_message(f"❌ No birthday record for **{member.name}**.", ephemeral=True)
        else:
            embed = discord.Embed(
                description=f"🎂 **{member.name}'s** birthday is **{doc['date']}**",
                color=0x2B2D31
            )
            await interaction.response.send_message(embed=embed)

    @commands.group(invoke_without_command=True)
    @ctx_mod()
    async def mimic(self, ctx, member: discord.Member = None, *, message: str = None):
        if not member or not message:
            return await ctx.reply("❌ Usage: `,mimic @user <message>`")
        if len(message) > 2000:
            return await ctx.reply("❌ Message payload exceeds 2000 characters.")
        if not ctx.channel.permissions_for(ctx.guild.me).manage_webhooks:
            return await ctx.reply("❌ Missing `Manage Webhooks` permission.")

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
            except Exception:
                pass
        except discord.Forbidden:
            await ctx.reply("❌ Permission denied while creating/using webhook.")

    @mimic.command(name="custom")
    @ctx_mod()
    async def mimic_custom(self, ctx, name: str = None, avatar_url: str = None, *, message: str = None):
        if not name or not avatar_url or not message:
            return await ctx.reply("❌ Usage: `,mimic custom <name> <avatar_url> <message>`")
        if not (avatar_url.startswith("http://") or avatar_url.startswith("https://")):
            return await ctx.reply("❌ Avatar URL must be a valid HTTP/HTTPS direct image link.")
        if not ctx.channel.permissions_for(ctx.guild.me).manage_webhooks:
            return await ctx.reply("❌ Missing `Manage Webhooks` permission.")

        try:
            webhooks = await ctx.channel.webhooks()
            wh = discord.utils.get(webhooks, name="HappyCustomWebhook")
            if not wh:
                wh = await ctx.channel.create_webhook(name="HappyCustomWebhook")

            await wh.send(
                content=message,
                username=name[:80],
                avatar_url=avatar_url
            )
            try:
                await ctx.message.delete()
            except Exception:
                pass
        except discord.Forbidden:
            await ctx.reply("❌ Insufficient webhook permissions.")
        except Exception as e:
            await ctx.reply(f"❌ Failed to dispatch custom webhook: `{e}`")

    @commands.command()
    @ctx_mod()
    async def echo(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply("❌ Usage: `,echo [ #channel ] <message>`")

        target_channel = ctx.channel
        content = text

        if ctx.message.channel_mentions:
            mentioned = ctx.message.channel_mentions[0]
            if text.startswith(mentioned.mention) or text.startswith(f"<#{mentioned.id}>"):
                target_channel = mentioned
                content = text.replace(mentioned.mention, "", 1).strip()

        if not content:
            return await ctx.reply("❌ Message content cannot be empty.")

        if not target_channel.permissions_for(ctx.guild.me).send_messages:
            return await ctx.reply("❌ Cannot send messages in the specified channel.")

        await target_channel.send(content)
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @commands.group(name="embed", invoke_without_command=True)
    @commands.has_permissions(manage_messages=True)
    async def embed_group(self, ctx):
        embed = discord.Embed(
            title="🛠️ Interactive Embed Manager",
            color=0x2B2D31,
            description=(
                "**Builder Commands:**\n"
                "`1.` `,embed create` — Initialize new draft\n"
                "`2.` `,embed title <text>` — Assign embed title\n"
                "`3.` `,embed description <text>` — Assign description\n"
                "`4.` `,embed color <#hex>` — Define color scheme\n"
                "`5.` `,embed thumbnail <url/{server_icon}>` — Add thumbnail\n"
                "`6.` `,embed image <url>` — Add image preview\n"
                "`7.` `,embed footer <text>` — Assign footer text\n"
                "`8.` `,embed preview` — Display builder preview with buttons\n"
                "`9.` `,embed send [#channel]` — Dispatch draft embed\n"
                "`10.` `,embed discard` — Wipe current draft\n\n"
                "**Variables Supported:** `{user}`, `{username}`, `{user_id}`, `{user_avatar}`, `{server}`, `{server_id}`, `{server_icon}`, `{members}`"
            )
        )
        await ctx.reply(embed=embed)

    @embed_group.command(name="create")
    @commands.has_permissions(manage_messages=True)
    async def emb_create(self, ctx):
        await embed_col.update_one(
            {"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)},
            {"$set": {
                "title": None,
                "description": "Welcome {user} to {server}!",
                "color": "2B2D31",
                "thumbnail": "{server_icon}",
                "image": None,
                "footer": None
            }},
            upsert=True
        )
        await ctx.reply("✨ Draft created. Run `,embed preview` or adjust settings with commands.")

    @embed_group.command(name="title")
    @commands.has_permissions(manage_messages=True)
    async def emb_title(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply("❌ Title field cannot be empty.")
        if len(text) > 256:
            return await ctx.reply("❌ Maximum 256 characters allowed for title.")
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("❌ Draft session non-existent. Run `,embed create` first.")
        await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"title": text}})
        await ctx.reply("✅ Title updated.")

    @embed_group.command(name="description", aliases=["desc"])
    @commands.has_permissions(manage_messages=True)
    async def emb_desc(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply("❌ Description field cannot be empty.")
        if len(text) > 4000:
            return await ctx.reply("❌ Maximum 4000 characters allowed.")
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("❌ Draft session non-existent. Run `,embed create` first.")
        await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"description": text}})
        await ctx.reply("✅ Description updated.")

    @embed_group.command(name="color", aliases=["colour"])
    @commands.has_permissions(manage_messages=True)
    async def emb_color(self, ctx, hex_code: str = None):
        if not hex_code:
            return await ctx.reply("❌ Provide a valid hex code.")
        clean = hex_code.replace("#", "").strip()
        try:
            int(clean, 16)
        except ValueError:
            return await ctx.reply("❌ Invalid hex color code.")
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("❌ Draft session non-existent.")
        await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"color": clean}})
        embed = discord.Embed(description=f"✅ Color configured to `#{clean}`.", color=int(clean, 16))
        await ctx.reply(embed=embed)

    @embed_group.command(name="thumbnail")
    @commands.has_permissions(manage_messages=True)
    async def emb_thumbnail(self, ctx, url: str = None):
        if not url:
            return await ctx.reply("❌ Provide a valid image link or variable.")
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("❌ Draft session non-existent.")
        await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"thumbnail": url}})
        await ctx.reply("✅ Thumbnail updated.")

    @embed_group.command(name="image")
    @commands.has_permissions(manage_messages=True)
    async def emb_image(self, ctx, url: str = None):
        if not url:
            return await ctx.reply("❌ Provide a valid image link.")
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("❌ Draft session non-existent.")
        await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"image": url}})
        await ctx.reply("✅ Main image updated.")

    @embed_group.command(name="footer")
    @commands.has_permissions(manage_messages=True)
    async def emb_footer(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply("❌ Footer text cannot be empty.")
        if len(text) > 2048:
            return await ctx.reply("❌ Maximum 2048 characters allowed for footer.")
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("❌ Draft session non-existent.")
        await embed_col.update_one({"_id": draft["_id"]}, {"$set": {"footer": text}})
        await ctx.reply("✅ Footer updated.")

    @embed_group.command(name="preview")
    @commands.has_permissions(manage_messages=True)
    async def emb_preview(self, ctx):
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("❌ No active draft found. Run `,embed create` to begin.")

        try:
            preview_embed = await self.build_embed_from_data(ctx, draft)
            view = EmbedPreviewView(self, ctx)
            await ctx.reply(content="✨ **Interactive Embed Builder**", embed=preview_embed, view=view)
        except Exception as e:
            await ctx.reply(f"❌ Preview generation failed: `{e}`")

    @embed_group.command(name="send")
    @commands.has_permissions(manage_messages=True)
    async def emb_send(self, ctx, channel: discord.TextChannel = None):
        target = channel or ctx.channel
        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if not draft:
            return await ctx.reply("❌ No active draft found to send.")

        if not target.permissions_for(ctx.guild.me).send_messages:
            return await ctx.reply("❌ Lacking send permissions in target channel.")

        try:
            final_embed = await self.build_embed_from_data(ctx, draft)
            await target.send(embed=final_embed)
            await embed_col.delete_one({"_id": draft["_id"]})
            await ctx.reply(f"✅ Embed successfully sent to {target.mention}.")
        except Exception as e:
            await ctx.reply(f"❌ Embed dispatch error: `{e}`")

    @embed_group.command(name="discard")
    @commands.has_permissions(manage_messages=True)
    async def emb_discard(self, ctx):
        result = await embed_col.delete_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id)})
        if result.deleted_count:
            await ctx.reply("✅ Draft deleted successfully.")
        else:
            await ctx.reply("❌ Active draft non-existent.")


async def setup(bot):
    await bot.add_cog(Utility(bot))