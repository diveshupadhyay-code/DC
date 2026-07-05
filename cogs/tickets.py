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

class TicketTypeSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=label, value=key, description=f"Click to open a {label} ticket")
            for key, label in TICKET_TYPES.items()
        ]
        super().__init__(
            placeholder="Select a department...",
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
        label="Create Ticket",
        style=discord.ButtonStyle.secondary,
        emoji="✉️",
        custom_id="ticket_open_btn"
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Select the department you need to reach:",
            view=TicketSelectView(),
            ephemeral=True
        )


class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Request",
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
        await interaction.response.send_message("Closing this ticket in 5 seconds...")
        await asyncio.sleep(5)
        await tickets_col.delete_one({"channel_id": str(interaction.channel.id)})
        try:
            await interaction.channel.delete(reason=f"Closed by {interaction.user}")
        except:
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
                f"You already have an open ticket right here: {ch.mention}", ephemeral=True
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
            read_messages=True, send_messages=True, attach_files=True
        ),
        guild.me: discord.PermissionOverwrite(
            read_messages=True, send_messages=True, attach_files=True, manage_messages=True
        ),
    }
    
    if guild_doc.get("staff_role_id"):
        staff = guild.get_role(int(guild_doc["staff_role_id"]))
        if staff:
            overwrites[staff] = discord.PermissionOverwrite(
                read_messages=True, send_messages=True, attach_files=True
            )

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
            name=f"ticket-{count:04d}",
            overwrites=overwrites,
            category=category,
            topic=f"{label} | Opened by {member}"
        )
    except discord.Forbidden:
        return await interaction.followup.send(
            "I don't have the required permissions to create a channel for you.", ephemeral=True
        )

    await tickets_col.insert_one({
        "guild_id": str(guild.id),
        "channel_id": str(ch.id),
        "owner_id": str(member.id),
        "active": True,
        "type": ticket_type
    })

    embed = discord.Embed(
        title=f"Ticket Support — {label}",
        description=(
            f"Hello {member.mention},\n\n"
            "Thank you for reaching out. Please state your question or issue in detail below, "
            "and our team will be with you shortly.\n\n"
            "**Useful Commands:**\n"
            "`,ticket add @user` ➔ Give access to a friend\n"
            "`,ticket remove @user` ➔ Remove access for a user\n"
            "`,ticket close` ➔ Shut down this channel"
        ),
        color=0x2B2D31
    )
    embed.set_footer(text="Click the button below to close this inquiry anytime.")
    await ch.send(content=member.mention, embed=embed, view=TicketCloseView())

    try:
        await interaction.followup.send(f"Your ticket has been created: {ch.mention}", ephemeral=True)
    except:
        try:
            await interaction.response.send_message(f"Your ticket has been created: {ch.mention}", ephemeral=True)
        except:
            pass


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(invoke_without_command=True)
    async def ticket(self, ctx):
        embed = discord.Embed(
            title="Ticket Management System", 
            description="Use the sub-commands below to manage server support requests.",
            color=0x2B2D31
        )
        embed.add_field(
            name="🛠️ Staff Tools",
            value=(
                "`,ticket setup` ➔ Drop the ticket setup panel here\n"
                "`,ticket staffrole @role` ➔ Set the designated role to view tickets\n"
                "`,ticket close` ➔ Close and delete the current ticket"
            ),
            inline=False
        )
        embed.add_field(
            name="👥 Member Management",
            value=(
                "`,ticket add @user` ➔ Invite a user into the active ticket\n"
                "`,ticket remove @user` ➔ Remove a user from the active ticket"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @ticket.command(name="setup")
    @commands.has_permissions(administrator=True)
    async def ticket_setup(self, ctx):
        embed = discord.Embed(
            title="Contact Server Support",
            description=(
                "Need assistance? Click the button below to connect with our team.\n\n"
                "Please make sure to choose the most relevant category so we can assist you faster."
            ),
            color=0x2B2D31
        )
        await ctx.send(embed=embed, view=TicketCreateView())
        try:
            await ctx.message.delete()
        except:
            pass

    @ticket.command(name="close")
    @ctx_mod()
    async def ticket_close(self, ctx):
        ticket = await tickets_col.find_one(
            {"channel_id": str(ctx.channel.id), "active": True}
        )
        if not ticket:
            return await ctx.reply("This channel isn't an active ticket.")
        await ctx.reply("Closing this channel in 5 seconds...")
        await asyncio.sleep(5)
        await tickets_col.delete_one({"channel_id": str(ctx.channel.id)})
        await log_event(self.bot, ctx.guild, "ticket_close", f"Ticket closed by {ctx.author}.")
        try:
            await ctx.channel.delete(reason=f"Closed by {ctx.author}")
        except:
            pass

    @ticket.command(name="add")
    @ctx_mod()
    async def ticket_add(self, ctx, member: discord.Member = None):
        if not member:
            return await ctx.reply("Please specify a user. Example: `,ticket add @username`")
        ticket = await tickets_col.find_one({"channel_id": str(ctx.channel.id), "active": True})
        if not ticket:
            return await ctx.reply("This command can only be used inside an active ticket channel.")
        await ctx.channel.set_permissions(
            member, read_messages=True, send_messages=True, attach_files=True
        )
        await ctx.reply(f"Added {member.mention} to this ticket thread.")

    @ticket.command(name="remove")
    @ctx_mod()
    async def ticket_remove(self, ctx, member: discord.Member = None):
        if not member:
            return await ctx.reply("Please specify a user. Example: `,ticket remove @username`")
        ticket = await tickets_col.find_one({"channel_id": str(ctx.channel.id), "active": True})
        if not ticket:
            return await ctx.reply("This command can only be used inside an active ticket channel.")
        await ctx.channel.set_permissions(member, overwrite=None)
        await ctx.reply(f"Removed {member.mention} from this ticket thread.")

    @ticket.command(name="staffrole")
    @commands.has_permissions(administrator=True)
    async def ticket_staffrole(self, ctx, role: discord.Role = None):
        if not role:
            return await ctx.reply("Please mention a role. Example: `,ticket staffrole @Staff`")
        await tickets_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"staff_role_id": str(role.id)}},
            upsert=True
        )
        await ctx.reply(f"Success! {role.mention} has been configured as the server support team.")


async def setup(bot):
    await bot.add_cog(Tickets(bot))