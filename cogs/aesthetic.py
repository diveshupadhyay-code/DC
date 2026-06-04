"""
cogs/aesthetic.py — Makes every server feel premium and polished.

FREE tier (all servers):
  - Clean embed responses everywhere
  - Auto-react to media/art posts
  - Typing indicator on bot replies
  - Server activity greeting cards
  - Clean join/leave DMs

PREMIUM tier (premium servers):
  - Auto-role on join with welcome DM card
  - Aesthetic channel headers (auto-topic banners)
  - Server stats embed (live vanity card)
  - Color roles system (members pick accent color)
  - Birthday announcement card with confetti
  - Milestone announcements (member count milestones)
  - Auto-thread on important announcements
  - Embed-only mode (delete plain text in specified channels)
  - Server card / invite card
  - Auto pin first message in announcement channels
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import random
from datetime import datetime, timezone

from utils.db import db, settings_col, premium_col
from utils.helpers import (
    BOT_OWNER_ID, ctx_mod, ctx_admin, ctx_premium,
    is_premium_server, log_event
)

# ── Extra collections ─────────────────────────────────────────────────────────
aesthetic_col   = db["aesthetic_config"]   # per-guild aesthetic settings
color_roles_col = db["color_roles"]         # {guild_id, roles: [{name, color, role_id}]}
embed_only_col  = db["embed_only_channels"] # {guild_id, channel_ids: [...]}
milestone_col   = db["milestones"]          # {guild_id, announced: [100, 500, ...]}

# ── Gold accent for premium ───────────────────────────────────────────────────
GOLD   = 0xF0C040
SILVER = 0xC0C0C0

# ── Member count milestones to celebrate ─────────────────────────────────────
MILESTONES = [10, 25, 50, 100, 200, 250, 500, 750, 1000,
              2000, 5000, 10000, 25000, 50000, 100000]

# ── Aesthetic color palette for color roles ───────────────────────────────────
DEFAULT_COLORS = [
    ("Rose",      0xE8425A),
    ("Lavender",  0xA78BFA),
    ("Sky",       0x38BDF8),
    ("Mint",      0x34D399),
    ("Peach",     0xFB923C),
    ("Gold",      0xFBBF24),
    ("Crimson",   0xDC2626),
    ("Sapphire",  0x3B82F6),
    ("Emerald",   0x10B981),
    ("Violet",    0x8B5CF6),
    ("Coral",     0xF97316),
    ("Steel",     0x64748B),
]


class Aesthetic(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ══════════════════════════════════════════════════════════════════════════
    #  FREE — AUTO REACTIONS ON MEDIA
    # ══════════════════════════════════════════════════════════════════════════

    # ══════════════════════════════════════════════════════════════════════════
    #  FREE — MEMBER JOIN CARD
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        gs  = await settings_col.find_one({"_id": str(member.guild.id)}) or {}
        cfg = await aesthetic_col.find_one({"guild_id": str(member.guild.id)}) or {}

        # ── DM welcome card ────────────────────────────────────────────────
        if cfg.get("dm_welcome"):
            try:
                embed = discord.Embed(
                    title=f"Welcome to {member.guild.name}",
                    description=(
                        cfg.get("dm_welcome_text") or
                        f"Hey {member.mention}, glad you're here!\n"
                        f"Make yourself at home."
                    ),
                    color=cfg.get("accent_color", 0x2B2D31)
                )
                if member.guild.icon:
                    embed.set_thumbnail(url=member.guild.icon.url)
                embed.set_footer(
                    text=f"{member.guild.name} · {datetime.now(timezone.utc).strftime('%d %b %Y')}"
                )
                await member.send(embed=embed)
            except:
                pass

        # ── Auto-role on join (premium) ────────────────────────────────────
        if await is_premium_server(member.guild.id):
            auto_role_id = cfg.get("auto_role_id")
            if auto_role_id:
                role = member.guild.get_role(int(auto_role_id))
                if role:
                    try:
                        await member.add_roles(role, reason="Auto-role on join")
                    except:
                        pass

        # ── Milestone check (premium) ──────────────────────────────────────
        if await is_premium_server(member.guild.id):
            await self._check_milestone(member.guild)

    # ══════════════════════════════════════════════════════════════════════════
    #  PREMIUM — MILESTONE ANNOUNCEMENTS
    # ══════════════════════════════════════════════════════════════════════════

    async def _check_milestone(self, guild: discord.Guild):
        count  = guild.member_count
        if count not in MILESTONES:
            return

        doc = await milestone_col.find_one({"guild_id": str(guild.id)}) or {}
        announced = doc.get("announced", [])
        if count in announced:
            return

        # Mark as announced
        await milestone_col.update_one(
            {"guild_id": str(guild.id)},
            {"$push": {"announced": count}},
            upsert=True
        )

        gs  = await settings_col.find_one({"_id": str(guild.id)}) or {}
        cfg = await aesthetic_col.find_one({"guild_id": str(guild.id)}) or {}
        cid = cfg.get("milestone_channel_id") or gs.get("welcome_channel")
        ch  = self.bot.get_channel(int(cid)) if cid else guild.system_channel
        if not ch:
            return

        embed = discord.Embed(
            title=f"{count} Members",
            description=(
                f"**{guild.name}** just hit **{count:,} members!**\n\n"
                f"Thank you to everyone who joined and made this community what it is."
            ),
            color=GOLD
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.set_footer(text=f"Milestone · {datetime.now(timezone.utc).strftime('%d %b %Y')}")
        await ch.send(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  PREMIUM — COLOR ROLES
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(invoke_without_command=True)
    async def color(self, ctx):
        """Pick an accent color role for yourself (Premium servers only)."""
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(
                description="Color roles are a **Happy Premium** feature.",
                color=SILVER
            ))

        doc = await color_roles_col.find_one({"guild_id": str(ctx.guild.id)})
        if not doc or not doc.get("roles"):
            return await ctx.reply(embed=discord.Embed(
                description=(
                    "Color roles aren't set up yet.\n"
                    "An admin can run `,color setup` to add the default palette."
                ),
                color=0x2B2D31
            ))

        roles  = doc["roles"]
        embed  = discord.Embed(
            title="Color Roles",
            description="Pick a color to show on your profile.",
            color=GOLD
        )
        lines = []
        for r in roles:
            role = ctx.guild.get_role(int(r["role_id"]))
            if role:
                has = "▶" if role in ctx.author.roles else "  "
                lines.append(f"{has} `{r['name']}`")
        embed.add_field(name="Available", value="\n".join(lines) or "None", inline=True)
        embed.add_field(
            name="Usage",
            value="`,color pick <name>` — pick a color\n`,color remove` — remove your color",
            inline=True
        )
        embed.set_footer(text="Happy Premium · Color Roles")
        await ctx.reply(embed=embed)

    @color.command(name="setup")
    @ctx_admin()
    async def color_setup(self, ctx):
        """Create the default color role palette for this server (Admin only)."""
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(
                description="Color roles are a **Happy Premium** feature.",
                color=SILVER
            ))

        msg    = await ctx.reply(embed=discord.Embed(
            description="Creating color roles...", color=GOLD
        ))
        created = []

        for name, hex_color in DEFAULT_COLORS:
            role = discord.utils.get(ctx.guild.roles, name=f"✦ {name}")
            if not role:
                role = await ctx.guild.create_role(
                    name=f"✦ {name}",
                    color=discord.Color(hex_color),
                    reason="Happy aesthetic color roles"
                )
            created.append({"name": name, "role_id": str(role.id), "color": hex_color})
            await asyncio.sleep(0.4)

        await color_roles_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"roles": created}},
            upsert=True
        )

        embed = discord.Embed(
            title="Color Roles Ready",
            description="\n".join(
                f"● `{r['name']}`" for r in created
            ),
            color=GOLD
        )
        embed.set_footer(text="Members can now use ,color pick <name>")
        await msg.edit(embed=embed)

    @color.command(name="pick")
    async def color_pick(self, ctx, *, name: str = None):
        """Pick a color role."""
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(
                description="Color roles are a **Happy Premium** feature.",
                color=SILVER
            ))

        if not name:
            return await ctx.reply("Usage: `,color pick <color name>`  e.g. `,color pick Lavender`")

        doc = await color_roles_col.find_one({"guild_id": str(ctx.guild.id)})
        if not doc:
            return await ctx.reply("Color roles not set up. Ask an admin to run `,color setup`.")

        # Find matching role
        match = next(
            (r for r in doc["roles"] if r["name"].lower() == name.lower()),
            None
        )
        if not match:
            names = ", ".join(f"`{r['name']}`" for r in doc["roles"])
            return await ctx.reply(f"Color not found. Available: {names}")

        role = ctx.guild.get_role(int(match["role_id"]))
        if not role:
            return await ctx.reply("That color role no longer exists. Ask an admin to run `,color setup` again.")

        # Remove any existing color roles first
        existing_color_roles = [
            ctx.guild.get_role(int(r["role_id"]))
            for r in doc["roles"]
            if ctx.guild.get_role(int(r["role_id"])) in ctx.author.roles
        ]
        if existing_color_roles:
            await ctx.author.remove_roles(*existing_color_roles, reason="Color role swap")

        await ctx.author.add_roles(role, reason=f"Color role: {match['name']}")
        embed = discord.Embed(
            description=f"Your color is now **{match['name']}**.",
            color=match["color"]
        )
        await ctx.reply(embed=embed)

    @color.command(name="remove")
    async def color_remove(self, ctx):
        """Remove your color role."""
        doc = await color_roles_col.find_one({"guild_id": str(ctx.guild.id)})
        if not doc:
            return await ctx.reply("No color roles set up on this server.")

        removed = []
        for r in doc["roles"]:
            role = ctx.guild.get_role(int(r["role_id"]))
            if role and role in ctx.author.roles:
                await ctx.author.remove_roles(role)
                removed.append(r["name"])

        if removed:
            await ctx.reply(embed=discord.Embed(
                description=f"Color role **{', '.join(removed)}** removed.",
                color=0x2B2D31
            ))
        else:
            await ctx.reply("You don't have a color role set.")

    @color.command(name="add")
    @ctx_admin()
    async def color_add(self, ctx, name: str = None, hex_color: str = None):
        """Add a custom color to the palette. Usage: ,color add Sunset #FF6B35"""
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply("Premium only feature.")
        if not name or not hex_color:
            return await ctx.reply("Usage: `,color add <name> #hexcode`")
        try:
            color_int = int(hex_color.replace("#", ""), 16)
        except:
            return await ctx.reply("Invalid hex color. Use format `#FF6B35`.")

        role = await ctx.guild.create_role(
            name=f"✦ {name}",
            color=discord.Color(color_int),
            reason=f"Custom color role: {name}"
        )
        await color_roles_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$push": {"roles": {"name": name, "role_id": str(role.id), "color": color_int}}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(
            description=f"Color **{name}** added to the palette.",
            color=color_int
        ))

    # ══════════════════════════════════════════════════════════════════════════
    #  SERVER CARD  (premium)
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["scard", "invite"])
    async def servercard(self, ctx):
        """Generate a beautiful server info / invite card."""
        g      = ctx.guild
        is_prem = await is_premium_server(g.id)
        color  = GOLD if is_prem else 0x2B2D31

        # Build invite
        invite_url = None
        try:
            # Try existing vanity URL first
            if g.vanity_url_code:
                invite_url = f"https://discord.gg/{g.vanity_url_code}"
            else:
                # Find a general channel to create invite
                for ch in g.text_channels:
                    if ch.permissions_for(g.me).create_instant_invite:
                        inv = await ch.create_invite(max_age=0, max_uses=0, reason="Server card")
                        invite_url = inv.url
                        break
        except:
            pass

        bots   = sum(1 for m in g.members if m.bot)
        humans = g.member_count - bots
        online = sum(
            1 for m in g.members
            if m.status != discord.Status.offline and not m.bot
        )

        embed = discord.Embed(
            title=g.name,
            description=g.description or "A great community.",
            color=color
        )
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        if g.banner:
            embed.set_image(url=g.banner.url)

        embed.add_field(name="Members",  value=f"{humans:,} humans · {bots} bots", inline=True)
        embed.add_field(name="Online",   value=f"{online:,}",                       inline=True)
        embed.add_field(name="Boost",    value=f"Level {g.premium_tier} ({g.premium_subscription_count} boosts)", inline=True)
        embed.add_field(name="Channels", value=str(len(g.channels)),                inline=True)
        embed.add_field(name="Roles",    value=str(len(g.roles)),                   inline=True)
        embed.add_field(name="Created",  value=f"<t:{int(g.created_at.timestamp())}:D>", inline=True)

        if invite_url:
            embed.add_field(name="Invite", value=f"[Join {g.name}]({invite_url})", inline=False)

        if is_prem:
            embed.set_footer(text=f"Happy Premium · {g.name}")
        else:
            embed.set_footer(text=g.name)

        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  AESTHETIC LISTENER
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener("on_message")
    async def aesthetic_on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        cfg = await aesthetic_col.find_one({"guild_id": str(message.guild.id)}) or {}

        # ── Auto-react in media channels ──────────────────────────────────
        if cfg.get("media_channels"):
            if str(message.channel.id) in cfg["media_channels"]:
                if message.attachments or any(
                    ext in message.content.lower()
                    for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]
                ):
                    for emoji in ["❤️", "🔥"]:
                        try:
                            await asyncio.sleep(0.3)
                            await message.add_reaction(emoji)
                        except:
                            pass

        # ── Embed-only channels (premium) ─────────────────────────────────
        if cfg.get("embed_only_channels") and await is_premium_server(message.guild.id):
            if (str(message.channel.id) in cfg["embed_only_channels"]
                    and message.content
                    and not message.embeds
                    and not message.attachments
                    and not message.author.guild_permissions.manage_messages
                    and message.author.id != BOT_OWNER_ID):
                try:
                    await message.delete()
                    await message.channel.send(
                        embed=discord.Embed(
                            description=(
                                f"{message.author.mention} this channel only accepts "
                                "embeds and media."
                            ),
                            color=0x2B2D31
                        ),
                        delete_after=6
                    )
                except:
                    pass

        # ── Auto-thread on announcements (premium) ─────────────────────────
        if cfg.get("auto_thread_channels") and await is_premium_server(message.guild.id):
            if str(message.channel.id) in cfg["auto_thread_channels"]:
                try:
                    title = (
                        message.content[:50] + "..." if len(message.content) > 50
                        else message.content or "Discussion"
                    )
                    await message.create_thread(
                        name=title,
                        auto_archive_duration=1440
                    )
                except:
                    pass

        # ── Auto-pin first message in pin channels (premium) ───────────────
        if cfg.get("auto_pin_channels") and await is_premium_server(message.guild.id):
            if str(message.channel.id) in cfg["auto_pin_channels"]:
                try:
                    pins = await message.channel.pins()
                    if len(pins) < 50:
                        await message.pin()
                except:
                    pass

    # ══════════════════════════════════════════════════════════════════════════
    #  AESTHETIC CONFIG COMMANDS
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(invoke_without_command=True)
    @ctx_admin()
    async def aesthetic(self, ctx):
        """Aesthetic settings dashboard."""
        cfg     = await aesthetic_col.find_one({"guild_id": str(ctx.guild.id)}) or {}
        is_prem = await is_premium_server(ctx.guild.id)
        color   = GOLD if is_prem else 0x2B2D31

        def _fmt_ids(key):
            ids = cfg.get(key, [])
            if not ids:
                return "None"
            return " ".join(f"<#{i}>" for i in ids[:5])

        embed = discord.Embed(
            title="Aesthetic Settings",
            color=color
        )
        embed.add_field(name="Premium",            value="Yes" if is_prem else "No",           inline=True)
        embed.add_field(name="Accent Color",       value=f"`#{cfg.get('accent_color', '2B2D31'):06X}`", inline=True)
        embed.add_field(name="Auto-role on Join",  value=f"<@&{cfg['auto_role_id']}>" if cfg.get("auto_role_id") else "Off", inline=True)
        embed.add_field(name="DM Welcome Card",    value="On" if cfg.get("dm_welcome") else "Off",    inline=True)
        embed.add_field(name="Media Channels",     value=_fmt_ids("media_channels"),                  inline=True)
        embed.add_field(name="Embed-Only Channels",value=_fmt_ids("embed_only_channels"),              inline=True)
        embed.add_field(name="Auto-Thread",        value=_fmt_ids("auto_thread_channels"),             inline=True)
        embed.add_field(name="Auto-Pin",           value=_fmt_ids("auto_pin_channels"),                inline=True)
        embed.add_field(name="Milestone Channel",  value=f"<#{cfg['milestone_channel_id']}>" if cfg.get("milestone_channel_id") else "Default", inline=True)

        embed.add_field(
            name="Setup Commands",
            value=(
                "`,aesthetic color #hex` — set accent color\n"
                "`,aesthetic autorole @role` — auto-assign role on join\n"
                "`,aesthetic dmwelcome on/off` — toggle DM welcome card\n"
                "`,aesthetic dmtext <text>` — customize DM welcome text\n"
                "`,aesthetic media #channel` — add media auto-react channel\n"
                "`,aesthetic embedonly #channel` — embed-only channel (Premium)\n"
                "`,aesthetic autothread #channel` — auto-thread posts (Premium)\n"
                "`,aesthetic autopin #channel` — auto-pin messages (Premium)\n"
                "`,aesthetic milestone #channel` — milestone announce channel (Premium)"
            ),
            inline=False
        )
        embed.set_footer(text="Happy Premium · Aesthetic System")
        await ctx.reply(embed=embed)

    @aesthetic.command(name="color")
    @ctx_admin()
    async def aesthetic_color(self, ctx, hex_code: str = None):
        """Set the server accent color used in bot embeds."""
        if not hex_code:
            return await ctx.reply("Usage: `,aesthetic color #FF5500`")
        try:
            color_int = int(hex_code.replace("#", ""), 16)
        except:
            return await ctx.reply("Invalid hex. Use format `#FF5500`.")
        await aesthetic_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"accent_color": color_int}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(
            description=f"Accent color set to `{hex_code}`.",
            color=color_int
        ))

    @aesthetic.command(name="autorole")
    @ctx_admin()
    async def aesthetic_autorole(self, ctx, role: discord.Role = None):
        """Set a role to auto-assign when members join."""
        if not role:
            await aesthetic_col.update_one(
                {"guild_id": str(ctx.guild.id)},
                {"$unset": {"auto_role_id": ""}},
                upsert=True
            )
            return await ctx.reply("Auto-role removed.")
        await aesthetic_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"auto_role_id": str(role.id)}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(
            description=f"Auto-role set to {role.mention}. New members will receive it on join.",
            color=GOLD if await is_premium_server(ctx.guild.id) else 0x2B2D31
        ))

    @aesthetic.command(name="dmwelcome")
    @ctx_admin()
    async def aesthetic_dmwelcome(self, ctx, status: str = None):
        """Toggle DM welcome card when members join."""
        if not status or status.lower() not in ("on", "off"):
            return await ctx.reply("Usage: `,aesthetic dmwelcome on/off`")
        state = status.lower() == "on"
        await aesthetic_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"dm_welcome": state}},
            upsert=True
        )
        await ctx.reply(f"DM welcome card {'enabled' if state else 'disabled'}.")

    @aesthetic.command(name="dmtext")
    @ctx_admin()
    async def aesthetic_dmtext(self, ctx, *, text: str = None):
        """Set custom text for the DM welcome card."""
        if not text:
            return await ctx.reply("Usage: `,aesthetic dmtext Welcome to our server! Check #rules.`")
        if len(text) > 500:
            return await ctx.reply("Text must be 500 characters or fewer.")
        await aesthetic_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"dm_welcome_text": text}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(
            description=f"DM welcome text updated.\nPreview: *{text[:100]}*",
            color=0x2B2D31
        ))

    @aesthetic.command(name="media")
    @ctx_admin()
    async def aesthetic_media(self, ctx, channel: discord.TextChannel = None):
        """Toggle auto-react (❤️ 🔥) on images posted in a channel."""
        if not channel:
            return await ctx.reply("Usage: `,aesthetic media #channel`")
        cfg = await aesthetic_col.find_one({"guild_id": str(ctx.guild.id)}) or {}
        ids = cfg.get("media_channels", [])
        cid = str(channel.id)
        if cid in ids:
            ids.remove(cid)
            msg = f"Auto-react removed from {channel.mention}."
        else:
            ids.append(cid)
            msg = f"Auto-react enabled in {channel.mention}. Images will get ❤️ 🔥."
        await aesthetic_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"media_channels": ids}},
            upsert=True
        )
        await ctx.reply(msg)

    @aesthetic.command(name="embedonly")
    @ctx_admin()
    async def aesthetic_embedonly(self, ctx, channel: discord.TextChannel = None):
        """Toggle embed-only mode in a channel. Plain text gets auto-deleted. (Premium)"""
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(
                description="Embed-only channels are a **Happy Premium** feature.",
                color=SILVER
            ))
        if not channel:
            return await ctx.reply("Usage: `,aesthetic embedonly #channel`")
        cfg = await aesthetic_col.find_one({"guild_id": str(ctx.guild.id)}) or {}
        ids = cfg.get("embed_only_channels", [])
        cid = str(channel.id)
        if cid in ids:
            ids.remove(cid)
            msg = f"Embed-only mode removed from {channel.mention}."
        else:
            ids.append(cid)
            msg = f"{channel.mention} is now embed-only. Plain text messages will be deleted."
        await aesthetic_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"embed_only_channels": ids}},
            upsert=True
        )
        await ctx.reply(msg)

    @aesthetic.command(name="autothread")
    @ctx_admin()
    async def aesthetic_autothread(self, ctx, channel: discord.TextChannel = None):
        """Auto-create a discussion thread on every message in a channel. (Premium)"""
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(
                description="Auto-thread is a **Happy Premium** feature.",
                color=SILVER
            ))
        if not channel:
            return await ctx.reply("Usage: `,aesthetic autothread #channel`")
        cfg = await aesthetic_col.find_one({"guild_id": str(ctx.guild.id)}) or {}
        ids = cfg.get("auto_thread_channels", [])
        cid = str(channel.id)
        if cid in ids:
            ids.remove(cid)
            msg = f"Auto-thread removed from {channel.mention}."
        else:
            ids.append(cid)
            msg = f"Auto-thread enabled in {channel.mention}. Every post will get a discussion thread."
        await aesthetic_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"auto_thread_channels": ids}},
            upsert=True
        )
        await ctx.reply(msg)

    @aesthetic.command(name="autopin")
    @ctx_admin()
    async def aesthetic_autopin(self, ctx, channel: discord.TextChannel = None):
        """Auto-pin every message sent in a channel. (Premium)"""
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(
                description="Auto-pin is a **Happy Premium** feature.",
                color=SILVER
            ))
        if not channel:
            return await ctx.reply("Usage: `,aesthetic autopin #channel`")
        cfg = await aesthetic_col.find_one({"guild_id": str(ctx.guild.id)}) or {}
        ids = cfg.get("auto_pin_channels", [])
        cid = str(channel.id)
        if cid in ids:
            ids.remove(cid)
            msg = f"Auto-pin removed from {channel.mention}."
        else:
            ids.append(cid)
            msg = f"Auto-pin enabled in {channel.mention}."
        await aesthetic_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"auto_pin_channels": ids}},
            upsert=True
        )
        await ctx.reply(msg)

    @aesthetic.command(name="milestone")
    @ctx_admin()
    async def aesthetic_milestone(self, ctx, channel: discord.TextChannel = None):
        """Set the channel for milestone announcements. (Premium)"""
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(
                description="Milestone announcements are a **Happy Premium** feature.",
                color=SILVER
            ))
        if not channel:
            return await ctx.reply("Usage: `,aesthetic milestone #channel`")
        await aesthetic_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"milestone_channel_id": str(channel.id)}},
            upsert=True
        )
        milestones_str = " · ".join(f"{m:,}" for m in MILESTONES[:8]) + " ..."
        await ctx.reply(embed=discord.Embed(
            description=(
                f"Milestone announcements will go to {channel.mention}.\n"
                f"Milestones: {milestones_str}"
            ),
            color=GOLD
        ))

    # ══════════════════════════════════════════════════════════════════════════
    #  PREMIUM — SERVER POLISH COMMAND (one-click aesthetic setup)
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(aliases=["polish", "glow"])
    @ctx_admin()
    async def serverpolish(self, ctx):
        """
        One-click premium aesthetic setup.
        Applies recommended settings for a polished server experience.
        Premium servers get extra features activated automatically.
        """
        is_prem = await is_premium_server(ctx.guild.id)
        color   = GOLD if is_prem else 0x2B2D31
        msg     = await ctx.reply(embed=discord.Embed(
            description="Applying aesthetic settings...",
            color=color
        ))
        applied = []

        # ── DM welcome on by default ───────────────────────────────────────
        await aesthetic_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"dm_welcome": True}},
            upsert=True
        )
        applied.append("DM welcome card enabled")

        # ── Auto-react in any channel named 'media', 'art', 'showcase' ────
        media_chs = [
            str(ch.id) for ch in ctx.guild.text_channels
            if any(kw in ch.name for kw in ["media", "art", "showcase", "gallery", "fan-art"])
        ]
        if media_chs:
            await aesthetic_col.update_one(
                {"guild_id": str(ctx.guild.id)},
                {"$set": {"media_channels": media_chs}},
                upsert=True
            )
            applied.append(f"Auto-react in {len(media_chs)} media channel(s)")

        if is_prem:
            # ── Color roles setup ──────────────────────────────────────────
            existing_doc = await color_roles_col.find_one({"guild_id": str(ctx.guild.id)})
            if not existing_doc:
                created_roles = []
                for name, hex_color in DEFAULT_COLORS[:6]:  # top 6 only
                    role = discord.utils.get(ctx.guild.roles, name=f"✦ {name}")
                    if not role:
                        role = await ctx.guild.create_role(
                            name=f"✦ {name}",
                            color=discord.Color(hex_color),
                            reason="Happy server polish"
                        )
                    created_roles.append({"name": name, "role_id": str(role.id), "color": hex_color})
                    await asyncio.sleep(0.4)
                await color_roles_col.update_one(
                    {"guild_id": str(ctx.guild.id)},
                    {"$set": {"roles": created_roles}},
                    upsert=True
                )
                applied.append(f"Color roles created ({len(created_roles)} colors)")

            # ── Auto-thread in announcement channels ───────────────────────
            ann_chs = [
                str(ch.id) for ch in ctx.guild.text_channels
                if any(kw in ch.name for kw in ["announcement", "news", "update"])
            ]
            if ann_chs:
                await aesthetic_col.update_one(
                    {"guild_id": str(ctx.guild.id)},
                    {"$set": {"auto_thread_channels": ann_chs}},
                    upsert=True
                )
                applied.append(f"Auto-thread in {len(ann_chs)} announcement channel(s)")

            # ── Milestone channel (use welcome channel if set) ─────────────
            gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
            if gs.get("welcome_channel"):
                await aesthetic_col.update_one(
                    {"guild_id": str(ctx.guild.id)},
                    {"$set": {"milestone_channel_id": str(gs["welcome_channel"])}},
                    upsert=True
                )
                applied.append("Milestone announcements configured")

        # ── Final result embed ─────────────────────────────────────────────
        result = discord.Embed(
            title="Server Polish Complete" + (" ✦" if is_prem else ""),
            color=color,
        )
        result.add_field(
            name=f"Applied ({len(applied)})",
            value="\n".join(f"+ {a}" for a in applied) or "Nothing to apply.",
            inline=False
        )
        if is_prem:
            result.add_field(
                name="Premium Features Active",
                value=(
                    "Color roles — `,color pick <name>`\n"
                    "Auto-thread on announcements\n"
                    "Milestone announcements\n"
                    "DM welcome cards"
                ),
                inline=False
            )
        else:
            result.add_field(
                name="Upgrade to Premium",
                value=(
                    "Premium servers get color roles, auto-thread,\n"
                    "milestone cards, embed-only channels and more.\n"
                    "Contact the bot owner to activate."
                ),
                inline=False
            )
        result.set_footer(text="Happy · Aesthetic System")
        await msg.edit(embed=result)


async def setup(bot):
    await bot.add_cog(Aesthetic(bot))