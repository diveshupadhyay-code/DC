import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import re

from utils.helpers import ctx_admin, BOT_OWNER_ID
from cogs.hc_features import get_costs, try_charge

EMOJI_PATTERN = re.compile(r"<(a?):([a-zA-Z0-9_]{2,32}):(\d{15,20})>")
EMBED_COLOR = 0x2B2D31
SUCCESS_COLOR = 0x57F287
ERROR_COLOR = 0xED4245


async def _fetch_emoji_bytes(session: aiohttp.ClientSession, emoji_id: int, animated: bool) -> bytes | None:
    ext = "gif" if animated else "png"
    url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
    async with session.get(url) as resp:
        if resp.status == 200:
            return await resp.read()
    if not animated:
        return None
    url = f"https://cdn.discordapp.com/emojis/{emoji_id}.png"
    async with session.get(url) as resp:
        if resp.status == 200:
            return await resp.read()
    return None


def _slot_counts(guild: discord.Guild) -> tuple[int, int, int]:
    static = sum(1 for e in guild.emojis if not e.animated)
    animated = sum(1 for e in guild.emojis if e.animated)
    return static, animated, guild.emoji_limit


class EmojiPickSelect(discord.ui.Select):
    def __init__(self, results: list[discord.Emoji]):
        options = [
            discord.SelectOption(
                label=f":{e.name}:",
                value=str(e.id),
                emoji=e
            )
            for e in results[:25]
        ]
        super().__init__(placeholder="Select an emoji to add...", options=options)

    async def callback(self, interaction: discord.Interaction):
        view: "EmojiPickView" = self.view
        if interaction.user.id != view.author_id:
            return await interaction.response.send_message(
                "This picker is not for you.", ephemeral=True
            )
        
        emoji_id = int(self.values[0])
        source = interaction.client.get_emoji(emoji_id)
        
        if not source:
            return await interaction.response.edit_message(
                embed=discord.Embed(description="❌ Emoji is no longer available.", color=ERROR_COLOR),
                view=None
            )

        guild = interaction.guild
        if not guild.me.guild_permissions.manage_emojis_and_stickers:
            return await interaction.response.edit_message(
                embed=discord.Embed(description="❌ I need **Manage Emojis and Stickers** permission.", color=ERROR_COLOR),
                view=None
            )

        static, animated, limit = _slot_counts(guild)
        if source.animated and animated >= limit:
            return await interaction.response.edit_message(
                embed=discord.Embed(description="❌ No animated emoji slots remaining.", color=ERROR_COLOR),
                view=None
            )
        if not source.animated and static >= limit:
            return await interaction.response.edit_message(
                embed=discord.Embed(description="❌ No static emoji slots remaining.", color=ERROR_COLOR),
                view=None
            )
        if discord.utils.get(guild.emojis, name=source.name):
            return await interaction.response.edit_message(
                embed=discord.Embed(description=f"❌ An emoji named `{source.name}` already exists.", color=ERROR_COLOR),
                view=None
            )

        await interaction.response.defer()
        async with aiohttp.ClientSession() as session:
            img = await _fetch_emoji_bytes(session, source.id, source.animated)

        if not img:
            return await interaction.edit_original_response(
                embed=discord.Embed(description="❌ Failed to download the emoji.", color=ERROR_COLOR),
                view=None
            )

        try:
            new_emoji = await guild.create_custom_emoji(
                name=source.name, image=img, reason=f"Added by {interaction.user}"
            )
        except discord.Forbidden:
            return await interaction.edit_original_response(
                embed=discord.Embed(description="❌ Missing permissions to add this emoji.", color=ERROR_COLOR),
                view=None
            )
        except discord.HTTPException as e:
            return await interaction.edit_original_response(
                embed=discord.Embed(description=f"❌ Failed to add emoji: `{e}`", color=ERROR_COLOR),
                view=None
            )

        await interaction.edit_original_response(
            embed=discord.Embed(
                description=f"✅ Successfully added {new_emoji} as `:{new_emoji.name}:`",
                color=SUCCESS_COLOR
            ),
            view=None
        )


class EmojiPickView(discord.ui.View):
    def __init__(self, author_id: int, results: list[discord.Emoji]):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.add_item(EmojiPickSelect(results))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if hasattr(self, "message") and self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class Emotes(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="emoji", aliases=["emote", "emojis"], invoke_without_command=True)
    @ctx_admin()
    async def emoji(self, ctx):
        static, animated, limit = _slot_counts(ctx.guild)
        embed = discord.Embed(title="Emoji Manager", color=EMBED_COLOR)
        embed.add_field(name="Static Slots", value=f"`{static}/{limit}`", inline=True)
        embed.add_field(name="Animated Slots", value=f"`{animated}/{limit}`", inline=True)
        embed.add_field(name="Boost Level", value=f"Level `{ctx.guild.premium_tier}`", inline=True)
        embed.add_field(
            name="Available Commands",
            value=(
                "`,emoji steal <emojis>` • Copy emojis into this server\n"
                "`,emoji search <name>` • Search emojis across all available servers\n"
                "`,emoji addid <id> <name>` • Add an emoji using its raw ID\n"
                "`,emoji remove <emoji>` • Remove an emoji from this server\n"
                "`,emoji rename <emoji> <name>` • Rename an existing emoji\n"
                "`,emoji list` • View server emoji slots"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @emoji.command(name="steal", aliases=["add", "copy"])
    @ctx_admin()
    async def emoji_steal(self, ctx, *, args: str = None):
        matches = EMOJI_PATTERN.findall(ctx.message.content)
        if not matches:
            return await ctx.reply("Usage: `,emoji steal <paste custom emojis>`")
            
        if not ctx.guild.me.guild_permissions.manage_emojis_and_stickers:
            return await ctx.reply("I need **Manage Emojis and Stickers** permission to do that.")

        static, animated, limit = _slot_counts(ctx.guild)
        added, skipped, failed = [], [], []

        async with aiohttp.ClientSession() as session:
            for animated_flag, name, eid_str in matches:
                is_animated = bool(animated_flag)
                eid = int(eid_str)

                if discord.utils.get(ctx.guild.emojis, name=name):
                    skipped.append(name)
                    continue
                if is_animated and animated >= limit:
                    failed.append(f"{name} (no animated slots)")
                    continue
                if not is_animated and static >= limit:
                    failed.append(f"{name} (no static slots)")
                    continue

                img = await _fetch_emoji_bytes(session, eid, is_animated)
                if not img:
                    failed.append(f"{name} (download failed)")
                    continue

                costs = await get_costs()
                if not await try_charge(ctx.author.id, costs["emoji_cost"]):
                    failed.append(f"{name} (insufficient balance)")
                    continue

                try:
                    new_emoji = await ctx.guild.create_custom_emoji(
                        name=name[:32], image=img, reason=f"Stolen by {ctx.author}"
                    )
                    added.append(str(new_emoji))
                except discord.Forbidden:
                    failed.append(f"{name} (forbidden)")
                    from cogs.economy import add_wallet
                    await add_wallet(ctx.author.id, costs["emoji_cost"])
                except discord.HTTPException as e:
                    failed.append(f"{name} ({e.text if hasattr(e, 'text') else 'error'})")

                await asyncio.sleep(0.5)

        embed = discord.Embed(
            title="Emoji Steal Results",
            color=SUCCESS_COLOR if added else ERROR_COLOR
        )
        if added:
            embed.add_field(name=f"Added ({len(added)})", value=" ".join(added), inline=False)
        if skipped:
            embed.add_field(name=f"Skipped ({len(skipped)})", value=", ".join(f"`{n}`" for n in skipped), inline=False)
        if failed:
            embed.add_field(name=f"Failed ({len(failed)})", value=", ".join(f"`{n}`" for n in failed), inline=False)
            
        await ctx.reply(embed=embed)

    @emoji.command(name="addid")
    @ctx_admin()
    async def emoji_addid(self, ctx, emoji_id: int = None, *, name: str = None):
        if not emoji_id or not name:
            return await ctx.reply("Usage: `,emoji addid <emoji_id> <name>`")
            
        if not ctx.guild.me.guild_permissions.manage_emojis_and_stickers:
            return await ctx.reply("I need **Manage Emojis and Stickers** permission.")

        static, animated, limit = _slot_counts(ctx.guild)
        if discord.utils.get(ctx.guild.emojis, name=name):
            return await ctx.reply(f"An emoji named `{name}` already exists.")

        async with aiohttp.ClientSession() as session:
            source = self.bot.get_emoji(emoji_id)
            is_animated = source.animated if source else False
            img = await _fetch_emoji_bytes(session, emoji_id, is_animated)
            if not img and not is_animated:
                img = await _fetch_emoji_bytes(session, emoji_id, True)
                is_animated = bool(img)

        if not img:
            return await ctx.reply("Could not find or download that emoji.")

        if is_animated and animated >= limit:
            return await ctx.reply("No animated emoji slots left.")
        if not is_animated and static >= limit:
            return await ctx.reply("No static emoji slots left.")

        try:
            new_emoji = await ctx.guild.create_custom_emoji(
                name=name[:32], image=img, reason=f"Added by {ctx.author}"
            )
        except discord.Forbidden:
            return await ctx.reply("Missing permissions to add this emoji.")
        except discord.HTTPException as e:
            return await ctx.reply(f"Failed to add emoji: `{e}`")

        await ctx.reply(embed=discord.Embed(
            description=f"✅ Added {new_emoji} as `:{new_emoji.name}:`",
            color=SUCCESS_COLOR
        ))

    @emoji.command(name="search", aliases=["find"])
    @ctx_admin()
    async def emoji_search(self, ctx, *, query: str = None):
        if not query:
            return await ctx.reply("Usage: `,emoji search <name>`")

        q = query.lower()
        results = []
        for guild in self.bot.guilds:
            for e in guild.emojis:
                if q in e.name.lower():
                    results.append(e)
            if len(results) >= 25:
                break

        if not results:
            return await ctx.reply(f"No emojis found matching `{query}`.")

        embed = discord.Embed(
            title="Emoji Search",
            description=f"Found **{len(results)}** match(es) for `{query}`.\nSelect an emoji below to add it to this server.",
            color=EMBED_COLOR
        )
        view = EmojiPickView(ctx.author.id, results)
        view.message = await ctx.reply(embed=embed, view=view)

    @emoji.command(name="remove", aliases=["delete"])
    @ctx_admin()
    async def emoji_remove(self, ctx, emoji: discord.Emoji = None):
        if not emoji:
            return await ctx.reply("Usage: `,emoji remove <emoji>`")
            
        if emoji.guild_id != ctx.guild.id:
            return await ctx.reply("That emoji does not belong to this server.")
            
        try:
            await emoji.delete(reason=f"Removed by {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply("Missing permissions to remove that emoji.")
            
        await ctx.reply(embed=discord.Embed(
            description=f"🗑️ Removed `:{emoji.name}:`", color=EMBED_COLOR
        ))

    @emoji.command(name="rename")
    @ctx_admin()
    async def emoji_rename(self, ctx, emoji: discord.Emoji = None, *, new_name: str = None):
        if not emoji or not new_name:
            return await ctx.reply("Usage: `,emoji rename <emoji> <new_name>`")
            
        if emoji.guild_id != ctx.guild.id:
            return await ctx.reply("That emoji does not belong to this server.")
            
        old_name = emoji.name
        try:
            await emoji.edit(name=new_name[:32], reason=f"Renamed by {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply("Missing permissions to rename that emoji.")
            
        await ctx.reply(embed=discord.Embed(
            description=f"✏️ Renamed `:{old_name}:` to `:{new_name[:32]}:`", color=SUCCESS_COLOR
        ))

    @emoji.command(name="list")
    async def emoji_list(self, ctx):
        static, animated, limit = _slot_counts(ctx.guild)
        names = [f"`:{e.name}:`" for e in ctx.guild.emojis]
        
        embed = discord.Embed(title=f"Emoji Slots — {ctx.guild.name}", color=EMBED_COLOR)
        embed.add_field(name="Static Slots", value=f"`{static}/{limit}`", inline=True)
        embed.add_field(name="Animated Slots", value=f"`{animated}/{limit}`", inline=True)
        embed.add_field(name="Boost Level", value=f"Level `{ctx.guild.premium_tier}`", inline=True)
        
        if names:
            embed.add_field(name=f"Emojis ({len(names)})", value=" ".join(names[:60]), inline=False)
            
        await ctx.reply(embed=embed)

    @app_commands.command(name="emojisearch", description="Search and add emojis from available servers")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_emoji_search(self, interaction: discord.Interaction, query: str):
        q = query.lower()
        results = []
        for guild in self.bot.guilds:
            for e in guild.emojis:
                if q in e.name.lower():
                    results.append(e)
            if len(results) >= 25:
                break

        if not results:
            return await interaction.response.send_message(
                f"No emojis found matching `{query}`.", ephemeral=True
            )

        embed = discord.Embed(
            title="Emoji Search",
            description=f"Found **{len(results)}** match(es) for `{query}`.\nSelect an emoji below to add it to this server.",
            color=EMBED_COLOR
        )
        view = EmojiPickView(interaction.user.id, results)
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(Emotes(bot))