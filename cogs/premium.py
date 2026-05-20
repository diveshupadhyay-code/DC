"""
cogs/premium.py — Premium features: Global Call, VoiceMaster, Bump Reminder,
                   Custom Status, Premium management, Quick Setup.
                   Built to feel exclusive, not generic.
"""

import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from datetime import datetime, timezone

from utils.db import (
    premium_col, server_status_col, voicemaster_col,
    bump_col, settings_col, booster_roles_col
)
from utils.helpers import (
    BOT_OWNER_ID, ctx_owner, ctx_premium, ctx_admin,
    is_premium_server, is_premium_user, log_event
)

# ── Gold accent color for all premium embeds ─────────────────────────────────
GOLD   = 0xF0C040
SILVER = 0xC0C0C0

# ── In-memory call state ──────────────────────────────────────────────────────
_active_calls: dict = {}   # {server_id: {partner_channel, my_channel, guild_name}}
_waiting_list: list = []   # [{server_id, channel_id, guild_name, ts}]


# ── Shared premium gate embed ─────────────────────────────────────────────────
def _premium_required_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Happy Premium Required",
        description=(
            "This feature is exclusive to **Happy Premium** servers and users.\n\n"
            "Contact the bot owner to activate Premium for your server."
        ),
        color=GOLD
    )
    embed.set_footer(text="Happy Premium · Exclusive features for serious servers")
    return embed


class Premium(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ══════════════════════════════════════════════════════════════════════════
    #  PREMIUM STATUS  — ,mypremium
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["myprem", "ispremium"])
    async def mypremium(self, ctx):
        """Check your own premium status and what features you have access to."""
        user_prem   = await is_premium_user(ctx.author.id)
        server_prem = await is_premium_server(ctx.guild.id)
        has_prem    = user_prem or server_prem or ctx.author.id == BOT_OWNER_ID

        if has_prem:
            how = []
            if ctx.author.id == BOT_OWNER_ID:
                how.append("Bot Owner (full access)")
            if user_prem:
                how.append("Personal Premium")
            if server_prem:
                how.append("Server Premium")

            embed = discord.Embed(
                title="Happy Premium — Active",
                color=GOLD
            )
            embed.set_thumbnail(url=ctx.author.display_avatar.url)
            embed.add_field(name="Status",       value="\n".join(how),                             inline=False)
            embed.add_field(name="AI Chat",      value="Active — @mention or reply to Happy",      inline=True)
            embed.add_field(name="Global Call",  value="Active — `,call` to connect servers",      inline=True)
            embed.add_field(name="VoiceMaster",  value="Active — `,vcsetup` to configure",         inline=True)
            embed.add_field(name="Bump Reminder",value="Active — `,bumpreminder on`",              inline=True)
            embed.add_field(name="Custom Status",value="Active — `,setstatus <text>`",             inline=True)
            embed.add_field(name="Personal Prefix", value="Active — `,prefix self <symbol>`",             inline=True)
            embed.set_footer(text="Happy Premium · All features unlocked")
        else:
            embed = discord.Embed(
                title="Happy Premium — Not Active",
                description=(
                    "You don't have Premium on this server.\n\n"
                    "**Premium unlocks:**\n"
                    "— AI Chat (mention Happy to start chatting)\n"
                    "— Global Call (connect to other servers)\n"
                    "— VoiceMaster (auto temp voice channels)\n"
                    "— DISBOARD Bump Reminder\n"
                    "— Custom Bot Status\n"
                    "— Personal Prefix across all servers\n\n"
                    "Contact the bot owner to activate."
                ),
                color=SILVER
            )
            embed.set_footer(text="Happy Premium · Contact bot owner to activate")

        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  OWNER — PREMIUM MANAGEMENT
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(invoke_without_command=True)
    @ctx_owner()
    async def premium(self, ctx):
        """Manage premium entries (owner only)."""
        items   = await premium_col.find({}).to_list(100)
        servers = [i for i in items if i["type"] == "server"]
        users   = [i for i in items if i["type"] == "user"]

        embed = discord.Embed(
            title="Premium Management",
            color=GOLD,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(
            name=f"Active Servers ({len(servers)})",
            value="\n".join(f"`{i['id']}`" for i in servers) or "None",
            inline=True
        )
        embed.add_field(
            name=f"Active Users ({len(users)})",
            value="\n".join(f"`{i['id']}`" for i in users) or "None",
            inline=True
        )
        embed.add_field(
            name="Commands",
            value=(
                "`,premium add server <guild_id>` — activate for a server\n"
                "`,premium add user <user_id>` — activate for a user\n"
                "`,premium remove server/user <id>` — remove\n"
                "`,premium info <guild_id>` — server premium info"
            ),
            inline=False
        )
        embed.set_footer(text=f"Total entries: {len(items)}")
        await ctx.reply(embed=embed)

    @premium.command(name="add")
    @ctx_owner()
    async def premium_add(self, ctx, type_: str = None, target: str = None):
        """Activate premium for a server or user."""
        if not type_ or type_ not in ("server", "user") or not target:
            return await ctx.reply("Usage: `,premium add server/user <id>`")

        # Try to resolve the name for better display
        name = target
        if type_ == "server":
            guild = self.bot.get_guild(int(target))
            name  = guild.name if guild else target
        elif type_ == "user":
            try:
                user = await self.bot.fetch_user(int(target))
                name = str(user)
            except:
                name = target

        await premium_col.update_one(
            {"type": type_, "id": target},
            {"$set": {"type": type_, "id": target, "name": name,
                      "activated_at": datetime.now(timezone.utc)}},
            upsert=True
        )

        embed = discord.Embed(
            title="Premium Activated",
            description=f"**{name}** (`{target}`) now has Premium access.",
            color=GOLD
        )
        embed.add_field(name="Type",   value=type_.title(), inline=True)
        embed.add_field(name="ID",     value=f"`{target}`", inline=True)
        embed.set_footer(text="Happy Premium · Activated")
        await ctx.reply(embed=embed)

        # Notify the server/user if possible
        if type_ == "server":
            guild = self.bot.get_guild(int(target))
            if guild and guild.system_channel:
                try:
                    notify = discord.Embed(
                        title="Happy Premium — Activated!",
                        description=(
                            "This server now has **Happy Premium**.\n\n"
                            "**Unlocked features:**\n"
                            "— AI Chat (mention Happy)\n"
                            "— Global Call (`,call`)\n"
                            "— VoiceMaster (`,vcsetup`)\n"
                            "— Bump Reminder (`,bumpreminder on`)\n"
                            "— Custom Bot Status (`,setstatus`)\n\n"
                            "Use `,mypremium` to see your full access."
                        ),
                        color=GOLD
                    )
                    notify.set_footer(text="Happy Premium · Thank you for your support")
                    await guild.system_channel.send(embed=notify)
                except:
                    pass

    @premium.command(name="remove")
    @ctx_owner()
    async def premium_remove(self, ctx, type_: str = None, target: str = None):
        """Remove premium access."""
        if not type_ or type_ not in ("server", "user") or not target:
            return await ctx.reply("Usage: `,premium remove server/user <id>`")
        result = await premium_col.delete_one({"type": type_, "id": target})
        if result.deleted_count:
            await ctx.reply(embed=discord.Embed(
                description=f"Premium removed from {type_} `{target}`.",
                color=0x2B2D31
            ))
        else:
            await ctx.reply(f"No premium entry found for {type_} `{target}`.")

    @premium.command(name="info")
    @ctx_owner()
    async def premium_info(self, ctx, guild_id: int = None):
        """View premium details for a specific server."""
        if not guild_id:
            return await ctx.reply("Usage: `,premium info <guild_id>`")
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return await ctx.reply(f"Server `{guild_id}` not found.")
        doc = await premium_col.find_one({"type": "server", "id": str(guild_id)})
        vc  = await voicemaster_col.find_one({"guild_id": str(guild_id)})
        bmp = await bump_col.find_one({"guild_id": str(guild_id)})
        sts = await server_status_col.find_one({"guild_id": str(guild_id)})

        embed = discord.Embed(title=f"Premium Info — {guild.name}", color=GOLD)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Premium",        value="Active" if doc else "Not active",   inline=True)
        embed.add_field(name="Members",        value=guild.member_count,                  inline=True)
        embed.add_field(name="VoiceMaster",    value="Configured" if vc else "Not set",   inline=True)
        embed.add_field(name="Bump Reminder",  value="On" if bmp and bmp.get("enabled") else "Off", inline=True)
        embed.add_field(name="Custom Status",  value=f"`{sts['status']}`" if sts else "Not set", inline=True)
        if doc and doc.get("activated_at"):
            embed.add_field(name="Activated", value=f"<t:{int(doc['activated_at'].timestamp())}:R>", inline=True)
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  CUSTOM BOT STATUS
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @ctx_premium()
    @commands.has_permissions(administrator=True)
    async def setstatus(self, ctx, *, status: str = None):
        """
        Add a custom text to the bot's rotating status pool.
        Your server's status text will appear alongside others.
        Leave blank to remove.
        """
        if not status:
            await server_status_col.delete_one({"guild_id": str(ctx.guild.id)})
            embed = discord.Embed(
                description="Custom bot status removed from the rotation.",
                color=0x2B2D31
            )
            return await ctx.reply(embed=embed)

        if len(status) > 100:
            return await ctx.reply("Status text must be 100 characters or fewer.")

        await server_status_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"status": status, "guild_name": ctx.guild.name}},
            upsert=True
        )
        embed = discord.Embed(
            title="Custom Status Set",
            description=(
                f"The bot will now rotate: **Watching {status}**\n\n"
                "Your text joins the global rotation pool across all premium servers."
            ),
            color=GOLD
        )
        embed.set_footer(text="Use ,setstatus again to change, or blank to remove")
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  BUMP REMINDER
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @ctx_premium()
    @commands.has_permissions(administrator=True)
    async def bumpreminder(self, ctx, status: str = "on"):
        """
        Enable or disable the DISBOARD bump reminder.
        Happy watches for DISBOARD's bump confirmation and pings 2 hours later.
        Usage: ,bumpreminder on/off
        """
        state = status.lower() == "on"

        await bump_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {
                "enabled":    state,
                "channel_id": str(ctx.channel.id),
                "set_by":     str(ctx.author.id)
            }},
            upsert=True
        )

        if state:
            embed = discord.Embed(
                title="Bump Reminder — Enabled",
                description=(
                    f"Happy is now watching for DISBOARD bumps in this server.\n\n"
                    f"**How it works:**\n"
                    f"1. Someone uses `/bump` on DISBOARD\n"
                    f"2. Happy detects the bump confirmation\n"
                    f"3. After **2 hours**, Happy pings in {ctx.channel.mention}\n"
                    f"4. Never miss a bump again"
                ),
                color=GOLD
            )
            embed.set_footer(text="DISBOARD Bot ID: 302050872383242240")
        else:
            embed = discord.Embed(
                description="Bump reminder disabled.",
                color=0x2B2D31
            )
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  VOICEMASTER — Temporary Voice Channels
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(invoke_without_command=True)
    @ctx_premium()
    @commands.has_permissions(administrator=True)
    async def vcsetup(self, ctx):
        """
        Set up VoiceMaster — members create their own private temp voice channels.
        Sub-commands: setup, limit, name, remove
        """
        doc = await voicemaster_col.find_one({"guild_id": str(ctx.guild.id)})
        if doc:
            create_ch = ctx.guild.get_channel(int(doc.get("create_channel_id", 0)))
            embed = discord.Embed(
                title="VoiceMaster — Active",
                description=(
                    f"Members join **{create_ch.mention if create_ch else 'unknown channel'}** "
                    f"to create their own private VC.\n\n"
                    "When the last member leaves, the VC is auto-deleted."
                ),
                color=GOLD
            )
            embed.add_field(name="Default User Limit", value=doc.get("default_limit", 10), inline=True)
            embed.add_field(name="Name Template",      value=f"`{doc.get('name_template', '{user} VC')}`", inline=True)
            embed.add_field(
                name="Sub-commands",
                value=(
                    "`,vcsetup create` — create the Join to Create channel\n"
                    "`,vcsetup limit <n>` — default user limit\n"
                    "`,vcsetup name <template>` — name template (`{user}` = member name)\n"
                    "`,vcsetup remove` — remove VoiceMaster"
                ),
                inline=False
            )
            embed.set_footer(text="VoiceMaster · Happy Premium")
        else:
            embed = discord.Embed(
                title="VoiceMaster — Not Configured",
                description=(
                    "VoiceMaster lets members create their own temporary voice channels.\n\n"
                    "Run `,vcsetup create` to get started."
                ),
                color=SILVER
            )
        await ctx.reply(embed=embed)

    @vcsetup.command(name="create")
    @ctx_premium()
    @commands.has_permissions(administrator=True)
    async def vcsetup_create(self, ctx):
        """Create the 'Join to Create' VC that triggers VoiceMaster."""
        cat = discord.utils.get(ctx.guild.categories, name="Voice Channels")
        if not cat:
            cat = await ctx.guild.create_category("Voice Channels")

        # Check if already exists
        doc = await voicemaster_col.find_one({"guild_id": str(ctx.guild.id)})
        if doc:
            existing = ctx.guild.get_channel(int(doc.get("create_channel_id", 0)))
            if existing:
                return await ctx.reply(
                    embed=discord.Embed(
                        description=f"VoiceMaster is already set up. Join {existing.mention} to create a VC.",
                        color=GOLD
                    )
                )

        vc = await ctx.guild.create_voice_channel(
            name="✦ Join to Create",
            category=cat,
            reason="VoiceMaster setup"
        )
        await voicemaster_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {
                "create_channel_id": str(vc.id),
                "category_id":       str(cat.id),
                "default_limit":     10,
                "name_template":     "{user}'s VC"
            }},
            upsert=True
        )
        embed = discord.Embed(
            title="VoiceMaster Ready",
            description=(
                f"Members can now join {vc.mention} to get their own private voice channel.\n\n"
                "The channel is automatically deleted when everyone leaves."
            ),
            color=GOLD
        )
        embed.add_field(name="Default Limit",  value="10 members", inline=True)
        embed.add_field(name="Name Template",  value="`{user}'s VC`", inline=True)
        embed.set_footer(text="Customize with ,vcsetup limit and ,vcsetup name")
        await ctx.reply(embed=embed)

    @vcsetup.command(name="limit")
    @ctx_premium()
    @commands.has_permissions(administrator=True)
    async def vcsetup_limit(self, ctx, limit: int = None):
        """Set the default user limit for auto-created VCs (0 = unlimited)."""
        if limit is None or not 0 <= limit <= 99:
            return await ctx.reply("Usage: `,vcsetup limit <0-99>`  (0 = unlimited)")
        await voicemaster_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"default_limit": limit}},
            upsert=True
        )
        val = "Unlimited" if limit == 0 else str(limit)
        await ctx.reply(embed=discord.Embed(
            description=f"Default VC user limit set to **{val}**.",
            color=GOLD
        ))

    @vcsetup.command(name="name")
    @ctx_premium()
    @commands.has_permissions(administrator=True)
    async def vcsetup_name(self, ctx, *, template: str = None):
        """
        Set the name template for auto-created VCs.
        Use {user} for the member's display name.
        Example: ,vcsetup name {user}'s Room
        """
        if not template:
            return await ctx.reply("Usage: `,vcsetup name <template>`\nExample: `,vcsetup name {user}'s Room`")
        if len(template) > 50:
            return await ctx.reply("Template must be 50 characters or fewer.")
        await voicemaster_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"name_template": template}},
            upsert=True
        )
        preview = template.replace("{user}", ctx.author.display_name)
        await ctx.reply(embed=discord.Embed(
            description=f"Name template updated.\nPreview: **{preview}**",
            color=GOLD
        ))

    @vcsetup.command(name="remove")
    @ctx_premium()
    @commands.has_permissions(administrator=True)
    async def vcsetup_remove(self, ctx):
        """Remove VoiceMaster from this server."""
        doc = await voicemaster_col.find_one({"guild_id": str(ctx.guild.id)})
        if not doc:
            return await ctx.reply("VoiceMaster is not configured on this server.")
        # Delete the trigger channel
        ch = ctx.guild.get_channel(int(doc.get("create_channel_id", 0)))
        if ch:
            try:
                await ch.delete(reason="VoiceMaster removed")
            except:
                pass
        # Delete any lingering temp VCs
        for vcid in doc.get("temp_channels", []):
            tc = ctx.guild.get_channel(int(vcid))
            if tc:
                try:
                    await tc.delete(reason="VoiceMaster cleanup")
                except:
                    pass
        await voicemaster_col.delete_one({"guild_id": str(ctx.guild.id)})
        await ctx.reply(embed=discord.Embed(
            description="VoiceMaster removed and all temporary VCs deleted.",
            color=0x2B2D31
        ))

    # ── VoiceState handler ────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before:  discord.VoiceState,
        after:   discord.VoiceState
    ):
        doc = await voicemaster_col.find_one({"guild_id": str(member.guild.id)})
        if not doc:
            return

        # ── Joined the trigger channel ────────────────────────────────────────
        if after.channel and str(after.channel.id) == doc.get("create_channel_id"):
            cat      = member.guild.get_channel(int(doc["category_id"]))
            template = doc.get("name_template", "{user}'s VC")
            vc_name  = template.replace("{user}", member.display_name)
            limit    = doc.get("default_limit", 10)

            new_vc = await member.guild.create_voice_channel(
                name=vc_name,
                category=cat,
                user_limit=limit,
                reason=f"VoiceMaster: {member}"
            )
            # Give owner full control of their VC
            await new_vc.set_permissions(
                member,
                connect=True, speak=True, manage_channels=True,
                move_members=True, mute_members=True
            )
            await member.move_to(new_vc)
            await voicemaster_col.update_one(
                {"guild_id": str(member.guild.id)},
                {"$push": {"temp_channels": str(new_vc.id)}}
            )

        # ── Left a temp VC → delete if empty ─────────────────────────────────
        if before.channel and before.channel != (after.channel if after else None):
            temp_ids = doc.get("temp_channels", [])
            if (str(before.channel.id) in temp_ids
                    and len(before.channel.members) == 0):
                try:
                    await before.channel.delete(reason="VoiceMaster: empty channel")
                except:
                    pass
                await voicemaster_col.update_one(
                    {"guild_id": str(member.guild.id)},
                    {"$pull": {"temp_channels": str(before.channel.id)}}
                )

    # ══════════════════════════════════════════════════════════════════════════
    #  GLOBAL CALL  — Cross-server text relay
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @ctx_premium()
    @commands.has_permissions(administrator=True)
    async def call(self, ctx):
        """
        Connect this channel to another server's channel for a live cross-server chat.
        Messages sent here will be relayed to the partner server in real time.
        """
        sid = ctx.guild.id
        cid = ctx.channel.id

        if sid in _active_calls:
            data   = _active_calls[sid]
            pch    = self.bot.get_channel(data["partner_channel"])
            pfname = pch.guild.name if pch else "Unknown Server"
            embed  = discord.Embed(
                description=(
                    f"Already on a call with **{pfname}**.\n"
                    "Use `,hangup` to end it first."
                ),
                color=GOLD
            )
            return await ctx.reply(embed=embed)

        if any(d["server_id"] == sid for d in _waiting_list):
            pos = next(i for i, d in enumerate(_waiting_list) if d["server_id"] == sid) + 1
            embed = discord.Embed(
                description=f"Already in the queue at position **#{pos}**.\nUse `,hangup` to cancel.",
                color=GOLD
            )
            return await ctx.reply(embed=embed)

        if _waiting_list:
            # Match found
            partner   = _waiting_list.pop(0)
            p_sid     = partner["server_id"]
            p_cid     = partner["channel_id"]
            p_name    = partner.get("guild_name", "Unknown Server")

            _active_calls[sid]   = {"partner_channel": p_cid, "my_channel": cid,   "guild_name": p_name}
            _active_calls[p_sid] = {"partner_channel": cid,   "my_channel": p_cid, "guild_name": ctx.guild.name}

            # My side
            my_embed = discord.Embed(
                title="Call Connected",
                description=(
                    f"You are now live with **{p_name}**.\n\n"
                    "Messages in this channel are relayed to them in real time.\n"
                    "Use `,hangup` to end the call."
                ),
                color=GOLD
            )
            my_embed.set_footer(text="Happy Global Call · Premium Feature")
            await ctx.send(embed=my_embed)

            # Partner side
            partner_embed = discord.Embed(
                title="Call Connected",
                description=(
                    f"**{ctx.guild.name}** joined the call.\n\n"
                    "Messages in this channel are relayed to them in real time.\n"
                    "Use `,hangup` to end the call."
                ),
                color=GOLD
            )
            partner_embed.set_footer(text="Happy Global Call · Premium Feature")
            pch = self.bot.get_channel(p_cid)
            if pch:
                await pch.send(embed=partner_embed)
        else:
            # No match yet — add to queue
            _waiting_list.append({
                "server_id":  sid,
                "channel_id": cid,
                "guild_name": ctx.guild.name,
            })
            queue_pos = len(_waiting_list)
            embed = discord.Embed(
                title="Waiting for a Partner",
                description=(
                    f"Queue position: **#{queue_pos}**\n\n"
                    "As soon as another premium server uses `,call`, "
                    "you'll be connected instantly.\n"
                    "Use `,hangup` to cancel."
                ),
                color=GOLD
            )
            embed.set_footer(text="Happy Global Call · Premium Feature")
            await ctx.reply(embed=embed)

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def hangup(self, ctx):
        """End the current global call or remove yourself from the call queue."""
        sid = ctx.guild.id

        # Remove from waiting list
        in_wait = next((d for d in _waiting_list if d["server_id"] == sid), None)
        if in_wait:
            _waiting_list.remove(in_wait)
            embed = discord.Embed(
                description="Removed from the call queue.",
                color=0x2B2D31
            )
            return await ctx.reply(embed=embed)

        # End active call
        if sid in _active_calls:
            data   = _active_calls.pop(sid)
            p_cid  = data.get("partner_channel")
            p_name = data.get("guild_name", "the other server")

            # Remove partner entry
            for psid, pdata in list(_active_calls.items()):
                if pdata.get("my_channel") == p_cid:
                    del _active_calls[psid]
                    break

            embed = discord.Embed(
                title="Call Ended",
                description=f"The call with **{p_name}** has ended.",
                color=0x2B2D31
            )
            await ctx.reply(embed=embed)

            # Notify partner
            if p_cid:
                pch = self.bot.get_channel(p_cid)
                if pch:
                    try:
                        await pch.send(embed=discord.Embed(
                            title="Call Ended",
                            description=f"**{ctx.guild.name}** ended the call.",
                            color=0x2B2D31
                        ))
                    except:
                        pass
        else:
            await ctx.reply(embed=discord.Embed(
                description="No active call or queue entry found.",
                color=0x2B2D31
            ))

    @commands.command(aliases=["callstatus", "cs"])
    async def callinfo(self, ctx):
        """Check current call status for this server."""
        sid  = ctx.guild.id
        data = _active_calls.get(sid)

        if data:
            pch    = self.bot.get_channel(data["partner_channel"])
            pname  = pch.guild.name if pch else data.get("guild_name", "Unknown")
            embed  = discord.Embed(
                title="Call Active",
                description=(
                    f"Currently connected to **{pname}**.\n"
                    "Messages in this channel are being relayed."
                ),
                color=GOLD
            )
            embed.add_field(name="Partner Channel", value=pch.mention if pch else "Unknown", inline=True)
            embed.add_field(name="End Call",        value="`,hangup`",                        inline=True)
        elif any(d["server_id"] == sid for d in _waiting_list):
            pos   = next(i for i, d in enumerate(_waiting_list) if d["server_id"] == sid) + 1
            embed = discord.Embed(
                description=f"In queue at position **#{pos}**. Waiting for a partner.",
                color=GOLD
            )
            embed.add_field(name="Cancel", value="`,hangup`", inline=True)
            embed.add_field(name="Queue size", value=str(len(_waiting_list)), inline=True)
        else:
            embed = discord.Embed(
                description="No active call. Use `,call` to connect to another server.",
                color=0x2B2D31
            )
            embed.add_field(name="Servers in queue", value=str(len(_waiting_list)), inline=True)

        embed.set_footer(text="Happy Global Call · Premium Feature")
        await ctx.reply(embed=embed)

    # ── Message relay ─────────────────────────────────────────────────────────
    async def relay_call(self, message: discord.Message):
        """Called by Core cog to relay messages during an active call."""
        sid  = message.guild.id
        data = _active_calls.get(sid)
        if not data or message.channel.id != data.get("my_channel"):
            return
        # Safety: never relay @everyone or @here
        if message.mention_everyone:
            return
        pch = self.bot.get_channel(data["partner_channel"])
        if not pch:
            return
        try:
            relay_embed = discord.Embed(
                description=message.content[:1000] if message.content else "*[no text]*",
                color=0x2B2D31
            )
            relay_embed.set_author(
                name=f"{message.author.display_name} · {message.guild.name}",
                icon_url=message.author.display_avatar.url
            )
            if message.attachments:
                relay_embed.add_field(
                    name="Attachments",
                    value="\n".join(a.url for a in message.attachments[:3]),
                    inline=False
                )
            await pch.send(embed=relay_embed)
        except:
            pass

    # ══════════════════════════════════════════════════════════════════════════
    #  QUICK SETUP WIZARD
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def quicksetup(self, ctx):
        """
        Run the server quick-setup wizard.
        Auto-creates standard channels, categories, and roles.
        """
        embed = discord.Embed(
            title="Quick Setup Wizard",
            description="Setting up your server. This may take a moment...",
            color=GOLD
        )
        msg     = await ctx.reply(embed=embed)
        guild   = ctx.guild
        created = []
        skipped = []

        # ── Roles ─────────────────────────────────────────────────────────────
        roles_to_make = [
            ("Member",    discord.Color.from_rgb(88, 101, 242)),
            ("Moderator", discord.Color.from_rgb(87, 242, 135)),
            ("Admin",     discord.Color.from_rgb(237, 66, 69)),
            ("Muted",     discord.Color.dark_gray()),
        ]
        for rname, rcolor in roles_to_make:
            if not discord.utils.get(guild.roles, name=rname):
                await guild.create_role(name=rname, color=rcolor)
                created.append(f"Role `{rname}`")
            else:
                skipped.append(f"Role `{rname}` (exists)")
            await asyncio.sleep(0.3)

        # ── Categories & channels ─────────────────────────────────────────────
        cats = {
            "INFORMATION": ["rules", "announcements", "roles"],
            "GENERAL":     ["general", "off-topic", "media", "bot-commands"],
            "STAFF":       ["mod-logs", "mod-chat", "staff-chat"],
        }
        for cat_name, ch_names in cats.items():
            cat = discord.utils.get(guild.categories, name=cat_name)
            if not cat:
                cat = await guild.create_category(cat_name)
                created.append(f"Category `{cat_name}`")
            for ch_name in ch_names:
                if not discord.utils.get(guild.channels, name=ch_name):
                    await guild.create_text_channel(ch_name, category=cat)
                    created.append(f"#{ch_name}")
                else:
                    skipped.append(f"#{ch_name} (exists)")
                await asyncio.sleep(0.3)

        # ── Final embed ────────────────────────────────────────────────────────
        result_embed = discord.Embed(
            title="Quick Setup Complete",
            color=GOLD,
            timestamp=datetime.now(timezone.utc)
        )

        if created:
            result_embed.add_field(
                name=f"Created ({len(created)})",
                value="\n".join(f"+ {c}" for c in created[:20]),
                inline=True
            )
        if skipped:
            result_embed.add_field(
                name=f"Skipped ({len(skipped)})",
                value="\n".join(f"— {s}" for s in skipped[:10]),
                inline=True
            )

        result_embed.add_field(
            name="Next Steps",
            value=(
                "`,welcome set #channel` — set welcome channel\n"
                "`,bye set #channel` — set goodbye channel\n"
                "`,logs set #mod-logs` — enable logging\n"
                "`,ticket setup` — set up support tickets\n"
                "`,jailsetup` — set up jail system"
            ),
            inline=False
        )
        result_embed.set_footer(text="Happy Premium · Quick Setup Wizard")
        await msg.edit(embed=result_embed)


async def setup(bot):
    await bot.add_cog(Premium(bot))