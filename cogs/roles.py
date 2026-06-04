"""
cogs/roles.py — Reaction roles, button roles (premium), booster roles.
"""

import discord
from discord.ext import commands
from discord import app_commands
import re, asyncio

from utils.db import reaction_roles_col, button_roles_col, booster_roles_col, settings_col
from utils.helpers import BOT_OWNER_ID, ctx_admin, ctx_premium, log_event


# ── Persistent button roles view ──────────────────────────────────────────────
class ButtonRolesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)   # persists across restarts

    async def _toggle_role(self, interaction: discord.Interaction, role_id: int):
        role   = interaction.guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message("Role not found.", ephemeral=True)
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role, reason="Button Role")
            await interaction.response.send_message(f"Removed **{role.name}**.", ephemeral=True)
        else:
            await interaction.user.add_roles(role, reason="Button Role")
            await interaction.response.send_message(f"Added **{role.name}**.", ephemeral=True)


class DynamicButtonRole(discord.ui.Button):
    """A single button for one role. custom_id = 'btnrole_<role_id>'"""
    def __init__(self, label: str, role_id: int):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"btnrole_{role_id}"
        )
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id)
        if not role:
            return await interaction.response.send_message("Role not found.", ephemeral=True)
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role, reason="Button Role")
            await interaction.response.send_message(f"Removed **{role.name}**.", ephemeral=True)
        else:
            await interaction.user.add_roles(role, reason="Button Role")
            await interaction.response.send_message(f"Added **{role.name}**.", ephemeral=True)


def build_button_view(roles_data: list) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for item in roles_data:
        view.add_item(DynamicButtonRole(label=item["label"], role_id=int(item["role_id"])))
    return view


class Roles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Role add/remove ───────────────────────────────────────────────────────
    @commands.group(invoke_without_command=True)
    @ctx_admin()
    async def role(self, ctx):
        """Manage member roles. Sub-commands: add, remove"""
        await ctx.reply("Usage: `,role add @user @role` | `,role remove @user @role`")

    @role.command(name="add")
    @ctx_admin()
    async def role_add(self, ctx, member: discord.Member = None, role: discord.Role = None):
        """Add a role to a member."""
        if not member or not role:
            return await ctx.reply("Usage: `,role add @user @role`")
        if ctx.author.id != BOT_OWNER_ID and ctx.author.top_role <= role:
            return await ctx.reply("Cannot assign a role equal to or higher than yours.")
        if ctx.guild.me.top_role <= role:
            return await ctx.reply("My role is too low to assign that role.")
        await member.add_roles(role)
        await ctx.reply(f"Added {role.mention} to {member.mention}.")
        await log_event(self.bot, ctx.guild, "role_add", f"{role} added to {member} by {ctx.author}.")

    @role.command(name="remove")
    @ctx_admin()
    async def role_remove(self, ctx, member: discord.Member = None, role: discord.Role = None):
        """Remove a role from a member."""
        if not member or not role:
            return await ctx.reply("Usage: `,role remove @user @role`")
        if ctx.author.id != BOT_OWNER_ID and ctx.author.top_role <= role:
            return await ctx.reply("Cannot remove a role equal to or higher than yours.")
        await member.remove_roles(role)
        await ctx.reply(f"Removed {role.mention} from {member.mention}.")

    # ── Reaction roles ────────────────────────────────────────────────────────
    @commands.command(aliases=["rr"])
    @commands.has_permissions(manage_roles=True)
    async def reactionrole(self, ctx, action: str = None, message_link: str = None, emoji: str = None, role: discord.Role = None):
        """
        Set up a reaction role.
        Usage: `,reactionrole add <message_link> <emoji> @role`
        """
        if not action or action.lower() != "add" or not all([message_link, emoji, role]):
            return await ctx.reply(
                "Usage: `,reactionrole add <message_link> <emoji> @role`\n"
                "Example: `,reactionrole add https://discord.com/... 🎮 @Gamer`"
            )
        try:
            parts      = message_link.strip().split("/")
            channel_id = int(parts[-2])
            message_id = int(parts[-1])
        except:
            return await ctx.reply("Invalid message link.")
        try:
            ch  = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            msg = await ch.fetch_message(message_id)
            await msg.add_reaction(emoji)
        except Exception as e:
            return await ctx.reply(f"Error: {e}")

        await reaction_roles_col.update_one(
            {"message_id": str(message_id), "emoji": str(emoji)},
            {"$set": {"channel_id": str(channel_id), "guild_id": str(ctx.guild.id), "role_id": str(role.id)}},
            upsert=True
        )
        await ctx.reply(f"Reaction role set: {emoji} → {role.mention}")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        doc = await reaction_roles_col.find_one(
            {"message_id": str(payload.message_id), "emoji": str(payload.emoji)}
        )
        if doc:
            guild  = self.bot.get_guild(payload.guild_id)
            if not guild:
                return
            role   = guild.get_role(int(doc["role_id"]))
            member = payload.member or await guild.fetch_member(payload.user_id)
            if role and member:
                try:
                    await member.add_roles(role, reason="Reaction Role")
                except:
                    pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        doc = await reaction_roles_col.find_one(
            {"message_id": str(payload.message_id), "emoji": str(payload.emoji)}
        )
        if doc:
            guild = self.bot.get_guild(payload.guild_id)
            if not guild:
                return
            role  = guild.get_role(int(doc["role_id"]))
            try:
                member = await guild.fetch_member(payload.user_id)
            except:
                return
            if role and member:
                try:
                    await member.remove_roles(role, reason="Reaction Role removed")
                except:
                    pass

    # ── Button roles (Premium) ────────────────────────────────────────────────
    @commands.command(aliases=["br"])
    @commands.has_permissions(manage_roles=True)
    @ctx_premium()
    async def buttonrole(self, ctx, *, args: str = None):
        """
        Create a button role panel (Premium).
        Usage: `,buttonrole @role Label | @role2 Label2`
        """
        if not args:
            return await ctx.reply(
                "Usage: `,buttonrole @role Label | @role2 Label2`\n"
                "Example: `,buttonrole @Gamer Gaming | @Artist Art | @Music Music`"
            )
        entries    = [e.strip() for e in args.split("|")]
        roles_data = []
        for entry in entries:
            parts = entry.split()
            if not parts:
                continue
            role_m = parts[0]
            label  = " ".join(parts[1:]) or "Role"
            match  = re.search(r"\d+", role_m)
            if not match:
                continue
            role = ctx.guild.get_role(int(match.group()))
            if role:
                roles_data.append({"role_id": str(role.id), "label": label})

        if not roles_data:
            return await ctx.reply("No valid roles found.")

        # Save to DB for restart persistence
        await button_roles_col.update_one(
            {"guild_id": str(ctx.guild.id), "channel_id": str(ctx.channel.id)},
            {"$set": {"roles": roles_data}},
            upsert=True
        )

        view  = build_button_view(roles_data)
        embed = discord.Embed(
            title="Role Selection",
            description="Click a button to assign or remove a role.",
            color=0x2B2D31
        )
        await ctx.send(embed=embed, view=view)
        try:
            await ctx.message.delete()
        except:
            pass

    # ── Booster roles ─────────────────────────────────────────────────────────
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def boosterrole(self, ctx, role: discord.Role = None):
        """Set a reward role for server boosters."""
        if not role:
            return await ctx.reply("Usage: `,boosterrole @role`")
        await booster_roles_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"role_id": str(role.id)}},
            upsert=True
        )
        await ctx.reply(f"Booster reward role set to {role.mention}.")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.premium_since is None and after.premium_since is not None:
            doc = await booster_roles_col.find_one({"guild_id": str(after.guild.id)})
            if doc:
                role = after.guild.get_role(int(doc["role_id"]))
                if role:
                    await after.add_roles(role, reason="Server Boost reward")


async def setup(bot):
    await bot.add_cog(Roles(bot))