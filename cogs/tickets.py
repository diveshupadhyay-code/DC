import discord
from discord.ext import commands
import asyncio

from utils.db import tickets_col
from utils.helpers import ctx_mod, log_event

TICKET_TYPES = {
    "help": "General Support",
    "report": "Player Report",
    "staff": "Staff Application",
    "event": "Event Inquiry",
}

def build_embed(title: str, description: str = None, color: int = 0x2B2D31) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="Support Dashboard")
    return embed

class TicketTypeSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=label, value=key, description=f"Open a {label} ticket")
            for key, label in TICKET_TYPES.items()
        ]
        super().__init__(
            placeholder="Choose support department...",
            options=options,
            custom_id="ticket_type_select"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        ticket_type = self.values[0]
        await _create_ticket(interaction, ticket_type)

class TicketSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect())

class TicketCreateView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create Ticket",
        style=discord.ButtonStyle.secondary,
        emoji="✉️",
        custom_id="ticket_open_btn"
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Select the department you need help from:",
            view=TicketSelectView(),
            ephemeral=True
        )

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="ticket_close_btn"
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = await tickets_col.find_one(
            {"channel_id": str(interaction.channel.id), "active": True}
        )
        if not ticket:
            return await interaction.response.send_message(
                "This channel is not an active ticket.", ephemeral=True
            )
        await interaction.response.send_message("Closing ticket in 5 seconds...")
        await asyncio.sleep(5)
        await tickets_col.delete_one({"channel_id": str(interaction.channel.id)})
        try:
            await interaction.channel.delete(reason=f"Closed by {interaction.user}")
        except discord.HTTPException:
            pass

async def _create_ticket(interaction: discord.Interaction, ticket_type: str):
    guild = interaction.guild
    member = interaction.user

    existing = await tickets_col.find_one(
        {"guild_id": str(guild.id), "owner_id": str(member.id), "active": True}
    )
    if existing:
        ch = guild.get_channel(int(existing["channel_id"]))
        if ch:
            return await interaction.followup.send(
                f"You already have an open ticket here: {ch.mention}", ephemeral=True
            )

    guild_doc = await tickets_col.find_one({"_id": str(guild.id)}) or {}
    count = guild_doc.get("ticket_count", 0) + 1
    await tickets_col.update_one(
        {"_id": str(guild.id)}, {"$set": {"ticket_count": count}}, upsert=True
    )

    label = TICKET_TYPES.get(ticket_type, ticket_type.title())

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        member: discord.PermissionOverwrite(
            read_messages=True, send_messages=True, attach_files=True, embed_links=True
        ),
        guild.me: discord.PermissionOverwrite(
            read_messages=True, send_messages=True, attach_files=True, manage_channels=True, manage_messages=True
        ),
    }
    
    if guild_doc.get("staff_role_id"):
        staff = guild.get_role(int(guild_doc["staff_role_id"]))
        if staff:
            overwrites[staff] = discord.PermissionOverwrite(
                read_messages=True, send_messages=True, attach_files=True, embed_links=True
            )

    category = discord.utils.get(guild.categories, name="Tickets")
    if not category:
        try:
            category = await guild.create_category(
                "Tickets",
                overwrites={guild.default_role: discord.PermissionOverwrite(read_messages=False)}
            )
        except discord.Forbidden:
            category = None

    try:
        ch = await guild.create_text_channel(
            name=f"ticket-{count:04d}",
            overwrites=overwrites,
            category=category,
            topic=f"{label} | Created by {member}"
        )
    except discord.Forbidden:
        return await interaction.followup.send(
            "I lack permissions to create channel. Please give me Manage Channels permission.", ephemeral=True
        )

    await tickets_col.insert_one({
        "guild_id": str(guild.id),
        "channel_id": str(ch.id),
        "owner_id": str(member.id),
        "active": True,
        "type": ticket_type
    })

    embed = build_embed(
        f"Ticket Support — {label}",
        (
            f"Hello {member.mention},\n\n"
            "Welcome! Please describe your issue in detail. "
            "Our team will assist you shortly.\n\n"
            "**Commands:**\n"
            "`,ticket add @user` • Add someone to ticket\n"
            "`,ticket remove @user` • Remove someone from ticket\n"
            "`,ticket close` • Close this channel"
        )
    )
    await ch.send(content=member.mention, embed=embed, view=TicketCloseView())
    await interaction.followup.send(f"Your ticket is ready: {ch.mention}", ephemeral=True)

class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(invoke_without_command=True)
    async def ticket(self, ctx):
        embed = build_embed(
            "⚙️ Ticket System Control Panel",
            "Use the commands below to configure and control support tickets."
        )
        embed.add_field(
            name="🛠️ Admin & Staff Commands",
            value=(
                "`,ticket setup` • Send ticket creation panel\n"
                "`,ticket staffrole @role` • Set staff team role\n"
                "`,ticket close` • Close ticket channel"
            ),
            inline=False
        )
        embed.add_field(
            name="👥 Member Management",
            value=(
                "`,ticket add @user` • Give a user access to ticket\n"
                "`,ticket remove @user` • Remove user access from ticket"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @ticket.command(name="setup")
    @commands.has_permissions(administrator=True)
    async def ticket_setup(self, ctx):
        embed = build_embed(
            "📩 Contact Server Support",
            "Click the button below to start a ticket with our support team.\n\n"
            "Please pick the right category so we can help you faster!"
        )
        await ctx.send(embed=embed, view=TicketCreateView())
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @ticket.command(name="close")
    @ctx_mod()
    async def ticket_close(self, ctx):
        ticket = await tickets_col.find_one(
            {"channel_id": str(ctx.channel.id), "active": True}
        )
        if not ticket:
            return await ctx.reply(embed=build_embed("❌ Error", "This channel is not an active ticket."))
        
        await ctx.reply(embed=build_embed("🔒 Closing", "Closing this ticket channel in 5 seconds..."))
        await asyncio.sleep(5)
        await tickets_col.delete_one({"channel_id": str(ctx.channel.id)})
        await log_event(self.bot, ctx.guild, "ticket_close", f"Ticket closed by {ctx.author}.")
        try:
            await ctx.channel.delete(reason=f"Closed by {ctx.author}")
        except discord.HTTPException:
            pass

    @ticket.command(name="add")
    @ctx_mod()
    async def ticket_add(self, ctx, member: discord.Member = None):
        if not member:
            return await ctx.reply(embed=build_embed("❌ Error", "Please mention a user. Example: `,ticket add @user`"))
        ticket = await tickets_col.find_one({"channel_id": str(ctx.channel.id), "active": True})
        if not ticket:
            return await ctx.reply(embed=build_embed("❌ Error", "You can only use this command inside an active ticket."))
        
        await ctx.channel.set_permissions(
            member, read_messages=True, send_messages=True, attach_files=True, embed_links=True
        )
        await ctx.reply(embed=build_embed("✅ Done", f"Added {member.mention} to this ticket."))

    @ticket.command(name="remove")
    @ctx_mod()
    async def ticket_remove(self, ctx, member: discord.Member = None):
        if not member:
            return await ctx.reply(embed=build_embed("❌ Error", "Please mention a user. Example: `,ticket remove @user`"))
        ticket = await tickets_col.find_one({"channel_id": str(ctx.channel.id), "active": True})
        if not ticket:
            return await ctx.reply(embed=build_embed("❌ Error", "You can only use this command inside an active ticket."))
        
        await ctx.channel.set_permissions(member, overwrite=None)
        await ctx.reply(embed=build_embed("✅ Done", f"Removed {member.mention} from this ticket."))

    @ticket.command(name="staffrole")
    @commands.has_permissions(administrator=True)
    async def ticket_staffrole(self, ctx, role: discord.Role = None):
        if not role:
            return await ctx.reply(embed=build_embed("❌ Error", "Please mention a role. Example: `,ticket staffrole @Staff`"))
        
        await tickets_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"staff_role_id": str(role.id)}},
            upsert=True
        )
        await ctx.reply(embed=build_embed("✅ Success", f"Configured {role.mention} as the official support team role."))

async def setup(bot):
    await bot.add_cog(Tickets(bot))