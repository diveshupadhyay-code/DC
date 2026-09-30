import discord
from discord.ext import commands
import aiohttp
import platform
import time
from datetime import datetime, timezone

from utils.db import blacklist_col, premium_col
from utils.helpers import ctx_owner

START_TIME = time.time()
THEME_COLOR = 0x2B2D31
SUCCESS_COLOR = 0x57F287
ERROR_COLOR = 0xED4245


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
    @commands.is_owner()
    @ctx_owner()
    async def set_avatar(self, ctx, url: str = None):
        target_url = url or (ctx.message.attachments[0].url if ctx.message.attachments else None)
        if not target_url:
            embed = discord.Embed(
                title="System Configuration",
                description="Please provide an image URL or attach a file.",
                color=THEME_COLOR
            )
            embed.add_field(name="Usage", value="`,setavatar <url>`", inline=False)
            return await ctx.reply(embed=embed)

        data = await _fetch_bytes(target_url)
        if not data:
            embed = discord.Embed(
                title="Error Encountered",
                description="Unable to fetch media from the provided URL.",
                color=ERROR_COLOR
            )
            return await ctx.reply(embed=embed)

        try:
            await self.bot.user.edit(avatar=data)
            embed = discord.Embed(
                title="Avatar Updated",
                description="Bot profile picture updated successfully.",
                color=SUCCESS_COLOR
            )
            embed.set_thumbnail(url=target_url)
            await ctx.reply(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                title="Execution Failed",
                description=f"```{e.text}```",
                color=ERROR_COLOR
            )
            await ctx.reply(embed=embed)

    @commands.command(name="setbanner")
    @commands.is_owner()
    @ctx_owner()
    async def set_banner(self, ctx, url: str = None):
        target_url = url or (ctx.message.attachments[0].url if ctx.message.attachments else None)
        if not target_url:
            embed = discord.Embed(
                title="System Configuration",
                description="Please provide an image URL or attach a file.",
                color=THEME_COLOR
            )
            embed.add_field(name="Usage", value="`,setbanner <url>`", inline=False)
            return await ctx.reply(embed=embed)

        data = await _fetch_bytes(target_url)
        if not data:
            embed = discord.Embed(
                title="Error Encountered",
                description="Unable to fetch media from the provided URL.",
                color=ERROR_COLOR
            )
            return await ctx.reply(embed=embed)

        try:
            await self.bot.user.edit(banner=data)
            embed = discord.Embed(
                title="Banner Updated",
                description="Bot banner image updated successfully.",
                color=SUCCESS_COLOR
            )
            await ctx.reply(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                title="Execution Failed",
                description=f"```{e.text}```",
                color=ERROR_COLOR
            )
            await ctx.reply(embed=embed)

    @commands.command(name="setdisplayname", aliases=["setnick", "setname"])
    @commands.is_owner()
    @ctx_owner()
    @commands.guild_only()
    @commands.has_permissions(change_nickname=True)
    @commands.bot_has_permissions(change_nickname=True)
    async def set_display_name(self, ctx, *, name: str = None):
        if name and len(name) > 32:
            embed = discord.Embed(
                title="Validation Error",
                description="Display name must not exceed 32 characters.",
                color=ERROR_COLOR
            )
            return await ctx.reply(embed=embed)

        try:
            await ctx.guild.me.edit(nick=name)
            status_str = f"Display name set to **{name}**." if name else "Display name reset to default."
            embed = discord.Embed(
                title="Display Name Updated",
                description=status_str,
                color=SUCCESS_COLOR
            )
            await ctx.reply(embed=embed)
        except discord.Forbidden:
            embed = discord.Embed(
                title="Permission Denied",
                description="Role hierarchy prevents changing nickname in this server.",
                color=ERROR_COLOR
            )
            await ctx.reply(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                title="Execution Failed",
                description=f"```{e.text}```",
                color=ERROR_COLOR
            )
            await ctx.reply(embed=embed)

    @commands.group(invoke_without_command=True)
    @commands.is_owner()
    @ctx_owner()
    async def blacklist(self, ctx):
        docs = await blacklist_col.find({}).to_list(200)
        lines = [f"`{d['id']}` • **{d.get('type', 'user').upper()}** • {d.get('reason', 'No reason provided')}" for d in docs]
        
        embed = discord.Embed(
            title="Blacklist Management",
            description="\n".join(lines) if lines else "No entries currently blacklisted.",
            color=THEME_COLOR
        )
        embed.add_field(
            name="Available Commands",
            value="```\n,blacklist add user <id> [reason]\n,blacklist add server <id> [reason]\n,blacklist remove <id>\n```",
            inline=False
        )
        await ctx.reply(embed=embed)

    @blacklist.command(name="add")
    @commands.is_owner()
    @ctx_owner()
    async def blacklist_add(self, ctx, type_: str = None, target_id: str = None, *, reason: str = "No reason provided"):
        if not type_ or type_.lower() not in ("user", "server") or not target_id or not target_id.isdigit():
            embed = discord.Embed(
                title="Invalid Syntax",
                description="Usage: `,blacklist add <user|server> <id> [reason]`",
                color=ERROR_COLOR
            )
            return await ctx.reply(embed=embed)

        target_type = type_.lower()
        await blacklist_col.update_one(
            {"id": target_id},
            {"$set": {"id": target_id, "type": target_type, "reason": reason, "added_at": datetime.now(timezone.utc)}},
            upsert=True
        )
        
        embed = discord.Embed(
            title="Blacklist Updated",
            description=f"Successfully blacklisted {target_type} ID `{target_id}`.",
            color=SUCCESS_COLOR
        )
        embed.add_field(name="Reason", value=f"```{reason}```", inline=False)
        await ctx.reply(embed=embed)

        if target_type == "server":
            guild = self.bot.get_guild(int(target_id))
            if guild:
                try:
                    await guild.leave()
                except Exception:
                    pass

    @blacklist.command(name="remove")
    @commands.is_owner()
    @ctx_owner()
    async def blacklist_remove(self, ctx, target_id: str = None):
        if not target_id or not target_id.isdigit():
            embed = discord.Embed(
                title="Invalid Syntax",
                description="Usage: `,blacklist remove <id>`",
                color=ERROR_COLOR
            )
            return await ctx.reply(embed=embed)

        result = await blacklist_col.delete_one({"id": target_id})
        if result.deleted_count:
            embed = discord.Embed(
                title="Blacklist Updated",
                description=f"Removed ID `{target_id}` from blacklist.",
                color=SUCCESS_COLOR
            )
        else:
            embed = discord.Embed(
                title="Not Found",
                description=f"ID `{target_id}` is not blacklisted.",
                color=ERROR_COLOR
            )
        await ctx.reply(embed=embed)

    @commands.command()
    @commands.is_owner()
    @ctx_owner()
    async def reload(self, ctx, cog: str = None):
        if not cog:
            embed = discord.Embed(title="Invalid Syntax", description="Usage: `,reload <cog>`", color=ERROR_COLOR)
            return await ctx.reply(embed=embed)
        try:
            await self.bot.reload_extension(f"cogs.{cog}")
            embed = discord.Embed(title="Module Reloaded", description=f"Successfully reloaded `cogs.{cog}`.", color=SUCCESS_COLOR)
            await ctx.reply(embed=embed)
        except Exception as e:
            embed = discord.Embed(title="Reload Failed", description=f"```{e}```", color=ERROR_COLOR)
            await ctx.reply(embed=embed)

    @commands.command()
    @commands.is_owner()
    @ctx_owner()
    async def load(self, ctx, cog: str = None):
        if not cog:
            embed = discord.Embed(title="Invalid Syntax", description="Usage: `,load <cog>`", color=ERROR_COLOR)
            return await ctx.reply(embed=embed)
        try:
            await self.bot.load_extension(f"cogs.{cog}")
            embed = discord.Embed(title="Module Loaded", description=f"Successfully loaded `cogs.{cog}`.", color=SUCCESS_COLOR)
            await ctx.reply(embed=embed)
        except Exception as e:
            embed = discord.Embed(title="Load Failed", description=f"```{e}```", color=ERROR_COLOR)
            await ctx.reply(embed=embed)

    @commands.command()
    @commands.is_owner()
    @ctx_owner()
    async def unload(self, ctx, cog: str = None):
        if not cog:
            embed = discord.Embed(title="Invalid Syntax", description="Usage: `,unload <cog>`", color=ERROR_COLOR)
            return await ctx.reply(embed=embed)
        try:
            await self.bot.unload_extension(f"cogs.{cog}")
            embed = discord.Embed(title="Module Unloaded", description=f"Successfully unloaded `cogs.{cog}`.", color=SUCCESS_COLOR)
            await ctx.reply(embed=embed)
        except Exception as e:
            embed = discord.Embed(title="Unload Failed", description=f"```{e}```", color=ERROR_COLOR)
            await ctx.reply(embed=embed)

    @commands.command(aliases=["cogslist"])
    @commands.is_owner()
    @ctx_owner()
    async def cogs(self, ctx):
        loaded = sorted(self.bot.extensions.keys())
        formatted_cogs = "\n".join([f"`{c}`" for c in loaded]) if loaded else "None"
        embed = discord.Embed(
            title="Loaded Extension Modules",
            description=formatted_cogs,
            color=THEME_COLOR
        )
        embed.set_footer(text=f"Total Active Cogs: {len(loaded)}")
        await ctx.reply(embed=embed)

    @commands.command()
    @commands.is_owner()
    @ctx_owner()
    async def botinfo(self, ctx):
        uptime = int(time.time() - START_TIME)
        h, r = divmod(uptime, 3600)
        m, s = divmod(r, 60)
        premium_count = await premium_col.count_documents({})
        blacklist_count = await blacklist_col.count_documents({})

        embed = discord.Embed(
            title="System Telemetry & Status",
            color=THEME_COLOR,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="Guild Counter", value=f"```\n{len(self.bot.guilds):,} Guilds\n```", inline=True)
        embed.add_field(name="User Reach", value=f"```\n{len(self.bot.users):,} Users\n```", inline=True)
        embed.add_field(name="API Latency", value=f"```\n{round(self.bot.latency * 1000)} ms\n```", inline=True)
        embed.add_field(name="Uptime", value=f"```\n{h}h {m}m {s}s\n```", inline=True)
        embed.add_field(name="Premium Access", value=f"```\n{premium_count} Active\n```", inline=True)
        embed.add_field(name="Blacklisted", value=f"```\n{blacklist_count} Records\n```", inline=True)
        embed.add_field(name="Environment", value=f"```\nPython {platform.python_version()}\ndiscord.py v{discord.__version__}\n```", inline=False)
        embed.set_footer(text=f"Active Cogs: {len(self.bot.extensions)}")
        await ctx.reply(embed=embed)

    @commands.command()
    @commands.is_owner()
    @ctx_owner()
    async def broadcast(self, ctx, *, message: str = None):
        if not message:
            embed = discord.Embed(title="Invalid Syntax", description="Usage: `,broadcast <message>`", color=ERROR_COLOR)
            return await ctx.reply(embed=embed)

        sent, failed = 0, 0
        embed = discord.Embed(
            title="System Notification",
            description=message,
            color=THEME_COLOR,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=f"Broadcasted by {ctx.author}")

        for guild in self.bot.guilds:
            owner = guild.owner
            if not owner:
                continue
            try:
                await owner.send(embed=embed)
                sent += 1
            except Exception:
                failed += 1

        result_embed = discord.Embed(
            title="Broadcast Summary",
            description=f"Dispatch execution completed.",
            color=SUCCESS_COLOR
        )
        result_embed.add_field(name="Successful DMs", value=f"`{sent}`", inline=True)
        result_embed.add_field(name="Failed Executions", value=f"`{failed}`", inline=True)
        await ctx.reply(embed=result_embed)

    @commands.command()
    @commands.is_owner()
    @ctx_owner()
    async def shutdown(self, ctx):
        embed = discord.Embed(
            title="System Termination",
            description="Shutting down core processes...",
            color=ERROR_COLOR
        )
        await ctx.reply(embed=embed)
        await self.bot.close()


async def setup(bot):
    await bot.add_cog(Owner(bot))