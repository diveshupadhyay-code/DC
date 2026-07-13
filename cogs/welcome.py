import discord
from discord.ext import commands
from utils.db import settings_col, logs_col, counters_col
from utils.helpers import ctx_admin, ctx_mod, log_event, update_server_data

VARS_HELP = "`{user}` mention  `{username}` name  `{server}` server name  `{count}` member count  `{usertag}` user#0000"

class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def welcome(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        cid = gs.get("welcome_channel")
        ch = f"<#{cid}>" if cid else "Not set"
        en = "🟢 Enabled" if gs.get("welcome_enabled") else "🔴 Disabled"
        custom = gs.get("welcome_custom_msg")
        gif_url = gs.get("welcome_gif")
        
        embed = discord.Embed(title="⚙️ Welcome Configuration", color=0x2B2D31)
        embed.add_field(name="Status", value=en, inline=True)
        embed.add_field(name="Channel", value=ch, inline=True)
        
        msg_val = f"`{custom[:80]}...`" if custom and len(custom) > 80 else (f"`{custom}`" if custom else "✨ Default Message Active")
        embed.add_field(name="Welcome Message", value=msg_val, inline=False)
        
        gif_val = f"[Click to View]({gif_url})" if gif_url else "None"
        embed.add_field(name="Welcome GIF", value=gif_val, inline=False)
        
        embed.add_field(
            name="Available Commands",
            value=(
                "`,welcome set #channel` — Bind channel\n"
                "`,welcome enable` / `,welcome disable` — Toggle state\n"
                "`,welcome message <text>` — Set custom message\n"
                "`,welcome gif <url>` — Attach a welcome banner/GIF\n"
                "`,welcome resetmsg` — Reset to default text\n"
                "`,welcome resetgif` — Remove active GIF\n"
                "`,welcome test` — Send a test preview\n\n"
                f"**Placeholders:** {VARS_HELP}"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @welcome.command(name="set")
    @commands.has_permissions(administrator=True)
    async def welcome_set(self, ctx, channel: discord.TextChannel = None):
        if not channel:
            return await ctx.reply("❌ Mention a channel: `,welcome set #channel`")
        await update_server_data(ctx.guild.id, "welcome_channel", channel.id)
        await ctx.reply(f"✅ Welcome channel successfully set to {channel.mention}.")

    @welcome.command(name="enable")
    @commands.has_permissions(administrator=True)
    async def welcome_enable(self, ctx):
        await update_server_data(ctx.guild.id, "welcome_enabled", True)
        await ctx.reply("✅ Welcome messages have been enabled.")

    @welcome.command(name="disable")
    @commands.has_permissions(administrator=True)
    async def welcome_disable(self, ctx):
        await update_server_data(ctx.guild.id, "welcome_enabled", False)
        await ctx.reply("✅ Welcome messages have been disabled.")

    @welcome.command(name="test")
    @commands.has_permissions(administrator=True)
    async def welcome_test(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        custom = gs.get("welcome_custom_msg")
        gif = gs.get("welcome_gif")
        embed = self._build_welcome_embed(ctx.author, ctx.guild, custom, gif)
        await ctx.reply("📦 **Welcome Preview:**", embed=embed)

    @welcome.command(name="message", aliases=["msg", "setmsg"])
    @commands.has_permissions(administrator=True)
    async def welcome_message(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply(
                f"❌ Usage: `,welcome message <text>`\n"
                f"Variables: {VARS_HELP}"
            )
        if len(text) > 500:
            return await ctx.reply("❌ Custom message cannot exceed 500 characters.")
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"welcome_custom_msg": text}},
            upsert=True
        )
        preview = self._render(text, ctx.author, ctx.guild)
        embed = discord.Embed(title="✅ Welcome Message Saved", color=0x2B2D31)
        embed.add_field(name="Raw Output", value=f"`{text}`", inline=False)
        embed.add_field(name="Rendered Preview", value=preview, inline=False)
        await ctx.reply(embed=embed)

    @welcome.command(name="gif")
    @commands.has_permissions(administrator=True)
    async def welcome_gif(self, ctx, url: str = None):
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            return await ctx.reply("❌ Provide a valid image/GIF URL: `,welcome gif <url>`")
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"welcome_gif": url}},
            upsert=True
        )
        await ctx.reply("✅ Welcome banner/GIF successfully updated.")

    @welcome.command(name="resetmsg")
    @commands.has_permissions(administrator=True)
    async def welcome_resetmsg(self, ctx):
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$unset": {"welcome_custom_msg": ""}},
            upsert=True
        )
        await ctx.reply("✅ Welcome message layout reset to default.")

    @welcome.command(name="resetgif")
    @commands.has_permissions(administrator=True)
    async def welcome_resetgif(self, ctx):
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$unset": {"welcome_gif": ""}},
            upsert=True
        )
        await ctx.reply("✅ Welcome banner/GIF removed.")

    def _render(self, text: str, member: discord.Member, guild: discord.Guild) -> str:
        return (
            text
            .replace("{user}", member.mention)
            .replace("{username}", member.display_name)
            .replace("{usertag}", str(member))
            .replace("{server}", guild.name)
            .replace("{count}", str(guild.member_count))
        )

    def _build_welcome_embed(self, member: discord.Member, guild: discord.Guild, custom_msg: str = None, gif_url: str = None) -> discord.Embed:
        embed = discord.Embed(color=0x2B2D31)
        embed.set_author(name=f"Welcome to {guild.name}", icon_url=guild.icon.url if guild.icon else None)
        embed.set_thumbnail(url=member.display_avatar.url)
        
        if custom_msg:
            embed.description = self._render(custom_msg, member, guild)
        else:
            embed.description = (
                f"👋 Hey {member.mention}, welcome to the family!\n"
                f"We are now **{guild.member_count}** strong. Buckle up! 🎉"
            )
        
        if gif_url:
            embed.set_image(url=gif_url)
            
        embed.set_footer(text=f"ID: {member.id} • Created: {member.created_at.strftime('%d %b %Y')}")
        return embed

    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def bye(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        cid = gs.get("bye_channel")
        ch = f"<#{cid}>" if cid else "Not set"
        en = "🟢 Enabled" if gs.get("bye_enabled") else "🔴 Disabled"
        custom = gs.get("bye_custom_msg")
        gif_url = gs.get("bye_gif")
        
        embed = discord.Embed(title="⚙️ Goodbye Configuration", color=0x2B2D31)
        embed.add_field(name="Status", value=en, inline=True)
        embed.add_field(name="Channel", value=ch, inline=True)
        
        msg_val = f"`{custom[:80]}...`" if custom and len(custom) > 80 else (f"`{custom}`" if custom else "✨ Default Message Active")
        embed.add_field(name="Goodbye Message", value=msg_val, inline=False)
        
        gif_val = f"[Click to View]({gif_url})" if gif_url else "None"
        embed.add_field(name="Goodbye GIF", value=gif_val, inline=False)
        
        embed.add_field(
            name="Available Commands",
            value=(
                "`,bye set #channel` — Bind channel\n"
                "`,bye enable` / `,bye disable` — Toggle state\n"
                "`,bye message <text>` — Set custom message\n"
                "`,bye gif <url>` — Attach a goodbye banner/GIF\n"
                "`,bye resetmsg` — Reset to default text\n"
                "`,bye resetgif` — Remove active GIF\n"
                "`,bye test` — Send a test preview\n\n"
                f"**Placeholders:** {VARS_HELP}"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @bye.command(name="set")
    @commands.has_permissions(administrator=True)
    async def bye_set(self, ctx, channel: discord.TextChannel = None):
        if not channel:
            return await ctx.reply("❌ Mention a channel: `,bye set #channel`")
        await update_server_data(ctx.guild.id, "bye_channel", channel.id)
        await update_server_data(ctx.guild.id, "bye_enabled", True)
        await ctx.reply(f"✅ Goodbye channel set to {channel.mention} and activated.")

    @bye.command(name="enable")
    @commands.has_permissions(administrator=True)
    async def bye_enable(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        if not gs.get("bye_channel"):
            return await ctx.reply("❌ Configure a channel first: `,bye set #channel`")
        await update_server_data(ctx.guild.id, "bye_enabled", True)
        await ctx.reply("✅ Goodbye messages enabled.")

    @bye.command(name="disable")
    @commands.has_permissions(administrator=True)
    async def bye_disable(self, ctx):
        await update_server_data(ctx.guild.id, "bye_enabled", False)
        await ctx.reply("✅ Goodbye messages disabled.")

    @bye.command(name="test")
    @commands.has_permissions(administrator=True)
    async def bye_test(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        custom = gs.get("bye_custom_msg")
        gif = gs.get("bye_gif")
        embed = self._build_bye_embed(ctx.author, ctx.guild, custom, gif)
        await ctx.reply("📦 **Goodbye Preview:**", embed=embed)

    @bye.command(name="message", aliases=["msg", "setmsg"])
    @commands.has_permissions(administrator=True)
    async def bye_message(self, ctx, *, text: str = None):
        if not text:
            return await ctx.reply(
                f"❌ Usage: `,bye message <text>`\n"
                f"Variables: {VARS_HELP}"
            )
        if len(text) > 500:
            return await ctx.reply("❌ Custom message cannot exceed 500 characters.")
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"bye_custom_msg": text}},
            upsert=True
        )
        preview = self._render(text, ctx.author, ctx.guild)
        embed = discord.Embed(title="✅ Goodbye Message Saved", color=0x2B2D31)
        embed.add_field(name="Raw Output", value=f"`{text}`", inline=False)
        embed.add_field(name="Rendered Preview", value=preview, inline=False)
        await ctx.reply(embed=embed)

    @bye.command(name="gif")
    @commands.has_permissions(administrator=True)
    async def bye_gif(self, ctx, url: str = None):
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            return await ctx.reply("❌ Provide a valid image/GIF URL: `,bye gif <url>`")
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"bye_gif": url}},
            upsert=True
        )
        await ctx.reply("✅ Goodbye banner/GIF successfully updated.")

    @bye.command(name="resetmsg")
    @commands.has_permissions(administrator=True)
    async def bye_resetmsg(self, ctx):
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$unset": {"bye_custom_msg": ""}},
            upsert=True
        )
        await ctx.reply("✅ Goodbye message layout reset to default.")

    @bye.command(name="resetgif")
    @commands.has_permissions(administrator=True)
    async def bye_resetgif(self, ctx):
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$unset": {"bye_gif": ""}},
            upsert=True
        )
        await ctx.reply("✅ Goodbye banner/GIF removed.")

    def _build_bye_embed(self, member: discord.Member, guild: discord.Guild, custom_msg: str = None, gif_url: str = None) -> discord.Embed:
        embed = discord.Embed(color=0x2B2D31)
        embed.set_author(name=f"{member.display_name} Left", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        
        if custom_msg:
            embed.description = self._render(custom_msg, member, guild)
        else:
            embed.description = (
                f"🏃‍♂️ **{member.mention}** just walked out of the server.\n"
                f"We're down to **{guild.member_count}** members now."
            )
            
        if gif_url:
            embed.set_image(url=gif_url)
            
        embed.set_footer(text=f"Joined: {member.joined_at.strftime('%d %b %Y') if member.joined_at else 'Unknown Timeline'}")
        return embed

    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def logs(self, ctx):
        cfg = await logs_col.find_one({"guild_id": str(ctx.guild.id)})
        ch = f"<#{cfg['channel_id']}>" if cfg and cfg.get("channel_id") else "Not set"
        embed = discord.Embed(title="📋 System Logs Configuration", color=0x2B2D31)
        embed.add_field(name="Target Channel", value=ch, inline=True)
        embed.add_field(
            name="Tracked Events",
            value="`Joins/Leaves`, `Bans/Kicks`, `Message Edits/Deletions`, `Channel Locks`, `Mod Logs`",
            inline=False
        )
        embed.add_field(name="Management Commands", value="`,logs set #channel` | `,logs disable`", inline=False)
        await ctx.reply(embed=embed)

    @logs.command(name="set")
    @commands.has_permissions(administrator=True)
    async def logs_set(self, ctx, channel: discord.TextChannel = None):
        if not channel:
            return await ctx.reply("❌ Mention a channel: `,logs set #channel`")
        await logs_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"channel_id": str(channel.id)}},
            upsert=True
        )
        await ctx.reply(f"✅ Logging stream targeted to {channel.mention}.")

    @logs.command(name="disable")
    @commands.has_permissions(administrator=True)
    async def logs_disable(self, ctx):
        await logs_col.delete_one({"guild_id": str(ctx.guild.id)})
        await ctx.reply("✅ Logging engine offline.")

    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def automod(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        inv = "🟢 Active Protection" if gs.get("invite_block") else "🔴 Unprotected"
        embed = discord.Embed(title="🛡️ AutoMod Settings", color=0x2B2D31)
        embed.add_field(name="Anti-Invite Links", value=inv, inline=True)
        embed.add_field(name="Toggle Commands", value="`,automod invite on` / `,automod invite off`", inline=False)
        await ctx.reply(embed=embed)

    @automod.command(name="invite")
    @commands.has_permissions(administrator=True)
    async def automod_invite(self, ctx, status: str = None):
        if not status or status.lower() not in ("on", "off"):
            return await ctx.reply("❌ Usage: `,automod invite on` or `,automod invite off`")
        state = status.lower() == "on"
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"invite_block": state}},
            upsert=True
        )
        await ctx.reply(f"✅ Anti-invite policy updated: **{status.upper()}**.")

    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def counter(self, ctx):
        embed = discord.Embed(
            title="📊 Dynamic Channel Counters",
            description=(
                "Sync live statistics directly onto your voice channel layouts.\n\n"
                "**Setup System:**\n"
                "`,counter create members #vc-channel`\n"
                "`,counter create bots #vc-channel`\n"
                "`,counter create channels #vc-channel`"
            ),
            color=0x2B2D31
        )
        await ctx.reply(embed=embed)

    @counter.command(name="create")
    @commands.has_permissions(administrator=True)
    async def counter_create(self, ctx, ctype: str = None, channel: discord.VoiceChannel = None):
        if not ctype or ctype.lower() not in ("members", "bots", "channels") or not channel:
            return await ctx.reply("❌ Correct usage: `,counter create members/bots/channels #vc`")
        await counters_col.update_one(
            {"guild_id": str(ctx.guild.id), "type": ctype.lower()},
            {"$set": {"channel_id": str(channel.id)}},
            upsert=True
        )
        await self._update_counters(ctx.guild)
        await ctx.reply(f"✅ Tracker matrix updated. Linked `{ctype}` metric streams into {channel.mention}.")

    async def _update_counters(self, guild: discord.Guild):
        async for doc in counters_col.find({"guild_id": str(guild.id)}):
            ch = guild.get_channel(int(doc["channel_id"]))
            if not ch:
                continue
            ctype = doc["type"]
            try:
                if ctype == "members":
                    await ch.edit(name=f"Members: {guild.member_count}")
                elif ctype == "bots":
                    bots = sum(1 for m in guild.members if m.bot)
                    await ch.edit(name=f"Bots: {bots}")
                elif ctype == "channels":
                    await ch.edit(name=f"Channels: {len(guild.channels)}")
            except:
                pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._update_counters(member.guild)
        gs = await settings_col.find_one({"_id": str(member.guild.id)}) or {}
        if gs.get("welcome_enabled") and gs.get("welcome_channel"):
            channel = member.guild.get_channel(int(gs["welcome_channel"]))
            if channel:
                custom = gs.get("welcome_custom_msg")
                gif = gs.get("welcome_gif")
                embed = self._build_welcome_embed(member, member.guild, custom, gif)
                await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._update_counters(member.guild)
        gs = await settings_col.find_one({"_id": str(member.guild.id)}) or {}
        if gs.get("bye_enabled") and gs.get("bye_channel"):
            channel = member.guild.get_channel(int(gs["bye_channel"]))
            if channel:
                custom = gs.get("bye_custom_msg")
                gif = gs.get("bye_gif")
                embed = self._build_bye_embed(member, member.guild, custom, gif)
                await channel.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Welcome(bot))