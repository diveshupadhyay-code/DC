"""
cogs/premium.py — Global call, VoiceMaster, bump reminder, custom status.
                   Also owns the ,premium management commands (owner only).
"""

import discord
from discord.ext import commands
import asyncio

from utils.db import (
    premium_col, server_status_col, voicemaster_col,
    bump_col, settings_col, booster_roles_col
)
from utils.helpers import BOT_OWNER_ID, ctx_owner, ctx_premium, ctx_admin, log_event

# In-memory call state
_active_calls  = {}  # {server_id: {partner_channel, my_channel}}
_waiting_list  = []  # [{server_id, channel_id}]


class Premium(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Owner: manage premium ──────────────────────────────────────────────────
    @commands.group(invoke_without_command=True)
    @ctx_owner()
    async def premium(self, ctx):
        """Manage premium entries (owner only). Sub-commands: add, remove, list"""
        await ctx.reply(
            "Sub-commands:\n"
            "`,premium add server <guild_id>`\n"
            "`,premium add user <user_id>`\n"
            "`,premium remove server/user <id>`\n"
            "`,premium list`"
        )

    @premium.command(name="add")
    @ctx_owner()
    async def premium_add(self, ctx, type_: str = None, target: str = None):
        """Activate premium for a server or user."""
        if not type_ or type_ not in ("server","user") or not target:
            return await ctx.reply("Usage: `,premium add server/user <id>`")
        await premium_col.update_one(
            {"type": type_, "id": target},
            {"$set": {"type": type_, "id": target}},
            upsert=True
        )
        await ctx.reply(f"Premium activated for {type_} `{target}`.")

    @premium.command(name="remove")
    @ctx_owner()
    async def premium_remove(self, ctx, type_: str = None, target: str = None):
        """Remove premium from a server or user."""
        if not type_ or type_ not in ("server","user") or not target:
            return await ctx.reply("Usage: `,premium remove server/user <id>`")
        await premium_col.delete_one({"type": type_, "id": target})
        await ctx.reply(f"Premium removed from {type_} `{target}`.")

    @premium.command(name="list")
    @ctx_owner()
    async def premium_list(self, ctx):
        """List all premium users and servers."""
        items = await premium_col.find({}).to_list(100)
        if not items:
            return await ctx.reply("No premium entries found.")
        servers = [i["id"] for i in items if i["type"] == "server"]
        users   = [i["id"] for i in items if i["type"] == "user"]
        embed = discord.Embed(title="Premium Entries", color=0xffd700)
        embed.add_field(name=f"Servers ({len(servers)})", value="\n".join(servers) or "None", inline=True)
        embed.add_field(name=f"Users ({len(users)})",     value="\n".join(users)   or "None", inline=True)
        await ctx.reply(embed=embed)

    # ── Custom bot status (premium server) ────────────────────────────────────
    @commands.command()
    @ctx_premium()
    @commands.has_permissions(administrator=True)
    async def setstatus(self, ctx, *, status: str = None):
        """Set a custom bot status for your server (Premium). Leave blank to remove."""
        if not status:
            await server_status_col.delete_one({"guild_id": str(ctx.guild.id)})
            return await ctx.reply("Custom bot status removed.")
        await server_status_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"status": status}},
            upsert=True
        )
        await ctx.reply(f"Custom status set to: `{status}`")

    # ── Bump reminder (premium) ───────────────────────────────────────────────
    @commands.command()
    @ctx_premium()
    @commands.has_permissions(administrator=True)
    async def bumpreminder(self, ctx, status: str = "on"):
        """Enable/disable the DISBOARD bump reminder (Premium)."""
        state = status.lower() == "on"
        await bump_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"enabled": state, "channel_id": str(ctx.channel.id)}},
            upsert=True
        )
        await ctx.reply(
            f"Bump reminder {'enabled' if state else 'disabled'} in {ctx.channel.mention}.\n"
            "Happy will remind you 2 hours after every DISBOARD bump."
        )

    # ── VoiceMaster (premium) ─────────────────────────────────────────────────
    @commands.command()
    @ctx_premium()
    @commands.has_permissions(administrator=True)
    async def vcsetup(self, ctx):
        """Set up VoiceMaster — temporary private voice channels (Premium)."""
        cat = discord.utils.get(ctx.guild.categories, name="Voice Channels")
        if not cat:
            cat = await ctx.guild.create_category("Voice Channels")
        vc = await ctx.guild.create_voice_channel("Join to Create", category=cat)
        await voicemaster_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"create_channel_id": str(vc.id), "category_id": str(cat.id)}},
            upsert=True
        )
        await ctx.reply(
            f"VoiceMaster ready! Join {vc.mention} to create your own temporary voice channel."
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after:  discord.VoiceState
    ):
        doc = await voicemaster_col.find_one({"guild_id": str(member.guild.id)})
        if not doc:
            return

        # Member joined the "Join to Create" channel
        if after.channel and str(after.channel.id) == doc.get("create_channel_id"):
            cat = member.guild.get_channel(int(doc["category_id"]))
            new_vc = await member.guild.create_voice_channel(
                name=f"{member.display_name}'s VC",
                category=cat,
                user_limit=10
            )
            await member.move_to(new_vc)
            await voicemaster_col.update_one(
                {"guild_id": str(member.guild.id)},
                {"$push": {"temp_channels": str(new_vc.id)}}
            )

        # Clean up empty temp VCs
        if before.channel and before.channel != (after.channel if after else None):
            temp_ids = doc.get("temp_channels", [])
            if str(before.channel.id) in temp_ids and len(before.channel.members) == 0:
                try:
                    await before.channel.delete(reason="Empty VoiceMaster channel")
                except:
                    pass
                await voicemaster_col.update_one(
                    {"guild_id": str(member.guild.id)},
                    {"$pull": {"temp_channels": str(before.channel.id)}}
                )

    # ── Global Call (premium) ─────────────────────────────────────────────────
    @commands.command()
    @ctx_premium()
    @commands.has_permissions(administrator=True)
    async def call(self, ctx):
        """Connect this channel to another random server's channel (Premium)."""
        global _active_calls, _waiting_list
        sid = ctx.guild.id
        cid = ctx.channel.id

        if sid in _active_calls:
            return await ctx.reply("Already on a call. Use `,hangup` to end it first.")
        if any(d["server_id"] == sid for d in _waiting_list):
            return await ctx.reply("Already waiting for a partner.")

        if _waiting_list:
            partner = _waiting_list.pop(0)
            p_sid   = partner["server_id"]
            p_cid   = partner["channel_id"]
            _active_calls[sid]   = {"partner_channel": p_cid, "my_channel": cid}
            _active_calls[p_sid] = {"partner_channel": cid,   "my_channel": p_cid}

            embed = discord.Embed(
                title="Call Connected",
                description=(
                    "You are now connected to another server.\n"
                    "Messages in this channel will be relayed. Use `,hangup` to end."
                ),
                color=0x2B2D31
            )
            await ctx.send(embed=embed)
            pch = self.bot.get_channel(p_cid)
            if pch:
                await pch.send(embed=discord.Embed(
                    title="Call Connected",
                    description="A partner server joined. Use `,hangup` to end.",
                    color=0x2B2D31
                ))
        else:
            _waiting_list.append({"server_id": sid, "channel_id": cid})
            embed = discord.Embed(
                description="Waiting for another server to connect...\nUse `,hangup` to cancel.",
                color=0x2B2D31
            )
            await ctx.reply(embed=embed)

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def hangup(self, ctx):
        """End the current global call or leave the waiting queue."""
        global _active_calls, _waiting_list
        sid = ctx.guild.id

        # Remove from waiting
        in_wait = next((d for d in _waiting_list if d["server_id"] == sid), None)
        if in_wait:
            _waiting_list.remove(in_wait)
            return await ctx.reply("Removed from the call queue.")

        # End active call
        if sid in _active_calls:
            data  = _active_calls.pop(sid)
            p_cid = data.get("partner_channel")
            # Remove partner side
            for psid, pdata in list(_active_calls.items()):
                if pdata.get("my_channel") == p_cid:
                    del _active_calls[psid]
                    break
            await ctx.reply("Call ended.")
            if p_cid:
                pch = self.bot.get_channel(p_cid)
                if pch:
                    await pch.send(embed=discord.Embed(
                        description="The other server ended the call.",
                        color=0x2B2D31
                    ))
        else:
            await ctx.reply("No active call found.")

    async def relay_call(self, message: discord.Message):
        """Called by Core to relay messages across calls."""
        sid  = message.guild.id
        data = _active_calls.get(sid)
        if not data:
            return
        if message.channel.id != data.get("my_channel"):
            return
        # Don't relay @everyone or @here
        if message.mention_everyone:
            return
        pch = self.bot.get_channel(data["partner_channel"])
        if pch:
            try:
                await pch.send(f"**{message.author.display_name}:** {message.content[:1000]}")
            except:
                pass

    # ── Quick server setup (admin) ─────────────────────────────────────────────
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def quicksetup(self, ctx):
        """Auto-create standard channels, categories, and roles for a new server."""
        msg = await ctx.reply("Running quick setup...")
        guild   = ctx.guild
        created = []

        roles_to_make = ["Member", "Moderator", "Admin", "Muted"]
        for rname in roles_to_make:
            if not discord.utils.get(guild.roles, name=rname):
                await guild.create_role(name=rname)
                created.append(f"Role: `{rname}`")
            await asyncio.sleep(0.3)

        cats = {
            "INFORMATION": ["rules", "announcements", "roles"],
            "GENERAL":     ["general", "off-topic", "media"],
            "MODERATION":  ["mod-logs", "mod-chat"],
        }
        for cat_name, ch_names in cats.items():
            cat = discord.utils.get(guild.categories, name=cat_name)
            if not cat:
                cat = await guild.create_category(cat_name)
                created.append(f"Category: `{cat_name}`")
            for ch_name in ch_names:
                if not discord.utils.get(guild.channels, name=ch_name):
                    await guild.create_text_channel(ch_name, category=cat)
                    created.append(f"Channel: `#{ch_name}`")
                await asyncio.sleep(0.3)

        summary = "\n".join(created) or "Everything already existed."
        embed = discord.Embed(
            title="Quick Setup Complete",
            description=summary,
            color=0x2B2D31
        )
        embed.set_footer(text="Configure welcome/bye/logs/tickets separately.")
        await msg.edit(content=None, embed=embed)


async def setup(bot):
    await bot.add_cog(Premium(bot))
