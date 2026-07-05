import copy
import discord
from discord.ext import commands
from utils.db import cmd_aliases_col
from utils.helpers import ctx_admin


class Aliases(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return

        content = message.content.strip()
        if not content:
            return

        prefix_used = None
        used_prefixes = await self.bot.get_prefix(message)
        if isinstance(used_prefixes, str):
            used_prefixes = [used_prefixes]

        for p in used_prefixes:
            if content.lower().startswith(p.lower()):
                prefix_used = content[: len(p)]
                break

        if not prefix_used:
            return

        remainder = content[len(prefix_used) :].strip()
        if not remainder:
            return

        parts = remainder.split(None, 1)
        alias_used = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        doc = await cmd_aliases_col.find_one(
            {"guild_id": str(message.guild.id), "alias": alias_used}
        )
        if not doc:
            return

        real_cmd = doc["command"]
        
        fake_message = copy.copy(message)
        fake_message.content = f"{prefix_used}{real_cmd}" + (
            f" {rest}" if rest else ""
        )

        new_ctx = await self.bot.get_context(fake_message)
        if new_ctx.valid:
            await self.bot.invoke(new_ctx)

    @commands.group(name="alias", invoke_without_command=True)
    @ctx_admin()
    async def alias(self, ctx):
        docs = await cmd_aliases_col.find({"guild_id": str(ctx.guild.id)}).to_list(
            50
        )
        if not docs:
            embed = discord.Embed(
                title="Command Aliases",
                description=(
                    "No aliases set up yet.\n\n"
                    "`,alias add <alias> <command>` — create an alias\n"
                    "`,alias remove <alias>` — delete an alias\n"
                    "`,alias list` — view all aliases"
                ),
                color=0x2B2D31,
            )
            return await ctx.reply(embed=embed)

        lines = [f"`{d['alias']}` → `{d['command']}`" for d in docs]
        embed = discord.Embed(
            title=f"Command Aliases — {ctx.guild.name}",
            description="\n".join(lines),
            color=0x2B2D31,
        )
        embed.set_footer(text=f"{len(docs)}/50 aliases used")
        await ctx.reply(embed=embed)

    @alias.command(name="add")
    @ctx_admin()
    async def alias_add(
        self, ctx, alias_name: str = None, *, command_name: str = None
    ):
        if not alias_name or not command_name:
            return await ctx.reply(
                embed=discord.Embed(
                    description="Usage: `,alias add <alias> <command>`\nExample: `,alias add bc balance`",
                    color=0xED4245,
                )
            )

        alias_name = alias_name.lower().strip()
        command_name = command_name.lower().strip()

        if len(alias_name) > 30:
            return await ctx.reply(
                "Alias name must be 30 characters or fewer."
            )

        real_cmd = self.bot.get_command(command_name)
        if not real_cmd:
            return await ctx.reply(
                embed=discord.Embed(
                    description=f"Command `{command_name}` not found.",
                    color=0xED4245,
                )
            )

        existing = await cmd_aliases_col.find_one(
            {"guild_id": str(ctx.guild.id), "alias": alias_name}
        )
        if existing:
            return await ctx.reply(
                embed=discord.Embed(
                    description=f"Alias `{alias_name}` already exists. Remove it first with `,alias remove {alias_name}`.",
                    color=0xED4245,
                )
            )

        count = await cmd_aliases_col.count_documents(
            {"guild_id": str(ctx.guild.id)}
        )
        if count >= 50:
            return await ctx.reply("Maximum 50 aliases per server.")

        await cmd_aliases_col.insert_one(
            {
                "guild_id": str(ctx.guild.id),
                "alias": alias_name,
                "command": command_name,
                "created_by": str(ctx.author.id),
            }
        )

        await ctx.reply(
            embed=discord.Embed(
                title="Alias Created",
                description=f"`{alias_name}` → `{command_name}`\n\nUse `,{alias_name}` to run `,{command_name}`.",
                color=0x57F287,
            )
        )

    @alias.command(name="remove", aliases=["delete", "del"])
    @ctx_admin()
    async def alias_remove(self, ctx, alias_name: str = None):
        if not alias_name:
            return await ctx.reply("Usage: `,alias remove <alias>`")

        result = await cmd_aliases_col.delete_one(
            {
                "guild_id": str(ctx.guild.id),
                "alias": alias_name.lower().strip(),
            }
        )

        if result.deleted_count:
            await ctx.reply(
                embed=discord.Embed(
                    description=f"Alias `{alias_name}` removed.", color=0x2B2D31
                )
            )
        else:
            await ctx.reply(
                embed=discord.Embed(
                    description=f"No alias named `{alias_name}` found.",
                    color=0xED4245,
                )
            )

    @alias.command(name="info")
    async def alias_info(self, ctx, alias_name: str = None):
        if not alias_name:
            return await ctx.reply("Usage: `,alias info <alias>`")

        doc = await cmd_aliases_col.find_one(
            {"guild_id": str(ctx.guild.id), "alias": alias_name.lower()}
        )
        if not doc:
            return await ctx.reply(
                embed=discord.Embed(
                    description=f"No alias named `{alias_name}` found.",
                    color=0xED4245,
                )
            )

        creator = ctx.guild.get_member(int(doc["created_by"]))
        embed = discord.Embed(
            title=f"Alias Info — {doc['alias']}", color=0x2B2D31
        )
        embed.add_field(name="Alias", value=f"`{doc['alias']}`", inline=True)
        embed.add_field(
            name="Points to", value=f"`{doc['command']}`", inline=True
        )
        embed.add_field(
            name="Created by",
            value=creator.mention if creator else f"`{doc['created_by']}`",
            inline=True,
        )
        await ctx.reply(embed=embed)

    @alias.command(name="clear")
    @ctx_admin()
    async def alias_clear(self, ctx):
        count = await cmd_aliases_col.count_documents(
            {"guild_id": str(ctx.guild.id)}
        )
        if count == 0:
            return await ctx.reply("No aliases to clear.")

        await cmd_aliases_col.delete_many({"guild_id": str(ctx.guild.id)})
        await ctx.reply(
            embed=discord.Embed(
                description=f"Cleared **{count}** alias(es) from this server.",
                color=0x2B2D31,
            )
        )


async def setup(bot):
    await bot.add_cog(Aliases(bot))