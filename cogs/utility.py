import asyncio
import datetime
import io
import json
import logging
import re
import time
from datetime import timezone
from typing import Literal, Optional

import aiohttp
import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from utils.db import (
    afk_col, sticky_col, profiles_col, embed_col,
    birthdays_col, settings_col, levels_col, autoresponder_col
)
from utils.helpers import ctx_mod
from utils import embeds as style

log = logging.getLogger(__name__)

DRAFT_LIMIT = 10
RULE_LIMIT = 50
RULE_COOLDOWN = 4
RULE_CACHE_SECONDS = 30
LANGUAGE_PATTERN = re.compile(r"^[a-z]{2,3}(-[a-z]{2,4})?$")
DATE_PATTERN = re.compile(r"^(\d{1,2})[/.-](\d{1,2})$")
RESET_WORDS = {"clear", "none", "remove", "reset"}


def with_permissions(**needed):
    def decorator(func):
        func = commands.has_permissions(**needed)(func)
        return commands.guild_only()(func)
    return decorator


def parse_birthday(text):
    found = DATE_PATTERN.match((text or "").strip())
    if not found:
        return None
    day, month = int(found.group(1)), int(found.group(2))
    try:
        datetime.date(2000, month, day)
    except ValueError:
        return None
    return day, month


def one_line(text, limit):
    return discord.utils.escape_mentions(" ".join(str(text).split()))[:limit]


class Drafts:
    @staticmethod
    def key(guild_id, author_id, name):
        return {"guild_id": str(guild_id), "author_id": str(author_id), "draft_name": name}

    @staticmethod
    def owner_filter(guild_id, author_id):
        return {"guild_id": str(guild_id), "author_id": str(author_id), "draft_name": {"$exists": True}}

    @classmethod
    async def get(cls, guild_id, author_id, name):
        return await embed_col.find_one(cls.key(guild_id, author_id, name))

    @classmethod
    async def save(cls, guild_id, author_id, name, data):
        await embed_col.update_one(cls.key(guild_id, author_id, name), {"$set": data}, upsert=True)

    @classmethod
    async def delete(cls, guild_id, author_id, name):
        result = await embed_col.delete_one(cls.key(guild_id, author_id, name))
        return bool(result.deleted_count)

    @classmethod
    async def count(cls, guild_id, author_id):
        return await embed_col.count_documents(cls.owner_filter(guild_id, author_id))

    @classmethod
    async def names(cls, guild_id, author_id):
        found = []
        async for doc in embed_col.find(cls.owner_filter(guild_id, author_id)):
            found.append(doc["draft_name"])
        return sorted(found)

    @staticmethod
    async def set_active(guild_id, author_id, name):
        await embed_col.update_one(
            {"guild_id": str(guild_id), "author_id": str(author_id), "state": "active"},
            {"$set": {"name": name}},
            upsert=True,
        )

    @staticmethod
    async def active(guild_id, author_id):
        doc = await embed_col.find_one({"guild_id": str(guild_id), "author_id": str(author_id), "state": "active"})
        return doc.get("name") if doc else None


class TextModal(ui.Modal, title="Edit text"):
    def __init__(self, studio, draft):
        super().__init__(timeout=300)
        self.studio = studio
        self.title_input = ui.TextInput(label="Title", required=False, max_length=256, default=draft.get("title"))
        self.description_input = ui.TextInput(
            label="Message", style=discord.TextStyle.paragraph, required=False, max_length=4000, default=draft.get("description")
        )
        self.footer_input = ui.TextInput(label="Footer", required=False, max_length=2048, default=draft.get("footer"))
        self.color_input = ui.TextInput(
            label="Color (example: #5865F2)", required=False, max_length=7, default=f"#{draft.get('color') or '2B2D31'}"
        )
        for item in (self.title_input, self.description_input, self.footer_input, self.color_input):
            self.add_item(item)

    async def on_submit(self, interaction):
        await self.studio.apply(
            interaction,
            {
                "title": self.title_input.value,
                "description": self.description_input.value,
                "footer": self.footer_input.value,
                "color": self.color_input.value,
            },
        )


class ImageModal(ui.Modal, title="Edit pictures"):
    def __init__(self, studio, draft):
        super().__init__(timeout=300)
        self.studio = studio
        self.thumbnail_input = ui.TextInput(
            label="Small picture link (top right)", required=False, max_length=500, default=draft.get("thumbnail")
        )
        self.image_input = ui.TextInput(
            label="Large picture link (bottom)", required=False, max_length=500, default=draft.get("image")
        )
        self.add_item(self.thumbnail_input)
        self.add_item(self.image_input)

    async def on_submit(self, interaction):
        await self.studio.apply(
            interaction,
            {"thumbnail": self.thumbnail_input.value, "image": self.image_input.value},
        )


class EmbedStudio(ui.View):
    def __init__(self, cog, owner_id, guild_id, name):
        super().__init__(timeout=900)
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.name = name
        self.message = None

    async def allowed(self, interaction):
        if interaction.guild is None or interaction.guild.id != self.guild_id or interaction.user.id != self.owner_id:
            await interaction.response.send_message(embed=style.failure("Only the person who opened this can use it."), ephemeral=True)
            return False
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message(embed=style.failure("You need the Manage Messages permission."), ephemeral=True)
            return False
        return True

    async def interaction_check(self, interaction):
        return await self.allowed(interaction)

    def header(self, draft):
        channel_id = draft.get("channel_id")
        target = f"<#{channel_id}>" if channel_id else "this channel"
        return f"**Embed Studio**  ·  draft `{self.name}`  ·  sending to {target}"

    async def refresh(self, interaction):
        draft = await Drafts.get(self.guild_id, self.owner_id, self.name)
        if not draft:
            return await interaction.response.send_message(embed=style.failure("This draft no longer exists."), ephemeral=True)
        embed = style.build_embed(draft, interaction.user, interaction.guild, placeholder=True)
        await interaction.response.edit_message(content=self.header(draft), embed=embed, view=self)

    async def apply(self, interaction, changes):
        if not await self.allowed(interaction):
            return
        draft = await Drafts.get(self.guild_id, self.owner_id, self.name)
        if not draft:
            return await interaction.response.send_message(embed=style.failure("This draft no longer exists."), ephemeral=True)
        clean, error = style.clean_draft({**draft, **changes})
        if error:
            return await interaction.response.send_message(embed=style.failure(error), ephemeral=True)
        await Drafts.save(self.guild_id, self.owner_id, self.name, clean)
        await self.refresh(interaction)

    @ui.select(
        cls=ui.ChannelSelect,
        channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        placeholder="Choose where to send this embed",
        min_values=1,
        max_values=1,
        row=0,
    )
    async def pick_channel(self, interaction, select):
        channel = interaction.guild.get_channel(select.values[0].id)
        if channel is None:
            return await interaction.response.send_message(embed=style.failure("I cannot find that channel."), ephemeral=True)
        await Drafts.save(self.guild_id, self.owner_id, self.name, {"channel_id": str(channel.id)})
        await self.refresh(interaction)

    @ui.button(label="Edit text", style=discord.ButtonStyle.primary, row=1)
    async def edit_text(self, interaction, button):
        draft = await Drafts.get(self.guild_id, self.owner_id, self.name) or {}
        await interaction.response.send_modal(TextModal(self, draft))

    @ui.button(label="Edit pictures", style=discord.ButtonStyle.secondary, row=1)
    async def edit_images(self, interaction, button):
        draft = await Drafts.get(self.guild_id, self.owner_id, self.name) or {}
        await interaction.response.send_modal(ImageModal(self, draft))

    @ui.button(label="Send", style=discord.ButtonStyle.success, row=1)
    async def send_now(self, interaction, button):
        draft = await Drafts.get(self.guild_id, self.owner_id, self.name)
        if not draft:
            return await interaction.response.send_message(embed=style.failure("This draft no longer exists."), ephemeral=True)
        channel = interaction.channel
        if draft.get("channel_id"):
            channel = interaction.guild.get_channel(int(draft["channel_id"]))
        error = await self.cog.deliver(interaction.user, interaction.guild, draft, channel)
        if error:
            return await interaction.response.send_message(embed=style.failure(error), ephemeral=True)
        await interaction.response.send_message(
            embed=style.success(f"Your embed was sent to {channel.mention}. The draft is saved, so you can send it again."),
            ephemeral=True,
        )

    @ui.button(label="Discard", style=discord.ButtonStyle.danger, row=1)
    async def discard(self, interaction, button):
        await Drafts.delete(self.guild_id, self.owner_id, self.name)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=f"Draft `{self.name}` was discarded.", embed=None, view=self)
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Utility(commands.Cog):
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

    def __init__(self, bot):
        self.bot = bot
        self.rule_cache = {}
        self.rule_seen = {}

    async def cog_load(self):
        self.birthday_check.start()

    async def cog_unload(self):
        self.birthday_check.cancel()

    async def cog_app_command_error(self, interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            message = "You do not have permission to use this command."
        elif isinstance(error, app_commands.NoPrivateMessage):
            message = "This command only works inside a server."
        elif isinstance(error, app_commands.CommandOnCooldown):
            message = f"Please wait {error.retry_after:.0f} seconds and try again."
        else:
            log.error("Command failed", exc_info=error)
            message = "Something went wrong. Please try again."
        embed = style.failure(message)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def deliver(self, member, guild, draft, channel):
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return "I cannot find a channel to send to. Please choose one."
        yours = channel.permissions_for(member)
        mine = channel.permissions_for(guild.me)
        if not (yours.view_channel and yours.send_messages and yours.embed_links):
            return f"You cannot send embeds in {channel.mention}."
        if not (mine.view_channel and mine.send_messages and mine.embed_links):
            return f"I cannot send embeds in {channel.mention}. Please check my permissions."
        clean, error = style.clean_draft(draft)
        if error:
            return error
        if not style.has_content(clean):
            return "The embed is empty. Add some text or a picture first."
        try:
            await channel.send(embed=style.build_embed(clean, member, guild), allowed_mentions=style.NO_PINGS)
        except discord.HTTPException:
            return "Discord did not accept this embed. Please check your text and links."
        return None

    @tasks.loop(time=datetime.time(hour=0, minute=1, tzinfo=timezone.utc))
    async def birthday_check(self):
        now = datetime.datetime.now(timezone.utc)
        async for doc in birthdays_col.find({"day": now.day, "month": now.month}):
            try:
                guild = self.bot.get_guild(int(doc.get("guild_id")))
                if not guild:
                    continue
                member = guild.get_member(int(doc.get("user_id")))
                if not member or member.bot:
                    continue
                settings = await settings_col.find_one({"_id": str(guild.id)}) or {}
                channel_id = settings.get("birthday_channel")
                channel = guild.get_channel(int(channel_id)) if channel_id else guild.system_channel
                if not isinstance(channel, discord.TextChannel):
                    continue
                perms = channel.permissions_for(guild.me)
                if not (perms.send_messages and perms.embed_links):
                    continue
                embed = style.card("Happy Birthday", f"Everyone wish {member.mention} a wonderful day.", 0xF0B232)
                embed.set_thumbnail(url=member.display_avatar.url)
                await channel.send(embed=embed, allowed_mentions=style.NO_PINGS)
            except (discord.HTTPException, TypeError, ValueError):
                continue

    @birthday_check.before_loop
    async def before_birthday_check(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_command(name="ping", description="Check how fast the bot is")
    async def ping(self, ctx):
        latency = round(self.bot.latency * 1000)
        color = style.SUCCESS if latency < 100 else (0xFEE75C if latency < 200 else style.FAILURE)
        await style.respond(ctx, embed=style.card("Pong", f"Response time: `{latency} ms`", color))

    @commands.hybrid_command(name="userinfo", aliases=["whois", "ui"], description="See details about a member")
    @app_commands.describe(member="The member to look up")
    @commands.guild_only()
    async def userinfo(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        created = member.created_at.replace(tzinfo=timezone.utc)
        age_days = (datetime.datetime.now(timezone.utc) - created).days
        roles = [role.mention for role in reversed(member.roles) if role != ctx.guild.default_role]
        shown = " ".join(roles[:10]) + (f" `+{len(roles) - 10} more`" if len(roles) > 10 else "") if roles else "None"
        statuses = {
            discord.Status.online: "Online",
            discord.Status.idle: "Idle",
            discord.Status.dnd: "Do Not Disturb",
        }
        color = member.color if member.color != discord.Color.default() else style.NEUTRAL

        embed = discord.Embed(color=color)
        embed.set_author(name=member.name, icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
        embed.add_field(name="Status", value=statuses.get(member.status, "Offline"), inline=True)
        embed.add_field(name="Bot", value="Yes" if member.bot else "No", inline=True)
        embed.add_field(name="Joined server", value=f"<t:{int(member.joined_at.timestamp())}:D>" if member.joined_at else "Unknown", inline=True)
        embed.add_field(name="Account created", value=f"<t:{int(created.timestamp())}:D>\n{age_days} days ago", inline=True)
        embed.add_field(name="Top role", value=member.top_role.mention, inline=True)
        if member.premium_since:
            embed.add_field(name="Boosting since", value=f"<t:{int(member.premium_since.timestamp())}:D>", inline=True)
        embed.add_field(name=f"Roles ({len(roles)})", value=shown, inline=False)
        embed.set_footer(text=f"Asked by {ctx.author.name}")
        await style.respond(ctx, private=False, embed=embed)

    @commands.hybrid_command(name="avatar", aliases=["av", "pfp"], description="See a member's picture")
    @app_commands.describe(member="The member to look up")
    async def avatar(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        asset = member.display_avatar
        embed = discord.Embed(
            description=f"[PNG]({asset.with_format('png').url})  ·  [JPG]({asset.with_format('jpg').url})  ·  [WEBP]({asset.with_format('webp').url})",
            color=style.NEUTRAL,
        )
        embed.set_author(name=f"{member.name}'s picture", icon_url=asset.url)
        embed.set_image(url=asset.url)
        await style.respond(ctx, private=False, embed=embed)

    @commands.hybrid_command(name="serverinfo", aliases=["si", "server", "guildinfo"], description="See details about this server")
    @commands.guild_only()
    async def serverinfo(self, ctx):
        guild = ctx.guild
        bots = sum(1 for m in guild.members if m.bot)
        humans = max(guild.member_count - bots, 0)
        online = sum(1 for m in guild.members if m.status != discord.Status.offline and not m.bot)

        embed = discord.Embed(
            title=guild.name,
            description=guild.description or "This server has no description.",
            color=style.NEUTRAL,
            timestamp=datetime.datetime.now(timezone.utc),
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if guild.banner:
            embed.set_image(url=guild.banner.url)
        embed.add_field(name="Owner", value=guild.owner.mention if guild.owner else "Unknown", inline=True)
        embed.add_field(name="Members", value=f"{humans} people\n{bots} bots", inline=True)
        embed.add_field(name="Online", value=str(online), inline=True)
        embed.add_field(name="Channels", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="Emojis", value=str(len(guild.emojis)), inline=True)
        embed.add_field(name="Boost level", value=f"Level {guild.premium_tier}\n{guild.premium_subscription_count} boosts", inline=True)
        embed.add_field(name="Created", value=f"<t:{int(guild.created_at.timestamp())}:D>", inline=True)
        embed.set_footer(text=f"Server ID: {guild.id}")
        await style.respond(ctx, private=False, embed=embed)

    @commands.hybrid_command(name="membercount", aliases=["mc"], description="See how many members are here")
    @commands.guild_only()
    async def membercount(self, ctx):
        guild = ctx.guild
        bots = sum(1 for m in guild.members if m.bot)
        humans = max(guild.member_count - bots, 0)
        online = sum(1 for m in guild.members if m.status != discord.Status.offline and not m.bot)

        embed = discord.Embed(color=style.NEUTRAL)
        embed.set_author(name=f"{guild.name} members", icon_url=guild.icon.url if guild.icon else None)
        embed.add_field(name="Total", value=str(guild.member_count), inline=True)
        embed.add_field(name="People", value=str(humans), inline=True)
        embed.add_field(name="Bots", value=str(bots), inline=True)
        embed.add_field(name="People online", value=str(online), inline=True)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        await style.respond(ctx, private=False, embed=embed)

    @commands.hybrid_command(name="afk", description="Let others know you are away")
    @app_commands.describe(reason="Why you are away")
    @commands.guild_only()
    async def afk(self, ctx, *, reason: str = "Away from keyboard"):
        reason = one_line(reason, 200) or "Away from keyboard"
        await afk_col.update_one(
            {"user_id": str(ctx.author.id), "guild_id": str(ctx.guild.id)},
            {"$set": {"reason": reason, "time": datetime.datetime.now(timezone.utc)}},
            upsert=True,
        )
        try:
            me = ctx.guild.me
            if (
                me.guild_permissions.manage_nicknames
                and not ctx.author.display_name.startswith("[AFK]")
                and ctx.author != ctx.guild.owner
                and me.top_role > ctx.author.top_role
            ):
                await ctx.author.edit(nick=f"[AFK] {ctx.author.display_name[:25]}")
        except discord.HTTPException:
            pass
        await style.respond(ctx, private=False, embed=style.card("You are now AFK", f"{ctx.author.mention}\n**Reason:** {reason}"), allowed_mentions=style.NO_PINGS)

    @commands.command()
    @ctx_mod()
    @commands.guild_only()
    async def sticky(self, ctx, *, text: str = None):
        if not text:
            return await style.respond(ctx, embed=style.usage(f"{ctx.clean_prefix}sticky <message>"))
        if len(text) > 1900:
            return await style.respond(ctx, embed=style.failure("The sticky message must be under 1900 characters."))
        perms = ctx.channel.permissions_for(ctx.guild.me)
        if not (perms.send_messages and perms.embed_links):
            return await style.respond(ctx, embed=style.failure("I cannot send embeds in this channel."))

        old = await sticky_col.find_one({"channel_id": str(ctx.channel.id)})
        if old:
            try:
                previous = await ctx.channel.fetch_message(int(old["message_id"]))
                await previous.delete()
            except discord.HTTPException:
                pass

        embed = style.card(None, text.replace("\\n", "\n"), footer="Sticky message")
        message = await ctx.channel.send(embed=embed, allowed_mentions=style.NO_PINGS)
        await sticky_col.update_one(
            {"channel_id": str(ctx.channel.id)},
            {"$set": {"message_id": str(message.id), "content": text}},
            upsert=True,
        )
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @commands.command()
    @ctx_mod()
    @commands.guild_only()
    async def unsticky(self, ctx):
        data = await sticky_col.find_one({"channel_id": str(ctx.channel.id)})
        if not data:
            return await style.respond(ctx, embed=style.failure("There is no sticky message in this channel."))
        try:
            previous = await ctx.channel.fetch_message(int(data["message_id"]))
            await previous.delete()
        except discord.HTTPException:
            pass
        await sticky_col.delete_one({"channel_id": str(ctx.channel.id)})
        await ctx.reply(embed=style.success("The sticky message was removed."), mention_author=False, delete_after=5)

    def _language(self, word):
        word = word.lower()
        if word in self.LANG_MAP:
            return self.LANG_MAP[word]
        if word in self.LANG_MAP.values():
            return word
        return None

    @commands.command(aliases=["tr"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def translate(self, ctx, *, query: str = None):
        parts = (query or "").split(None, 1)
        code = "en"
        text = query
        if parts:
            found = self._language(parts[0])
            if found:
                code = found
                text = parts[1] if len(parts) > 1 else None

        if not text and ctx.message.reference:
            reference = ctx.message.reference.resolved
            if not isinstance(reference, discord.Message):
                try:
                    reference = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                except discord.HTTPException:
                    return await style.respond(ctx, embed=style.failure("I could not read the message you replied to."))
            text = reference.content
            if not text and reference.embeds:
                text = reference.embeds[0].description or reference.embeds[0].title

        if not text or not text.strip():
            return await style.respond(ctx, embed=style.usage(f"{ctx.clean_prefix}translate [language] <text>", "You can also reply to a message."))
        text = text.strip()
        if len(text) > 1000:
            return await style.respond(ctx, embed=style.failure("That text is too long. Please keep it under 1000 characters."))
        if not LANGUAGE_PATTERN.match(code):
            return await style.respond(ctx, embed=style.failure("I do not know that language."))

        params = {"client": "gtx", "sl": "auto", "tl": code, "dt": "t", "q": text}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
                async with session.get("https://translate.googleapis.com/translate_a/single", params=params, headers=headers) as response:
                    if response.status != 200:
                        return await style.respond(ctx, embed=style.failure("The translation service is busy. Please try again later."))
                    data = await response.json(content_type=None)
            result = "".join(part[0] for part in data[0] if part and part[0])
            detected = str(data[2]).upper() if len(data) > 2 else "?"
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, IndexError, TypeError):
            return await style.respond(ctx, embed=style.failure("I could not translate that. Please try again later."))

        if not result.strip():
            return await style.respond(ctx, embed=style.failure("The translation came back empty."))

        def block(value):
            return "```\n" + value.replace("```", "'''")[:900] + "\n```"

        embed = style.card("Translation")
        embed.add_field(name=f"Original ({detected})", value=block(text), inline=False)
        embed.add_field(name=f"Translated ({code.upper()})", value=block(result), inline=False)
        await style.respond(ctx, private=False, embed=embed)

    @commands.command(aliases=["ud"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def urban(self, ctx, *, word: str = None):
        if not word:
            return await style.respond(ctx, embed=style.usage(f"{ctx.clean_prefix}urban <word>"))
        word = word.strip()[:100]
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
                async with session.get("https://api.urbandictionary.com/v0/define", params={"term": word}) as response:
                    if response.status != 200:
                        return await style.respond(ctx, embed=style.failure("The dictionary is not answering. Please try again later."))
                    data = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return await style.respond(ctx, embed=style.failure("I could not reach the dictionary. Please try again later."))

        entries = data.get("list") if isinstance(data, dict) else None
        if not entries:
            return await style.respond(ctx, embed=style.failure(f"I found nothing for **{discord.utils.escape_markdown(word)}**."))

        top = entries[0]
        definition = str(top.get("definition", "")).replace("[", "").replace("]", "").strip()
        example = str(top.get("example", "")).replace("[", "").replace("]", "").strip()
        link = str(top.get("permalink", ""))
        if not link.startswith(("https://www.urbandictionary.com/", "https://urbandictionary.com/")):
            link = None

        embed = discord.Embed(title=style.trim(str(top.get("word", word)), 256), url=link, color=style.NEUTRAL)
        embed.add_field(name="Meaning", value=style.trim(definition, 1024) or "None", inline=False)
        if example:
            embed.add_field(name="Example", value=style.trim(f"*{example}*", 1024), inline=False)
        embed.set_footer(text=f"👍 {top.get('thumbs_up', 0)}  ·  👎 {top.get('thumbs_down', 0)}  ·  by {style.trim(str(top.get('author', 'Unknown')), 60)}")
        await style.respond(ctx, private=False, embed=embed)

    @commands.group(aliases=["card"], invoke_without_command=True)
    @commands.guild_only()
    async def profile(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        guild_id, user_id = str(ctx.guild.id), str(member.id)
        doc = await profiles_col.find_one({"guild_id": guild_id, "user_id": user_id}) or {}
        bio = doc.get("bio") or f"Not set. Use {ctx.clean_prefix}profile bio <text>"
        location = doc.get("location") or "Not set"

        level_doc = await levels_col.find_one({"guild_id": guild_id, "user_id": user_id}) or {}
        level = level_doc.get("level", 0)
        xp = level_doc.get("xp", 0)
        needed = (level + 1) * 100
        filled = max(0, min(10, int((xp / needed) * 10))) if needed else 0
        bar = "█" * filled + "░" * (10 - filled)

        birthday_doc = await birthdays_col.find_one({"guild_id": guild_id, "user_id": user_id}) or {}
        birthday = birthday_doc.get("date") or "Not set"

        color = member.color if member.color != discord.Color.default() else style.NEUTRAL
        embed = discord.Embed(color=color)
        embed.set_author(name=f"{member.name}'s profile", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="About me", value=style.trim(discord.utils.escape_mentions(str(bio)), 300), inline=False)
        embed.add_field(name="Location", value=style.code(location, 60), inline=True)
        embed.add_field(name="Birthday", value=style.code(birthday, 20), inline=True)
        embed.add_field(name="Level", value=f"{level}  ({xp}/{needed} XP)", inline=True)
        embed.add_field(name="Progress", value=f"`{bar}`", inline=True)
        embed.add_field(name="Roles", value=str(len(member.roles) - 1), inline=True)
        embed.add_field(name="Joined", value=f"<t:{int(member.joined_at.timestamp())}:D>" if member.joined_at else "Unknown", inline=True)
        embed.set_footer(text=f"ID: {member.id}")
        await style.respond(ctx, private=False, embed=embed)

    @profile.command(name="bio")
    async def profile_bio(self, ctx, *, text: str = None):
        if not text:
            return await style.respond(ctx, embed=style.usage(f"{ctx.clean_prefix}profile bio <text>"))
        text = one_line(text, 500)
        if len(text) > 150:
            return await style.respond(ctx, embed=style.failure("Your bio can be 150 characters at most."))
        await profiles_col.update_one({"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)}, {"$set": {"bio": text}}, upsert=True)
        await style.respond(ctx, embed=style.success("Your bio was updated."))

    @profile.command(name="location", aliases=["loc"])
    async def profile_location(self, ctx, *, city: str = None):
        if not city:
            return await style.respond(ctx, embed=style.usage(f"{ctx.clean_prefix}profile location <city>"))
        city = one_line(city, 200)
        if len(city) > 40:
            return await style.respond(ctx, embed=style.failure("Your location can be 40 characters at most."))
        await profiles_col.update_one({"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)}, {"$set": {"location": city}}, upsert=True)
        await style.respond(ctx, embed=style.success(f"Your location is now {style.code(city, 40)}."))

    @profile.command(name="clear")
    async def profile_clear(self, ctx):
        await profiles_col.delete_one({"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)})
        await style.respond(ctx, embed=style.success("Your profile was cleared."))

    @commands.group(aliases=["bday"], invoke_without_command=True)
    @commands.guild_only()
    async def birthday(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        doc = await birthdays_col.find_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
        if not doc:
            return await style.respond(ctx, embed=style.failure(f"**{style.clean_name(member.name)}** has not set a birthday."))
        await style.respond(ctx, private=False, embed=style.card("Birthday", f"**{style.clean_name(member.name)}**'s birthday is on **{doc['date']}**."))

    @birthday.command(name="list")
    async def birthday_list(self, ctx):
        records = [doc async for doc in birthdays_col.find({"guild_id": str(ctx.guild.id)})]
        if not records:
            return await style.respond(ctx, embed=style.card("Birthdays", "No birthdays have been added yet."))
        records.sort(key=lambda doc: (doc.get("month", 0), doc.get("day", 0)))
        lines = []
        for doc in records[:25]:
            member = ctx.guild.get_member(int(doc["user_id"]))
            lines.append(f"{member.mention if member else '`Left the server`'}  ·  **{doc['date']}**")
        embed = style.card(f"Birthdays ({len(records)})", "\n".join(lines))
        if len(records) > 25:
            embed.set_footer(text=f"Showing 25 of {len(records)}")
        await style.respond(ctx, private=False, embed=embed, allowed_mentions=style.NO_PINGS)

    async def _save_birthday(self, ctx, member, date):
        parsed = parse_birthday(date)
        if not parsed:
            return await style.respond(ctx, embed=style.failure("That date is not valid. Use `DD/MM`, for example `25/12`."))
        day, month = parsed
        text = f"{day:02d}/{month:02d}"
        await birthdays_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(member.id)},
            {"$set": {"date": text, "day": day, "month": month}},
            upsert=True,
        )
        await style.respond(ctx, embed=style.success(f"The birthday for {member.mention} is saved as **{text}**."), allowed_mentions=style.NO_PINGS)

    @birthday.command(name="set")
    async def birthday_set(self, ctx, date: str = None):
        if not date:
            return await style.respond(ctx, embed=style.usage(f"{ctx.clean_prefix}birthday set DD/MM"))
        await self._save_birthday(ctx, ctx.author, date)

    @birthday.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def birthday_add(self, ctx, member: discord.Member = None, date: str = None):
        if not member or not date:
            return await style.respond(ctx, embed=style.usage(f"{ctx.clean_prefix}birthday add @member DD/MM"))
        await self._save_birthday(ctx, member, date)

    @birthday.command(name="remove")
    async def birthday_remove(self, ctx):
        result = await birthdays_col.delete_one({"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)})
        if result.deleted_count:
            await style.respond(ctx, embed=style.success("Your birthday was removed."))
        else:
            await style.respond(ctx, embed=style.failure("You have not set a birthday."))

    @birthday.command(name="channel")
    @commands.has_permissions(manage_guild=True)
    async def birthday_channel(self, ctx, channel: discord.TextChannel = None):
        if channel is None:
            settings = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
            channel_id = settings.get("birthday_channel")
            if channel_id:
                return await style.respond(ctx, embed=style.card("Birthday channel", f"Birthday wishes go to <#{channel_id}>."))
            return await style.respond(ctx, embed=style.card("Birthday channel", "No channel is set. Wishes go to the server's system channel."))
        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            return await style.respond(ctx, embed=style.failure(f"I need permission to see, send messages and add embeds in {channel.mention}."))
        await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$set": {"birthday_channel": str(channel.id)}}, upsert=True)
        await style.respond(ctx, embed=style.success(f"Birthday wishes will go to {channel.mention}."))

    @birthday.command(name="channelremove", aliases=["removechannel"])
    @commands.has_permissions(manage_guild=True)
    async def birthday_channel_remove(self, ctx):
        await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$unset": {"birthday_channel": ""}}, upsert=True)
        await style.respond(ctx, embed=style.success("The birthday channel was removed."))

    @app_commands.command(name="birthday", description="See a member's birthday")
    @app_commands.describe(member="The member to look up")
    @app_commands.guild_only()
    async def slash_birthday(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        doc = await birthdays_col.find_one({"guild_id": str(interaction.guild.id), "user_id": str(member.id)})
        if not doc:
            embed = style.failure(f"**{style.clean_name(member.name)}** has not set a birthday.")
        else:
            embed = style.card("Birthday", f"**{style.clean_name(member.name)}**'s birthday is on **{doc['date']}**.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    def _can_mimic(self, invoker, target):
        if invoker.id == invoker.guild.owner_id:
            return True
        if target.id == invoker.guild.owner_id:
            return False
        return target.top_role < invoker.top_role

    async def _find_hook(self, channel, name):
        base = channel.parent if isinstance(channel, discord.Thread) else channel
        if not isinstance(base, discord.TextChannel):
            return None, None, "Webhooks do not work in this kind of channel."
        if not base.permissions_for(base.guild.me).manage_webhooks:
            return None, None, "I need the Manage Webhooks permission in this channel."
        hooks = await base.webhooks()
        hook = discord.utils.find(lambda h: h.name == name and h.user and h.user.id == self.bot.user.id, hooks)
        if hook is None:
            hook = await base.create_webhook(name=name)
        thread = channel if isinstance(channel, discord.Thread) else discord.utils.MISSING
        return hook, thread, None

    async def _speak_as(self, channel, hook_name, display_name, avatar_url, text):
        try:
            hook, thread, error = await self._find_hook(channel, hook_name)
            if error:
                return error
            await hook.send(content=text, username=display_name[:80], avatar_url=avatar_url, allowed_mentions=style.NO_PINGS, thread=thread)
        except discord.Forbidden:
            return "I do not have permission to do that here."
        except discord.HTTPException:
            return "Discord did not accept that name or message. Please try something else."
        return None

    @commands.group(invoke_without_command=True)
    @ctx_mod()
    @commands.guild_only()
    async def mimic(self, ctx, member: discord.Member = None, *, message: str = None):
        if not member or not message:
            return await style.respond(ctx, embed=style.usage(f"{ctx.clean_prefix}mimic @member <message>"))
        if len(message) > 2000:
            return await style.respond(ctx, embed=style.failure("Your message must be under 2000 characters."))
        if not self._can_mimic(ctx.author, member):
            return await style.respond(ctx, embed=style.failure("You cannot copy someone with a role equal to or higher than yours."))
        error = await self._speak_as(ctx.channel, "HappyMimic", member.display_name, member.display_avatar.url, message)
        if error:
            return await style.respond(ctx, embed=style.failure(error))
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @mimic.command(name="custom")
    @ctx_mod()
    async def mimic_custom(self, ctx, name: str = None, avatar_url: str = None, *, message: str = None):
        if not name or not avatar_url or not message:
            return await style.respond(ctx, embed=style.usage(f"{ctx.clean_prefix}mimic custom <name> <picture link> <message>"))
        if not style.is_link(avatar_url) or avatar_url.startswith("{"):
            return await style.respond(ctx, embed=style.failure("The picture link must start with `https://`."))
        if len(message) > 2000:
            return await style.respond(ctx, embed=style.failure("Your message must be under 2000 characters."))
        error = await self._speak_as(ctx.channel, "HappyCustomWebhook", name.strip(), avatar_url.strip(), message)
        if error:
            return await style.respond(ctx, embed=style.failure(error))
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    mimic_group = app_commands.Group(name="mimic", description="Send a message as someone else", guild_only=True)

    @mimic_group.command(name="user", description="Send a message that looks like it came from a member")
    @app_commands.describe(member="The member to copy", message="What to say")
    @app_commands.checks.has_permissions(manage_webhooks=True)
    async def mimic_user(self, interaction: discord.Interaction, member: discord.Member, message: str):
        if len(message) > 2000:
            return await interaction.response.send_message(embed=style.failure("Your message must be under 2000 characters."), ephemeral=True)
        if not self._can_mimic(interaction.user, member):
            return await interaction.response.send_message(embed=style.failure("You cannot copy someone with a role equal to or higher than yours."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        error = await self._speak_as(interaction.channel, "HappyMimic", member.display_name, member.display_avatar.url, message)
        if error:
            return await interaction.followup.send(embed=style.failure(error), ephemeral=True)
        await interaction.followup.send(embed=style.success("Your message was sent."), ephemeral=True)

    async def _echo(self, member, channel, text):
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return "I cannot send messages there."
        if not channel.permissions_for(member).send_messages:
            return f"You cannot send messages in {channel.mention}."
        if not channel.permissions_for(channel.guild.me).send_messages:
            return f"I cannot send messages in {channel.mention}."
        try:
            await channel.send(text, allowed_mentions=style.NO_PINGS)
        except discord.HTTPException:
            return "Discord did not accept that message."
        return None

    @commands.command()
    @ctx_mod()
    @commands.guild_only()
    async def echo(self, ctx, *, text: str = None):
        if not text:
            return await style.respond(ctx, embed=style.usage(f"{ctx.clean_prefix}echo [#channel] <message>"))
        target = ctx.channel
        content = text
        if ctx.message.channel_mentions:
            mentioned = ctx.message.channel_mentions[0]
            if text.startswith(mentioned.mention):
                target = mentioned
                content = text[len(mentioned.mention):].strip()
        if not content:
            return await style.respond(ctx, embed=style.failure("Your message cannot be empty."))
        if len(content) > 2000:
            return await style.respond(ctx, embed=style.failure("Your message must be under 2000 characters."))
        error = await self._echo(ctx.author, target, content)
        if error:
            return await style.respond(ctx, embed=style.failure(error))
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @app_commands.command(name="echo", description="Make the bot send a message")
    @app_commands.describe(message="What to say", channel="Where to send it")
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def slash_echo(self, interaction: discord.Interaction, message: app_commands.Range[str, 1, 2000], channel: Optional[discord.TextChannel] = None):
        error = await self._echo(interaction.user, channel or interaction.channel, message)
        if error:
            return await interaction.response.send_message(embed=style.failure(error), ephemeral=True)
        await interaction.response.send_message(embed=style.success("Your message was sent."), ephemeral=True)

    async def _pick(self, ctx, name=None):
        if name is None:
            name = await Drafts.active(ctx.guild.id, ctx.author.id)
            if not name:
                await style.respond(ctx, embed=style.failure(f"Start a draft first with `{ctx.clean_prefix}embed create <name>`.", "No draft selected"))
                return None
        elif not style.NAME_PATTERN.match(name):
            await style.respond(ctx, embed=style.failure("A draft name can only use letters, numbers, dashes and underscores. It can be 32 characters long."))
            return None
        draft = await Drafts.get(ctx.guild.id, ctx.author.id, name)
        if not draft:
            await style.respond(ctx, embed=style.failure(f"You have no draft named `{name}`. See your drafts with `{ctx.clean_prefix}embed list`.", "Draft not found"))
            return None
        return name, draft

    async def _open_studio(self, ctx, name, draft):
        studio = EmbedStudio(self, ctx.author.id, ctx.guild.id, name)
        embed = style.build_embed(draft, ctx.author, ctx.guild, placeholder=True)
        studio.message = await style.respond(ctx, content=studio.header(draft), embed=embed, view=studio, allowed_mentions=style.NO_PINGS)
        await Drafts.set_active(ctx.guild.id, ctx.author.id, name)

    async def _edit_field(self, ctx, field, value, label):
        picked = await self._pick(ctx)
        if not picked:
            return
        name, draft = picked
        if value is None:
            return await style.respond(ctx, embed=style.usage(f"{ctx.clean_prefix}embed {field} <{label}>", f"Type `clear` to remove it. You are editing `{name}`."))
        new_value = None if value.strip().lower() in RESET_WORDS else value
        if field == "color" and new_value is None:
            new_value = "2B2D31"
        clean, error = style.clean_draft({**draft, field: new_value})
        if error:
            return await style.respond(ctx, embed=style.failure(error))
        await Drafts.save(ctx.guild.id, ctx.author.id, name, clean)
        await style.respond(ctx, embed=style.success(f"The {label} was updated for draft `{name}`.\nSee it with `{ctx.clean_prefix}embed preview`."))

    @commands.hybrid_group(name="embed", fallback="help", invoke_without_command=True, description="Build and send custom embeds")
    @with_permissions(manage_messages=True)
    async def embed_group(self, ctx):
        p = ctx.clean_prefix
        embed = style.card("Embed Studio", "Build a clean embed, preview it, then send it or use it for welcome and leave messages.")
        embed.add_field(
            name="Get started",
            value=(
                f"`{p}embed create <name>`  Start a new draft\n"
                f"`{p}embed preview [name]`  Open a draft to edit\n"
                f"`{p}embed send [#channel] [name]`  Send a draft\n"
                f"`{p}embed list`  See your drafts\n"
                f"`{p}embed use <name>`  Pick which draft quick edits change\n"
                f"`{p}embed discard [name]`  Delete a draft"
            ),
            inline=False,
        )
        embed.add_field(
            name="Quick edits",
            value=(
                f"`{p}embed title <text>`  ·  `{p}embed desc <text>`\n"
                f"`{p}embed color <#hex>`  ·  `{p}embed footer <text>`\n"
                f"`{p}embed thumbnail <link>`  ·  `{p}embed image <link>`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Share and reuse",
            value=(
                f"`{p}embed import <json>`  Load an embed from JSON\n"
                f"`{p}embed export [name]`  Get a draft as JSON\n"
                f"`{p}welcome embed <name>`  Use a draft for welcome messages\n"
                f"`{p}bye embed <name>`  Use a draft for leave messages"
            ),
            inline=False,
        )
        embed.set_footer(text=f"Words like {{user}} and {{server}} work inside embeds. See them all with {p}variables")
        await style.respond(ctx, embed=embed)

    @embed_group.command(name="create", description="Start a new embed draft")
    @app_commands.describe(name="A short name for your draft")
    @with_permissions(manage_messages=True)
    async def embed_create(self, ctx, name: str = "default"):
        if not style.NAME_PATTERN.match(name):
            return await style.respond(ctx, embed=style.failure("A draft name can only use letters, numbers, dashes and underscores. It can be 32 characters long."))
        existing = await Drafts.get(ctx.guild.id, ctx.author.id, name)
        if existing:
            return await self._open_studio(ctx, name, existing)
        if await Drafts.count(ctx.guild.id, ctx.author.id) >= DRAFT_LIMIT:
            return await style.respond(ctx, embed=style.failure(f"You can keep {DRAFT_LIMIT} drafts. Delete one with `{ctx.clean_prefix}embed discard <name>`."))
        draft = {
            "title": "Your title",
            "description": "Write your message here. Use {user} to mention someone and {server} for the server name.",
            "color": "2B2D31",
            "thumbnail": None,
            "image": None,
            "footer": None,
            "channel_id": None,
        }
        await Drafts.save(ctx.guild.id, ctx.author.id, name, draft)
        await self._open_studio(ctx, name, draft)

    @embed_group.command(name="preview", aliases=["edit", "show", "open"], description="Open a draft to see and edit it")
    @app_commands.describe(name="The draft to open")
    @with_permissions(manage_messages=True)
    async def embed_preview(self, ctx, name: Optional[str] = None):
        picked = await self._pick(ctx, name)
        if picked:
            await self._open_studio(ctx, *picked)

    @embed_group.command(name="use", description="Pick which draft the quick edit commands change")
    @app_commands.describe(name="The draft to pick")
    @with_permissions(manage_messages=True)
    async def embed_use(self, ctx, name: str):
        picked = await self._pick(ctx, name)
        if not picked:
            return
        await Drafts.set_active(ctx.guild.id, ctx.author.id, picked[0])
        await style.respond(ctx, embed=style.success(f"Quick edits will now change draft `{picked[0]}`."))

    @embed_group.command(name="list", description="See your drafts")
    @with_permissions(manage_messages=True)
    async def embed_list(self, ctx):
        names = await Drafts.names(ctx.guild.id, ctx.author.id)
        if not names:
            return await style.respond(ctx, embed=style.card("Your drafts", f"You have no drafts yet. Start one with `{ctx.clean_prefix}embed create <name>`."))
        active = await Drafts.active(ctx.guild.id, ctx.author.id)
        lines = [f"`{item}`" + ("  ·  selected" if item == active else "") for item in names]
        await style.respond(ctx, embed=style.card(f"Your drafts ({len(names)}/{DRAFT_LIMIT})", "\n".join(lines)))

    @embed_group.command(name="title", description="Change the title")
    @app_commands.describe(text="The new title, or clear to remove it")
    @with_permissions(manage_messages=True)
    async def embed_title(self, ctx, *, text: Optional[str] = None):
        await self._edit_field(ctx, "title", text, "title")

    @embed_group.command(name="desc", aliases=["description"], description="Change the message text")
    @app_commands.describe(text="The new message, or clear to remove it")
    @with_permissions(manage_messages=True)
    async def embed_desc(self, ctx, *, text: Optional[str] = None):
        await self._edit_field(ctx, "description", text, "message")

    @embed_group.command(name="color", aliases=["colour"], description="Change the color")
    @app_commands.describe(text="A color like #5865F2")
    @with_permissions(manage_messages=True)
    async def embed_color(self, ctx, text: Optional[str] = None):
        await self._edit_field(ctx, "color", text, "color")

    @embed_group.command(name="footer", description="Change the footer")
    @app_commands.describe(text="The new footer, or clear to remove it")
    @with_permissions(manage_messages=True)
    async def embed_footer(self, ctx, *, text: Optional[str] = None):
        await self._edit_field(ctx, "footer", text, "footer")

    @embed_group.command(name="thumbnail", description="Change the small picture")
    @app_commands.describe(text="A picture link starting with https://, or clear")
    @with_permissions(manage_messages=True)
    async def embed_thumbnail(self, ctx, text: Optional[str] = None):
        await self._edit_field(ctx, "thumbnail", text, "small picture")

    @embed_group.command(name="image", description="Change the large picture")
    @app_commands.describe(text="A picture link starting with https://, or clear")
    @with_permissions(manage_messages=True)
    async def embed_image(self, ctx, text: Optional[str] = None):
        await self._edit_field(ctx, "image", text, "large picture")

    @embed_group.command(name="send", description="Send a draft to a channel")
    @app_commands.describe(channel="Where to send it", name="The draft to send")
    @with_permissions(manage_messages=True)
    async def embed_send(self, ctx, channel: Optional[discord.TextChannel] = None, name: Optional[str] = None):
        picked = await self._pick(ctx, name)
        if not picked:
            return
        draft_name, draft = picked
        target = channel
        if target is None and draft.get("channel_id"):
            target = ctx.guild.get_channel(int(draft["channel_id"]))
        target = target or ctx.channel
        error = await self.deliver(ctx.author, ctx.guild, draft, target)
        if error:
            return await style.respond(ctx, embed=style.failure(error))
        await style.respond(ctx, embed=style.success(f"Draft `{draft_name}` was sent to {target.mention}. The draft is saved, so you can send it again."))

    @embed_group.command(name="discard", aliases=["delete"], description="Delete a draft")
    @app_commands.describe(name="The draft to delete")
    @with_permissions(manage_messages=True)
    async def embed_discard(self, ctx, name: Optional[str] = None):
        picked = await self._pick(ctx, name)
        if not picked:
            return
        await Drafts.delete(ctx.guild.id, ctx.author.id, picked[0])
        await style.respond(ctx, embed=style.success(f"Draft `{picked[0]}` was deleted."))

    @embed_group.command(name="import", description="Load an embed from JSON")
    @app_commands.describe(name="A name for the new draft", json_data="The embed JSON")
    @with_permissions(manage_messages=True)
    async def embed_import(self, ctx, name: str, *, json_data: str):
        if not style.NAME_PATTERN.match(name):
            return await style.respond(ctx, embed=style.failure("A draft name can only use letters, numbers, dashes and underscores. It can be 32 characters long."))
        clean, error = style.parse_import(json_data)
        if error:
            return await style.respond(ctx, embed=style.failure(error))
        if not style.has_content(clean):
            return await style.respond(ctx, embed=style.failure("That embed is empty."))
        exists = await Drafts.get(ctx.guild.id, ctx.author.id, name)
        if not exists and await Drafts.count(ctx.guild.id, ctx.author.id) >= DRAFT_LIMIT:
            return await style.respond(ctx, embed=style.failure(f"You can keep {DRAFT_LIMIT} drafts. Delete one first."))
        clean["channel_id"] = None
        await Drafts.save(ctx.guild.id, ctx.author.id, name, clean)
        draft = await Drafts.get(ctx.guild.id, ctx.author.id, name)
        await self._open_studio(ctx, name, draft)

    @embed_group.command(name="export", description="Get a draft as JSON")
    @app_commands.describe(name="The draft to export")
    @with_permissions(manage_messages=True)
    async def embed_export(self, ctx, name: Optional[str] = None):
        picked = await self._pick(ctx, name)
        if not picked:
            return
        draft_name, draft = picked
        clean, error = style.clean_draft(draft)
        if error:
            return await style.respond(ctx, embed=style.failure(error))
        text = json.dumps({key: value for key, value in clean.items() if value}, indent=2, ensure_ascii=False)
        if len(text) < 1800:
            return await style.respond(ctx, content=f"```json\n{text}\n```")
        file = discord.File(io.BytesIO(text.encode("utf-8")), filename=f"{draft_name}.json")
        await style.respond(ctx, file=file)

    async def _rules(self, guild_id):
        cached = self.rule_cache.get(guild_id)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        rules = [doc async for doc in autoresponder_col.find({"guild_id": str(guild_id)})]
        self.rule_cache[guild_id] = (time.monotonic() + RULE_CACHE_SECONDS, rules)
        return rules

    @commands.hybrid_group(name="autoresponder", aliases=["ar"], fallback="help", invoke_without_command=True, description="Reply automatically to certain words")
    @with_permissions(manage_guild=True)
    async def ar_group(self, ctx):
        p = ctx.clean_prefix
        embed = style.card("Autoresponder", "Reply automatically when someone sends a certain word or phrase.")
        embed.add_field(
            name="Commands",
            value=(
                f"`{p}ar add <trigger> text <reply>`  Reply with text\n"
                f"`{p}ar add <trigger> embed <draft>`  Reply with a saved embed\n"
                f"`{p}ar remove <trigger>`  Remove a reply\n"
                f"`{p}ar list`  See all replies"
            ),
            inline=False,
        )
        embed.set_footer(text='For a phrase with spaces, put it in quotes. Example: "good morning"')
        await style.respond(ctx, embed=embed)

    @ar_group.command(name="add", description="Add an automatic reply")
    @app_commands.describe(trigger="The word or phrase to listen for", kind="Reply with text or with an embed", content="The reply text, or the name of your embed draft")
    @with_permissions(manage_guild=True)
    async def ar_add(self, ctx, trigger: str, kind: Literal["text", "embed"], *, content: str):
        trigger = " ".join(trigger.lower().split())
        if not trigger or len(trigger) > 100:
            return await style.respond(ctx, embed=style.failure("The trigger must be between 1 and 100 characters."))

        if kind == "embed":
            draft = await Drafts.get(ctx.guild.id, ctx.author.id, content.strip())
            if not draft:
                return await style.respond(ctx, embed=style.failure(f"You have no draft named `{content.strip()}`."))
            stored, error = style.clean_draft(draft)
            if error:
                return await style.respond(ctx, embed=style.failure(error))
            if not style.has_content(stored):
                return await style.respond(ctx, embed=style.failure("That draft is empty."))
        else:
            if len(content) > 1900:
                return await style.respond(ctx, embed=style.failure("The reply must be under 1900 characters."))
            stored = content

        if await autoresponder_col.find_one({"guild_id": str(ctx.guild.id), "trigger": trigger}):
            return await style.respond(ctx, embed=style.failure("This trigger already has a reply. Remove it first to change it."))
        if await autoresponder_col.count_documents({"guild_id": str(ctx.guild.id)}) >= RULE_LIMIT:
            return await style.respond(ctx, embed=style.failure(f"This server can have {RULE_LIMIT} automatic replies at most."))

        await autoresponder_col.update_one(
            {"guild_id": str(ctx.guild.id), "trigger": trigger},
            {"$set": {"response_type": kind, "content": stored, "creator_id": str(ctx.author.id)}},
            upsert=True,
        )
        self.rule_cache.pop(ctx.guild.id, None)
        await style.respond(ctx, embed=style.success(f"I will now reply to {style.code(trigger, 100)}."))

    @ar_group.command(name="remove", description="Remove an automatic reply")
    @app_commands.describe(trigger="The trigger to remove")
    @with_permissions(manage_guild=True)
    async def ar_remove(self, ctx, trigger: str):
        result = await autoresponder_col.delete_one({"guild_id": str(ctx.guild.id), "trigger": " ".join(trigger.lower().split())})
        self.rule_cache.pop(ctx.guild.id, None)
        if result.deleted_count:
            await style.respond(ctx, embed=style.success("The automatic reply was removed."))
        else:
            await style.respond(ctx, embed=style.failure("I could not find that trigger."))

    @ar_group.command(name="list", description="See all automatic replies")
    @with_permissions(manage_guild=True)
    async def ar_list(self, ctx):
        rules = [doc async for doc in autoresponder_col.find({"guild_id": str(ctx.guild.id)})]
        if not rules:
            return await style.respond(ctx, embed=style.card("Autoresponder", "No automatic replies have been added yet."))
        lines = [f"{style.code(doc['trigger'], 60)}  ·  {doc['response_type']}" for doc in rules[:25]]
        embed = style.card(f"Autoresponder ({len(rules)})", "\n".join(lines))
        if len(rules) > 25:
            embed.set_footer(text=f"Showing 25 of {len(rules)}")
        await style.respond(ctx, embed=embed)

    @commands.Cog.listener("on_message")
    async def on_autoresponder_message(self, message):
        if message.author.bot or not message.guild:
            return
        text = " ".join(message.content.lower().split())
        if not text or len(text) > 300:
            return

        rules = await self._rules(message.guild.id)
        if not rules:
            return

        for doc in rules:
            trigger = doc.get("trigger")
            if not trigger:
                continue
            if not (text == trigger or text.startswith(trigger + " ") or text.endswith(" " + trigger)):
                continue

            marker = (message.guild.id, message.author.id)
            now = time.monotonic()
            if now - self.rule_seen.get(marker, 0) < RULE_COOLDOWN:
                return
            if len(self.rule_seen) > 5000:
                self.rule_seen.clear()
            self.rule_seen[marker] = now

            perms = message.channel.permissions_for(message.guild.me)
            if not perms.send_messages:
                return
            try:
                if doc.get("response_type") == "text" and isinstance(doc.get("content"), str):
                    await message.channel.send(doc["content"], allowed_mentions=style.NO_PINGS)
                elif doc.get("response_type") == "embed" and isinstance(doc.get("content"), dict) and perms.embed_links:
                    clean, error = style.clean_draft(doc["content"])
                    if not error and style.has_content(clean):
                        member = message.guild.get_member(message.author.id) or message.author
                        await message.channel.send(embed=style.build_embed(clean, member, message.guild), allowed_mentions=style.NO_PINGS)
            except discord.HTTPException:
                pass
            return


async def setup(bot):
    await bot.add_cog(Utility(bot))