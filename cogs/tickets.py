"""
cogs/tickets.py — Multi-purpose ticket system with persistent views.
Types: General Help, Report, Join Staff, Server Event.
"""

import discord
from discord.ext import commands
import asyncio

from utils.db import tickets_col
from utils.helpers import ctx_mod, log_event

TICKET_TYPES = {
    "help":   "General Help",
    "report": "Report a User",
    "staff":  "Join the Staff",
    "event":  "Server Event",
}


# ── Persistent Views ─────────────────────────────────────────────────────────

class TicketTypeSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=label, value=key, description=f"Open a {label} ticket")
            for key, label in TICKET_TYPES.items()
        ]
        super().__init__(
            placeholder="Choose ticket type...",
            options=options,
            custom_id="ticket_type_select"
        )

    async def callback(self, interaction: discord.Interaction):
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
        label="Open a Ticket",
        style=discord.ButtonStyle.primary,
        emoji="📩",
        custom_id="ticket_open_btn"
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Select the type of support you need:",
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
                "This is not an active ticket.", ephemeral=True
            )
        await interaction.response.send_message("Closing ticket in 5 seconds...")
        await asyncio.sleep(5)
        await tickets_col.delete_one({"channel_id": str(interaction.channel.id)})
        try:
            await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        except:
            pass


# ── Ticket creation logic ─────────────────────────────────────────────────────

async def _create_ticket(interaction: discord.Interaction, ticket_type: str):
    guild  = interaction.guild
    member = interaction.user

    # Check for existing open ticket
    existing = await tickets_col.find_one(
        {"guild_id": str(guild.id), "owner_id": str(member.id), "active": True}
    )
    if existing:
        ch = guild.get_channel(int(existing["channel_id"]))
        if ch:
            return await interaction.followup.send(
                f"You already have an open ticket: {ch.mention}", ephemeral=True
            )

    # Increment counter
    guild_doc = await tickets_col.find_one({"_id": str(guild.id)}) or {}
    count     = guild_doc.get("ticket_count", 0) + 1
    await tickets_col.update_one(
        {"_id": str(guild.id)}, {"$set": {"ticket_count": count}}, upsert=True
    )

    label = TICKET_TYPES.get(ticket_type, ticket_type.title())

    # Channel permissions
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        member:             discord.PermissionOverwrite(
            read_messages=True, send_messages=True, attach_files=True
        ),
        guild.me:           discord.PermissionOverwrite(
            read_messages=True, send_messages=True, attach_files=True, manage_messages=True
        ),
    }
    # Staff role access
    if guild_doc.get("staff_role_id"):
        staff = guild.get_role(int(guild_doc["staff_role_id"]))
        if staff:
            overwrites[staff] = discord.PermissionOverwrite(
                read_messages=True, send_messages=True, attach_files=True
            )

    # Find or create ticket category
    category = discord.utils.get(guild.categories, name="Tickets")
    if not category:
        try:
            category = await guild.create_category(
                "Tickets",
                overwrites={guild.default_role: discord.PermissionOverwrite(read_messages=False)}
            )
        except:
            category = None

    try:
        ch = await guild.create_text_channel(
            name=f"{ticket_type}-{count:04d}",
            overwrites=overwrites,
            category=category,
            topic=f"{label} | {member} (ID: {member.id})"
        )
    except discord.Forbidden:
        return await interaction.followup.send(
            "Missing permissions to create ticket channel.", ephemeral=True
        )

    # Save to DB
    await tickets_col.insert_one({
        "guild_id":   str(guild.id),
        "channel_id": str(ch.id),
        "owner_id":   str(member.id),
        "active":     True,
        "type":       ticket_type
    })

    # Welcome embed inside ticket
    embed = discord.Embed(
        title=f"Ticket #{count:04d} — {label}",
        description=(
            f"Welcome {member.mention}.\n"
            "Describe your issue and a staff member will assist you.\n\n"
            "**Staff commands:**\n"
            "`,ticket add @user` | `,ticket remove @user` | `,ticket close`"
        ),
        color=0x2B2D31
    )
    embed.set_footer(text="Use the button below or ,ticket close when done.")
    await ch.send(content=member.mention, embed=embed, view=TicketCloseView())

    try:
        await interaction.followup.send(f"Ticket opened: {ch.mention}", ephemeral=True)
    except:
        try:
            await interaction.response.send_message(f"Ticket opened: {ch.mention}", ephemeral=True)
        except:
            pass


# ── Cog ───────────────────────────────────────────────────────────────────────

class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(invoke_without_command=True)
    async def ticket(self, ctx):
        """Ticket system management. Sub-commands: setup, close, add, remove, staffrole"""
        embed = discord.Embed(title="Ticket System", color=0x2B2D31)
        embed.add_field(
            name="Admin Commands",
            value=(
                "`,ticket setup` — send the ticket panel\n"
                "`,ticket staffrole @role` — set staff role\n"
                "`,ticket close` — close & delete ticket channel"
            ),
            inline=False
        )
        embed.add_field(
            name="Inside Ticket",
            value=(
                "`,ticket add @user` — add user to ticket\n"
                "`,ticket remove @user` — remove user from ticket"
            ),
            inline=False
        )
        embed.add_field(
            name="Ticket Types",
            value="\n".join(f"`{k}` — {v}" for k, v in TICKET_TYPES.items()),
            inline=False
        )
        await ctx.reply(embed=embed)

    @ticket.command(name="setup")
    @commands.has_permissions(administrator=True)
    async def ticket_setup(self, ctx):
        """Send the ticket creation panel to this channel."""
        embed = discord.Embed(
            title="Support Tickets",
            description=(
                "Need help? Click the button below to open a support ticket.\n"
                "Choose the type of ticket that best matches your need."
            ),
            color=0x2B2D31
        )
        embed.set_footer(text="Only open tickets for valid reasons.")
        await ctx.send(embed=embed, view=TicketCreateView())
        try:
            await ctx.message.delete()
        except:
            pass

    @ticket.command(name="close")
    @ctx_mod()
    async def ticket_close(self, ctx):
        """Close and delete this ticket channel."""
        ticket = await tickets_col.find_one(
            {"channel_id": str(ctx.channel.id), "active": True}
        )
        if not ticket:
            return await ctx.reply("This is not an active ticket channel.")
        await ctx.reply("Closing in 5 seconds...")
        await asyncio.sleep(5)
        await tickets_col.delete_one({"channel_id": str(ctx.channel.id)})
        await log_event(self.bot, ctx.guild, "ticket_close", f"Ticket closed by {ctx.author}.")
        try:
            await ctx.channel.delete(reason=f"Ticket closed by {ctx.author}")
        except:
            pass

    @ticket.command(name="add")
    @ctx_mod()
    async def ticket_add(self, ctx, member: discord.Member = None):
        """Add a user to this ticket."""
        if not member:
            return await ctx.reply("Mention a member: `,ticket add @user`")
        ticket = await tickets_col.find_one({"channel_id": str(ctx.channel.id), "active": True})
        if not ticket:
            return await ctx.reply("Not an active ticket channel.")
        await ctx.channel.set_permissions(
            member, read_messages=True, send_messages=True, attach_files=True
        )
        await ctx.reply(f"Added {member.mention} to this ticket.")

    @ticket.command(name="remove")
    @ctx_mod()
    async def ticket_remove(self, ctx, member: discord.Member = None):
        """Remove a user from this ticket."""
        if not member:
            return await ctx.reply("Mention a member: `,ticket remove @user`")
        ticket = await tickets_col.find_one({"channel_id": str(ctx.channel.id), "active": True})
        if not ticket:
            return await ctx.reply("Not an active ticket channel.")
        await ctx.channel.set_permissions(member, overwrite=None)
        await ctx.reply(f"Removed {member.mention} from this ticket.")

    @ticket.command(name="staffrole")
    @commands.has_permissions(administrator=True)
    async def ticket_staffrole(self, ctx, role: discord.Role = None):
        """Set the staff role that can see all tickets."""
        if not role:
            return await ctx.reply("Mention a role: `,ticket staffrole @Staff`")
        await tickets_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"staff_role_id": str(role.id)}},
            upsert=True
        )
        await ctx.reply(f"Staff role set to {role.mention}. They will see all new tickets.")


async def setup(bot):
    await bot.add_cog(Tickets(bot))
