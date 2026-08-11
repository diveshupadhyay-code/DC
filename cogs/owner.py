import discord
from discord.ext import commands
import aiohttp
import platform
import time
from datetime import datetime, timezone

from utils.db import blacklist_col, premium_col
from utils.helpers import ctx_owner

START_TIME = time.time()
COLOR = 0x2B2D31


async def _fetch_bytes(url: str) -> bytes | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception:
        return None
    return None


class Owner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="setavatar", aliases=["setpfp", "botavatar"])
    @ctx_owner()
    async def set_avatar(self, ctx, url: str = None):
        target_url = url or (ctx.message.attachments[0].url if ctx.message.attachments else None)
        if not target_url:
            return await ctx.reply(embed=discord.Embed(
                description="Usage: `,setavatar <url>` or attach an image.",
                color=COLOR
            ))
        data = await _fetch_bytes(target_url)
        if not data:
            return await ctx.reply(embed=discord.Embed(description="Could not download that image.", color=0xED4245))
        try:
            await self.bot.user.edit(avatar=data)
        except discord.HTTPException as e:
            return await ctx.reply(embed=discord.Embed(description=f"Failed to update avatar: `{e}`", color=0xED4245))
        embed = discord.Embed(description="Bot avatar updated.", color=0x57F287)
        embed.set_thumbnail(url=target_url)
        await ctx.reply(embed=embed)

    @commands.command(name="setbanner")
    @ctx_owner()
    async def set_banner(self, ctx, url: str = None):
        target_url = url or (ctx.message.attachments[0].url if ctx.message.attachments else None)
        if not target_url:
            return await ctx.reply(embed=discord.Embed(
                description="Usage: `,setbanner <url>` or attach an image.",
                color=COLOR
            ))
        data = await _fetch_bytes(target_url)
        if not data:
            return await ctx.reply(embed=discord.Embed(description="Could not download that image.", color=0xED4245))
        try:
            await self.bot.user.edit(banner=data)
        except discord.HTTPException as e:
            return await ctx.reply(embed=discord.Embed(description=f"Failed to update banner: `{e}`", color=0xED4245))
        await ctx.reply(embed=discord.Embed(description="Bot banner updated.", color=0x57F287))

    @commands.command(name="setbotname", aliases=["setusername"])
    @ctx_owner()
    async def set_bot_name(self, ctx, *, name: str = None):
        if not name or len(name) > 32:
            return await ctx.reply(embed=discord.Embed(
                description="Usage: `,setbotname <name>` (max 32 characters).",
                color=COLOR
            ))
        try:
            await self.bot.user.edit(username=name)
        except discord.HTTPException as e:
            return await ctx.reply(embed=discord.Embed(description=f"Failed to update username: `{e}`", color=0xED4245))
        await ctx.reply(embed=discord.Embed(description=f"Bot username updated to **{name}**.", color=0x57F287))

    @commands.group(invoke_without_command=True)
    @ctx_owner()
    async def blacklist(self, ctx):
        docs = await blacklist_col.find({}).to_list(200)
        lines = [f"`{d['id']}` — {d.get('type','user')} — {d.get('reason','No reason')}" for d in docs]
        embed = discord.Embed(
            title="Blacklist",
            description="\n".join(lines) if lines else "Nobody is blacklisted.",
            color=COLOR
        )
        embed.add_field(
            name="Commands",
            value=(
                "`,blacklist add user <id> [reason]`\n"
                "`,blacklist add server <id> [reason]`\n"
                "`,blacklist remove <id>`"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @blacklist.command(name="add")
    @ctx_owner()
    async def blacklist_add(self, ctx, type_: str = None, target_id: str = None, *, reason: str = "No reason provided"):
        if not type_ or type_ not in ("user", "server") or not target_id:
            return await ctx.reply("Usage: `,blacklist add user/server <id> [reason]`")
        await blacklist_col.update_one(
            {"id": target_id},
            {"$set": {"id": target_id, "type": type_, "reason": reason, "added_at": datetime.now(timezone.utc)}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(description=f"Blacklisted {type_} `{target_id}`.", color=0x57F287))
        if type_ == "server":
            guild = self.bot.get_guild(int(target_id))
            if guild:
                try:
                    await guild.leave()
                except Exception:
                    pass

    @blacklist.command(name="remove")
    @ctx_owner()
    async def blacklist_remove(self, ctx, target_id: str = None):
        if not target_id:
            return await ctx.reply("Usage: `,blacklist remove <id>`")
        result = await blacklist_col.delete_one({"id": target_id})
        await ctx.reply(embed=discord.Embed(
            description=f"Removed `{target_id}` from the blacklist." if result.deleted_count else "That ID is not blacklisted.",
            color=COLOR
        ))

    @commands.command()
    @ctx_owner()
    async def reload(self, ctx, cog: str = None):
        if not cog:
            return await ctx.reply("Usage: `,reload <cog>`")
        try:
            await self.bot.reload_extension(f"cogs.{cog}")
        except Exception as e:
            return await ctx.reply(embed=discord.Embed(description=f"Failed: `{e}`", color=0xED4245))
        await ctx.reply(embed=discord.Embed(description=f"Reloaded `{cog}`.", color=0x57F287))

    @commands.command()
    @ctx_owner()
    async def load(self, ctx, cog: str = None):
        if not cog:
            return await ctx.reply("Usage: `,load <cog>`")
        try:
            await self.bot.load_extension(f"cogs.{cog}")
        except Exception as e:
            return await ctx.reply(embed=discord.Embed(description=f"Failed: `{e}`", color=0xED4245))
        await ctx.reply(embed=discord.Embed(description=f"Loaded `{cog}`.", color=0x57F287))

    @commands.command()
    @ctx_owner()
    async def unload(self, ctx, cog: str = None):
        if not cog:
            return await ctx.reply("Usage: `,unload <cog>`")
        try:
            await self.bot.unload_extension(f"cogs.{cog}")
        except Exception as e:
            return await ctx.reply(embed=discord.Embed(description=f"Failed: `{e}`", color=0xED4245))
        await ctx.reply(embed=discord.Embed(description=f"Unloaded `{cog}`.", color=0x57F287))

    @commands.command(aliases=["cogslist"])
    @ctx_owner()
    async def cogs(self, ctx):
        loaded = sorted(self.bot.extensions.keys())
        embed = discord.Embed(
            title="Loaded Cogs",
            description="\n".join(f"`{c}`" for c in loaded),
            color=COLOR
        )
        embed.set_footer(text=f"{len(loaded)} cog(s) loaded")
        await ctx.reply(embed=embed)

    @commands.command()
    @ctx_owner()
    async def botinfo(self, ctx):
        uptime = int(time.time() - START_TIME)
        h, r = divmod(uptime, 3600)
        m, s = divmod(r, 60)
        premium_count = await premium_col.count_documents({})
        blacklist_count = await blacklist_col.count_documents({})

        embed = discord.Embed(title="Bot Information", color=COLOR, timestamp=datetime.now(timezone.utc))
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="Servers", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(name="Users", value=str(len(self.bot.users)), inline=True)
        embed.add_field(name="Latency", value=f"{round(self.bot.latency*1000)}ms", inline=True)
        embed.add_field(name="Uptime", value=f"{h}h {m}m {s}s", inline=True)
        embed.add_field(name="Premium Entries", value=str(premium_count), inline=True)
        embed.add_field(name="Blacklisted", value=str(blacklist_count), inline=True)
        embed.add_field(name="discord.py", value=discord.__version__, inline=True)
        embed.add_field(name="Python", value=platform.python_version(), inline=True)
        embed.add_field(name="Cogs Loaded", value=str(len(self.bot.extensions)), inline=True)
        await ctx.reply(embed=embed)

    @commands.command()
    @ctx_owner()
    async def broadcast(self, ctx, *, message: str = None):
        if not message:
            return await ctx.reply("Usage: `,broadcast <message>`")
        sent, failed = 0, 0
        embed = discord.Embed(title="Message from the Bot Owner", description=message, color=COLOR)
        for guild in self.bot.guilds:
            owner = guild.owner
            if not owner:
                continue
            try:
                await owner.send(embed=embed)
                sent += 1
            except Exception:
                failed += 1
        await ctx.reply(embed=discord.Embed(
            description=f"Sent to **{sent}** server owner(s). Failed: **{failed}**.",
            color=COLOR
        ))

    @commands.command()
    @ctx_owner()
    async def shutdown(self, ctx):
        await ctx.reply(embed=discord.Embed(description="Shutting down.", color=0xED4245))
        await self.bot.close()


async def setup(bot):
    await bot.add_cog(Owner(bot))
