import discord
from discord.ext import commands, tasks
from discord import app_commands, ui
import aiohttp, asyncio, urllib.parse, datetime, json
from datetime import timezone

from utils.db import (
    afk_col, sticky_col, profiles_col, embed_col,
    birthdays_col, settings_col, levels_col, autoresponder_col
)
from utils.helpers import BOT_OWNER_ID, ctx_mod, ctx_admin

def parse_embed_variables(text: str, user: discord.Member, guild: discord.Guild) -> str:
    if not text:
        return text
    replacements = {
        "{user}": user.mention,
        "{username}": user.name,
        "{user_id}": str(user.id),
        "{user_avatar}": user.display_avatar.url,
        "{server}": guild.name,
        "{server_id}": str(guild.id),
        "{server_icon}": guild.icon.url if guild.icon else "",
        "{members}": str(guild.member_count),
    }
    for key, val in replacements.items():
        text = text.replace(key, val)
    return text

class EmbedEditModal(ui.Modal, title="Embed Studio"):
    embed_title = ui.TextInput(label="Title", style=discord.TextStyle.short, required=False, max_length=256)
    embed_desc = ui.TextInput(label="Description", style=discord.TextStyle.paragraph, required=False, max_length=4000)
    embed_color = ui.TextInput(label="Color (Hex)", style=discord.TextStyle.short, required=False, max_length=7)
    embed_thumb = ui.TextInput(label="Thumbnail URL", style=discord.TextStyle.short, required=False, max_length=1000)
    embed_image = ui.TextInput(label="Image URL", style=discord.TextStyle.short, required=False, max_length=1000)
    embed_footer = ui.TextInput(label="Footer Text", style=discord.TextStyle.short, required=False, max_length=2048)

    def __init__(self, cog, author_id, guild_id, draft_name, draft_data):
        super().__init__()
        self.cog = cog
        self.author_id = author_id
        self.guild_id = guild_id
        self.draft_name = draft_name

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
        if draft_data.get("footer"):
            self.embed_footer.default = draft_data["footer"]

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id or str(interaction.guild.id) != str(self.guild_id):
            return await interaction.response.send_message("This session is locked.", ephemeral=True)
        if not interaction.user.guild_permissions.manage_messages and not interaction.permissions.manage_messages:
            return await interaction.response.send_message("You lack moderator permissions to edit this draft.", ephemeral=True)

        clean_color = self.embed_color.value.replace("#", "").strip() if self.embed_color.value else "2B2D31"
        try:
            int(clean_color, 16)
        except ValueError:
            return await interaction.response.send_message("Invalid color code provided.", ephemeral=True)

        thumb_val = self.embed_thumb.value.strip() if self.embed_thumb.value else None
        if thumb_val and not (thumb_val.startswith("http://") or thumb_val.startswith("https://") or thumb_val.startswith("{")):
            return await interaction.response.send_message("Invalid thumbnail URL format.", ephemeral=True)

        image_val = self.embed_image.value.strip() if self.embed_image.value else None
        if image_val and not (image_val.startswith("http://") or image_val.startswith("https://")):
            return await interaction.response.send_message("Invalid image URL format.", ephemeral=True)

        update_data = {
            "title": self.embed_title.value or None,
            "description": self.embed_desc.value or None,
            "color": clean_color,
            "thumbnail": thumb_val,
            "image": image_val,
            "footer": self.embed_footer.value or None,
        }

        await embed_col.update_one(
            {"guild_id": str(interaction.guild.id), "author_id": str(interaction.user.id), "draft_name": self.draft_name},
            {"$set": update_data},
            upsert=True
        )

        draft = await embed_col.find_one({"guild_id": str(interaction.guild.id), "author_id": str(interaction.user.id), "draft_name": self.draft_name})
        preview_embed = await self.cog.build_embed_from_data(interaction.user, interaction.guild, draft)
        view = EmbedPreviewView(self.cog, self.author_id, self.guild_id, self.draft_name)
        
        await interaction.response.edit_message(content=f"Draft: `{self.draft_name}`", embed=preview_embed, view=view)

class EmbedPreviewView(ui.View):
    def __init__(self, cog, author_id, guild_id, draft_name):
        super().__init__(timeout=180)
        self.cog = cog
        self.author_id = author_id
        self.guild_id = guild_id
        self.draft_name = draft_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id or str(interaction.guild.id) != str(self.guild_id):
            await interaction.response.send_message("You cannot control this session.", ephemeral=True)
            return False
        if not interaction.user.guild_permissions.manage_messages and not interaction.permissions.manage_messages:
            await interaction.response.send_message("You lack moderator permissions to manage this embed draft.", ephemeral=True)
            return False
        return True

    @ui.button(label="Edit", style=discord.ButtonStyle.primary, emoji="✏️")
    async def edit_button(self, interaction: discord.Interaction, button: ui.Button):
        draft = await embed_col.find_one({"guild_id": str(interaction.guild.id), "author_id": str(interaction.user.id), "draft_name": self.draft_name}) or {}
        modal = EmbedEditModal(self.cog, self.author_id, self.guild_id, self.draft_name, draft)
        await interaction.response.send_modal(modal)

    @ui.button(label="Send", style=discord.ButtonStyle.success, emoji="🚀")
    async def send_button(self, interaction: discord.Interaction, button: ui.Button):
        draft = await embed_col.find_one({"guild_id": str(interaction.guild.id), "author_id": str(interaction.user.id), "draft_name": self.draft_name})
        if not draft:
            return await interaction.response.send_message("No active draft found.", ephemeral=True)

        target_channel_id = draft.get("channel_id")
        target = interaction.channel
        if target_channel_id:
            ch = interaction.guild.get_channel(int(target_channel_id))
            if ch:
                target = ch
            else:
                return await interaction.response.send_message("The selected channel no longer exists or cannot be accessed.", ephemeral=True)

        if not target.permissions_for(interaction.guild.me).send_messages:
            return await interaction.response.send_message("Missing send permissions in target channel.", ephemeral=True)

        final_embed = await self.cog.build_embed_from_data(interaction.user, interaction.guild, draft)
        allowed = discord.AllowedMentions(everyone=False, roles=False, users=True)
        try:
            await target.send(embed=final_embed, allowed_mentions=allowed)
            await embed_col.delete_one({"_id": draft["_id"]})
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(content=f"Embed draft `{self.draft_name}` successfully sent to {target.mention}!", view=self)
            self.stop()
        except Exception as e:
            await interaction.response.send_message(f"Dispatch error: `{e}`", ephemeral=True)

    @ui.button(label="Discard", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def discard_button(self, interaction: discord.Interaction, button: ui.Button):
        await embed_col.delete_one({"guild_id": str(interaction.guild.id), "author_id": str(interaction.user.id), "draft_name": self.draft_name})
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=f"Embed draft `{self.draft_name}` discarded.", embed=None, view=self)
        self.stop()

class Utility(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.birthday_check.start()

    def cog_unload(self):
        self.birthday_check.cancel()

    async def build_embed_from_data(self, user: discord.Member, guild: discord.Guild, draft: dict) -> discord.Embed:
        title = parse_embed_variables(draft.get("title"), user, guild)
        description = parse_embed_variables(draft.get("description"), user, guild)
        
        try:
            color = int(draft.get("color", "2B2D31"), 16)
        except ValueError:
            color = 0x2B2D31

        embed = discord.Embed(title=title, description=description, color=color)

        if draft.get("thumbnail"):
            parsed_thumb = parse_embed_variables(draft["thumbnail"], user, guild)
            if parsed_thumb.startswith("http://") or parsed_thumb.startswith("https://"):
                embed.set_thumbnail(url=parsed_thumb)

        if draft.get("image"):
            parsed_img = parse_embed_variables(draft["image"], user, guild)
            if parsed_img.startswith("http://") or parsed_img.startswith("https://"):
                embed.set_image(url=parsed_img)

        if draft.get("footer"):
            parsed_footer = parse_embed_variables(draft["footer"], user, guild)
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
                        description=f"Happy Birthday {member.mention}! Wishing you a brilliant day!",
                        color=0x57F287
                    )
                    embed.set_thumbnail(url=member.display_avatar.url)
                    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions(users=True))
            except Exception:
                continue

    @commands.command()
    async def ping(self, ctx):
        lat = round(self.bot.latency * 1000)
        color = 0x57F287 if lat < 100 else (0xFEE75C if lat < 200 else 0xED4245)
        embed = discord.Embed(description=f"System Latency: `{lat}ms`", color=color)
        await ctx.reply(embed=embed)

    @app_commands.command(name="ping", description="Check bot latency")
    async def slash_ping(self, interaction: discord.Interaction):
        lat = round(self.bot.latency * 1000)
        color = 0x57F287 if lat < 100 else (0xFEE75C if lat < 200 else 0xED4245)
        embed = discord.Embed(description=f"System Latency: `{lat}ms`", color=color)
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
            discord.Status.online: "Online",
            discord.Status.idle: "Idle",
            discord.Status.dnd: "Do Not Disturb",
            discord.Status.offline: "Offline",
        }
        status_str = status_emoji.get(member.status, "Offline")

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
    @app_commands.describe(member="Member to inspect")
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

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.command(aliases=["av", "pfp"])
    async def avatar(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        embed = discord.Embed(color=0x2B2D31)
        embed.set_author(name=f"{member.name}'s Avatar", icon_url=member.display_avatar.url)
        embed.set_image(url=member.display_avatar.url)

        png = member.display_avatar.with_format('png').url
        jpg = member.display_avatar.with_format('jpg').url
        webp = member.display_avatar.with_format('webp').url
        embed.description = f"[PNG]({png}) · [JPG]({jpg}) · [WEBP]({webp})"

        await ctx.reply(embed=embed)

    @app_commands.command(name="avatar", description="View a user's avatar")
    @app_commands.describe(member="Member whose avatar to view")
    async def slash_avatar(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        embed = discord.Embed(color=0x2B2D31)
        embed.set_author(name=f"{member.name}'s Avatar", icon_url=member.display_avatar.url)
        embed.set_image(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.command(aliases=["si", "server", "guildinfo"])
    async def serverinfo(self, ctx):
        g = ctx.guild
        bots = sum(1 for m in g.members if m.bot)
        humans = g.member_count - bots
        online = sum(1 for m in g.members if m.status != discord.Status.offline and not m.bot)

        embed = discord.Embed(title=g.name, description=g.description or "No description available.", color=0x2B2D31, timestamp=datetime.datetime.now(timezone.utc))
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
            return await ctx.reply("Reason must not exceed 200 characters.")

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

        embed = discord.Embed(description=f"{ctx.author.mention} is now AFK.\n**Reason:** `{reason}`", color=0x2B2D31)
        await ctx.reply(embed=embed)

    @app_commands.command(name="afk", description="Set your AFK status")
    @app_commands.describe(reason="Reason for being AFK")
    async def slash_afk(self, interaction: discord.Interaction, reason: str = "Away from keyboard"):
        if len(reason) > 200:
            return await interaction.response.send_message("Reason must not exceed 200 characters.", ephemeral=True)

        await afk_col.update_one(
            {"user_id": str(interaction.user.id), "guild_id": str(interaction.guild.id)},
            {"$set": {"reason": reason, "time": datetime.datetime.now(timezone.utc)}},
            upsert=True
        )
        embed = discord.Embed(description=f"AFK set.\n**Reason:** `{reason}`", color=0x2B2D31)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.command()
    @ctx_mod()
    async def sticky(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply("Usage: `,sticky <message>`")
        if len(text) > 1900:
            return await ctx.reply("Sticky message must be under 1900 characters.")

        old = await sticky_col.find_one({"channel_id": str(ctx.channel.id)})
        if old:
            try:
                om = await ctx.channel.fetch_message(int(old["message_id"]))
                await om.delete()
            except Exception:
                pass

        embed = discord.Embed(description=text.replace("\\n", "\n"), color=0x2B2D31)
        embed.set_footer(text="Sticky Message")
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
            return await ctx.reply("No active sticky message found in this channel.")
        try:
            om = await ctx.channel.fetch_message(int(data["message_id"]))
            await om.delete()
        except Exception:
            pass
        await sticky_col.delete_one({"channel_id": str(ctx.channel.id)})
        await ctx.reply("Sticky message removed.", delete_after=4)

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
                return await ctx.reply("Could not retrieve referenced message.")

            if target_lang not in self.LANG_MAP and target_lang not in self.LANG_MAP.values():
                if text_to_translate:
                    text_to_translate = f"{target_lang} {text_to_translate}"
                else:
                    text_to_translate = target_lang
                target_lang = "english"
        else:
            if not text_to_translate:
                return await ctx.reply("Usage: `,translate [language] <text>` or reply to a message.")

        if not text_to_translate or not text_to_translate.strip():
            return await ctx.reply("No content found to translate.")

        if len(text_to_translate) > 1000:
            return await ctx.reply("Input exceeds 1000 character limit.")

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
                        return await ctx.reply("Service unavailable. Try again later.")
                    response_data = await response.json(content_type=None)

            translated_segments = "".join(segment[0] for segment in response_data[0] if segment and segment[0])
            detected_language = response_data[2] if len(response_data) > 2 else "Unknown"

            if not translated_segments.strip():
                return await ctx.reply("Empty translation response.")

            embed = discord.Embed(color=0x2B2D31)
            embed.set_author(name="Google Translate")
            embed.add_field(name=f"Source ({str(detected_language).upper()})", value=f"```text\n{text_to_translate[:900]}```", inline=False)
            embed.add_field(name=f"Output ({resolved_lang_code.upper()})", value=f"```text\n{translated_segments[:900]}```", inline=False)
            await ctx.reply(embed=embed)

        except Exception as error_log:
            await ctx.reply(f"Translation error: `{error_log}`")

    @commands.command(aliases=["ud"])
    async def urban(self, ctx, *, word: str = None):
        if not word:
            return await ctx.reply("Usage: `,urban <term>`")

        url = f"https://api.urbandictionary.com/v0/define?term={urllib.parse.quote(word)}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status != 200:
                        return await ctx.reply("Dictionary API unreachable.")
                    data = await r.json()
        except asyncio.TimeoutError:
            return await ctx.reply("Connection timed out.")
        except Exception as e:
            return await ctx.reply(f"Error: `{e}`")

        if not data.get("list"):
            return await ctx.reply(f"No definitions found for **{word}**.")

        top = data["list"][0]
        definition = top.get("definition", "").replace("[", "").replace("]", "").strip()
        example = top.get("example", "").replace("[", "").replace("]", "").strip()

        embed = discord.Embed(title=top.get("word", word), url=top.get("permalink", ""), color=0x2B2D31)
        embed.add_field(name="Definition", value=definition[:1024] or "None", inline=False)
        if example:
            embed.add_field(name="Example", value=f"*{example[:512]}*", inline=False)
        embed.set_footer(text=f"👍 {top.get('thumbs_up', 0)} · 👎 {top.get('thumbs_down', 0)} · Author: {top.get('author', 'Anonymous')}")
        await ctx.reply(embed=embed)

    @commands.group(aliases=["card"], invoke_without_command=True)
    async def profile(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        doc = await profiles_col.find_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
        bio = doc.get("bio", "Not set. Use `,profile bio <text>`") if doc else "Not set."
        loc = doc.get("location", "Not set") if doc else "Not set"

        lvl_doc = await levels_col.find_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
        level = lvl_doc.get("level", 0) if lvl_doc else 0
        xp = lvl_doc.get("xp", 0) if lvl_doc else 0
        nxt = (level + 1) * 100
        pct = int((xp / nxt) * 10) if nxt else 0
        bar = "█" * pct + "░" * (10 - pct)

        bday_doc = await birthdays_col.find_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
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
        embed.add_field(name="Joined", value=f"<t:{int(member.joined_at.timestamp())}:D>" if member.joined_at else "`Unknown`", inline=True)
        embed.set_footer(text=f"User ID: {member.id}")
        await ctx.reply(embed=embed)

    @profile.command(name="bio")
    async def profile_bio(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply("Usage: `,profile bio <text>`")
        if len(text) > 150:
            return await ctx.reply("Bio limit is 150 characters.")
        await profiles_col.update_one({"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)}, {"$set": {"bio": text}}, upsert=True)
        embed = discord.Embed(description="Bio updated successfully.", color=0x57F287)
        await ctx.reply(embed=embed)

    @profile.command(name="location", aliases=["loc"])
    async def profile_location(self, ctx, *, city: str = None):
        if not city:
            return await ctx.reply("Usage: `,profile location <city>`")
        if len(city) > 40:
            return await ctx.reply("Location limit is 40 characters.")
        await profiles_col.update_one({"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)}, {"$set": {"location": city}}, upsert=True)
        embed = discord.Embed(description=f"Location updated to `{city}`.", color=0x57F287)
        await ctx.reply(embed=embed)

    @profile.command(name="clear")
    async def profile_clear(self, ctx):
        await profiles_col.delete_one({"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)})
        embed = discord.Embed(description="Profile cleared.", color=0x57F287)
        await ctx.reply(embed=embed)

    @commands.group(aliases=["bday"], invoke_without_command=True)
    async def birthday(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        doc = await birthdays_col.find_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
        if not doc:
            return await ctx.reply(f"No birthday record set for **{member.name}**.")
        
        embed = discord.Embed(description=f"**{member.name}'s** birthday is set to **{doc['date']}**", color=0x2B2D31)
        await ctx.reply(embed=embed)

    @birthday.command(name="list")
    async def birthday_list(self, ctx):
        cursor = birthdays_col.find({"guild_id": str(ctx.guild.id)})
        birthdays = []
        async for doc in cursor:
            birthdays.append(doc)

        if not birthdays:
            return await ctx.reply("No birthdays registered in this server.")

        birthdays.sort(key=lambda x: (x.get("month", 0), x.get("day", 0)))
        formatted_list = []
        for doc in birthdays:
            member = ctx.guild.get_member(int(doc["user_id"]))
            name = member.mention if member else f"User ID `{doc['user_id']}`"
            formatted_list.append(f"• {name} — **{doc['date']}**")

        embed = discord.Embed(title=f"Server Birthdays ({len(birthdays)})", description="\n".join(formatted_list[:25]), color=0x2B2D31)
        if len(formatted_list) > 25:
            embed.set_footer(text=f"Showing 25 of {len(formatted_list)} records.")
        await ctx.reply(embed=embed)

    @birthday.command(name="set")
    async def birthday_set(self, ctx, date: str = None):
        if not date:
            return await ctx.reply("Usage: `,birthday set DD/MM`")
        try:
            day, month = map(int, date.strip().split("/"))
            if not (1 <= day <= 31 and 1 <= month <= 12):
                raise ValueError
        except Exception:
            return await ctx.reply("Invalid format. Use DD/MM.")

        await birthdays_col.update_one({"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)}, {"$set": {"date": date, "day": day, "month": month}}, upsert=True)
        embed = discord.Embed(description=f"Birthday recorded as **{date}**.", color=0x57F287)
        await ctx.reply(embed=embed)

    @birthday.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def birthday_add(self, ctx, member: discord.Member = None, date: str = None):
        if not member or not date:
            return await ctx.reply("Usage: `,birthday add @member DD/MM`")
        try:
            day, month = map(int, date.strip().split("/"))
            if not (1 <= day <= 31 and 1 <= month <= 12):
                raise ValueError
        except Exception:
            return await ctx.reply("Invalid format. Use DD/MM.")

        await birthdays_col.update_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)}, {"$set": {"date": date, "day": day, "month": month}}, upsert=True)
        embed = discord.Embed(description=f"Birthday for {member.mention} set to **{date}**.", color=0x57F287)
        await ctx.reply(embed=embed)

    @birthday.command(name="remove")
    async def birthday_remove(self, ctx):
        result = await birthdays_col.delete_one({"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)})
        if result.deleted_count:
            await ctx.reply("Birthday removed.")
        else:
            await ctx.reply("No birthday entry found.")

    @birthday.command(name="channel")
    @commands.has_permissions(manage_guild=True)
    async def birthday_channel(self, ctx, channel: discord.TextChannel = None):
        if channel is None:
            gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
            cid = gs.get("birthday_channel")
            if cid:
                ch = ctx.guild.get_channel(int(cid))
                return await ctx.reply(embed=discord.Embed(description=f"Announcements active in {ch.mention if ch else f'<#{cid}>'}.", color=0x2B2D31))
            return await ctx.reply(embed=discord.Embed(description="No specific channel designated. Defaulting to system channel.", color=0x2B2D31))

        await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$set": {"birthday_channel": str(channel.id)}}, upsert=True)
        embed = discord.Embed(description=f"Birthday notifications directed to {channel.mention}.", color=0x57F287)
        await ctx.reply(embed=embed)

    @birthday.command(name="channelremove", aliases=["removechannel"])
    @commands.has_permissions(manage_guild=True)
    async def birthday_channel_remove(self, ctx):
        await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$unset": {"birthday_channel": ""}}, upsert=True)
        await ctx.reply("Birthday channel preference removed.")

    @app_commands.command(name="birthday", description="View or set your birthday")
    @app_commands.describe(member="Member to check")
    async def slash_birthday(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        doc = await birthdays_col.find_one({"guild_id": str(interaction.guild.id), "user_id": str(member.id)})
        if not doc:
            await interaction.response.send_message(f"No birthday record for **{member.name}**.", ephemeral=True)
        else:
            embed = discord.Embed(description=f"**{member.name}'s** birthday is **{doc['date']}**", color=0x2B2D31)
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.group(invoke_without_command=True)
    @ctx_mod()
    async def mimic(self, ctx, member: discord.Member = None, *, message: str = None):
        if not member or not message:
            return await ctx.reply("Usage: `,mimic @user <message>`")
        if len(message) > 2000:
            return await ctx.reply("Message exceeds 2000 characters.")
        if not ctx.channel.permissions_for(ctx.guild.me).manage_webhooks:
            return await ctx.reply("Missing permissions to manage webhooks.")

        try:
            webhooks = await ctx.channel.webhooks()
            wh = discord.utils.get(webhooks, name="HappyMimic")
            if not wh:
                wh = await ctx.channel.create_webhook(name="HappyMimic")
            await wh.send(content=message, username=member.display_name, avatar_url=member.display_avatar.url, allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True))
            try:
                await ctx.message.delete()
            except Exception:
                pass
        except discord.Forbidden:
            await ctx.reply("Permission denied.")

    @mimic.command(name="custom")
    @ctx_mod()
    async def mimic_custom(self, ctx, name: str = None, avatar_url: str = None, *, message: str = None):
        if not name or not avatar_url or not message:
            return await ctx.reply("Usage: `,mimic custom <name> <avatar_url> <message>`")
        if not (avatar_url.startswith("http://") or avatar_url.startswith("https://")):
            return await ctx.reply("Avatar URL must be a valid image link.")
        if not ctx.channel.permissions_for(ctx.guild.me).manage_webhooks:
            return await ctx.reply("Missing permissions to manage webhooks.")

        try:
            webhooks = await ctx.channel.webhooks()
            wh = discord.utils.get(webhooks, name="HappyCustomWebhook")
            if not wh:
                wh = await ctx.channel.create_webhook(name="HappyCustomWebhook")
            await wh.send(content=message, username=name[:80], avatar_url=avatar_url, allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True))
            try:
                await ctx.message.delete()
            except Exception:
                pass
        except discord.Forbidden:
            await ctx.reply("Permission denied.")
        except Exception as e:
            await ctx.reply(f"Failed to dispatch: `{e}`")

    @commands.command()
    @ctx_mod()
    async def echo(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply("Usage: `,echo [ #channel ] <message>`")

        target_channel = ctx.channel
        content = text

        if ctx.message.channel_mentions:
            mentioned = ctx.message.channel_mentions[0]
            if text.startswith(mentioned.mention) or text.startswith(f"<#{mentioned.id}>"):
                target_channel = mentioned
                content = text.replace(mentioned.mention, "", 1).strip()

        if not content:
            return await ctx.reply("Message content cannot be empty.")

        if not target_channel.permissions_for(ctx.guild.me).send_messages:
            return await ctx.reply("Cannot send messages in that channel.")

        await target_channel.send(content, allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True))
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @commands.group(name="embed", invoke_without_command=True)
    @commands.has_permissions(manage_messages=True)
    async def embed_group_prefix(self, ctx):
        embed = discord.Embed(
            title="Embed Manager",
            color=0x2B2D31,
            description="Use `/embed` slash commands to create, preview, and send custom embeds."
        )
        await ctx.reply(embed=embed)

    embed_group = app_commands.Group(name="embed", description="Interactive Embed Manager")

    @embed_group.command(name="create", description="Create a new embed draft")
    @app_commands.describe(name="Draft name identifier")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def emb_create(self, interaction: discord.Interaction, name: str = "default"):
        await embed_col.update_one(
            {"guild_id": str(interaction.guild.id), "author_id": str(interaction.user.id), "draft_name": name},
            {"$set": {
                "title": None,
                "description": "Welcome {user} to {server}!",
                "color": "2B2D31",
                "thumbnail": "{server_icon}",
                "image": None,
                "footer": None,
                "channel_id": None
            }},
            upsert=True
        )
        await interaction.response.send_message(f"Draft `{name}` created. Run `/embed preview name:{name}` to view and edit.", ephemeral=True)

    @embed_group.command(name="title", description="Set or change the title of a draft")
    @app_commands.describe(name="Draft name", title="Embed title text")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def emb_title(self, interaction: discord.Interaction, name: str = "default", *, title: str = None):
        if title and len(title) > 256:
            return await interaction.response.send_message("Title cannot exceed 256 characters.", ephemeral=True)
        await embed_col.update_one(
            {"guild_id": str(interaction.guild.id), "author_id": str(interaction.user.id), "draft_name": name},
            {"$set": {"title": title}},
            upsert=True
        )
        await interaction.response.send_message(f"Title updated for draft `{name}`.", ephemeral=True)

    @embed_group.command(name="desc", description="Set or change the description of a draft")
    @app_commands.describe(name="Draft name", description="Embed description text")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def emb_desc(self, interaction: discord.Interaction, name: str = "default", *, description: str = None):
        if description and len(description) > 4000:
            return await interaction.response.send_message("Description cannot exceed 4000 characters.", ephemeral=True)
        await embed_col.update_one(
            {"guild_id": str(interaction.guild.id), "author_id": str(interaction.user.id), "draft_name": name},
            {"$set": {"description": description}},
            upsert=True
        )
        await interaction.response.send_message(f"Description updated for draft `{name}`.", ephemeral=True)

    @embed_group.command(name="color", description="Set or change the color of a draft")
    @app_commands.describe(name="Draft name", color="Hex color code")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def emb_color(self, interaction: discord.Interaction, name: str = "default", color: str = "2B2D31"):
        clean = color.replace("#", "").strip()
        try:
            int(clean, 16)
        except ValueError:
            return await interaction.response.send_message("Invalid hex color code.", ephemeral=True)
        await embed_col.update_one(
            {"guild_id": str(interaction.guild.id), "author_id": str(interaction.user.id), "draft_name": name},
            {"$set": {"color": clean}},
            upsert=True
        )
        await interaction.response.send_message(f"Color updated for draft `{name}`.", ephemeral=True)

    @embed_group.command(name="image", description="Set or change the image URL of a draft")
    @app_commands.describe(name="Draft name", url="Image URL")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def emb_image(self, interaction: discord.Interaction, name: str = "default", url: str = None):
        if url and not (url.startswith("http://") or url.startswith("https://")):
            return await interaction.response.send_message("Invalid image URL format.", ephemeral=True)
        await embed_col.update_one(
            {"guild_id": str(interaction.guild.id), "author_id": str(interaction.user.id), "draft_name": name},
            {"$set": {"image": url}},
            upsert=True
        )
        await interaction.response.send_message(f"Image updated for draft `{name}`.", ephemeral=True)

    @embed_group.command(name="footer", description="Set or change the footer of a draft")
    @app_commands.describe(name="Draft name", text="Footer text")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def emb_footer(self, interaction: discord.Interaction, name: str = "default", *, text: str = None):
        if text and len(text) > 2048:
            return await interaction.response.send_message("Footer cannot exceed 2048 characters.", ephemeral=True)
        await embed_col.update_one(
            {"guild_id": str(interaction.guild.id), "author_id": str(interaction.user.id), "draft_name": name},
            {"$set": {"footer": text}},
            upsert=True
        )
        await interaction.response.send_message(f"Footer updated for draft `{name}`.", ephemeral=True)

    @embed_group.command(name="thumbnail", description="Set or change the thumbnail of a draft")
    @app_commands.describe(name="Draft name", url="Thumbnail URL")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def emb_thumbnail(self, interaction: discord.Interaction, name: str = "default", url: str = None):
        if url and not (url.startswith("http://") or url.startswith("https://") or url.startswith("{")):
            return await interaction.response.send_message("Invalid thumbnail URL format.", ephemeral=True)
        await embed_col.update_one(
            {"guild_id": str(interaction.guild.id), "author_id": str(interaction.user.id), "draft_name": name},
            {"$set": {"thumbnail": url}},
            upsert=True
        )
        await interaction.response.send_message(f"Thumbnail updated for draft `{name}`.", ephemeral=True)

    @embed_group.command(name="preview", description="Preview and edit your embed draft")
    @app_commands.describe(name="Draft name", channel="Target channel for sending")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def emb_preview(self, interaction: discord.Interaction, name: str = "default", channel: discord.TextChannel = None):
        if channel:
            if not channel.permissions_for(interaction.guild.me).send_messages:
                return await interaction.response.send_message("The selected channel is invalid or the bot lacks permission to send messages there.", ephemeral=True)
            await embed_col.update_one(
                {"guild_id": str(interaction.guild.id), "author_id": str(interaction.user.id), "draft_name": name},
                {"$set": {"channel_id": str(channel.id)}},
                upsert=True
            )

        draft = await embed_col.find_one({"guild_id": str(interaction.guild.id), "author_id": str(interaction.user.id), "draft_name": name})
        if not draft:
            return await interaction.response.send_message(f"No draft found with name `{name}`.", ephemeral=True)

        try:
            preview_embed = await self.build_embed_from_data(interaction.user, interaction.guild, draft)
            view = EmbedPreviewView(self, interaction.user.id, interaction.guild.id, name)
            ch_mention = f" (Target: <#{draft.get('channel_id')}>)" if draft.get('channel_id') else ""
            await interaction.response.send_message(content=f"Draft: `{name}`{ch_mention}", embed=preview_embed, view=view, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Preview failed: `{e}`", ephemeral=True)

    @embed_group.command(name="send", description="Send your active embed draft to a channel")
    @app_commands.describe(channel="Target channel", name="Draft name")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def emb_send(self, interaction: discord.Interaction, channel: discord.TextChannel = None, name: str = "default"):
        target = channel or interaction.channel
        if channel:
            if not isinstance(channel, discord.TextChannel) or not channel.permissions_for(interaction.guild.me).send_messages:
                return await interaction.response.send_message("The selected channel is invalid or the bot lacks permission to send messages there.", ephemeral=True)

        draft = await embed_col.find_one({"guild_id": str(interaction.guild.id), "author_id": str(interaction.user.id), "draft_name": name})
        if not draft:
            return await interaction.response.send_message(f"No draft found with name `{name}`.", ephemeral=True)

        if not target.permissions_for(interaction.guild.me).send_messages:
            return await interaction.response.send_message("Missing send permissions in target channel.", ephemeral=True)

        try:
            final_embed = await self.build_embed_from_data(interaction.user, interaction.guild, draft)
            allowed = discord.AllowedMentions(everyone=False, roles=False, users=True)
            await target.send(embed=final_embed, allowed_mentions=allowed)
            await embed_col.delete_one({"_id": draft["_id"]})
            await interaction.response.send_message(f"Embed `{name}` successfully sent to {target.mention}.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Dispatch error: `{e}`", ephemeral=True)

    @embed_group.command(name="discard", description="Discard your embed draft")
    @app_commands.describe(name="Draft name")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def emb_discard(self, interaction: discord.Interaction, name: str = "default"):
        result = await embed_col.delete_one({"guild_id": str(interaction.guild.id), "author_id": str(interaction.user.id), "draft_name": name})
        if result.deleted_count:
            await interaction.response.send_message(f"Draft `{name}` deleted successfully.", ephemeral=True)
        else:
            await interaction.response.send_message(f"No draft found with name `{name}`.", ephemeral=True)

    @embed_group.command(name="import", description="Import an embed from JSON data")
    @app_commands.describe(name="Draft name", json_data="JSON string representation")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def emb_import(self, interaction: discord.Interaction, name: str = "default", *, json_data: str):
        try:
            data = json.loads(json_data)
            if not isinstance(data, dict):
                raise ValueError
        except Exception:
            return await interaction.response.send_message("Invalid JSON format provided.", ephemeral=True)

        title = data.get("title")
        desc = data.get("description")
        color = str(data.get("color", "2B2D31")).replace("#", "")
        thumb = data.get("thumbnail")
        image = data.get("image")
        footer = data.get("footer")

        if title and len(title) > 256:
            return await interaction.response.send_message("Title exceeds 256 characters.", ephemeral=True)
        if desc and len(desc) > 4000:
            return await interaction.response.send_message("Description exceeds 4000 characters.", ephemeral=True)

        sanitized = {
            "title": title,
            "description": desc,
            "color": color,
            "thumbnail": thumb,
            "image": image,
            "footer": footer,
            "channel_id": None
        }

        await embed_col.update_one(
            {"guild_id": str(interaction.guild.id), "author_id": str(interaction.user.id), "draft_name": name},
            {"$set": sanitized},
            upsert=True
        )
        await interaction.response.send_message(f"Embed successfully imported into draft `{name}`.", ephemeral=True)

    ar_group = app_commands.Group(name="autoresponder", description="Manage autoresponders")

    @ar_group.command(name="add", description="Add an autoresponder")
    @app_commands.describe(trigger="Trigger phrase", response_type="text or embed", content="Plain text or draft name")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ar_add(self, interaction: discord.Interaction, trigger: str, response_type: str, *, content: str):
        rt = response_type.lower().strip()
        if rt not in ["text", "embed"]:
            return await interaction.response.send_message("Response type must be either 'text' or 'embed'.", ephemeral=True)

        trig_clean = trigger.strip().lower()
        if len(trig_clean) > 100:
            return await interaction.response.send_message("Trigger phrase is too long.", ephemeral=True)

        stored_content = content
        if rt == "embed":
            draft = await embed_col.find_one({"guild_id": str(interaction.guild.id), "author_id": str(interaction.user.id), "draft_name": content.strip()})
            if not draft:
                return await interaction.response.send_message(f"Draft `{content}` not found to bind as embed response.", ephemeral=True)
            stored_content = draft

        existing = await autoresponder_col.find_one({"guild_id": str(interaction.guild.id), "trigger": trig_clean})
        if existing:
            return await interaction.response.send_message("An autoresponder with this trigger already exists in this server.", ephemeral=True)

        await autoresponder_col.update_one(
            {"guild_id": str(interaction.guild.id), "trigger": trig_clean},
            {"$set": {"response_type": rt, "content": stored_content, "creator_id": str(interaction.user.id)}},
            upsert=True
        )
        await interaction.response.send_message(f"Autoresponder added for trigger `{trig_clean}`.", ephemeral=True)

    @ar_group.command(name="remove", description="Remove an autoresponder")
    @app_commands.describe(trigger="Trigger phrase to remove")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ar_remove(self, interaction: discord.Interaction, trigger: str):
        res = await autoresponder_col.delete_one({"guild_id": str(interaction.guild.id), "trigger": trigger.strip().lower()})
        if res.deleted_count:
            await interaction.response.send_message(f"Autoresponder for `{trigger}` removed.", ephemeral=True)
        else:
            await interaction.response.send_message("Autoresponder trigger not found.", ephemeral=True)

    @ar_group.command(name="list", description="List all server autoresponders")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ar_list(self, interaction: discord.Interaction):
        cursor = autoresponder_col.find({"guild_id": str(interaction.guild.id)})
        items = []
        async for doc in cursor:
            items.append(f"• `{doc['trigger']}` ({doc['response_type']})")

        if not items:
            return await interaction.response.send_message("No autoresponders configured in this server.", ephemeral=True)

        embed = discord.Embed(title=f"Server Autoresponders ({len(items)})", description="\n".join(items[:25]), color=0x2B2D31)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener("on_message")
    async def on_autoresponder_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        content_lower = message.content.strip().lower()
        if not content_lower:
            return

        cursor = autoresponder_col.find({"guild_id": str(message.guild.id)})
        async for doc in cursor:
            trigger = doc["trigger"]
            if content_lower == trigger or content_lower.startswith(trigger + " ") or content_lower.endswith(" " + trigger):
                rt = doc["response_type"]
                resp_content = doc["content"]
                allowed = discord.AllowedMentions(everyone=False, roles=False, users=True)

                if rt == "text":
                    await message.channel.send(resp_content, allowed_mentions=allowed)
                elif rt == "embed" and isinstance(resp_content, dict):
                    member = message.guild.get_member(message.author.id) or message.author
                    built_embed = await self.build_embed_from_data(member, message.guild, resp_content)
                    await message.channel.send(embed=built_embed, allowed_mentions=allowed)
                break

    mimic_group = app_commands.Group(name="mimic", description="Mimic commands")

    @mimic_group.command(name="user", description="Mimic a member")
    @app_commands.describe(member="Member to mimic", message="Message to send")
    @app_commands.checks.has_permissions(manage_webhooks=True)
    async def mimic_user(self, interaction: discord.Interaction, member: discord.Member, message: str):
        if len(message) > 2000:
            return await interaction.response.send_message("Message exceeds 2000 characters.", ephemeral=True)
        try:
            webhooks = await interaction.channel.webhooks()
            wh = discord.utils.get(webhooks, name="HappyMimic")
            if not wh:
                wh = await interaction.channel.create_webhook(name="HappyMimic")
            await wh.send(content=message, username=member.display_name, avatar_url=member.display_avatar.url, allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True))
            await interaction.response.send_message("Message sent!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: `{e}`", ephemeral=True)

    @app_commands.command(name="echo", description="Echo a message")
    @app_commands.describe(message="Message content", channel="Target channel")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_echo(self, interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
        target = channel or interaction.channel
        if not target.permissions_for(interaction.guild.me).send_messages:
            return await interaction.response.send_message("Cannot send messages in that channel.", ephemeral=True)
        await target.send(message, allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True))
        await interaction.response.send_message("Message echoed successfully!", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Utility(bot))