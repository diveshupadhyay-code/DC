"""
cogs/leveling.py — XP, levelling, leaderboard, level rewards.
Note: XP awarding happens in core.py to keep on_message clean.
This cog owns the commands.
"""

import discord
from discord.ext import commands
from discord import app_commands

from utils.db import levels_col, settings_col
from utils.helpers import ctx_admin


class Leveling(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(aliases=["rank", "xp"])
    async def level(self, ctx, member: discord.Member = None):
        """Check your level and XP progress."""
        member = member or ctx.author
        doc    = await levels_col.find_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
        if not doc:
            return await ctx.reply(f"**{member.display_name}** hasn't earned any XP yet.")
        lvl = doc.get("level", 0)
        xp  = doc.get("xp",    0)
        nxt = (lvl + 1) * 100
        pct = int((xp / nxt) * 10)
        bar = "█" * pct + "░" * (10 - pct)
        embed = discord.Embed(title=f"Level — {member.display_name}", color=0x2B2D31)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Level",    value=f"**{lvl}**",     inline=True)
        embed.add_field(name="XP",       value=f"{xp} / {nxt}", inline=True)
        embed.add_field(name="Progress", value=f"`{bar}`",       inline=False)
        await ctx.reply(embed=embed)

    @app_commands.command(name="level", description="Check your level and XP")
    async def slash_level(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        doc    = await levels_col.find_one({"guild_id": str(interaction.guild.id), "user_id": str(member.id)})
        if not doc:
            return await interaction.response.send_message(f"No XP yet for **{member}**.", ephemeral=True)
        lvl = doc.get("level", 0)
        xp  = doc.get("xp",    0)
        nxt = (lvl + 1) * 100
        embed = discord.Embed(
            description=f"**{member.display_name}** — Level **{lvl}** ({xp}/{nxt} XP)",
            color=0x2B2D31
        )
        await interaction.response.send_message(embed=embed)

    @commands.command(aliases=["lb"])
    async def leaderboard(self, ctx):
        """View the top 10 most active members by level."""
        cursor = levels_col.find({"guild_id": str(ctx.guild.id)}).sort("level", -1).limit(10)
        docs   = await cursor.to_list(10)
        if not docs:
            return await ctx.reply("No level data yet in this server.")
        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for i, doc in enumerate(docs, 1):
            m    = ctx.guild.get_member(int(doc["user_id"]))
            name = m.display_name if m else f"Unknown ({doc['user_id']})"
            pos  = medals[i - 1] if i <= 3 else f"`{i}.`"
            lines.append(f"{pos} **{name}** — Level {doc.get('level', 0)} ({doc.get('xp', 0)} XP)")
        embed = discord.Embed(
            title=f"Leaderboard — {ctx.guild.name}",
            description="\n".join(lines),
            color=0x2B2D31
        )
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        await ctx.reply(embed=embed)

    @commands.command()
    @ctx_admin()
    async def resetxp(self, ctx, member: discord.Member = None):
        """Reset XP for a member or the whole server (Admin only)."""
        if member:
            await levels_col.delete_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)})
            await ctx.reply(f"XP reset for **{member}**.")
        else:
            await levels_col.delete_many({"guild_id": str(ctx.guild.id)})
            await ctx.reply("All server XP data has been reset.")

    @commands.command()
    @ctx_admin()
    async def setlevel(self, ctx, member: discord.Member = None, lvl: int = None):
        """Set a member's level directly (Admin only)."""
        if not member or lvl is None:
            return await ctx.reply("Usage: `,setlevel @user <level>`")
        if lvl < 0:
            return await ctx.reply("Level cannot be negative.")
        await levels_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(member.id)},
            {"$set": {"level": lvl, "xp": 0}},
            upsert=True
        )
        await ctx.reply(f"Level of **{member.display_name}** set to **{lvl}**.")


async def setup(bot):
    await bot.add_cog(Leveling(bot))
