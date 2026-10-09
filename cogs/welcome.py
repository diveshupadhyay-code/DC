import discord
from discord.ext import commands
from utils.db import settings_col, logs_col, counters_col
from utils.helpers import ctx_admin, ctx_mod, log_event, update_server_data

VARS_HELP = (
    "`{user}` - Mention user\n"
    "`{username}` - User name\n"
    "`{server}` - Server name\n"
    "`{count}` - Member count\n"
    "`{usertag}` - Full tag"
)

def build_embed(title: str, description: str = None, color: int = 0x2B2D31) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="System Dashboard")
    return embed

class Greeting(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _render(self, text: str, member: discord.Member, guild: discord.Guild) -> str:
        if not text:
            return ""
        return (
            text
            .replace("{user}", member.mention)
            .replace("{username}", member.display_name)
            .replace("{usertag}", str(member))
            .replace("{server}", guild.name)
            .replace("{count}", str(guild.member_count))
        )

    def _build_event_embed(self, member: discord.Member, guild: discord.Guild, event_type: str, custom_msg: str = None, gif_url: str = None) -> discord.Embed:
        embed = discord.Embed(color=0x2B2D31)
        if event_type == "welcome":
            embed.set_author(name=f"Welcome to {guild.name}", icon_url=guild.icon.url if guild.icon else None)
            default_desc = f"👋 Hey {member.mention}, welcome to the server!\nWe are now **{guild.member_count}** members strong."
        else:
            embed.set_author(name=f"{member.display_name} Left", icon_url=member.display_avatar.url)
            default_desc = f"🏃‍♂️ **{member.mention}** just left the server.\nWe are down to **{guild.member_count}** members."

        embed.set_thumbnail(url=member.display_avatar.url)
        embed.description = self._render(custom_msg, member, guild) if custom_msg else default_desc

        if gif_url:
            embed.set_image(url=gif_url)

        embed.set_footer(text=f"ID: {member.id} • Joined: {member.joined_at.strftime('%d %b %Y') if member.joined_at else 'Unknown'}")
        return embed

    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def welcome(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        cid = gs.get("welcome_channel")
        ch = f"<#{cid}>" if cid else "Not set"
        en = "🟢 Active" if gs.get("welcome_enabled") else "🔴 Inactive"
        custom = gs.get("welcome_custom_msg")
        gif_url = gs.get("welcome_gif")

        embed = build_embed("⚙️ Welcome Settings")
        embed.add_field(name="Status", value=en, inline=True)
        embed.add_field(name="Channel", value=ch, inline=True)
        
        msg_val = f"`{custom[:60]}...`" if custom and len(custom) > 60 else (f"`{custom}`" if custom else "✨ Standard Message Active")
        embed.add_field(name="Message Template", value=msg_val, inline=False)
        embed.add_field(name="Banner GIF", value=f"[Link]({gif_url})" if gif_url else "None", inline=False)
        embed.add_field(
            name="Available Commands",
            value=(
                "`,welcome set #channel` • Set target channel\n"
                "`,welcome enable` / `,welcome disable` • Turn on/off\n"
                "`,welcome message <text>` • Change message\n"
                "`,welcome gif <url>` • Set picture or GIF\n"
                "`,welcome removechannel` • Clear channel\n"
                "`,welcome test` • Send a test message"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @welcome.command(name="set", aliases=["setchannel"])
    @commands.has_permissions(administrator=True)
    async def welcome_set(self, ctx, channel: discord.TextChannel = None):
        if not channel:
            return await ctx.reply(embed=build_embed("❌ Error", "Please mention a channel: `,welcome set #channel`"))
        await update_server_data(ctx.guild.id, "welcome_channel", channel.id)
        await update_server_data(ctx.guild.id, "welcome_enabled", True)
        await ctx.reply(embed=build_embed("✅ Done", f"Welcome messages will now go to {channel.mention}."))

    @welcome.command(name="removechannel")
    @commands.has_permissions(administrator=True)
    async def welcome_removechannel(self, ctx):
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$unset": {"welcome_channel": ""}, "$set": {"welcome_enabled": False}},
            upsert=True
        )
        await ctx.reply(embed=build_embed("✅ Done", "Welcome channel removed and feature turned off."))

    @welcome.command(name="enable")
    @commands.has_permissions(administrator=True)
    async def welcome_enable(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        if not gs.get("welcome_channel"):
            return await ctx.reply(embed=build_embed("❌ Error", "Set a channel first using `,welcome set #channel`."))
        await update_server_data(ctx.guild.id, "welcome_enabled", True)
        await ctx.reply(embed=build_embed("✅ Done", "Welcome messages are now active."))

    @welcome.command(name="disable")
    @commands.has_permissions(administrator=True)
    async def welcome_disable(self, ctx):
        await update_server_data(ctx.guild.id, "welcome_enabled", False)
        await ctx.reply(embed=build_embed("✅ Done", "Welcome messages are now disabled."))

    @welcome.command(name="message", aliases=["setmessage", "msg"])
    @commands.has_permissions(administrator=True)
    async def welcome_message(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply(embed=build_embed("❌ Error", f"Please write a message.\n\n**Variables you can use:**\n{VARS_HELP}"))
        if len(text) > 1000:
            return await ctx.reply(embed=build_embed("❌ Error", "Message is too long. Keep it under 1000 characters."))
        await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$set": {"welcome_custom_msg": text}}, upsert=True)
        await ctx.reply(embed=build_embed("✅ Saved", f"Your new welcome message:\n`{text}`"))

    @welcome.command(name="gif")
    @commands.has_permissions(administrator=True)
    async def welcome_gif(self, ctx, url: str = None):
        if not url or not url.startswith(("http://", "https://")):
            return await ctx.reply(embed=build_embed("❌ Error", "Please provide a valid image link starting with http:// or https://"))
        await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$set": {"welcome_gif": url}}, upsert=True)
        await ctx.reply(embed=build_embed("✅ Saved", "Welcome picture updated."))

    @welcome.command(name="test")
    @commands.has_permissions(administrator=True)
    async def welcome_test(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        embed = self._build_event_embed(ctx.author, ctx.guild, "welcome", gs.get("welcome_custom_msg"), gs.get("welcome_gif"))
        await ctx.reply(content="📦 **Test Welcome Preview:**", embed=embed)

    @commands.group(aliases=["leave"], invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def bye(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        cid = gs.get("bye_channel")
        ch = f"<#{cid}>" if cid else "Not set"
        en = "🟢 Active" if gs.get("bye_enabled") else "🔴 Inactive"
        custom = gs.get("bye_custom_msg")
        gif_url = gs.get("bye_gif")

        embed = build_embed("⚙️ Leave Settings")
        embed.add_field(name="Status", value=en, inline=True)
        embed.add_field(name="Channel", value=ch, inline=True)
        
        msg_val = f"`{custom[:60]}...`" if custom and len(custom) > 60 else (f"`{custom}`" if custom else "✨ Standard Message Active")
        embed.add_field(name="Message Template", value=msg_val, inline=False)
        embed.add_field(name="Banner GIF", value=f"[Link]({gif_url})" if gif_url else "None", inline=False)
        embed.add_field(
            name="Available Commands",
            value=(
                "`,bye set #channel` • Set target channel\n"
                "`,bye enable` / `,bye disable` • Turn on/off\n"
                "`,bye message <text>` • Change message\n"
                "`,bye gif <url>` • Set picture or GIF\n"
                "`,bye removechannel` • Clear channel\n"
                "`,bye test` • Send a test message"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @bye.command(name="set", aliases=["setchannel"])
    @commands.has_permissions(administrator=True)
    async def bye_set(self, ctx, channel: discord.TextChannel = None):
        if not channel:
            return await ctx.reply(embed=build_embed("❌ Error", "Please mention a channel: `,bye set #channel`"))
        await update_server_data(ctx.guild.id, "bye_channel", channel.id)
        await update_server_data(ctx.guild.id, "bye_enabled", True)
        await ctx.reply(embed=build_embed("✅ Done", f"Leave messages will now go to {channel.mention}."))

    @bye.command(name="removechannel")
    @commands.has_permissions(administrator=True)
    async def bye_removechannel(self, ctx):
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$unset": {"bye_channel": ""}, "$set": {"bye_enabled": False}},
            upsert=True
        )
        await ctx.reply(embed=build_embed("✅ Done", "Leave channel removed and feature turned off."))

    @bye.command(name="enable")
    @commands.has_permissions(administrator=True)
    async def bye_enable(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        if not gs.get("bye_channel"):
            return await ctx.reply(embed=build_embed("❌ Error", "Set a channel first using `,bye set #channel`."))
        await update_server_data(ctx.guild.id, "bye_enabled", True)
        await ctx.reply(embed=build_embed("✅ Done", "Leave messages are now active."))

    @bye.command(name="disable")
    @commands.has_permissions(administrator=True)
    async def bye_disable(self, ctx):
        await update_server_data(ctx.guild.id, "bye_enabled", False)
        await ctx.reply(embed=build_embed("✅ Done", "Leave messages are now disabled."))

    @bye.command(name="message", aliases=["setmessage", "msg"])
    @commands.has_permissions(administrator=True)
    async def bye_message(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply(embed=build_embed("❌ Error", f"Please write a message.\n\n**Variables you can use:**\n{VARS_HELP}"))
        if len(text) > 1000:
            return await ctx.reply(embed=build_embed("❌ Error", "Message is too long. Keep it under 1000 characters."))
        await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$set": {"bye_custom_msg": text}}, upsert=True)
        await ctx.reply(embed=build_embed("✅ Saved", f"Your new leave message:\n`{text}`"))

    @bye.command(name="gif")
    @commands.has_permissions(administrator=True)
    async def bye_gif(self, ctx, url: str = None):
        if not url or not url.startswith(("http://", "https://")):
            return await ctx.reply(embed=build_embed("❌ Error", "Please provide a valid image link starting with http:// or https://"))
        await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$set": {"bye_gif": url}}, upsert=True)
        await ctx.reply(embed=build_embed("✅ Saved", "Leave picture updated."))

    @bye.command(name="test")
    @commands.has_permissions(administrator=True)
    async def bye_test(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        embed = self._build_event_embed(ctx.author, ctx.guild, "leave", gs.get("bye_custom_msg"), gs.get("bye_gif"))
        await ctx.reply(content="📦 **Test Leave Preview:**", embed=embed)

    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def boost(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        cid = gs.get("boost_channel")
        ch = f"<#{cid}>" if cid else "Not set"
        en = "🟢 Active" if gs.get("boost_enabled") else "🔴 Inactive"

        embed = build_embed("🚀 Boost Settings")
        embed.add_field(name="Status", value=en, inline=True)
        embed.add_field(name="Channel", value=ch, inline=True)
        embed.add_field(
            name="Available Commands",
            value=(
                "`,boost set #channel` • Set target channel\n"
                "`,boost message <text>` • Set boost message\n"
                "`,boost removechannel` • Clear channel\n"
                "`,boost test` • Send a test message"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @boost.command(name="set", aliases=["setchannel"])
    @commands.has_permissions(administrator=True)
    async def boost_set(self, ctx, channel: discord.TextChannel = None):
        if not channel:
            return await ctx.reply(embed=build_embed("❌ Error", "Please mention a channel: `,boost set #channel`"))
        await update_server_data(ctx.guild.id, "boost_channel", channel.id)
        await update_server_data(ctx.guild.id, "boost_enabled", True)
        await ctx.reply(embed=build_embed("✅ Done", f"Boost messages will now go to {channel.mention}."))

    @boost.command(name="removechannel")
    @commands.has_permissions(administrator=True)
    async def boost_removechannel(self, ctx):
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$unset": {"boost_channel": ""}, "$set": {"boost_enabled": False}},
            upsert=True
        )
        await ctx.reply(embed=build_embed("✅ Done", "Boost channel removed."))

    @boost.command(name="message", aliases=["setmessage"])
    @commands.has_permissions(administrator=True)
    async def boost_message(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply(embed=build_embed("❌ Error", f"Please write a message.\n\n**Variables you can use:**\n{VARS_HELP}"))
        await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$set": {"boost_custom_msg": text}}, upsert=True)
        await ctx.reply(embed=build_embed("✅ Saved", "Boost message updated."))

    @boost.command(name="test")
    @commands.has_permissions(administrator=True)
    async def boost_test(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        custom = gs.get("boost_custom_msg") or "🚀 **{user}** just boosted **{server}**! Thank you!"
        rendered = self._render(custom, ctx.author, ctx.guild)
        embed = build_embed("🚀 Server Boost", rendered)
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        await ctx.reply(content="📦 **Test Boost Preview:**", embed=embed)

    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def joindm(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        en = "🟢 Active" if gs.get("joindm_enabled") else "🔴 Inactive"
        embed = build_embed("📥 Direct Message Settings")
        embed.add_field(name="Status", value=en, inline=True)
        embed.add_field(
            name="Available Commands",
            value=(
                "`,joindm message <text>` • Set private message\n"
                "`,joindm disable` • Turn off DM sending\n"
                "`,joindm test` • Test DM sending"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @joindm.command(name="message", aliases=["setmessage"])
    @commands.has_permissions(administrator=True)
    async def joindm_message(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply(embed=build_embed("❌ Error", f"Please write a message.\n\n**Variables you can use:**\n{VARS_HELP}"))
        await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$set": {"joindm_msg": text, "joindm_enabled": True}}, upsert=True)
        await ctx.reply(embed=build_embed("✅ Saved", "Join DM enabled and saved."))

    @joindm.command(name="disable")
    @commands.has_permissions(administrator=True)
    async def joindm_disable(self, ctx):
        await update_server_data(ctx.guild.id, "joindm_enabled", False)
        await ctx.reply(embed=build_embed("✅ Done", "Join DM messages turned off."))

    @joindm.command(name="test")
    @commands.has_permissions(administrator=True)
    async def joindm_test(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        custom = gs.get("joindm_msg") or "Welcome to **{server}**, {username}!"
        rendered = self._render(custom, ctx.author, ctx.guild)
        try:
            await ctx.author.send(rendered)
            await ctx.reply(embed=build_embed("✅ Sent", "Check your direct messages for the test message."))
        except discord.Forbidden:
            await ctx.reply(embed=build_embed("❌ Error", "Could not send DM. Please open your private messages."))

    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def poj(self, ctx):
        embed = build_embed("🔔 Ping On Join (POJ) Settings")
        embed.add_field(
            name="Available Commands",
            value=(
                "`,poj start #channel [delay]` • Start pinging on join\n"
                "`,poj stop #channel` • Stop pinging in channel\n"
                "`,poj list` • Show active channels"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @poj.command(name="start")
    @commands.has_permissions(administrator=True)
    async def poj_start(self, ctx, channel: discord.TextChannel = None, delay: int = 0):
        if not channel:
            return await ctx.reply(embed=build_embed("❌ Error", "Please mention a channel: `,poj start #channel`"))
        if delay < 0 or delay > 300:
            return await ctx.reply(embed=build_embed("❌ Error", "Delay must be between 0 and 300 seconds."))
        
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {f"poj_channels.{channel.id}": {"delay": delay}}},
            upsert=True
        )
        await ctx.reply(embed=build_embed("✅ Saved", f"Pinging enabled in {channel.mention} with `{delay}s` delay."))

    @poj.command(name="stop")
    @commands.has_permissions(administrator=True)
    async def poj_stop(self, ctx, channel: discord.TextChannel = None):
        if not channel:
            return await ctx.reply(embed=build_embed("❌ Error", "Please mention a channel: `,poj stop #channel`"))
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$unset": {f"poj_channels.{channel.id}": ""}},
            upsert=True
        )
        await ctx.reply(embed=build_embed("✅ Saved", f"Stopped pinging in {channel.mention}."))

    @poj.command(name="list")
    @commands.has_permissions(administrator=True)
    async def poj_list(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        poj_data = gs.get("poj_channels", {})
        if not poj_data:
            return await ctx.reply(embed=build_embed("📊 POJ Channels", "No channels configured."))
        
        lines = []
        for ch_id, cfg in poj_data.items():
            lines.append(f"• <#{ch_id}> — Delay: `{cfg.get('delay', 0)}s`")
        
        embed = build_embed("📊 Active POJ Channels", "\n".join(lines))
        await ctx.reply(embed=embed)

    @commands.command(name="variables", aliases=["vars"])
    async def variables(self, ctx):
        embed = build_embed("📝 Text Variables", VARS_HELP)
        await ctx.reply(embed=embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        gs = await settings_col.find_one({"_id": str(member.guild.id)}) or {}
        
        if gs.get("welcome_enabled") and gs.get("welcome_channel"):
            channel = member.guild.get_channel(int(gs["welcome_channel"]))
            if channel and channel.permissions_for(member.guild.me).send_messages:
                embed = self._build_event_embed(member, member.guild, "welcome", gs.get("welcome_custom_msg"), gs.get("welcome_gif"))
                await channel.send(embed=embed)

        if gs.get("joindm_enabled") and gs.get("joindm_msg"):
            try:
                rendered = self._render(gs["joindm_msg"], member, member.guild)
                await member.send(rendered)
            except discord.Forbidden:
                pass

        poj_data = gs.get("poj_channels", {})
        if poj_data:
            import asyncio
            for ch_id, cfg in poj_data.items():
                channel = member.guild.get_channel(int(ch_id))
                if channel and channel.permissions_for(member.guild.me).send_messages:
                    delay = cfg.get("delay", 0)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    msg = await channel.send(member.mention)
                    await msg.delete()

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        gs = await settings_col.find_one({"_id": str(member.guild.id)}) or {}
        if gs.get("bye_enabled") and gs.get("bye_channel"):
            channel = member.guild.get_channel(int(gs["bye_channel"]))
            if channel and channel.permissions_for(member.guild.me).send_messages:
                embed = self._build_event_embed(member, member.guild, "leave", gs.get("bye_custom_msg"), gs.get("bye_gif"))
                await channel.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Greeting(bot))