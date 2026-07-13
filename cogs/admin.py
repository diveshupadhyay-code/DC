import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import random
import re
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

    @commands.command(aliases=["config", "panel"])
    @commands.has_permissions(administrator=True)
    async def settings(self, ctx):
        gid = str(ctx.guild.id)
        gs, log_cfg, bump_cfg, ticket_cfg, vc_cfg = await asyncio.gather(
            settings_col.find_one({"_id": gid}),
            logs_col.find_one({"guild_id": gid}),
            bump_col.find_one({"guild_id": gid}),
            tickets_col.find_one({"_id": gid}),
            voicemaster_col.find_one({"guild_id": gid}),
        )
        gs = gs or {}
        log_cfg = log_cfg or {}
        ticket_cfg = ticket_cfg or {}

        counter_docs = await counters_col.find({"guild_id": gid}).to_list(10)
        counter_list = ", ".join(f"`{d['type']}`" for d in counter_docs) or "None"

        dis_docs = await disabled_cmds_col.find({"guild_id": gid}).to_list(20)
        dis_list = ", ".join(f"`{d['command_name']}`" for d in dis_docs) or "None"

        def _ch(key):
            cid = gs.get(key)
            return f"<#{cid}>" if cid else "❌ Not Setup"

        def _on(key):
            return "🟢 On" if gs.get(key) else "🔴 Off"

        is_prem = await is_premium_server(ctx.guild.id)
        log_ch = f"<#{log_cfg['channel_id']}>" if log_cfg.get("channel_id") else "❌ Not Setup"

        embed = discord.Embed(
            title=f"📊 Server Settings — {ctx.guild.name}",
            color=0x2B2D31,
            timestamp=datetime.now(timezone.utc)
        )
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)

        embed.add_field(
            name="⚙️ General",
            value=f"**Prefix:** `{gs.get('prefix', ',')}`\n**Premium:** {'💎 Yes' if is_prem else '❌ No'}",
            inline=True
        )
        embed.add_field(
            name="👋 Welcome & Goodbye",
            value=f"**Welcome:** {_on('welcome_enabled')} → {_ch('welcome_channel')}\n**Goodbye:** {_on('bye_enabled')} → {_ch('bye_channel')}",
            inline=True
        )
        embed.add_field(
            name="📜 Logs",
            value=f"**Channel:** {log_ch}",
            inline=True
        )
        embed.add_field(
            name="🛡️ Security",
            value=f"**Block Invites:** {_on('invite_block')}\n**Jail System:** {'🟢 Ready' if gs.get('jail_role_id') else '❌ Not Setup'}",
            inline=True
        )
        embed.add_field(
            name="✨ Extras",
            value=f"**Level Alerts:** {'🟢 On' if gs.get('levels_enabled', True) else '🔴 Off'}\n**Auto Reactions:** {'🟢 On' if gs.get('reactions_enabled', True) else '🔴 Off'}",
            inline=True
        )
        embed.add_field(
            name="🎫 Tickets",
            value=f"**Total Open:** `{ticket_cfg.get('ticket_count', 0)}`\n**Staff Role:** {'🟢 Set' if ticket_cfg.get('staff_role_id') else '❌ Not Set'}",
            inline=True
        )
        embed.add_field(
            name="💎 Premium Features",
            value=f"**Bump Alert:** {'🟢 On' if bump_cfg and bump_cfg.get('enabled') else '🔴 Off'}\n**VoiceMaster:** {'🟢 Ready' if vc_cfg else '❌ Not Setup'}\n**Counters:** {counter_list}",
            inline=False
        )
        embed.add_field(name="🚫 Off Commands", value=dis_list, inline=False)
        embed.set_footer(text=f"Server ID: {gid} • Type ,help for info")
        await ctx.reply(embed=embed)

    @app_commands.command(name="settings", description="View server settings dashboard")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_settings(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild.id)
        gs = await settings_col.find_one({"_id": gid}) or {}
        is_prem = await is_premium_server(interaction.guild.id)
        
        embed = discord.Embed(title=f"📊 Settings — {interaction.guild.name}", color=0x2B2D31)
        embed.add_field(name="Prefix", value=f"`{gs.get('prefix', ',')}`", inline=True)
        embed.add_field(name="Premium", value="💎 Yes" if is_prem else "❌ No", inline=True)
        embed.add_field(name="Welcome System", value="🟢 On" if gs.get("welcome_enabled") else "🔴 Off", inline=True)
        embed.set_footer(text="Use text command ,settings for all options")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @commands.group(invoke_without_command=True)
    async def prefix(self, ctx):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        current = gs.get("prefix", ",")
        personal = await personal_prefix_col.find_one({"user_id": str(ctx.author.id)})
        pp = personal.get("prefix") if personal else None
        is_prem = await is_premium_user(ctx.author.id) or await is_premium_server(ctx.guild.id)

        embed = discord.Embed(title="⚙️ Prefix Settings", color=0x2B2D31)
        embed.add_field(name="Server Default", value=f"`{current}`", inline=True)
        embed.add_field(name="Your Prefix", value=f"`{pp}`" if pp else "`None`", inline=True)
        embed.add_field(name="Premium status", value="💎 Active" if is_prem else "❌ Regular", inline=True)
        embed.add_field(
            name="Commands",
            value=(
                "`,prefix set <symbol>` — Change server prefix\n"
                "`,prefix remove` — Reset server prefix to `,`\n"
                "`,prefix self <symbol>` — Set your own personal prefix\n"
                "`,prefix selfremove` — Remove your personal prefix"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @prefix.command(name="set")
    @commands.has_permissions(administrator=True)
    async def prefix_set(self, ctx, new_prefix: str = None):
        if not new_prefix:
            return await ctx.reply("❌ Use: `,prefix set <symbol>`")
        if len(new_prefix) > 3:
            return await ctx.reply("❌ Prefix cannot be longer than 3 characters.")
        if new_prefix in ("<", ">", "@", "#"):
            return await ctx.reply("❌ You cannot use those characters as a prefix.")
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)}, {"$set": {"prefix": new_prefix}}, upsert=True
        )
        await ctx.reply(f"🟢 Server prefix changed to `{new_prefix}`")
        await log_event(self.bot, ctx.guild, "prefix_change", f"{ctx.author} changed prefix to `{new_prefix}`")

    @prefix.command(name="remove")
    @commands.has_permissions(administrator=True)
    async def prefix_remove(self, ctx):
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)}, {"$unset": {"prefix": ""}}, upsert=True
        )
        await ctx.reply("🟢 Server prefix reset back to `,`")

    @prefix.command(name="self")
    @ctx_premium()
    async def prefix_self(self, ctx, new_prefix: str = None):
        if not new_prefix:
            return await ctx.reply("❌ Use: `,prefix self <symbol>`")
        if len(new_prefix) > 3:
            return await ctx.reply("❌ Personal prefix cannot be longer than 3 characters.")
        await personal_prefix_col.update_one(
            {"user_id": str(ctx.author.id)}, {"$set": {"prefix": new_prefix}}, upsert=True
        )
        await ctx.reply(f"💎 Your personal prefix is now `{new_prefix}`")

    @prefix.command(name="selfremove")
    async def prefix_selfremove(self, ctx):
        result = await personal_prefix_col.delete_one({"user_id": str(ctx.author.id)})
        await ctx.reply("🟢 Personal prefix removed." if result.deleted_count else "❌ You don't have a personal prefix set.")

    @commands.command()
    @ctx_mod()
    async def announce(self, ctx, channel: discord.TextChannel = None, *, content: str = None):
        if not content:
            if channel:
                content = channel.name
                channel = None
            else:
                return await ctx.reply("❌ Use: `,announce [#channel] <content>`\n💡 Add `--ping` to alert everyone.")

        ping = "--ping" in content
        content = content.replace("--ping", "").strip()
        if not content:
            return await ctx.reply("❌ Announcement content cannot be empty.")

        target = channel or ctx.channel
        embed = discord.Embed(description=content, color=0x2B2D31, timestamp=datetime.now(timezone.utc))
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
        embed.set_footer(text=f"By: {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        
        await target.send(content="@everyone" if ping else None, embed=embed)
        if target != ctx.channel:
            await ctx.reply(f"🟢 Announcement sent to {target.mention}", delete_after=5)
        try:
            await ctx.message.delete()
        except:
            pass
        await log_event(self.bot, ctx.guild, "announcement", f"{ctx.author} sent announcement to {target.mention}. Ping: {ping}")

    @app_commands.command(name="announce", description="Send a server announcement")
    @app_commands.checks.has_permissions(manage_messages=True)
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
        embed = discord.Embed(title=title, description=description, color=embed_color, timestamp=datetime.now(timezone.utc))
        embed.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
        embed.set_footer(text=f"By: {interaction.user.display_name}")
        await target.send(content="@everyone" if ping_everyone else None, embed=embed)
        await interaction.response.send_message(f"🟢 Announcement sent to {target.mention}", ephemeral=True)

    @commands.group(invoke_without_command=True)
    @ctx_mod()
    async def giveaway(self, ctx, duration: str = None, winners: int = 1, *, prize: str = None):
        if not duration or not prize:
            embed = discord.Embed(title="🎉 Giveaway Setup", color=0x2B2D31)
            embed.add_field(
                name="How to start",
                value=(
                    "`,giveaway <duration> <winners> <prize>`\n"
                    "Time formats: `30m`, `2h`, `1d` \n\n"
                    "**Requirements (Optional):**\n"
                    "`--msgs <count>` — Minimum chat messages needed\n"
                    "`--invites <count>` — Minimum invites needed\n\n"
                    "💡 *Example: `,giveaway 1h 1 Nitro --msgs 50 --invites 2`*"
                ),
                inline=False
            )
            embed.add_field(name="End Early", value="`,giveaway end <message_id>`", inline=True)
            embed.add_field(name="Reroll Winners", value="`,giveaway reroll <message_id>`", inline=True)
            return await ctx.reply(embed=embed)

        minutes = self._parse_duration(duration)
        if minutes is None:
            return await ctx.reply("❌ Invalid time format. Use: `15m`, `4h`, `7d`.")
        if minutes < 1:
            return await ctx.reply("❌ Giveaway must last at least 1 minute.")
        if minutes > 43200:
            return await ctx.reply("❌ Giveaway cannot last longer than 30 days.")

        min_msgs = 0
        min_invites = 0

        msgs_match = re.search(r"--msgs\s+(\d+)", prize)
        if msgs_match:
            min_msgs = int(msgs_match.group(1))
            prize = prize[:msgs_match.start()] + prize[msgs_match.end():]

        inv_match = re.search(r"--invites\s+(\d+)", prize)
        if inv_match:
            min_invites = int(inv_match.group(1))
            prize = prize[:inv_match.start()] + prize[inv_match.end():]

        prize = prize.strip()
        if not prize:
            return await ctx.reply("❌ Giveaway prize cannot be blank.")

        end_time = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        ts = int(end_time.timestamp())

        desc_lines = ["React with 🎉 to enter!", ""]
        desc_lines.append(f"🏆 **Winners:** {winners}")
        desc_lines.append(f"⏱️ **Ends:** <t:{ts}:R> (<t:{ts}:f>)")

        if min_msgs or min_invites:
            desc_lines.append("")
            desc_lines.append("⚠️ **Requirements to Win:**")
            if min_msgs:
                desc_lines.append(f"• Must have at least **{min_msgs}** messages")
            if min_invites:
                desc_lines.append(f"• Must have at least **{min_invites}** invites")

        embed = discord.Embed(title=f"🎁 Giveaway: {prize}", description="\n".join(desc_lines), color=0x2B2D31)
        footer_parts = [f"Hosted by: {ctx.author.display_name}", f"{winners} Winners"]
        if min_msgs:
            footer_parts.append(f"{min_msgs}+ Messages required")
        if min_invites:
            footer_parts.append(f"{min_invites}+ Invites required")
        embed.set_footer(text=" • ".join(footer_parts), icon_url=ctx.author.display_avatar.url)

        try:
            await ctx.message.delete()
        except:
            pass

        msg = await ctx.channel.send(embed=embed)
        await msg.add_reaction("🎉")

        await giveaways_col.insert_one({
            "message_id": str(msg.id),
            "channel_id": str(ctx.channel.id),
            "guild_id": str(ctx.guild.id),
            "host_id": str(ctx.author.id),
            "prize": prize,
            "winners": winners,
            "min_msgs": min_msgs,
            "min_invites": min_invites,
            "end_time": end_time,
            "status": "active",
        })

    @giveaway.command(name="end")
    @ctx_mod()
    async def giveaway_end(self, ctx, message_id: int = None):
        if not message_id:
            return await ctx.reply("❌ Use: `,giveaway end <message_id>`")
        doc = await giveaways_col.find_one({"message_id": str(message_id), "status": "active"})
        if not doc:
            return await ctx.reply("❌ Giveaway not found or already ended.")
        channel = self.bot.get_channel(int(doc["channel_id"])) or ctx.channel
        await self._end_giveaway(channel, message_id)
        await ctx.reply("🟢 Giveaway ended.", delete_after=4)

    @giveaway.command(name="reroll")
    @ctx_mod()
    async def giveaway_reroll(self, ctx, message_id: int = None):
        if not message_id:
            return await ctx.reply("❌ Use: `,giveaway reroll <message_id>`")
        data = await giveaways_col.find_one({"message_id": str(message_id)})
        if not data:
            return await ctx.reply("❌ Giveaway data not found.")
        try:
            msg = await ctx.channel.fetch_message(message_id)
            reaction = next((r for r in msg.reactions if str(r.emoji) == "🎉"), None)
            if not reaction:
                return await ctx.reply("❌ Could not find reactions on that message.")
            users = [u async for u in reaction.users() if not u.bot]
            if not users:
                return await ctx.reply("❌ Nobody entered the giveaway.")

            eligible = await self._filter_eligible(ctx.guild, users, data.get("min_msgs", 0), data.get("min_invites", 0))
            if not eligible:
                return await ctx.reply("❌ No users match the giveaway requirements.")

            chosen = random.sample(eligible, min(data["winners"], len(eligible)))
            mentions = ", ".join(w.mention for w in chosen)
            
            reroll_embed = discord.Embed(
                title="✨ New Winners Drawn",
                description=f"🎉 **Prize:** **{data['prize']}**\n🏆 **Winners:** {mentions}",
                color=0x2B2D31
            )
            await ctx.reply(embed=reroll_embed)
        except discord.NotFound:
            await ctx.reply("❌ Message not found in this channel.")
        except Exception as e:
            await ctx.reply(f"❌ An error occurred: {e}")

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

        reaction = next((r for r in msg.reactions if str(r.emoji) == "🎉"), None)
        if not reaction:
            no_entry = discord.Embed(title="🛑 Giveaway Ended", description=f"**Prize:** **{data['prize']}**\n\n❌ Nobody entered the giveaway.", color=0x2B2D31)
            try:
                await msg.edit(embed=no_entry)
            except:
                pass
            await channel.send(embed=no_entry)
            return

        reactors = [u async for u in reaction.users() if not u.bot]
        if not reactors:
            no_entry = discord.Embed(title="🛑 Giveaway Ended", description=f"**Prize:** **{data['prize']}**\n\n❌ No valid users entered.", color=0x2B2D31)
            try:
                await msg.edit(embed=no_entry)
            except:
                pass
            await channel.send(embed=no_entry)
            return

        min_msgs = data.get("min_msgs", 0)
        min_invites = data.get("min_invites", 0)
        eligible = await self._filter_eligible(channel.guild, reactors, min_msgs, min_invites)
        disqualified_count = len(reactors) - len(eligible)

        if not eligible:
            cond_parts = []
            if min_msgs:
                cond_parts.append(f"{min_msgs}+ messages")
            if min_invites:
                cond_parts.append(f"{min_invites}+ invites")
            no_win_embed = discord.Embed(
                title="🛑 Giveaway Ended — No Winners",
                description=f"**Prize:** **{data['prize']}**\n\n❌ Nobody met the requirements: {' and '.join(cond_parts)}\n📊 Total entries: `{len(reactors)}` | Passed: `0`",
                color=0x2B2D31
            )
            try:
                await msg.edit(embed=no_win_embed)
            except:
                pass
            await channel.send(embed=no_win_embed)
            return

        chosen = random.sample(eligible, min(data["winners"], len(eligible)))
        mentions = ", ".join(w.mention for w in chosen)

        result_lines = [f"**Prize:** **{data['prize']}**", f"🏆 **Winners:** {mentions}"]
        if min_msgs or min_invites:
            cond_parts = []
            if min_msgs:
                cond_parts.append(f"{min_msgs}+ msgs")
            if min_invites:
                cond_parts.append(f"{min_invites}+ invites")
            result_lines.append(f"⚙️ **Requirements:** {', '.join(cond_parts)}")
            result_lines.append(f"📊 **Stats:** `{len(eligible)}/{len(reactors)}` passed checks")

        new_embed = discord.Embed(title="🎉 Giveaway Results", description="\n".join(result_lines), color=0x2B2D31)
        new_embed.set_footer(text="Use command: ,giveaway reroll <id> to pick new winners")

        try:
            await msg.edit(embed=new_embed)
        except:
            pass

        win_announcement = discord.Embed(
            description=f"✨ **Congratulations** {mentions}! You won **{data['prize']}**!",
            color=0x2B2D31
        )
        await channel.send(content=mentions, embed=win_announcement)

        if disqualified_count:
            disq_embed = discord.Embed(
                description=f"ℹ️ `{disqualified_count}` users were removed from the draw because they didn't meet the requirements.",
                color=0x2B2D31
            )
            await channel.send(embed=disq_embed, delete_after=30)

    @staticmethod
    def _parse_duration(s: str):
        m = re.fullmatch(r"(\d+)(m|h|d)?", s.strip().lower())
        if not m:
            return None
        val, unit = int(m.group(1)), m.group(2) or "m"
        return val * {"m": 1, "h": 60, "d": 1440}[unit]

    @commands.command()
    @ctx_mod()
    async def slowmode(self, ctx, seconds: int = None, channel: discord.TextChannel = None):
        if seconds is None:
            return await ctx.reply("❌ Use: `,slowmode <seconds> [#channel]`\n💡 Set to `0` to turn it off. Max is `21600` seconds.")
        if not 0 <= seconds <= 21600:
            return await ctx.reply("❌ Time must be between 0 and 21600 seconds.")
        target = channel or ctx.channel
        await target.edit(slowmode_delay=seconds)
        status_msg = "🔓 Slowmode turned off" if seconds == 0 else f"⏳ Slowmode set to **{seconds}s**"
        await ctx.reply(f"🟢 {status_msg} in {target.mention}")
        await log_event(self.bot, ctx.guild, "slowmode", f"{ctx.author} set slowmode to {seconds}s in #{target.name}")

    @app_commands.command(name="slowmode", description="Set channel slowmode delay")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slash_slowmode(self, interaction: discord.Interaction, seconds: int, channel: discord.TextChannel = None):
        if not 0 <= seconds <= 21600:
            return await interaction.response.send_message("❌ Time must be between 0 and 21600 seconds.", ephemeral=True)
        target = channel or interaction.channel
        await target.edit(slowmode_delay=seconds)
        status_msg = "🔓 Slowmode turned off" if seconds == 0 else f"⏳ Slowmode set to **{seconds}s**"
        await interaction.response.send_message(f"🟢 {status_msg} in {target.mention}", ephemeral=True)

    _protected_cmds = {"settings", "prefix", "disable", "enable", "help", "premium", "maintenance", "aimode", "sync"}

    @commands.command(name="disable")
    @commands.has_permissions(administrator=True)
    async def disable_command(self, ctx, *, cmd_name: str = None):
        if not cmd_name:
            docs = await disabled_cmds_col.find({"guild_id": str(ctx.guild.id)}).to_list(50)
            names = [d["command_name"] for d in docs]
            embed = discord.Embed(title="🚫 Disabled Commands", color=0x2B2D31)
            embed.description = (
                f"**Currently Disabled:** {', '.join(f'`{n}`' for n in names) if names else '`None`'}\n\n"
                "⚙️ `,disable <command>` — Turn off a command\n"
                "⚙️ `,enable <command>` — Turn on a command"
            )
            return await ctx.reply(embed=embed)

        cmd_name = cmd_name.lower().strip()
        if cmd_name in self._protected_cmds:
            return await ctx.reply(f"❌ You cannot disable the `{cmd_name}` command.")
        if not self.bot.get_command(cmd_name):
            return await ctx.reply(f"❌ Command not found: `{cmd_name}`")
        await disabled_cmds_col.update_one(
            {"guild_id": str(ctx.guild.id), "command_name": cmd_name},
            {"$set": {"guild_id": str(ctx.guild.id), "command_name": cmd_name}},
            upsert=True
        )
        await ctx.reply(f"🟢 Disabled the `{cmd_name}` command.")

    @commands.command(name="enable")
    @commands.has_permissions(administrator=True)
    async def enable_command(self, ctx, *, cmd_name: str = None):
        if not cmd_name:
            return await ctx.reply("❌ Use: `,enable <command_name>`")
        result = await disabled_cmds_col.delete_one(
            {"guild_id": str(ctx.guild.id), "command_name": cmd_name.lower().strip()}
        )
        await ctx.reply(f"🟢 Enabled the `{cmd_name}` command." if result.deleted_count else f"❌ That command wasn't disabled: `{cmd_name}`")

    @commands.Cog.listener()
    async def on_command(self, ctx):
        if not ctx.guild or ctx.author.id == BOT_OWNER_ID or ctx.command is None:
            return
        doc = await disabled_cmds_col.find_one(
            {"guild_id": str(ctx.guild.id), "command_name": ctx.command.name}
        )
        if doc:
            await ctx.reply(f"❌ Access Denied: The `{ctx.command.name}` command is disabled in this server.", delete_after=6)
            raise commands.DisabledCommand()

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def premiumrole(self, ctx, role: discord.Role = None):
        if not role:
            gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
            rid = gs.get("premium_role_id")
            cur = ctx.guild.get_role(rid) if rid else None
            return await ctx.reply(f"💎 Current Premium Role: {cur.mention if cur else '`Not setup`'}\n⚙️ Use: `,premiumrole @role`")
        await update_server_data(ctx.guild.id, "premium_role_id", role.id)
        await ctx.reply(f"🟢 Premium role updated to {role.mention}")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        gs = await settings_col.find_one({"_id": str(after.guild.id)}) or {}
        rid = gs.get("premium_role_id")
        if not rid:
            return
        had = discord.utils.get(before.roles, id=rid) is not None
        has = discord.utils.get(after.roles, id=rid) is not None
        if not had and has:
            await premium_col.update_one(
                {"type": "user", "id": str(after.id)},
                {"$set": {"type": "user", "id": str(after.id), "via_role": True, "guild_id": str(after.guild.id)}},
                upsert=True
            )
        elif had and not has:
            await premium_col.delete_one(
                {"type": "user", "id": str(after.id), "via_role": True, "guild_id": str(after.guild.id)}
            )

    @commands.command()
    @ctx_mod()
    async def topic(self, ctx, *, text: str = None):
        await ctx.channel.edit(topic=text or "")
        await ctx.reply(f"🟢 Channel topic {'updated' if text else 'cleared'}.", delete_after=5)
        try:
            await ctx.message.delete()
        except:
            pass

    @commands.command()
    @ctx_mod()
    async def rename(self, ctx, *, name: str = None):
        if not name:
            return await ctx.reply("❌ Use: `,rename <new_name>`")
        old = ctx.channel.name
        await ctx.channel.edit(name=name)
        await ctx.reply(f"🟢 Channel renamed: `{old}` → `{name}`")
        await log_event(self.bot, ctx.guild, "channel_rename", f"{ctx.author} renamed channel from #{old} to #{name}")

    @commands.command()
    @ctx_owner()
    async def maintenance(self, ctx, status: str = None):
        if not hasattr(self.bot, "maintenance"):
            self.bot.maintenance = False
        if not status:
            state = "🟢 ON" if self.bot.maintenance else "🔴 OFF"
            return await ctx.reply(f"⚙️ Maintenance Mode Status: {state}")
        self.bot.maintenance = status.lower() in ("on", "true", "1", "yes")
        state = "🟢 ACTIVATED" if self.bot.maintenance else "🔴 DEACTIVATED"
        
        m_embed = discord.Embed(
            description=f"🔧 Maintenance status changed to **{state}**",
            color=0x2B2D31
        )
        await ctx.reply(embed=m_embed)

    @commands.command()
    @commands.has_permissions(manage_guild=True)
    async def togglelevels(self, ctx, status: str = None):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        current = gs.get("levels_enabled", True)

        if not status:
            state = "🟢 ON" if current else "🔴 OFF"
            return await ctx.reply(embed=discord.Embed(
                description=f"📊 Level Alerts are currently: **{state}**\n💡 Use: `,togglelevels on/off`",
                color=0x2B2D31
            ))

        new_state = status.lower() in ("on", "true", "1", "yes")
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"levels_enabled": new_state}},
            upsert=True
        )
        state = "🟢 ON" if new_state else "🔴 MUTED"
        await ctx.reply(embed=discord.Embed(description=f"🟢 Level up alerts are now **{state}**", color=0x2B2D31))

    @commands.command()
    @commands.has_permissions(manage_guild=True)
    async def togglereactions(self, ctx, status: str = None):
        gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        current = gs.get("reactions_enabled", True)

        if not status:
            state = "🟢 ON" if current else "🔴 OFF"
            return await ctx.reply(embed=discord.Embed(
                description=f"✨ Auto Reactions are currently: **{state}**\n💡 Use: `,togglereactions on/off`",
                color=0x2B2D31
            ))

        new_state = status.lower() in ("on", "true", "1", "yes")
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"reactions_enabled": new_state}},
            upsert=True
        )
        state = "🟢 ON" if new_state else "🔴 OFF"
        await ctx.reply(embed=discord.Embed(description=f"🟢 Auto reactions are now **{state}**", color=0x2B2D31))

    @commands.group(invoke_without_command=True)
    @ctx_owner()
    async def botstatus(self, ctx):
        from utils.db import global_status_col, server_status_col
        gov = await global_status_col.find_one({"type": "owner"})
        docs = await server_status_col.find({}).to_list(50)

        embed = discord.Embed(title="🛡️ Bot Status Dashboard", color=0x2B2D31)

        if gov:
            expires = gov.get("expires_at")
            exp_str = f"<t:{int(expires.timestamp())}:R>" if expires else "`Permanent`"
            embed.add_field(
                name="👑 Master Status Override",
                value=f"✨ **{gov.get('activity','watching').title()}** • `{gov['status']}`\n⏱️ Expires: {exp_str}",
                inline=False
            )
        else:
            embed.add_field(name="👑 Master Status Override", value="`None` — Running normal rotation.", inline=False)

        if docs:
            lines = [f"• `{d.get('guild_name','?')}` → `{d['status']}`" for d in docs[:10]]
            embed.add_field(name=f"📊 Custom Server Statuses ({len(docs)})", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="📊 Custom Server Statuses", value="`No custom server statuses set`", inline=False)

        embed.add_field(
            name="⚙️ Commands",
            value=(
                "`,botstatus set <activity> <text>` — Set permanent status\n"
                "`,botstatus set24h <activity> <text>` — Set temporary 24-hour status\n"
                "`,botstatus reset` — Remove override and back to default\n"
                "`,botstatus view` — Refresh status view\n"
                "`,botstatus removeserver <guild_id>` — Remove a server status"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @botstatus.command(name="set")
    @ctx_owner()
    async def botstatus_set(self, ctx, activity: str = None, *, text: str = None):
        if not activity or not text:
            return await ctx.reply("❌ Use: `,botstatus set <watching/playing/listening/competing> <text>`")
        types = {"watching": "watching", "playing": "playing", "listening": "listening", "competing": "competing"}
        atype = types.get(activity.lower())
        if not atype:
            return await ctx.reply("❌ Invalid activity type. Choose from: watching, playing, listening, competing")

        from utils.db import global_status_col
        await global_status_col.update_one(
            {"type": "owner"},
            {"$set": {"type": "owner", "status": text, "activity": atype, "expires_at": None}},
            upsert=True
        )
        await self.bot.change_presence(activity=discord.Activity(type=getattr(discord.ActivityType, atype), name=text))
        
        s_embed = discord.Embed(
            title="👑 Master Status Set",
            description=f"⚙️ Status updated to: **{activity.title()}** `{text}`",
            color=0x2B2D31
        )
        await ctx.reply(embed=s_embed)

    @botstatus.command(name="set24h")
    @ctx_owner()
    async def botstatus_set24h(self, ctx, activity: str = None, *, text: str = None):
        if not activity or not text:
            return await ctx.reply("❌ Use: `,botstatus set24h <activity> <text>`")
        types = {"watching": "watching", "playing": "playing", "listening": "listening", "competing": "competing"}
        atype = types.get(activity.lower())
        if not atype:
            return await ctx.reply("❌ Invalid activity type. Use: watching, playing, listening, competing")

        from utils.db import global_status_col
        expires = datetime.now(timezone.utc) + timedelta(hours=24)
        await global_status_col.update_one(
            {"type": "owner"},
            {"$set": {"type": "owner", "status": text, "activity": atype, "expires_at": expires}},
            upsert=True
        )
        await self.bot.change_presence(activity=discord.Activity(type=getattr(discord.ActivityType, atype), name=text))
        
        s24_embed = discord.Embed(
            title="⏱️ Temporary Status Set (24h)",
            description=f"⚙️ Status set to: **{activity.title()}** `{text}`\n\n⏳ Ends: <t:{int(expires.timestamp())}:R>",
            color=0x2B2D31
        )
        await ctx.reply(embed=s24_embed)

    @botstatus.command(name="reset")
    @ctx_owner()
    async def botstatus_reset(self, ctx):
        from utils.db import global_status_col
        result = await global_status_col.delete_one({"type": "owner"})
        if result.deleted_count:
            await ctx.reply(embed=discord.Embed(description="🟢 Master status removed. Default status restored.", color=0x2B2D31))
        else:
            await ctx.reply("❌ No master status override is currently active.")

    @botstatus.command(name="view")
    @ctx_owner()
    async def botstatus_view(self, ctx):
        await ctx.invoke(self.botstatus)

    @botstatus.command(name="removeserver")
    @ctx_owner()
    async def botstatus_removeserver(self, ctx, guild_id: str = None):
        if not guild_id:
            return await ctx.reply("❌ Use: `,botstatus removeserver <guild_id>`")
        from utils.db import server_status_col
        doc = await server_status_col.find_one({"guild_id": guild_id})
        if not doc:
            return await ctx.reply(f"❌ No custom status found for server ID: `{guild_id}`")
        gname = doc.get("guild_name", guild_id)
        status = doc.get("status", "?")
        await server_status_col.delete_one({"guild_id": guild_id})
        
        rem_embed = discord.Embed(
            description=f"🟢 Removed custom status for **{gname}**:\n`{status}`",
            color=0x2B2D31
        )
        await ctx.reply(embed=rem_embed)

    @commands.command(aliases=["guildlist"])
    @ctx_owner()
    async def serverlist(self, ctx):
        guilds = sorted(self.bot.guilds, key=lambda g: g.member_count, reverse=True)
        lines = [f"`{i+1}.` **{g.name}** — `{g.member_count}` members *(ID: {g.id})*" for i, g in enumerate(guilds[:20])]
        
        embed = discord.Embed(
            title=f"🌐 Server List ({len(self.bot.guilds)} Total)",
            description="\n".join(lines) + (f"\n*...and {len(guilds)-20} more servers.*" if len(guilds) > 20 else ""),
            color=0x2B2D31
        )
        await ctx.reply(embed=embed)

    @commands.command()
    @ctx_owner()
    async def leaveguild(self, ctx, guild_id: int = None):
        if not guild_id:
            return await ctx.reply("❌ Use: `,leaveguild <guild_id>`")
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return await ctx.reply(f"❌ Bot is not in a server with ID: `{guild_id}`")
        name = guild.name
        await guild.leave()
        await ctx.reply(f"🟢 Left server: **{name}**")

    @commands.command()
    @ctx_owner()
    async def dm(self, ctx, user: discord.User = None, *, message: str = None):
        if not user or not message:
            return await ctx.reply("❌ Use: `,dm @user <message>`")
        try:
            await user.send(message)
            await ctx.reply(f"🟢 Message sent to **{user}**")
        except discord.Forbidden:
            await ctx.reply(f"❌ Cannot send message to **{user}** (DMs closed).")

    @commands.command()
    @ctx_owner()
    async def sync(self, ctx, guild_id: int = None):
        if guild_id:
            g = discord.Object(id=guild_id)
            self.bot.tree.copy_global_to(guild=g)
            synced = await self.bot.tree.sync(guild=g)
            await ctx.reply(f"🟢 Synced `{len(synced)}` slash commands to server `{guild_id}`")
        else:
            synced = await self.bot.tree.sync()
            await ctx.reply(f"🌐 Globally synced `{len(synced)}` slash commands.")

async def setup(bot):
    await bot.add_cog(Admin(bot))