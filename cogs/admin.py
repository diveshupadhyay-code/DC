"""
cogs/admin.py — Settings dashboard, prefix, announce, giveaway, server config,
                 command disable/enable, slowmode, channel tools,
                 giveaway reroll, owner tools.
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio, random, re
from datetime import datetime, timedelta, timezone

from utils.db import (
    settings_col, personal_prefix_col, premium_col,
    logs_col, bump_col, tickets_col, disabled_cmds_col,
    voicemaster_col, counters_col, db
)
from utils.helpers import (
    BOT_OWNER_ID, ctx_owner, ctx_admin, ctx_mod, ctx_premium,
    is_premium_server, is_premium_user,
    update_server_data, get_server_data, log_event
)

giveaways_col = db["giveaways"]


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.giveaway_checker.start()

    def cog_unload(self):
        self.giveaway_checker.cancel()

    @tasks.loop(seconds=20)
    async def giveaway_checker(self):
        await self.bot.wait_until_ready()
        now = datetime.now(timezone.utc)
        cursor = giveaways_col.find({"status": "active", "end_time": {"$lte": now}})
        async for doc in cursor:
            channel = self.bot.get_channel(int(doc["channel_id"]))
            if not channel:
                await giveaways_col.update_one({"_id": doc["_id"]}, {"$set": {"status": "ended"}})
                continue
            await self._end_giveaway(channel, int(doc["message_id"]))

    @giveaway_checker.before_loop
    async def before_giveaway_checker(self):
        await self.bot.wait_until_ready()

    # ══════════════════════════════════════════════════════════════════════════
    #  SETTINGS DASHBOARD
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["config", "panel"])
    @commands.has_permissions(administrator=True)
    async def settings(self, ctx):
        """Full server configuration dashboard."""
        gid = str(ctx.guild.id)

        gs, log_cfg, bump_cfg, ticket_cfg, vc_cfg = await asyncio.gather(
            settings_col.find_one({"_id": gid}),
            logs_col.find_one({"guild_id": gid}),
            bump_col.find_one({"guild_id": gid}),
            tickets_col.find_one({"_id": gid}),
            voicemaster_col.find_one({"guild_id": gid}),
        )
        gs         = gs         or {}
        log_cfg    = log_cfg    or {}
        ticket_cfg = ticket_cfg or {}

        counter_docs = await counters_col.find({"guild_id": gid}).to_list(10)
        counter_list = ", ".join(d["type"] for d in counter_docs) or "None"

        dis_docs = await disabled_cmds_col.find({"guild_id": gid}).to_list(20)
        dis_list = ", ".join(f"`{d['command_name']}`" for d in dis_docs) or "None"

        def _ch(key):
            cid = gs.get(key)
            return f"<#{cid}>" if cid else "Not set"

        def _on(key):
            return "On" if gs.get(key) else "Off"

        is_prem  = await is_premium_server(ctx.guild.id)
        log_ch   = f"<#{log_cfg['channel_id']}>" if log_cfg.get("channel_id") else "Not set"

        embed = discord.Embed(
            title=f"Settings — {ctx.guild.name}",
            color=0x2B2D31,
            timestamp=datetime.now(timezone.utc)
        )
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)

        embed.add_field(
            name="General",
            value=f"Prefix: `{gs.get('prefix', ',')}`\nPremium: {'Yes' if is_prem else 'No'}",
            inline=True
        )
        embed.add_field(
            name="Welcome / Bye",
            value=f"Welcome: {_on('welcome_enabled')} → {_ch('welcome_channel')}\nBye: {_on('bye_enabled')} → {_ch('bye_channel')}",
            inline=True
        )
        embed.add_field(
            name="Logging",
            value=f"Channel: {log_ch}",
            inline=True
        )
        embed.add_field(
            name="Moderation",
            value=f"Anti-Invite: {_on('invite_block')}\nJail: {'Configured' if gs.get('jail_role_id') else 'Not set'}",
            inline=True
        )
        embed.add_field(
            name="Features",
            value=(
                f"Level-up msgs: {_on('levels_enabled') if 'levels_enabled' in gs else 'On'}\n"
                f"Auto reactions: {_on('reactions_enabled') if 'reactions_enabled' in gs else 'On'}"
            ),
            inline=True
        )
        embed.add_field(
            name="Tickets",
            value=f"Total opened: {ticket_cfg.get('ticket_count', 0)}\nStaff role: {'Set' if ticket_cfg.get('staff_role_id') else 'Not set'}",
            inline=True
        )
        embed.add_field(
            name="Premium Features",
            value=f"Bump Reminder: {'On' if bump_cfg and bump_cfg.get('enabled') else 'Off'}\nVoiceMaster: {'Configured' if vc_cfg else 'Not set'}\nCounters: {counter_list}",
            inline=True
        )
        embed.add_field(name="Disabled Commands", value=dis_list, inline=False)
        embed.set_footer(text=f"Server ID: {gid} • ,help admin for setup commands")
        await ctx.reply(embed=embed)

    @app_commands.command(name="settings", description="View server configuration dashboard")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_settings(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gid     = str(interaction.guild.id)
        gs      = await settings_col.find_one({"_id": gid}) or {}
        is_prem = await is_premium_server(interaction.guild.id)
        embed   = discord.Embed(title=f"Settings — {interaction.guild.name}", color=0x2B2D31)
        embed.add_field(name="Prefix",  value=f"`{gs.get('prefix', ',')}`", inline=True)
        embed.add_field(name="Premium", value="Yes" if is_prem else "No",   inline=True)
        embed.add_field(name="Welcome", value="On" if gs.get("welcome_enabled") else "Off", inline=True)
        embed.set_footer(text="Use ,settings for full details")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  PREFIX
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(invoke_without_command=True)
    async def prefix(self, ctx):
        """View prefix info and sub-commands."""
        gs       = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        current  = gs.get("prefix", ",")
        personal = await personal_prefix_col.find_one({"user_id": str(ctx.author.id)})
        pp       = personal.get("prefix") if personal else None
        is_prem  = await is_premium_user(ctx.author.id) or await is_premium_server(ctx.guild.id)

        embed = discord.Embed(title="Prefix Settings", color=0x2B2D31)
        embed.add_field(name="Server Prefix",   value=f"`{current}`",              inline=True)
        embed.add_field(name="Personal Prefix", value=f"`{pp}`" if pp else "None", inline=True)
        embed.add_field(name="Premium",         value="Yes" if is_prem else "No",  inline=True)
        embed.add_field(
            name="Commands",
            value=(
                "`,prefix set <symbol>` — set server prefix (Admin)\n"
                "`,prefix remove` — reset to `,` (Admin)\n"
                "`,prefix self <symbol>` — personal prefix, all servers (Premium)\n"
                "`,prefix selfremove` — remove personal prefix"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @prefix.command(name="set")
    @commands.has_permissions(administrator=True)
    async def prefix_set(self, ctx, new_prefix: str = None):
        """Set the server prefix (Admin only, max 3 chars)."""
        if not new_prefix:
            return await ctx.reply("Usage: `,prefix set <symbol>`")
        if len(new_prefix) > 3:
            return await ctx.reply("Prefix must be 3 characters or fewer.")
        if new_prefix in ("<", ">", "@", "#"):
            return await ctx.reply(f"`{new_prefix}` conflicts with Discord syntax.")
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)}, {"$set": {"prefix": new_prefix}}, upsert=True
        )
        await ctx.reply(f"Server prefix updated to `{new_prefix}`.")
        await log_event(self.bot, ctx.guild, "prefix_change",
                        f"{ctx.author} changed prefix to `{new_prefix}`.")

    @prefix.command(name="remove")
    @commands.has_permissions(administrator=True)
    async def prefix_remove(self, ctx):
        """Reset server prefix to the default `,`."""
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)}, {"$unset": {"prefix": ""}}, upsert=True
        )
        await ctx.reply("Server prefix reset to default `,`.")

    @prefix.command(name="self")
    @ctx_premium()
    async def prefix_self(self, ctx, new_prefix: str = None):
        """Set a personal prefix that works across ALL servers (Premium)."""
        if not new_prefix:
            return await ctx.reply("Usage: `,prefix self <symbol>`")
        if len(new_prefix) > 3:
            return await ctx.reply("Personal prefix must be 3 characters or fewer.")
        await personal_prefix_col.update_one(
            {"user_id": str(ctx.author.id)}, {"$set": {"prefix": new_prefix}}, upsert=True
        )
        await ctx.reply(f"Personal prefix set to `{new_prefix}` across all servers.")

    @prefix.command(name="selfremove")
    async def prefix_selfremove(self, ctx):
        """Remove your personal prefix."""
        result = await personal_prefix_col.delete_one({"user_id": str(ctx.author.id)})
        await ctx.reply("Personal prefix removed." if result.deleted_count else "No personal prefix set.")

    # ══════════════════════════════════════════════════════════════════════════
    #  ANNOUNCE
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @ctx_mod()
    async def announce(self, ctx, channel: discord.TextChannel = None, *, content: str = None):
        """
        Send an announcement embed.
        Usage: `,announce [#channel] <message>`
        Add --ping anywhere to include @everyone.
        """
        if not content:
            if channel:
                content = channel.name
                channel = None
            else:
                return await ctx.reply(
                    "Usage: `,announce [#channel] <message>`\n"
                    "Add `--ping` to include @everyone."
                )

        ping    = "--ping" in content
        content = content.replace("--ping", "").strip()
        if not content:
            return await ctx.reply("Message cannot be empty.")

        target = channel or ctx.channel
        embed  = discord.Embed(description=content, color=0x2B2D31, timestamp=datetime.now(timezone.utc))
        embed.set_author(
            name=ctx.guild.name,
            icon_url=ctx.guild.icon.url if ctx.guild.icon else None
        )
        embed.set_footer(
            text=f"Announced by {ctx.author.display_name}",
            icon_url=ctx.author.display_avatar.url
        )
        await target.send(content="@everyone" if ping else None, embed=embed)
        if target != ctx.channel:
            await ctx.reply(f"Announcement sent to {target.mention}.", delete_after=5)
        try:
            await ctx.message.delete()
        except:
            pass
        await log_event(self.bot, ctx.guild, "announcement",
                        f"{ctx.author} announced in {target.mention}. Ping: {ping}")

    @app_commands.command(name="announce", description="Send a server announcement")
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.describe(
        title="Announcement heading",
        description="Body text",
        channel="Target channel (defaults to current)",
        color="Hex color e.g. #FF5500",
        ping_everyone="Ping @everyone"
    )
    async def slash_announce(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
        channel: discord.TextChannel = None,
        color: str = None,
        ping_everyone: bool = False
    ):
        target = channel or interaction.channel
        try:
            embed_color = int(color.replace("#", ""), 16) if color else 0x2B2D31
        except:
            embed_color = 0x2B2D31
        embed = discord.Embed(
            title=title, description=description,
            color=embed_color, timestamp=datetime.now(timezone.utc)
        )
        embed.set_author(
            name=interaction.guild.name,
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None
        )
        embed.set_footer(text=f"Announced by {interaction.user.display_name}")
        await target.send(content="@everyone" if ping_everyone else None, embed=embed)
        await interaction.response.send_message(f"Sent to {target.mention}.", ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  GIVEAWAY
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(invoke_without_command=True)
    @ctx_mod()
    async def giveaway(self, ctx, duration: str = None, winners: int = 1, *, prize: str = None):
        """
        Start a giveaway with optional entry conditions.
        Usage: ,giveaway <duration> <winners> <prize> [--msgs <n>] [--invites <n>]
        Duration: 30m  2h  1d  or plain minutes
        """
        if not duration or not prize:
            embed = discord.Embed(title="Giveaway Commands", color=0x2B2D31)
            embed.add_field(
                name="Start",
                value=(
                    "`,giveaway <duration> <winners> <prize>`\n"
                    "Duration: `30m` `2h` `1d` or minutes\n\n"
                    "Optional conditions, add anywhere after prize:\n"
                    "`--msgs <n>` — need n messages to enter\n"
                    "`--invites <n>` — need n invites to enter\n\n"
                    "Example: `,giveaway 1h 1 Nitro --msgs 50 --invites 2`"
                ),
                inline=False
            )
            embed.add_field(name="End early", value="`,giveaway end <message_id>`",    inline=True)
            embed.add_field(name="Reroll",    value="`,giveaway reroll <message_id>`", inline=True)
            return await ctx.reply(embed=embed)

        minutes = self._parse_duration(duration)
        if minutes is None:
            return await ctx.reply("Invalid duration. Use `30m`, `2h`, `1d`, or plain minutes.")
        if minutes < 1:
            return await ctx.reply("Minimum duration is 1 minute.")
        if minutes > 43200:
            return await ctx.reply("Maximum duration is 30 days.")

        min_msgs    = 0
        min_invites = 0

        msgs_match = re.search(r"--msgs\s+(\d+)", prize)
        if msgs_match:
            min_msgs = int(msgs_match.group(1))
            prize    = prize[:msgs_match.start()] + prize[msgs_match.end():]

        inv_match = re.search(r"--invites\s+(\d+)", prize)
        if inv_match:
            min_invites = int(inv_match.group(1))
            prize       = prize[:inv_match.start()] + prize[inv_match.end():]

        prize = prize.strip()
        if not prize:
            return await ctx.reply("Prize cannot be empty.")

        end_time = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        ts       = int(end_time.timestamp())

        desc_lines = ["React with <a:tada:1522638851250720969> to enter!", ""]
        desc_lines.append(f"Winners: **{winners}**")
        desc_lines.append(f"Ends: <t:{ts}:R> (<t:{ts}:f>)")

        if min_msgs or min_invites:
            desc_lines.append("")
            desc_lines.append("**Entry Requirements:**")
            if min_msgs:
                desc_lines.append(f"— Minimum **{min_msgs}** messages in this server")
            if min_invites:
                desc_lines.append(f"— Minimum **{min_invites}** invite(s) accepted")

        embed = discord.Embed(
            title=f"Giveaway — {prize}",
            description="\n".join(desc_lines),
            color=0x2B2D31
        )

        footer_parts = [f"Hosted by {ctx.author.display_name}", f"{winners} winner(s)"]
        if min_msgs:
            footer_parts.append(f"{min_msgs}+ msgs required")
        if min_invites:
            footer_parts.append(f"{min_invites}+ invites required")
        embed.set_footer(
            text=" • ".join(footer_parts),
            icon_url=ctx.author.display_avatar.url
        )
        try:
            await ctx.message.delete()
        except:
            pass

        msg = await ctx.channel.send(embed=embed)
        await msg.add_reaction("<a:tada:1522638851250720969>")

        await giveaways_col.insert_one({
            "message_id":  str(msg.id),
            "channel_id":  str(ctx.channel.id),
            "guild_id":    str(ctx.guild.id),
            "host_id":     str(ctx.author.id),
            "prize":       prize,
            "winners":     winners,
            "min_msgs":    min_msgs,
            "min_invites": min_invites,
            "end_time":    end_time,
            "status":      "active",
        })

    @giveaway.command(name="end")
    @ctx_mod()
    async def giveaway_end(self, ctx, message_id: int = None):
        """End a running giveaway early."""
        if not message_id:
            return await ctx.reply("Usage: `,giveaway end <message_id>`")
        doc = await giveaways_col.find_one({"message_id": str(message_id), "status": "active"})
        if not doc:
            return await ctx.reply("No active giveaway found with that message ID.")
        channel = self.bot.get_channel(int(doc["channel_id"])) or ctx.channel
        await self._end_giveaway(channel, message_id)
        await ctx.reply("Giveaway ended.", delete_after=4)

    @giveaway.command(name="reroll")
    @ctx_mod()
    async def giveaway_reroll(self, ctx, message_id: int = None):
        """Reroll winners for an ended giveaway."""
        if not message_id:
            return await ctx.reply("Usage: `,giveaway reroll <message_id>`")
        data = await giveaways_col.find_one({"message_id": str(message_id)})
        if not data:
            return await ctx.reply("Giveaway not found in the database.")
        try:
            msg      = await ctx.channel.fetch_message(message_id)
            reaction = next((r for r in msg.reactions if str(r.emoji) == "<a:tada:1522638851250720969>"), None)
            if not reaction:
                return await ctx.reply("No <a:tada:1522638851250720969> reactions found on that message.")
            users = [u async for u in reaction.users() if not u.bot]
            if not users:
                return await ctx.reply("No valid entries to reroll from.")

            eligible = await self._filter_eligible(ctx.guild, users, data.get("min_msgs", 0), data.get("min_invites", 0))
            if not eligible:
                return await ctx.reply("No entrants currently meet the entry requirements.")

            chosen   = random.sample(eligible, min(data["winners"], len(eligible)))
            mentions = ", ".join(w.mention for w in chosen)
            await ctx.reply(embed=discord.Embed(
                title="Giveaway Rerolled",
                description=f"New winner(s) for **{data['prize']}**: {mentions}",
                color=0xffd700
            ))
        except discord.NotFound:
            await ctx.reply("Message not found in this channel.")
        except Exception as e:
            await ctx.reply(f"Reroll error: {e}")

    async def _filter_eligible(self, guild, users, min_msgs, min_invites):
        if not min_msgs and not min_invites:
            return list(users)
        msg_col = db["message_counts"]
        inv_col = db["invite_tracker"]
        eligible = []
        for user in users:
            ok = True
            if min_msgs:
                m = await msg_col.find_one({"guild_id": str(guild.id), "user_id": str(user.id)})
                if (m.get("count", 0) if m else 0) < min_msgs:
                    ok = False
            if ok and min_invites:
                docs = await inv_col.find({"guild_id": str(guild.id), "inviter_id": str(user.id)}).to_list(100)
                if sum(d.get("uses", 0) for d in docs) < min_invites:
                    ok = False
            if ok:
                eligible.append(user)
        return eligible

    async def _end_giveaway(self, channel, msg_id: int):
        data = await giveaways_col.find_one({"message_id": str(msg_id), "status": "active"})
        if not data:
            return

        await giveaways_col.update_one({"message_id": str(msg_id)}, {"$set": {"status": "ended"}})

        try:
            msg = await channel.fetch_message(msg_id)
        except (discord.NotFound, discord.Forbidden):
            return

        reaction = next((r for r in msg.reactions if str(r.emoji) == "<a:tada:1522638851250720969>"), None)
        if not reaction:
            no_entry = discord.Embed(
                title="Giveaway Ended",
                description=f"Prize: **{data['prize']}**\n\nNo one entered this giveaway.",
                color=0xED4245
            )
            try:
                await msg.edit(embed=no_entry)
            except:
                pass
            await channel.send(embed=no_entry)
            return

        reactors = [u async for u in reaction.users() if not u.bot]
        if not reactors:
            no_entry = discord.Embed(
                title="Giveaway Ended",
                description=f"Prize: **{data['prize']}**\n\nNo valid entries.",
                color=0xED4245
            )
            try:
                await msg.edit(embed=no_entry)
            except:
                pass
            await channel.send(embed=no_entry)
            return

        min_msgs    = data.get("min_msgs", 0)
        min_invites = data.get("min_invites", 0)
        eligible    = await self._filter_eligible(channel.guild, reactors, min_msgs, min_invites)
        disqualified_count = len(reactors) - len(eligible)

        if not eligible:
            cond_parts = []
            if min_msgs:
                cond_parts.append(f"{min_msgs}+ messages")
            if min_invites:
                cond_parts.append(f"{min_invites}+ invites")
            no_win_embed = discord.Embed(
                title="Giveaway Ended — No Eligible Entrants",
                description=(
                    f"Prize: **{data['prize']}**\n\n"
                    f"No one met the requirements: {' and '.join(cond_parts)}\n"
                    f"Total reactors: {len(reactors)}  •  Eligible: 0"
                ),
                color=0xED4245
            )
            no_win_embed.set_footer(text="Use ,giveaway reroll <message_id> to reroll once requirements are met")
            try:
                await msg.edit(embed=no_win_embed)
            except:
                pass
            await channel.send(embed=no_win_embed)
            return

        chosen   = random.sample(eligible, min(data["winners"], len(eligible)))
        mentions = ", ".join(w.mention for w in chosen)

        result_lines = [f"Prize: **{data['prize']}**", f"Winner(s): {mentions}"]
        if min_msgs or min_invites:
            cond_parts = []
            if min_msgs:
                cond_parts.append(f"{min_msgs}+ msgs")
            if min_invites:
                cond_parts.append(f"{min_invites}+ invites")
            result_lines.append(f"Conditions: {', '.join(cond_parts)}")
            result_lines.append(f"Eligible entrants: {len(eligible)}/{len(reactors)}")

        new_embed = discord.Embed(
            title="Giveaway Ended",
            description="\n".join(result_lines),
            color=discord.Color.gold()
        )
        new_embed.set_footer(text="Use ,giveaway reroll <message_id> to reroll")

        try:
            await msg.edit(embed=new_embed)
        except:
            pass

        await channel.send(
            content=mentions,
            embed=discord.Embed(
                description=f"Congratulations {mentions}! You won **{data['prize']}**!",
                color=discord.Color.gold()
            )
        )

        if disqualified_count:
            await channel.send(
                embed=discord.Embed(
                    description=f"{disqualified_count} entrant(s) did not meet the requirements and were excluded.",
                    color=0x2B2D31
                ),
                delete_after=30
            )

    @staticmethod
    def _parse_duration(s: str):
        """Parse '30m', '2h', '1d' or plain int → minutes. Returns None on failure."""
        m = re.fullmatch(r"(\d+)(m|h|d)?", s.strip().lower())
        if not m:
            return None
        val, unit = int(m.group(1)), m.group(2) or "m"
        return val * {"m": 1, "h": 60, "d": 1440}[unit]

    # ══════════════════════════════════════════════════════════════════════════
    #  SLOWMODE
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @ctx_mod()
    async def slowmode(self, ctx, seconds: int = None, channel: discord.TextChannel = None):
        """
        Set channel slowmode.
        Usage: `,slowmode <seconds> [#channel]`   — use 0 to disable.
        """
        if seconds is None:
            return await ctx.reply("Usage: `,slowmode <seconds> [#channel]`\nUse `0` to disable. Max `21600`.")
        if not 0 <= seconds <= 21600:
            return await ctx.reply("Value must be between 0 and 21600 seconds.")
        target = channel or ctx.channel
        await target.edit(slowmode_delay=seconds)
        msg = f"Slowmode {'disabled' if seconds == 0 else f'set to **{seconds}s**'} in {target.mention}."
        await ctx.reply(msg)
        await log_event(self.bot, ctx.guild, "slowmode",
                        f"{ctx.author} set slowmode to {seconds}s in {target}.")

    @app_commands.command(name="slowmode", description="Set channel slowmode delay")
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.describe(seconds="0 to disable, max 21600", channel="Target channel")
    async def slash_slowmode(self, interaction: discord.Interaction, seconds: int,
                              channel: discord.TextChannel = None):
        if not 0 <= seconds <= 21600:
            return await interaction.response.send_message("Value must be 0–21600.", ephemeral=True)
        target = channel or interaction.channel
        await target.edit(slowmode_delay=seconds)
        await interaction.response.send_message(
            f"Slowmode {'disabled' if seconds == 0 else f'set to {seconds}s'} in {target.mention}.",
            ephemeral=True
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  DISABLE / ENABLE COMMANDS
    # ══════════════════════════════════════════════════════════════════════════

    _protected_cmds = {"settings", "prefix", "disable", "enable", "help",
                       "premium", "maintenance", "aimode", "sync"}

    @commands.command(name="disable")
    @commands.has_permissions(administrator=True)
    async def disable_command(self, ctx, *, cmd_name: str = None):
        """Disable a bot command for this server. Usage: `,disable <command>`"""
        if not cmd_name:
            docs  = await disabled_cmds_col.find({"guild_id": str(ctx.guild.id)}).to_list(50)
            names = [d["command_name"] for d in docs]
            embed = discord.Embed(title="Disabled Commands", color=0x2B2D31)
            embed.description = (
                f"Disabled: {', '.join(f'`{n}`' for n in names) if names else 'None'}\n\n"
                "`,disable <command>` — disable\n"
                "`,enable <command>` — re-enable"
            )
            return await ctx.reply(embed=embed)

        cmd_name = cmd_name.lower().strip()
        if cmd_name in self._protected_cmds:
            return await ctx.reply(f"`{cmd_name}` cannot be disabled.")
        if not self.bot.get_command(cmd_name):
            return await ctx.reply(f"Command `{cmd_name}` not found.")
        await disabled_cmds_col.update_one(
            {"guild_id": str(ctx.guild.id), "command_name": cmd_name},
            {"$set": {"guild_id": str(ctx.guild.id), "command_name": cmd_name}},
            upsert=True
        )
        await ctx.reply(f"Command `{cmd_name}` disabled in this server.")

    @commands.command(name="enable")
    @commands.has_permissions(administrator=True)
    async def enable_command(self, ctx, *, cmd_name: str = None):
        """Re-enable a disabled command. Usage: `,enable <command>`"""
        if not cmd_name:
            return await ctx.reply("Usage: `,enable <command_name>`")
        result = await disabled_cmds_col.delete_one(
            {"guild_id": str(ctx.guild.id), "command_name": cmd_name.lower().strip()}
        )
        await ctx.reply(
            f"Command `{cmd_name}` re-enabled." if result.deleted_count
            else f"`{cmd_name}` was not disabled."
        )

    @commands.Cog.listener()
    async def on_command(self, ctx):
        """Block disabled commands before they execute."""
        if not ctx.guild or ctx.author.id == BOT_OWNER_ID or ctx.command is None:
            return
        doc = await disabled_cmds_col.find_one(
            {"guild_id": str(ctx.guild.id), "command_name": ctx.command.name}
        )
        if doc:
            await ctx.reply(
                f"`{ctx.command.name}` is disabled in this server.", delete_after=6
            )
            raise commands.DisabledCommand()

    # ══════════════════════════════════════════════════════════════════════════
    #  PREMIUM ROLE
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def premiumrole(self, ctx, role: discord.Role = None):
        """
        Set which role grants premium AI access.
        Members who receive this role automatically get AI chat.
        """
        if not role:
            gs  = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
            rid = gs.get("premium_role_id")
            cur = ctx.guild.get_role(rid) if rid else None
            return await ctx.reply(
                f"Current premium role: {cur.mention if cur else 'Not set'}\n"
                f"Usage: `,premiumrole @role`"
            )
        await update_server_data(ctx.guild.id, "premium_role_id", role.id)
        await ctx.reply(
            f"Premium role set to {role.mention}. Members with this role get AI chat access."
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Auto-grant/revoke per-user premium when a member gains/loses the premium role."""
        gs  = await settings_col.find_one({"_id": str(after.guild.id)}) or {}
        rid = gs.get("premium_role_id")
        if not rid:
            return
        had = discord.utils.get(before.roles, id=rid) is not None
        has = discord.utils.get(after.roles,  id=rid) is not None
        if not had and has:
            await premium_col.update_one(
                {"type": "user", "id": str(after.id)},
                {"$set": {"type": "user", "id": str(after.id),
                           "via_role": True, "guild_id": str(after.guild.id)}},
                upsert=True
            )
        elif had and not has:
            await premium_col.delete_one(
                {"type": "user", "id": str(after.id),
                 "via_role": True, "guild_id": str(after.guild.id)}
            )

    # ══════════════════════════════════════════════════════════════════════════
    #  CHANNEL TOOLS
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @ctx_mod()
    async def topic(self, ctx, *, text: str = None):
        """Set or clear the current channel's topic."""
        await ctx.channel.edit(topic=text or "")
        await ctx.reply(
            f"Channel topic {'updated' if text else 'cleared'}.", delete_after=5
        )
        try:
            await ctx.message.delete()
        except:
            pass

    @commands.command()
    @ctx_mod()
    async def rename(self, ctx, *, name: str = None):
        """Rename the current channel."""
        if not name:
            return await ctx.reply("Usage: `,rename <new name>`")
        old = ctx.channel.name
        await ctx.channel.edit(name=name)
        await ctx.reply(f"Channel renamed: `{old}` → `{name}`.")
        await log_event(self.bot, ctx.guild, "channel_rename",
                        f"{ctx.author} renamed #{old} → #{name}.")

    # ══════════════════════════════════════════════════════════════════════════
    #  OWNER TOOLS
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @ctx_owner()
    async def maintenance(self, ctx, status: str = None):
        """Toggle maintenance mode (owner only)."""
        if not hasattr(self.bot, "maintenance"):
            self.bot.maintenance = False
        if not status:
            state = "ON" if self.bot.maintenance else "OFF"
            return await ctx.reply(f"Maintenance mode: **{state}**.")
        self.bot.maintenance = status.lower() in ("on", "true", "1", "yes")
        state = "ON" if self.bot.maintenance else "OFF"
        await ctx.reply(embed=discord.Embed(
            description=f"Maintenance mode: **{state}**.",
            color=0xff4444 if self.bot.maintenance else 0x2B2D31
        ))

    # aimode moved to cogs/ai_chat.py

    @commands.command()
    @commands.has_permissions(manage_guild=True)
    async def togglelevels(self, ctx, status: str = None):
        """
        Enable or disable level-up messages for this server.
        XP is still earned silently — only the level-up announcement is controlled.
        Usage: ,togglelevels on/off
        """
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        current = gs.get("levels_enabled", True)

        if not status:
            state = "ON" if current else "OFF"
            return await ctx.reply(embed=discord.Embed(
                description=(
                    f"Level-up messages are currently **{state}**.\nUse `,togglelevels on` or `,togglelevels off` to change."
                ),
                color=0x2B2D31
            ))

        new_state = status.lower() in ("on", "true", "1", "yes")
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"levels_enabled": new_state}},
            upsert=True
        )
        state = "ON" if new_state else "OFF"
        await ctx.reply(embed=discord.Embed(
            description=f"Level-up messages turned **{state}** for this server.",
            color=0x2B2D31
        ))

    @commands.command()
    @commands.has_permissions(manage_guild=True)
    async def togglereactions(self, ctx, status: str = None):
        """
        Enable or disable Happy's automatic heart reactions to greetings.
        Usage: ,togglereactions on/off
        """
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        current = gs.get("reactions_enabled", True)

        if not status:
            state = "ON" if current else "OFF"
            return await ctx.reply(embed=discord.Embed(
                description=(
                    f"Auto reactions are currently **{state}**.\nUse `,togglereactions on` or `,togglereactions off` to change."
                ),
                color=0x2B2D31
            ))

        new_state = status.lower() in ("on", "true", "1", "yes")
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"reactions_enabled": new_state}},
            upsert=True
        )
        state = "ON" if new_state else "OFF"
        await ctx.reply(embed=discord.Embed(
            description=f"Auto reactions turned **{state}** for this server.",
            color=0x2B2D31
        ))

    @commands.group(invoke_without_command=True)
    @ctx_owner()
    async def botstatus(self, ctx):
        """
        Owner global status controls.
        Sub-commands: set, set24h, reset, view, removeserver
        """
        from utils.db import global_status_col, server_status_col
        gov  = await global_status_col.find_one({"type": "owner"})
        docs = await server_status_col.find({}).to_list(50)

        embed = discord.Embed(title="Bot Status — Owner Panel", color=0xffd700)

        if gov:
            expires = gov.get("expires_at")
            exp_str = f"<t:{int(expires.timestamp())}:R>" if expires else "Never (always-on)"
            embed.add_field(
                name="Owner Override (ACTIVE)",
                value=f"**{gov.get('activity','watching').title()}** `{gov['status']}`\nExpires: {exp_str}",
                inline=False
            )
        else:
            embed.add_field(name="Owner Override", value="Not set — rotation active", inline=False)

        if docs:
            lines = [f"`{d.get('guild_name','?')}` — {d['status']}" for d in docs[:10]]
            embed.add_field(name=f"Server Statuses ({len(docs)})", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Server Statuses", value="None set", inline=False)

        embed.add_field(
            name="Commands",
            value=(
                "`,botstatus set <activity> <text>` — set always-on override\n"
                "`,botstatus set24h <activity> <text>` — set 24h override\n"
                "`,botstatus reset` — remove owner override\n"
                "`,botstatus view` — this panel\n"
                "`,botstatus removeserver <guild_id>` — remove a server's status"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @botstatus.command(name="set")
    @ctx_owner()
    async def botstatus_set(self, ctx, activity: str = None, *, text: str = None):
        """
        Set a permanent global owner status override.
        Usage: ,botstatus set watching Happy Users
        Activity types: watching playing listening competing
        """
        if not activity or not text:
            return await ctx.reply("Usage: `,botstatus set <watching/playing/listening/competing> <text>`")
        types = {"watching": "watching", "playing": "playing",
                 "listening": "listening", "competing": "competing"}
        atype = types.get(activity.lower())
        if not atype:
            return await ctx.reply("Activity must be: watching, playing, listening, competing")

        from utils.db import global_status_col
        await global_status_col.update_one(
            {"type": "owner"},
            {"$set": {"type": "owner", "status": text, "activity": atype, "expires_at": None}},
            upsert=True
        )
        await self.bot.change_presence(
            activity=discord.Activity(
                type=getattr(discord.ActivityType, atype),
                name=text
            )
        )
        embed = discord.Embed(
            title="Owner Status Set (Always-On)",
            description=f"**{activity.title()}** `{text}`\n\nThis overrides all server statuses globally.",
            color=0xffd700
        )
        embed.set_footer(text=",botstatus reset to remove")
        await ctx.reply(embed=embed)

    @botstatus.command(name="set24h")
    @ctx_owner()
    async def botstatus_set24h(self, ctx, activity: str = None, *, text: str = None):
        """
        Set a 24-hour global owner status override.
        Automatically removed after 24 hours.
        """
        if not activity or not text:
            return await ctx.reply("Usage: `,botstatus set24h <activity> <text>`")
        types = {"watching": "watching", "playing": "playing",
                 "listening": "listening", "competing": "competing"}
        atype = types.get(activity.lower())
        if not atype:
            return await ctx.reply("Activity must be: watching, playing, listening, competing")

        from utils.db import global_status_col
        from datetime import datetime, timezone, timedelta
        expires = datetime.now(timezone.utc) + timedelta(hours=24)
        await global_status_col.update_one(
            {"type": "owner"},
            {"$set": {"type": "owner", "status": text, "activity": atype, "expires_at": expires}},
            upsert=True
        )
        await self.bot.change_presence(
            activity=discord.Activity(
                type=getattr(discord.ActivityType, atype),
                name=text
            )
        )
        embed = discord.Embed(
            title="Owner Status Set (24 Hours)",
            description=(
                f"**{activity.title()}** `{text}`\n\n"
                f"Expires: <t:{int(expires.timestamp())}:R>\n"
                "After expiry, rotation resumes automatically."
            ),
            color=0xffd700
        )
        await ctx.reply(embed=embed)

    @botstatus.command(name="reset")
    @ctx_owner()
    async def botstatus_reset(self, ctx):
        """Remove owner status override — returns to normal rotation."""
        from utils.db import global_status_col
        result = await global_status_col.delete_one({"type": "owner"})
        if result.deleted_count:
            await ctx.reply(embed=discord.Embed(
                description="Owner status override removed. Rotation resumed.",
                color=0x2B2D31
            ))
        else:
            await ctx.reply("No owner override was active.")

    @botstatus.command(name="view")
    @ctx_owner()
    async def botstatus_view(self, ctx):
        """View current owner override and all server statuses."""
        await ctx.invoke(self.botstatus)

    @botstatus.command(name="removeserver")
    @ctx_owner()
    async def botstatus_removeserver(self, ctx, guild_id: str = None):
        """
        Remove a server's custom status (owner moderation).
        Usage: ,botstatus removeserver <guild_id>
        """
        if not guild_id:
            return await ctx.reply("Usage: `,botstatus removeserver <guild_id>`")
        from utils.db import server_status_col
        doc    = await server_status_col.find_one({"guild_id": guild_id})
        if not doc:
            return await ctx.reply(f"No status found for guild `{guild_id}`.")
        gname  = doc.get("guild_name", guild_id)
        status = doc.get("status", "?")
        await server_status_col.delete_one({"guild_id": guild_id})
        embed = discord.Embed(
            description=f"Removed status for **{gname}**:\n`{status}`",
            color=0x2B2D31
        )
        embed.set_footer(text="The server will need to set a new status with ,setstatus")
        await ctx.reply(embed=embed)

    @commands.command(aliases=["guildlist"])
    @ctx_owner()
    async def serverlist(self, ctx):
        """List all servers the bot is in (owner only)."""
        guilds = sorted(self.bot.guilds, key=lambda g: g.member_count, reverse=True)
        lines  = [
            f"`{i+1}.` **{g.name}** — {g.member_count} members (ID: {g.id})"
            for i, g in enumerate(guilds[:20])
        ]
        embed = discord.Embed(
            title=f"Servers ({len(self.bot.guilds)} total)",
            description="\n".join(lines) + (f"\n…and {len(guilds)-20} more" if len(guilds) > 20 else ""),
            color=0x2B2D31
        )
        await ctx.reply(embed=embed)

    @commands.command()
    @ctx_owner()
    async def leaveguild(self, ctx, guild_id: int = None):
        """Force the bot to leave a server by ID (owner only)."""
        if not guild_id:
            return await ctx.reply("Usage: `,leaveguild <guild_id>`")
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return await ctx.reply(f"Server `{guild_id}` not found.")
        name = guild.name
        await guild.leave()
        await ctx.reply(f"Left **{name}**.")

    @commands.command()
    @ctx_owner()
    async def dm(self, ctx, user: discord.User = None, *, message: str = None):
        """Send a DM to any user as the bot (owner only)."""
        if not user or not message:
            return await ctx.reply("Usage: `,dm @user <message>`")
        try:
            await user.send(message)
            await ctx.reply(f"DM sent to **{user}**.")
        except discord.Forbidden:
            await ctx.reply(f"Cannot DM **{user}** — DMs may be closed.")

    @commands.command()
    @ctx_owner()
    async def sync(self, ctx, guild_id: int = None):
        """
        Sync slash commands (owner only).
        `,sync` — global   |   `,sync <guild_id>` — instant guild sync
        """
        if guild_id:
            g = discord.Object(id=guild_id)
            self.bot.tree.copy_global_to(guild=g)
            synced = await self.bot.tree.sync(guild=g)
            await ctx.reply(f"Synced {len(synced)} commands to guild `{guild_id}`.")
        else:
            synced = await self.bot.tree.sync()
            await ctx.reply(f"Globally synced {len(synced)} slash commands.")


async def setup(bot):
    await bot.add_cog(Admin(bot))