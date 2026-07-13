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

aesthetic_col   = db["aesthetic_config"]
color_roles_col = db["color_roles"]
embed_only_col  = db["embed_only_channels"]
milestone_col   = db["milestones"]

GOLD   = 0xF0C040
SILVER = 0xC0C0C0

MILESTONES = [10, 25, 50, 100, 200, 250, 500, 750, 1000,
              2000, 5000, 10000, 25000, 50000, 100000]

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

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        gs  = await settings_col.find_one({"_id": str(member.guild.id)}) or {}
        cfg = await aesthetic_col.find_one({"guild_id": str(member.guild.id)}) or {}

        if cfg.get("dm_welcome", True):
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

        if await is_premium_server(member.guild.id):
            auto_role_id = cfg.get("auto_role_id")
            if auto_role_id:
                role = member.guild.get_role(int(auto_role_id))
                if role:
                    try:
                        await member.add_roles(role, reason="Auto-role on join")
                    except:
                        pass

        if await is_premium_server(member.guild.id):
            await self._check_milestone(member.guild)

    async def _check_milestone(self, guild: discord.Guild):
        count  = guild.member_count
        if count not in MILESTONES:
            return

        doc = await milestone_col.find_one({"guild_id": str(guild.id)}) or {}
        announced = doc.get("announced", [])
        if count in announced:
            return

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

    @commands.group(invoke_without_command=True)
    async def color(self, ctx):
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

    @commands.command(aliases=["scard", "invite"])
    async def servercard(self, ctx):
        g      = ctx.guild
        is_prem = await is_premium_server(g.id)
        color  = GOLD if is_prem else 0x2B2D31

        invite_url = None
        try:
            if g.vanity_url_code:
                invite_url = f"https://discord.gg/{g.vanity_url_code}"
            else:
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

    @commands.Cog.listener("on_message")
    async def aesthetic_on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        cfg = await aesthetic_col.find_one({"guild_id": str(message.guild.id)}) or {}

        media_config = cfg.get("media_channel_config")
        if media_config and str(message.channel.id) == media_config.get("channel_id"):
            if message.attachments or any(
                ext in message.content.lower()
                for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]
            ):
                emoji = media_config.get("emoji", "❤️")
                try:
                    await message.add_reaction(emoji)
                except:
                    pass

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

        if cfg.get("auto_pin_channels") and await is_premium_server(message.guild.id):
            if str(message.channel.id) in cfg["auto_pin_channels"]:
                try:
                    pins = await message.channel.pins()
                    if len(pins) < 50:
                        await message.pin()
                except:
                    pass

    @commands.group(invoke_without_command=True)
    @ctx_admin()
    async def aesthetic(self, ctx):
        cfg     = await aesthetic_col.find_one({"guild_id": str(ctx.guild.id)}) or {}
        is_prem = await is_premium_server(ctx.guild.id)
        color   = GOLD if is_prem else 0x2B2D31

        def _fmt_ids(key):
            ids = cfg.get(key, [])
            if not ids:
                return "None"
            return " ".join(f"<#{i}>" for i in ids[:5])

        media_cfg = cfg.get("media_channel_config")
        if media_cfg:
            media_val = f"<#{media_cfg['channel_id']}> ({media_cfg['emoji']})"
        else:
            media_val = "None"

        embed = discord.Embed(
            title="Aesthetic Settings",
            color=color
        )
        embed.add_field(name="Premium",            value="Yes" if is_prem else "No",           inline=True)
        embed.add_field(name="Accent Color",       value=f"`#{cfg.get('accent_color', 2829617):06X}`", inline=True)
        embed.add_field(name="Auto-role on Join",  value=f"<@&{cfg['auto_role_id']}>" if cfg.get("auto_role_id") else "Off", inline=True)
        embed.add_field(name="DM Welcome Card",    value="On" if cfg.get("dm_welcome", True) else "Off",    inline=True)
        embed.add_field(name="Media Channel",      value=media_val,                                   inline=True)
        embed.add_field(name="Embed-Only Channels",value=_fmt_ids("embed_only_channels"),              inline=True)
        embed.add_field(name="Auto-Pin",           value=_fmt_ids("auto_pin_channels"),                inline=True)
        embed.add_field(name="Milestone Channel",  value=f"<#{cfg['milestone_channel_id']}>" if cfg.get("milestone_channel_id") else "Default", inline=True)

        embed.add_field(
            name="Setup Commands",
            value=(
                "`,aesthetic color #hex` — set accent color\n"
                "`,aesthetic autorole @role` — auto-assign role on join\n"
                "`,aesthetic dmwelcome on/off` — toggle DM welcome card\n"
                "`,aesthetic dmtext <text>` — customize DM welcome text\n"
                "`,aesthetic media #channel <emoji>` — set media channel & emoji\n"
                "`,aesthetic embedonly #channel` — embed-only channel (Premium)\n"
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
    async def aesthetic_media(self, ctx, channel: discord.TextChannel = None, emoji: str = None):
        if not channel or not emoji:
            return await ctx.reply("Usage: `,aesthetic media #channel <emoji>`")
        
        await aesthetic_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"media_channel_config": {"channel_id": str(channel.id), "emoji": emoji}}},
            upsert=True
        )
        await ctx.reply(f"Auto-react configured in {channel.mention} with emoji {emoji}.")

    @aesthetic.command(name="embedonly")
    @ctx_admin()
    async def aesthetic_embedonly(self, ctx, channel: discord.TextChannel = None):
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

    @aesthetic.command(name="autopin")
    @ctx_admin()
    async def aesthetic_autopin(self, ctx, channel: discord.TextChannel = None):
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

    @commands.command(aliases=["polish", "glow"])
    @ctx_admin()
    async def serverpolish(self, ctx):
        is_prem = await is_premium_server(ctx.guild.id)
        color   = GOLD if is_prem else 0x2B2D31
        msg     = await ctx.reply(embed=discord.Embed(
            description="Applying aesthetic settings...",
            color=color
        ))
        applied = []

        await aesthetic_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"dm_welcome": True}},
            upsert=True
        )
        applied.append("DM welcome card enabled")

        first_media_ch = next(
            (ch for ch in ctx.guild.text_channels if any(kw in ch.name for kw in ["media", "art", "showcase", "gallery", "fan-art"])), 
            None
        )
        if first_media_ch:
            await aesthetic_col.update_one(
                {"guild_id": str(ctx.guild.id)},
                {"$set": {"media_channel_config": {"channel_id": str(first_media_ch.id), "emoji": "❤️"}}},
                upsert=True
            )
            applied.append(f"Auto-react in media channel: {first_media_ch.mention}")

        if is_prem:
            existing_doc = await color_roles_col.find_one({"guild_id": str(ctx.guild.id)})
            if not existing_doc:
                created_roles = []
                for name, hex_color in DEFAULT_COLORS[:6]:
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

            gs = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
            if gs.get("welcome_channel"):
                await aesthetic_col.update_one(
                    {"guild_id": str(ctx.guild.id)},
                    {"$set": {"milestone_channel_id": str(gs["welcome_channel"])}},
                    upsert=True
                )
                applied.append("Milestone announcements configured")

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
                    "Milestone announcements\n"
                    "DM welcome cards"
                ),
                inline=False
            )
        else:
            result.add_field(
                name="Upgrade to Premium",
                value=(
                    "Premium servers get color roles,\n"
                    "milestone cards, embed-only channels and more.\n"
                    "Contact the bot owner to activate."
                ),
                inline=False
            )
        result.set_footer(text="Happy · Aesthetic System")
        await msg.edit(embed=result)

async def setup(bot):
    await bot.add_cog(Aesthetic(bot))