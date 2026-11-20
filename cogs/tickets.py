import asyncio
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands
from discord.utils import MISSING
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from utils.db import tickets_col
from utils.helpers import ctx_mod, log_event

NEUTRAL = 0x2B2D31
SUCCESS = 0x57F287
DANGER = 0xED4245

CLOSE_DELAY = 5
CLOSE_LOCK_SECONDS = 60
OPEN_COOLDOWN = 15
MAX_OPEN_PER_SERVER = 150
MAX_TYPES = 20
CATEGORY_NAME = "Tickets"
CATEGORY_LIMIT = 50
CUSTOM_KEY = "custom"

DEFAULT_TYPES = [
    {
        "key": "help",
        "label": "General Support",
        "emoji": "💬",
        "text": "Questions and everyday help",
    },
    {
        "key": "report",
        "label": "Player Report",
        "emoji": "🚨",
        "text": "Tell us about a player or a problem",
    },
    {
        "key": "staff",
        "label": "Staff Application",
        "emoji": "🛡️",
        "text": "Apply to join our team",
    },
    {
        "key": "event",
        "label": "Event Inquiry",
        "emoji": "🎉",
        "text": "Ask about events and plans",
    },
]

CUSTOM_TOPIC = {
    "key": CUSTOM_KEY,
    "label": "Custom Ticket",
    "emoji": "✨",
    "text": "Write your own subject and message",
}

UNSAFE_CHARS = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]"
)

opening_now: set[tuple[int, int]] = set()
cooldowns: dict[tuple[int, int], float] = {}


def clean_text(text, limit, multiline=False):
    text = UNSAFE_CHARS.sub("", text or "")
    text = discord.utils.escape_mentions(text)
    if multiline:
        text = "\n".join(line.rstrip() for line in text.splitlines())
        text = re.sub(r"\n{3,}", "\n\n", text)
    else:
        text = " ".join(text.split())
    return text.strip()[:limit]


def build_embed(title=None, description=None, color=NEUTRAL, guild=None):
    embed = discord.Embed(title=title, description=description, color=color)
    if guild:
        embed.set_footer(text=guild.name)
    return embed


def notice(text, color, icon):
    return discord.Embed(description=f"{icon}  {text}", color=color)


def error_embed(text, guild=None):
    return notice(text, DANGER, "❌")


def success_embed(text, guild=None):
    return notice(text, SUCCESS, "✅")


def info_embed(text, guild=None):
    return notice(text, NEUTRAL, "ℹ️")


def closing_embed(guild=None):
    return notice(f"This ticket will be deleted in {CLOSE_DELAY} seconds.", DANGER, "🔒")


def panel_embed(guild):
    embed = discord.Embed(
        title="Need help?",
        description=(
            "Pick a topic from the menu below.\n"
            "A private channel will open just for you and our team."
        ),
        color=NEUTRAL,
    )
    icon = guild.icon.url if guild.icon else None
    embed.set_author(name=f"{guild.name} Support", icon_url=icon)
    embed.set_footer(text="Only you and our team can see your ticket")
    return embed


def chat_access():
    return discord.PermissionOverwrite(
        read_messages=True,
        send_messages=True,
        read_message_history=True,
        attach_files=True,
        embed_links=True,
        mention_everyone=False,
        create_public_threads=False,
        create_private_threads=False,
    )


async def get_settings(guild_id):
    return await tickets_col.find_one({"_id": str(guild_id)}) or {}


async def get_ticket(channel):
    return await tickets_col.find_one({"channel_id": str(channel.id), "active": True})


def get_types(settings):
    stored = settings.get("types")
    if stored is None:
        return [dict(item) for item in DEFAULT_TYPES]
    return [
        item
        for item in stored
        if isinstance(item, dict) and item.get("key") and item.get("label")
    ]


def custom_enabled(settings):
    return settings.get("custom_enabled", True)


def find_topic(settings, key):
    if key == CUSTOM_KEY:
        return CUSTOM_TOPIC if custom_enabled(settings) else None
    for item in get_types(settings):
        if item["key"] == key:
            return item
    return None


def topic_options(settings):
    options = [
        discord.SelectOption(
            label=item["label"],
            value=item["key"],
            description=item.get("text") or None,
            emoji=item.get("emoji") or None,
        )
        for item in get_types(settings)[:MAX_TYPES]
    ]
    if custom_enabled(settings) or not options:
        options.append(
            discord.SelectOption(
                label=CUSTOM_TOPIC["label"],
                value=CUSTOM_KEY,
                description=CUSTOM_TOPIC["text"],
                emoji=CUSTOM_TOPIC["emoji"],
            )
        )
    return options


def is_staff(member, settings):
    perms = member.guild_permissions
    if perms.administrator or perms.manage_channels:
        return True
    role_id = settings.get("staff_role_id")
    return bool(role_id) and any(str(role.id) == role_id for role in member.roles)


def can_close(member, ticket, settings):
    return str(member.id) == ticket["owner_id"] or is_staff(member, settings)


def on_cooldown(key):
    now = time.monotonic()
    if len(cooldowns) > 500:
        for old in [k for k, t in cooldowns.items() if now - t > OPEN_COOLDOWN]:
            del cooldowns[old]
    last = cooldowns.get(key)
    if last is not None and now - last < OPEN_COOLDOWN:
        return int(OPEN_COOLDOWN - (now - last)) + 1
    cooldowns[key] = now
    return 0


async def find_closable(channel, user):
    ticket = await get_ticket(channel)
    if not ticket:
        return None, "This channel is not an open ticket."
    settings = await get_settings(channel.guild.id)
    if not can_close(user, ticket, settings):
        return None, "Only the person who opened this ticket or our team can close it."
    return ticket, None


async def claim_close(ticket):
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=CLOSE_LOCK_SECONDS)
    claimed = await tickets_col.find_one_and_update(
        {
            "_id": ticket["_id"],
            "$or": [
                {"closing": {"$ne": True}},
                {"closing_at": None},
                {"closing_at": {"$lt": stale}},
            ],
        },
        {"$set": {"closing": True, "closing_at": now}},
    )
    return claimed is not None


async def begin_close(channel, user):
    ticket, problem = await find_closable(channel, user)
    if problem:
        return problem
    if not await claim_close(ticket):
        return "This ticket is already being closed."
    return None


async def finish_close(bot, channel, closer):
    guild = channel.guild
    name = channel.name
    channel_id = str(channel.id)
    await asyncio.sleep(CLOSE_DELAY)
    try:
        await channel.delete(reason=f"Closed by {closer}")
    except discord.HTTPException:
        await tickets_col.update_one(
            {"channel_id": channel_id}, {"$set": {"closing": False}}
        )
        try:
            await channel.send(
                embed=error_embed(
                    "I could not close this ticket. Please check my permissions and try again."
                )
            )
        except discord.HTTPException:
            pass
        return
    await tickets_col.delete_one({"channel_id": channel_id})
    await log_event(bot, guild, "ticket_close", f"Ticket {name} was closed by {closer}.")


async def save_types(guild_id, types):
    await tickets_col.update_one(
        {"_id": str(guild_id)}, {"$set": {"types": types}}, upsert=True
    )


async def refresh_panel(guild):
    settings = await get_settings(guild.id)
    channel_id = settings.get("panel_channel_id")
    message_id = settings.get("panel_message_id")
    if not channel_id or not message_id:
        return False
    channel = guild.get_channel(int(channel_id))
    if channel is None:
        return False
    try:
        message = await channel.fetch_message(int(message_id))
        await message.edit(embed=panel_embed(guild), view=TicketPanelView(settings))
    except discord.HTTPException:
        return False
    return True


async def reset_panel(message, settings):
    if message is None:
        return
    try:
        await message.edit(view=TicketPanelView(settings))
    except discord.HTTPException:
        pass


async def get_category(guild, settings):
    stored = settings.get("category_id")
    category = guild.get_channel(int(stored)) if stored else None
    if not isinstance(category, discord.CategoryChannel):
        category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
    if isinstance(category, discord.CategoryChannel) and len(category.channels) < CATEGORY_LIMIT:
        chosen = category
    else:
        if category is None:
            name = CATEGORY_NAME
        else:
            total = len([c for c in guild.categories if c.name.startswith(CATEGORY_NAME)])
            name = f"{CATEGORY_NAME} {total + 1}"
        try:
            chosen = await guild.create_category(
                name,
                overwrites={guild.default_role: discord.PermissionOverwrite(read_messages=False)},
            )
        except discord.HTTPException:
            return None
    if str(chosen.id) != stored:
        await tickets_col.update_one(
            {"_id": str(guild.id)}, {"$set": {"category_id": str(chosen.id)}}, upsert=True
        )
    return chosen


async def reply(ctx, embed):
    return await ctx.reply(embed=embed, mention_author=False)


class GuildOnlyView(discord.ui.View):
    def __init__(self, timeout=None):
        super().__init__(timeout=timeout)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                embed=error_embed("Tickets only work inside a server."), ephemeral=True
            )
            return False
        return True


class TopicSelect(discord.ui.Select):
    def __init__(self, settings, panel, custom_id=MISSING):
        super().__init__(
            placeholder="Choose a topic to open a ticket",
            options=topic_options(settings),
            custom_id=custom_id,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        await handle_choice(interaction, self.values[0], self.panel)


class TicketPanelView(GuildOnlyView):
    def __init__(self, settings=None):
        super().__init__()
        self.add_item(TopicSelect(settings or {}, True, "ticket_panel_select"))


class TopicPickerView(GuildOnlyView):
    def __init__(self, settings, owner_id):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.add_item(TopicSelect(settings, False))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=error_embed("This menu is not for you."), ephemeral=True
            )
            return False
        return True


class LegacyOpenView(GuildOnlyView):
    @discord.ui.button(
        label="Open a Ticket",
        style=discord.ButtonStyle.primary,
        emoji="✉️",
        custom_id="ticket_open_btn",
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await get_settings(interaction.guild.id)
        await interaction.response.send_message(
            embed=info_embed("Choose a topic below."),
            view=TopicPickerView(settings, interaction.user.id),
            ephemeral=True,
        )


class ConfirmCloseView(GuildOnlyView):
    def __init__(self):
        super().__init__(timeout=30)

    @discord.ui.button(label="Yes, close it", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        channel = interaction.channel
        problem = await begin_close(channel, interaction.user)
        if problem:
            return await interaction.response.edit_message(embed=error_embed(problem), view=None)
        await interaction.response.edit_message(embed=success_embed("Closing now."), view=None)
        await channel.send(embed=closing_embed())
        await finish_close(interaction.client, channel, interaction.user)

    @discord.ui.button(label="Keep it open", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed("The ticket stays open."), view=None
        )


class TicketCloseView(GuildOnlyView):
    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="ticket_close_btn",
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket, problem = await find_closable(interaction.channel, interaction.user)
        if problem:
            return await interaction.response.send_message(
                embed=error_embed(problem), ephemeral=True
            )
        await interaction.response.send_message(
            embed=notice("Close this ticket? The channel will be deleted.", DANGER, "🔒"),
            view=ConfirmCloseView(),
            ephemeral=True,
        )


class CustomTicketModal(discord.ui.Modal, title="Custom Ticket"):
    subject = discord.ui.TextInput(
        label="Subject",
        placeholder="A short title for your ticket",
        min_length=3,
        max_length=80,
    )
    details = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        placeholder="Tell us what you need",
        min_length=10,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        subject = clean_text(self.subject.value, 80)
        details = clean_text(self.details.value, 1000, multiline=True)
        if len(subject) < 3 or len(details) < 10:
            return await interaction.followup.send(
                embed=error_embed("Please write a clear subject and message."), ephemeral=True
            )
        await create_ticket(interaction, CUSTOM_KEY, subject, details)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await super().on_error(interaction, error)
        embed = error_embed("Something went wrong. Please try again.")
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def handle_choice(interaction, choice, panel):
    settings = await get_settings(interaction.guild.id)
    if choice == CUSTOM_KEY and custom_enabled(settings):
        await interaction.response.send_modal(CustomTicketModal())
        if panel:
            await reset_panel(interaction.message, settings)
        return
    if panel:
        await interaction.response.edit_message(view=TicketPanelView(settings))
    else:
        await interaction.response.edit_message(
            embed=info_embed("Setting things up..."), view=None
        )
    await create_ticket(interaction, choice)


async def create_ticket(interaction, choice, subject=None, details=None):
    guild = interaction.guild
    member = interaction.user

    if guild is None or not isinstance(member, discord.Member):
        return await interaction.followup.send(
            embed=error_embed("Tickets only work inside a server."), ephemeral=True
        )

    settings = await get_settings(guild.id)
    topic = find_topic(settings, choice)
    if topic is None or (choice == CUSTOM_KEY and not (subject and details)):
        return await interaction.followup.send(
            embed=error_embed("That topic is not available right now."), ephemeral=True
        )

    key = (guild.id, member.id)
    wait = on_cooldown(key)
    if wait:
        return await interaction.followup.send(
            embed=error_embed(f"Please wait {wait} seconds before trying again."),
            ephemeral=True,
        )

    if key in opening_now:
        return await interaction.followup.send(
            embed=error_embed("Your ticket is being created. Please wait a moment."),
            ephemeral=True,
        )

    opening_now.add(key)
    try:
        await build_ticket(interaction, guild, member, topic, subject, details)
    finally:
        opening_now.discard(key)


async def build_ticket(interaction, guild, member, topic, subject, details):
    if not guild.me.guild_permissions.manage_channels:
        return await interaction.followup.send(
            embed=error_embed("I need the Manage Channels permission to open tickets."),
            ephemeral=True,
        )

    existing = await tickets_col.find_one(
        {"guild_id": str(guild.id), "owner_id": str(member.id), "active": True}
    )
    if existing:
        old_channel = guild.get_channel(int(existing["channel_id"]))
        if old_channel:
            return await interaction.followup.send(
                embed=error_embed(f"You already have an open ticket: {old_channel.mention}"),
                ephemeral=True,
            )
        await tickets_col.delete_one({"_id": existing["_id"]})

    open_count = await tickets_col.count_documents(
        {"guild_id": str(guild.id), "active": True}
    )
    if open_count >= MAX_OPEN_PER_SERVER:
        return await interaction.followup.send(
            embed=error_embed("Too many tickets are open right now. Please try again later."),
            ephemeral=True,
        )

    settings = await tickets_col.find_one_and_update(
        {"_id": str(guild.id)},
        {"$inc": {"ticket_count": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    count = settings.get("ticket_count", 1)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        member: chat_access(),
        guild.me: discord.PermissionOverwrite(
            read_messages=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
            manage_channels=True,
            manage_messages=True,
        ),
    }

    staff = None
    if settings.get("staff_role_id"):
        staff = guild.get_role(int(settings["staff_role_id"]))
        if staff:
            overwrites[staff] = chat_access()

    category = await get_category(guild, settings)

    try:
        channel = await guild.create_text_channel(
            name=f"ticket-{count:04d}",
            overwrites=overwrites,
            category=category,
            topic=clean_text(f"{topic['label']} | Opened by {member} ({member.id})", 1000),
            reason=f"Ticket opened by {member}",
        )
    except discord.Forbidden:
        return await interaction.followup.send(
            embed=error_embed(
                "I cannot create channels here. Please give me the Manage Channels permission."
            ),
            ephemeral=True,
        )
    except discord.HTTPException:
        return await interaction.followup.send(
            embed=error_embed(
                "I could not create your ticket. The server may have too many channels."
            ),
            ephemeral=True,
        )

    opened_at = datetime.now(timezone.utc)
    try:
        await tickets_col.insert_one(
            {
                "guild_id": str(guild.id),
                "channel_id": str(channel.id),
                "owner_id": str(member.id),
                "active": True,
                "closing": False,
                "type": topic["key"],
                "type_label": topic["label"],
                "subject": subject,
                "created_at": opened_at,
            }
        )
    except DuplicateKeyError:
        await channel.delete(reason="Duplicate ticket")
        return await interaction.followup.send(
            embed=error_embed("You already have an open ticket."), ephemeral=True
        )
    except Exception:
        await channel.delete(reason="Ticket could not be saved")
        await interaction.followup.send(
            embed=error_embed("Something went wrong while saving your ticket. Please try again."),
            ephemeral=True,
        )
        raise

    topic_name = f"{topic.get('emoji') or '🎫'} {topic['label']}"
    if subject:
        embed = discord.Embed(title=subject, description=details, color=NEUTRAL)
    else:
        embed = discord.Embed(
            title=topic_name,
            description=(
                "Thanks for reaching out! Tell us what you need help with "
                "and add as many details as you can. Our team will reply soon."
            ),
            color=NEUTRAL,
        )
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    embed.add_field(name="Topic", value=topic_name, inline=True)
    embed.add_field(name="Opened", value=f"<t:{int(opened_at.timestamp())}:R>", inline=True)
    if staff:
        embed.add_field(name="Team", value=staff.mention, inline=True)
    embed.set_footer(text="Use the button below to close this ticket")

    await channel.send(
        content=member.mention,
        embed=embed,
        view=TicketCloseView(),
        allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False),
    )

    link = discord.ui.View()
    link.add_item(discord.ui.Button(label="Go to ticket", url=channel.jump_url))
    await interaction.followup.send(
        embed=success_embed(f"Your ticket is ready: {channel.mention}"),
        view=link,
        ephemeral=True,
    )


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.locks = defaultdict(asyncio.Lock)

    async def cog_load(self):
        self.bot.add_view(LegacyOpenView())
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketCloseView())

    async def cog_check(self, ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        return True

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        await tickets_col.delete_one({"channel_id": str(channel.id), "active": True})

    @commands.group(invoke_without_command=True, help="Open the ticket control panel.")
    async def ticket(self, ctx):
        p = ctx.clean_prefix
        embed = discord.Embed(
            title="Ticket Panel",
            description="Simple tools to run your support tickets.",
            color=NEUTRAL,
        )
        if ctx.guild.icon:
            embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url)
        if ctx.author.guild_permissions.administrator:
            embed.add_field(
                name="Set up",
                value=(
                    f"`{p}ticket setup` · Post the ticket panel\n"
                    f"`{p}ticket staffrole @role` · Choose your support team\n"
                    f"`{p}ticket types` · See all topics\n"
                    f"`{p}ticket addtype Name | About` · Add a topic\n"
                    f"`{p}ticket removetype Name` · Remove a topic\n"
                    f"`{p}ticket custom on/off` · Custom tickets"
                ),
                inline=False,
            )
        embed.add_field(
            name="Inside a ticket",
            value=(
                f"`{p}ticket add @user` · Let someone join\n"
                f"`{p}ticket remove @user` · Remove someone\n"
                f"`{p}ticket close` · Close the ticket"
            ),
            inline=False,
        )
        await reply(ctx, embed)

    @ticket.command(name="setup", help="Post the ticket panel in this channel.")
    @commands.has_permissions(administrator=True)
    async def ticket_setup(self, ctx):
        settings = await get_settings(ctx.guild.id)
        message = await ctx.send(embed=panel_embed(ctx.guild), view=TicketPanelView(settings))
        await tickets_col.update_one(
            {"_id": str(ctx.guild.id)},
            {
                "$set": {
                    "panel_channel_id": str(ctx.channel.id),
                    "panel_message_id": str(message.id),
                }
            },
            upsert=True,
        )
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        if not settings.get("staff_role_id"):
            await ctx.send(
                embed=info_embed(
                    f"One more step: choose your support team with "
                    f"`{ctx.clean_prefix}ticket staffrole @role` so they can see new tickets."
                ),
                delete_after=20,
            )

    @ticket.command(name="staffrole", help="Choose the role that handles tickets.")
    @commands.has_permissions(administrator=True)
    async def ticket_staffrole(self, ctx, role: discord.Role = None):
        if not role:
            settings = await get_settings(ctx.guild.id)
            current = f"<@&{settings['staff_role_id']}>" if settings.get("staff_role_id") else "Not set"
            embed = discord.Embed(
                title="Support Team",
                description=(
                    f"Current role: {current}\n"
                    f"Change it with `{ctx.clean_prefix}ticket staffrole @Staff`"
                ),
                color=NEUTRAL,
            )
            return await reply(ctx, embed)

        if role.is_default():
            return await reply(
                ctx,
                error_embed("You cannot use @everyone. It would let everyone read every ticket."),
            )

        await tickets_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"staff_role_id": str(role.id)}},
            upsert=True,
        )
        await reply(ctx, success_embed(f"{role.mention} is now the support team."))

    @ticket.command(name="types", help="See all ticket topics.")
    @commands.has_permissions(administrator=True)
    async def ticket_types(self, ctx):
        settings = await get_settings(ctx.guild.id)
        types = get_types(settings)
        lines = [
            f"`{number}.` {item.get('emoji') or '🎫'} **{item['label']}** · {item.get('text') or ''}"
            for number, item in enumerate(types, start=1)
        ]
        state = "On" if custom_enabled(settings) else "Off"
        lines.append(f"\n✨ **Custom Ticket** · {state}")
        embed = discord.Embed(
            title="Ticket Topics",
            description="\n".join(lines).strip(),
            color=NEUTRAL,
        )
        embed.set_footer(text=f"{len(types)} of {MAX_TYPES} topics used")
        await reply(ctx, embed)

    @ticket.command(name="addtype", help="Add your own ticket topic.")
    @commands.has_permissions(administrator=True)
    async def ticket_addtype(self, ctx, *, text: str = None):
        if not text:
            return await reply(
                ctx,
                error_embed(
                    f"Tell me the name and a short line about it. "
                    f"Example: `{ctx.clean_prefix}ticket addtype Billing | Questions about payments`"
                ),
            )
        label, _, about = text.partition("|")
        label = clean_text(label, 30)
        about = clean_text(about, 80) or "Open a ticket for this topic"
        if len(label) < 2:
            return await reply(ctx, error_embed("The topic name must be at least 2 letters."))

        async with self.locks[ctx.guild.id]:
            settings = await get_settings(ctx.guild.id)
            types = get_types(settings)
            if len(types) >= MAX_TYPES:
                return await reply(
                    ctx, error_embed(f"You can have up to {MAX_TYPES} topics. Remove one first.")
                )
            taken = {item["label"].lower() for item in types}
            taken.add(CUSTOM_TOPIC["label"].lower())
            if label.lower() in taken:
                return await reply(ctx, error_embed("A topic with that name already exists."))
            types.append(
                {"key": uuid.uuid4().hex[:8], "label": label, "emoji": "🎫", "text": about}
            )
            await save_types(ctx.guild.id, types)

        shown = await refresh_panel(ctx.guild)
        note = (
            "The panel is updated."
            if shown
            else f"Post the panel again with `{ctx.clean_prefix}ticket setup` to show it."
        )
        await reply(ctx, success_embed(f"Added **{label}**. {note}"))

    @ticket.command(name="removetype", help="Remove a ticket topic.")
    @commands.has_permissions(administrator=True)
    async def ticket_removetype(self, ctx, *, name: str = None):
        if not name:
            return await reply(
                ctx,
                error_embed(
                    f"Tell me which topic to remove. Example: `{ctx.clean_prefix}ticket removetype Billing`"
                ),
            )

        async with self.locks[ctx.guild.id]:
            settings = await get_settings(ctx.guild.id)
            types = get_types(settings)
            target = None
            if name.strip().isdigit():
                number = int(name.strip())
                if 1 <= number <= len(types):
                    target = types[number - 1]
            else:
                wanted = clean_text(name, 30).lower()
                target = next((item for item in types if item["label"].lower() == wanted), None)
            if target is None:
                return await reply(
                    ctx,
                    error_embed(
                        f"I could not find that topic. See the list with `{ctx.clean_prefix}ticket types`."
                    ),
                )
            if len(types) == 1 and not custom_enabled(settings):
                return await reply(
                    ctx,
                    error_embed("Keep at least one topic, or turn custom tickets on first."),
                )
            types.remove(target)
            await save_types(ctx.guild.id, types)

        shown = await refresh_panel(ctx.guild)
        note = (
            "The panel is updated."
            if shown
            else f"Post the panel again with `{ctx.clean_prefix}ticket setup` to show it."
        )
        await reply(ctx, success_embed(f"Removed **{target['label']}**. {note}"))

    @ticket.command(name="custom", help="Turn custom tickets on or off.")
    @commands.has_permissions(administrator=True)
    async def ticket_custom(self, ctx, mode: str = None):
        settings = await get_settings(ctx.guild.id)
        if mode is None:
            state = "on" if custom_enabled(settings) else "off"
            return await reply(
                ctx,
                info_embed(
                    f"Custom tickets are **{state}**. "
                    f"Change it with `{ctx.clean_prefix}ticket custom on` or `off`."
                ),
            )
        mode = mode.lower()
        if mode not in ("on", "off"):
            return await reply(ctx, error_embed("Please say `on` or `off`."))
        enabled = mode == "on"

        async with self.locks[ctx.guild.id]:
            settings = await get_settings(ctx.guild.id)
            if not enabled and not get_types(settings):
                return await reply(
                    ctx, error_embed("Add at least one topic first, then turn custom tickets off.")
                )
            await tickets_col.update_one(
                {"_id": str(ctx.guild.id)},
                {"$set": {"custom_enabled": enabled}},
                upsert=True,
            )

        shown = await refresh_panel(ctx.guild)
        note = (
            "The panel is updated."
            if shown
            else f"Post the panel again with `{ctx.clean_prefix}ticket setup` to show it."
        )
        await reply(ctx, success_embed(f"Custom tickets are now **{mode}**. {note}"))

    @ticket.command(name="close", help="Close the ticket you are in.")
    async def ticket_close(self, ctx):
        problem = await begin_close(ctx.channel, ctx.author)
        if problem:
            return await reply(ctx, error_embed(problem))
        await reply(ctx, closing_embed())
        await finish_close(self.bot, ctx.channel, ctx.author)

    @ticket.command(name="add", help="Give someone access to this ticket.")
    @ctx_mod()
    async def ticket_add(self, ctx, member: discord.Member = None):
        if not member:
            return await reply(
                ctx,
                error_embed(f"Please mention a user. Example: `{ctx.clean_prefix}ticket add @user`"),
            )
        if not await get_ticket(ctx.channel):
            return await reply(ctx, error_embed("You can only use this inside an open ticket."))
        if member.bot:
            return await reply(ctx, error_embed("Bots cannot be added to tickets."))
        try:
            await ctx.channel.set_permissions(
                member, overwrite=chat_access(), reason=f"Added by {ctx.author}"
            )
        except discord.HTTPException:
            return await reply(
                ctx, error_embed("I could not change who can see this ticket.")
            )
        await reply(ctx, success_embed(f"{member.mention} can now see this ticket."))

    @ticket.command(name="remove", help="Take someone out of this ticket.")
    @ctx_mod()
    async def ticket_remove(self, ctx, member: discord.Member = None):
        if not member:
            return await reply(
                ctx,
                error_embed(
                    f"Please mention a user. Example: `{ctx.clean_prefix}ticket remove @user`"
                ),
            )
        ticket = await get_ticket(ctx.channel)
        if not ticket:
            return await reply(ctx, error_embed("You can only use this inside an open ticket."))
        if str(member.id) == ticket["owner_id"]:
            return await reply(
                ctx, error_embed("You cannot remove the person who opened this ticket.")
            )
        if member == ctx.guild.me:
            return await reply(ctx, error_embed("I cannot remove myself from the ticket."))
        settings = await get_settings(ctx.guild.id)
        if is_staff(member, settings):
            return await reply(
                ctx, error_embed("Team members always have access to tickets.")
            )
        try:
            await ctx.channel.set_permissions(
                member, overwrite=None, reason=f"Removed by {ctx.author}"
            )
        except discord.HTTPException:
            return await reply(
                ctx, error_embed("I could not change who can see this ticket.")
            )
        await reply(ctx, success_embed(f"{member.mention} can no longer see this ticket."))


async def setup(bot):
    await bot.add_cog(Tickets(bot))