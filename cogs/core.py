"""
cogs/core.py — on_ready, rotating status, global on_message dispatcher.
"""

import discord
from discord.ext import commands, tasks
import asyncio, random, time
import pytz, datetime
from datetime import datetime as dt, timezone, timedelta

from utils.db import (
    settings_col, afk_col, sticky_col, bump_col,
    levels_col, server_status_col, global_status_col
)
from utils.helpers import (
    BOT_OWNER_ID, log_event, get_server_data,
    is_premium_user, is_premium_server
)

STICKY_THRESHOLD = 1   # re-pin after every N messages
SESSION_TIMEOUT  = 300 # seconds


class Core(commands.Cog):
    def __init__(self, bot):
        self.bot        = bot
        self.sticky_ctr = {}   # {channel_id: count}
        self.sessions   = {}   # {channel_id: last_active_ts}

    # ── Startup ────────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_ready(self):
        print(f"\n  Happy is online as {self.bot.user} ({self.bot.user.id})")
        # Re-register persistent views
        from cogs.tickets import TicketCreateView, TicketCloseView
        from cogs.roles   import ButtonRolesView
        self.bot.add_view(TicketCreateView())
        self.bot.add_view(TicketCloseView())
        self.bot.add_view(ButtonRolesView())

        if not self.status_loop.is_running():
            self.status_loop.start()
        if not self.birthday_loop.is_running():
            self.birthday_loop.start()

        try:
            synced = await self.bot.tree.sync()
            print(f"  Synced {len(synced)} slash commands.\n")
        except Exception as e:
            print(f"  Sync error: {e}\n")

    # ── Status loop ────────────────────────────────────────────────────────────
    # HOW IT WORKS:
    # Discord only supports ONE global bot presence. So:
    # - Owner status   → overrides everything globally, optional 24h expiry
    # - Server status  → rotates into the pool, shown as "[ServerName] text"
    #                    so users know which server's status it is
    # - Default status → shown when no overrides exist
    # Priority: owner override > server statuses + defaults (mixed rotation)

    @tasks.loop(seconds=20)
    async def status_loop(self):
        await self.bot.wait_until_ready()
        from utils.db import global_status_col, server_status_col
        from datetime import datetime, timezone

        # ── 1. Owner global override (highest priority) ───────────────────────
        gov = await global_status_col.find_one({"type": "owner"})
        if gov:
            # Auto-expire 24h statuses
            if gov.get("expires_at"):
                expires = gov["expires_at"]
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > expires:
                    await global_status_col.delete_one({"type": "owner"})
                    gov = None

        if gov:
            atype = getattr(discord.ActivityType,
                            gov.get("activity", "watching"),
                            discord.ActivityType.watching)
            await self.bot.change_presence(
                activity=discord.Activity(type=atype, name=gov["status"])
            )
            return

        # ── 2. Build rotation pool ─────────────────────────────────────────────
        pool = []

        # Server statuses — only include if bot is still in that server
        server_docs = await server_status_col.find({}).to_list(50)
        for doc in server_docs:
            gid = doc.get("guild_id")
            if gid and not self.bot.get_guild(int(gid)):
                # Bot no longer in that server — skip silently
                continue
            guild_name = doc.get("guild_name", "")
            text       = doc.get("status", "")
            # Format: "[GuildName] status text" — capped at 128 chars
            label = f"[{guild_name}] {text}"[:128] if guild_name else text[:128]
            pool.append(label)

        # Default statuses (always in pool)
        defaults = [
            f"over {len(self.bot.guilds)} servers",
            f"{len(self.bot.users)} members",
            ",help · Happy Bot",
            "Happy Premium",
        ]
        pool.extend(defaults)

        if not pool:
            return

        text = random.choice(pool)
        await self.bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, name=text
            )
        )

    # ── Birthday loop ──────────────────────────────────────────────────────────
    @tasks.loop(hours=24)
    async def birthday_loop(self):
        await self.bot.wait_until_ready()
        from utils.db import birthdays_col
        now = dt.now(timezone.utc)
        async for doc in birthdays_col.find({"day": now.day, "month": now.month}):
            guild = self.bot.get_guild(int(doc.get("guild_id", 0)))
            if not guild:
                continue
            member = guild.get_member(int(doc["user_id"]))
            if not member:
                continue
            gs  = await settings_col.find_one({"_id": str(guild.id)})
            # Priority: dedicated birthday channel > welcome channel > system channel
            cid = (gs.get("birthday_channel")
                   or gs.get("welcome_channel")) if gs else None
            ch  = self.bot.get_channel(int(cid)) if cid else guild.system_channel
            if ch:
                embed = discord.Embed(
                    title="Happy Birthday!",
                    description=f"Today is {member.mention}'s birthday! Wish them a great day!",
                    color=0xF0C040
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_footer(text=f"Use ,birthday set DD/MM to register your birthday")
                await ch.send(embed=embed)

    # ── Global message handler ─────────────────────────────────────────────────
    # NOTE: This Cog listener handles SIDE EFFECTS only (AFK, sticky, XP, etc.)
    # process_commands is intentionally NOT called here.
    # discord.py's Bot.on_message fires automatically and handles command dispatch.
    # Calling process_commands here too would cause every command to run TWICE.
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            await self._check_disboard_bump(message)
            return

        if not message.guild:
            # DMs: Bot.on_message is suppressed, so we must handle process_commands here
            await self.bot.process_commands(message)
            return

        # ── 1. Anti-invite ──────────────────────────────────────────────────
        gs = await settings_col.find_one({"_id": str(message.guild.id)})
        if gs and gs.get("invite_block"):
            clean = message.content.lower().replace(" ", "")
            if "discord.gg/" in clean or "discord.com/invite/" in clean:
                if (not message.author.guild_permissions.administrator
                        and message.author.id != BOT_OWNER_ID):
                    try:
                        await message.delete()
                        await message.channel.send(
                            embed=discord.Embed(
                                description=f"{message.author.mention}, invite links are not allowed here.",
                                color=0xff0000
                            ), delete_after=5
                        )
                    except:
                        pass
                    return

        # ── 2. AFK return ───────────────────────────────────────────────────
        user_afk = await afk_col.find_one(
            {"user_id": str(message.author.id), "guild_id": str(message.guild.id)}
        )
        if user_afk:
            # 30-second grace period — don't clear AFK if they just set it
            afk_set_time = user_afk.get("time")
            if afk_set_time:
                if afk_set_time.tzinfo is None:
                    afk_set_time = afk_set_time.replace(tzinfo=timezone.utc)
                seconds_since = (dt.now(timezone.utc) - afk_set_time).total_seconds()
                if seconds_since < 30:
                    user_afk = None   # treat as not AFK yet — ignore this message
        if user_afk:
            away_str = ""
            if "time" in user_afk:
                t = user_afk["time"]
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                mins = int((dt.now(timezone.utc) - t).total_seconds() / 60)
                if mins > 0:
                    away_str = f" (away {mins}m)"
            await afk_col.delete_one({"user_id": str(message.author.id), "guild_id": str(message.guild.id)})
            try:
                if message.guild.me.guild_permissions.manage_nicknames:
                    new_nick = message.author.display_name.replace("[AFK] ", "")
                    await message.author.edit(nick=new_nick)
            except:
                pass
            await message.channel.send(
                embed=discord.Embed(
                    description=f"Welcome back, {message.author.mention}{away_str}!",
                    color=0x2B2D31
                ), delete_after=7
            )

        # ── 3. AFK mention notify ────────────────────────────────────────────
        # Skip @everyone / @here — only real member mentions
        real_mentions = [m for m in message.mentions
                         if not m.bot and m.id != message.author.id]
        for mentioned in real_mentions:
            t_afk = await afk_col.find_one(
                {"user_id": str(mentioned.id), "guild_id": str(message.guild.id)}
            )
            if t_afk:
                reason   = t_afk.get("reason", "Away from keyboard")
                afk_time = t_afk.get("time")
                if afk_time and afk_time.tzinfo is None:
                    afk_time = afk_time.replace(tzinfo=timezone.utc)
                ts_str   = f" (<t:{int(afk_time.timestamp())}:R>)" if afk_time else ""
                await message.reply(embed=discord.Embed(
                    description=f"💤 **{mentioned.display_name}** is AFK{ts_str}.\nReason: `{reason}`",
                    color=0x2B2D31
                ), mention_author=False)

        # ── 4. Sticky re-pin ─────────────────────────────────────────────────
        sticky_data = await sticky_col.find_one({"channel_id": message.channel.id})
        if sticky_data:
            cid = message.channel.id
            self.sticky_ctr[cid] = self.sticky_ctr.get(cid, 0) + 1
            if self.sticky_ctr[cid] >= STICKY_THRESHOLD:
                self.sticky_ctr[cid] = 0
                try:
                    old = await message.channel.fetch_message(sticky_data["message_id"])
                    await old.delete()
                except:
                    pass
                embed = discord.Embed(
                    description=sticky_data["content"], color=0x2B2D31
                )
                embed.set_footer(text="Sticky Message")
                new_s = await message.channel.send(embed=embed)
                await sticky_col.update_one(
                    {"channel_id": cid}, {"$set": {"message_id": new_s.id}}
                )

        # ── 5. XP (respect server toggle) ───────────────────────────────────
        if not gs or gs.get("levels_enabled", True):
            await self._award_xp(message)

        # ── 6. Heart reaction on greetings (respect server toggle) ───────────
        if not gs or gs.get("reactions_enabled", True):
            greetings = {"good morning","gm","good night","gn","happy birthday",
                         "hbd","hello","hi","welcome"}
            words = set(message.content.lower().split())
            if words & greetings:
                try:
                    await asyncio.sleep(random.uniform(0.2, 0.8))
                    await message.add_reaction("💖")
                except:
                    pass

        # ── 7. AI chat (delegated to ai_chat cog) ───────────────────────────
        ai_cog = self.bot.get_cog("AIChat")
        if ai_cog:
            await ai_cog.handle_message(message, self.sessions, SESSION_TIMEOUT)

        # ── 8. Global call relay ─────────────────────────────────────────────
        call_cog = self.bot.get_cog("Premium")
        if call_cog:
            await call_cog.relay_call(message)

        # ── 9. Process commands ───────────────────────────────────────────────
        # Must be called here. Bot.on_message (default) is suppressed in main.py
        # via @bot.event so it doesn't call process_commands a second time.
        await self.bot.process_commands(message)

    # ── Bump reminder helper ───────────────────────────────────────────────────
    async def _check_disboard_bump(self, message: discord.Message):
        if message.author.id != 302050872383242240:   # DISBOARD bot ID
            return
        if not message.embeds:
            return
        desc = message.embeds[0].description or ""
        if "Bump done" not in desc:
            return
        if not message.guild:
            return
        doc = await bump_col.find_one({"guild_id": str(message.guild.id)})
        if not doc or not doc.get("enabled") or not doc.get("channel_id"):
            return
        ch = self.bot.get_channel(int(doc["channel_id"]))
        if ch:
            await asyncio.sleep(7200)
            await ch.send(embed=discord.Embed(
                description="Time to bump the server! Use `/bump` on DISBOARD.",
                color=0x2B2D31
            ))

    # ── XP helper ─────────────────────────────────────────────────────────────
    async def _award_xp(self, message: discord.Message):
        import random as _r
        uid = str(message.author.id)
        gid = str(message.guild.id)
        doc  = await levels_col.find_one({"guild_id": gid, "user_id": uid})
        xp    = (doc.get("xp",0) if doc else 0) + _r.randint(5, 15)
        level = doc.get("level", 0) if doc else 0
        if xp >= (level + 1) * 100:
            xp    = 0
            level += 1
            await message.channel.send(
                embed=discord.Embed(
                    description=f"{message.author.mention} reached **Level {level}**!",
                    color=0x2B2D31
                ), delete_after=10
            )
        await levels_col.update_one(
            {"guild_id": gid, "user_id": uid},
            {"$set": {"xp": xp, "level": level}},
            upsert=True
        )

    # ── Member join/leave ──────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        data = await get_server_data(member.guild.id)
        if not data.get("welcome_enabled"):
            return
        cid = data.get("welcome_channel")
        ch  = self.bot.get_channel(cid) if cid else member.guild.system_channel
        if not ch:
            return

        welcome_cog = self.bot.get_cog("Welcome")
        custom_msg  = data.get("welcome_custom_msg")
        if welcome_cog:
            embed = welcome_cog._build_welcome_embed(member, member.guild, custom_msg)
        else:
            embed = discord.Embed(color=0x2B2D31)
            embed.set_author(
                name=f"Welcome to {member.guild.name}!",
                icon_url=member.guild.icon.url if member.guild.icon else None
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.description = (
                f"Hey {member.mention}, welcome!\n"
                f"You're member **#{member.guild.member_count}**."
            )
            embed.set_footer(text=f"Account created {member.created_at.strftime('%d %b %Y')}")
        await ch.send(embed=embed)
        await log_event(self.bot, member.guild, "member_join", f"{member} joined.")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        data = await get_server_data(member.guild.id)
        if not data.get("bye_enabled"):
            return
        cid = data.get("bye_channel")
        ch  = self.bot.get_channel(cid) if cid else None
        if not ch:
            return

        welcome_cog = self.bot.get_cog("Welcome")
        custom_msg  = data.get("bye_custom_msg")
        if welcome_cog:
            embed = welcome_cog._build_bye_embed(member, member.guild, custom_msg)
        else:
            embed = discord.Embed(color=0x2B2D31)
            embed.set_author(
                name=f"{member.display_name} left the server",
                icon_url=member.display_avatar.url
            )
            embed.description = (
                f"**{member.mention}** has left.\n"
                f"Members remaining: **{member.guild.member_count}**"
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(
                text=f"Joined {member.joined_at.strftime('%d %b %Y') if member.joined_at else 'Unknown'}"
            )
        await ch.send(embed=embed)
        await log_event(self.bot, member.guild, "member_leave", f"{member} left.")

    # ── Message edit / delete logs ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        await log_event(
            self.bot, message.guild, "message_delete",
            f"**{message.author}** deleted in {message.channel.mention}:\n> {message.content[:500]}"
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or not before.guild or before.content == after.content:
            return
        await log_event(
            self.bot, before.guild, "message_edit",
            f"**{before.author}** edited in {before.channel.mention}\n"
            f"**Before:** {before.content[:300]}\n**After:** {after.content[:300]}"
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        await log_event(self.bot, guild, "member_ban", f"**{user}** was banned.")

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user):
        await log_event(self.bot, guild, "member_unban", f"**{user}** was unbanned.")

    # ── Global slash error handler ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error):
        from discord import app_commands
        msg = str(error)
        if isinstance(error, app_commands.MissingPermissions):
            msg = "You don't have permission to use this command."
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"**Error:** {msg}", ephemeral=True)
            else:
                await interaction.followup.send(f"**Error:** {msg}", ephemeral=True)
        except:
            pass

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, (commands.MissingPermissions, commands.CheckFailure)):
            embed = discord.Embed(
                description=f"**Access Denied:** {str(error)}", color=0xff0000
            )
            return await ctx.reply(embed=embed, delete_after=8)
        if isinstance(error, commands.MissingRequiredArgument):
            embed = discord.Embed(
                description=f"Missing argument. Try `,help {ctx.command.name}`.",
                color=0xff0000
            )
            return await ctx.reply(embed=embed, delete_after=8)
        if isinstance(error, commands.BadArgument):
            embed = discord.Embed(
                description=f"Invalid argument: {error}", color=0xff0000
            )
            return await ctx.reply(embed=embed, delete_after=8)
        print(f"[Error][{ctx.command}] {error}")


async def setup(bot):
    await bot.add_cog(Core(bot))