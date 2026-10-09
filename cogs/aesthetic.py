import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timezone

from utils.db import db, settings_col
from utils.helpers import (
    BOT_OWNER_ID, ctx_admin, is_premium_server
)

aesthetic_col = db["aesthetic_config"]
color_roles_col = db["color_roles"]
milestone_col = db["milestones"]

GOLD = 0xF0C040
SILVER = 0xC0C0C0

MILESTONES = [10, 25, 50, 100, 200, 250, 500, 750, 1000, 2000, 5000, 10000, 25000, 50000, 100000]

DEFAULT_COLORS = [
    ("Rose", 0xE8425A),
    ("Lavender", 0xA78BFA),
    ("Sky", 0x38BDF8),
    ("Mint", 0x34D399),
    ("Peach", 0xFB923C),
    ("Gold", 0xFBBF24),
    ("Crimson", 0xDC2626),
    ("Sapphire", 0x3B82F6),
    ("Emerald", 0x10B981),
    ("Violet", 0x8B5CF6),
    ("Coral", 0xF97316),
    ("Steel", 0x64748B),
]

class Aesthetic(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        if not await is_premium_server(member.guild.id):
            return

        cfg = await aesthetic_col.find_one({"guild_id": str(member.guild.id)}) or {}
        
        auto_role_id = cfg.get("auto_role_id")
        if auto_role_id:
            role = member.guild.get_role(int(auto_role_id))
            if role and member.guild.me.guild_permissions.manage_roles and member.guild.me.top_role > role:
                try:
                    await member.add_roles(role, reason="Auto role assigned")
                except discord.HTTPException:
                    pass

        await self._check_milestone(member.guild)

    async def _check_milestone(self, guild: discord.Guild):
        count = guild.member_count
        if count not in MILESTONES:
            return

        doc = await milestone_col.find_one({"guild_id": str(guild.id)}) or {}
        if count in doc.get("announced", []):
            return

        await milestone_col.update_one(
            {"guild_id": str(guild.id)},
            {"$push": {"announced": count}},
            upsert=True
        )

        gs = await settings_col.find_one({"_id": str(guild.id)}) or {}
        cfg = await aesthetic_col.find_one({"guild_id": str(guild.id)}) or {}
        
        cid = cfg.get("milestone_channel_id") or gs.get("welcome_channel")
        ch = self.bot.get_channel(int(cid)) if cid and cid.isdigit() else guild.system_channel
        
        if not ch or not ch.permissions_for(guild.me).send_messages:
            return

        embed = discord.Embed(
            title="Milestone Reached",
            description=f"**{guild.name}** reached **{count:,} members**.",
            color=GOLD
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.set_footer(text="Community Milestone")
        await ch.send(embed=embed)

    @commands.group(invoke_without_command=True)
    async def color(self, ctx):
        if not ctx.guild:
            return

        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(
                description="Color roles are a premium feature.",
                color=SILVER
            ))

        doc = await color_roles_col.find_one({"guild_id": str(ctx.guild.id)})
        if not doc or not doc.get("roles"):
            return await ctx.reply(embed=discord.Embed(
                description="Color roles are not configured yet.",
                color=GOLD
            ))

        roles = doc["roles"]
        lines = []
        for r in roles:
            role = ctx.guild.get_role(int(r["role_id"]))
            if role:
                indicator = "✦" if role in ctx.author.roles else "•"
                lines.append(f"{indicator} `{r['name']}`")

        embed = discord.Embed(
            title="Color Palette",
            description="\n".join(lines) if lines else "No roles available.",
            color=GOLD
        )
        embed.add_field(
            name="Commands",
            value="`,color pick <name>`\n`,color remove`",
            inline=False
        )
        await ctx.reply(embed=embed)

    @color.command(name="setup")
    @ctx_admin()
    async def color_setup(self, ctx):
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(
                description="Color roles are a premium feature.",
                color=SILVER
            ))

        if not ctx.guild.me.guild_permissions.manage_roles:
            return await ctx.reply(embed=discord.Embed(
                description="Missing permission to manage roles.",
                color=SILVER
            ))

        msg = await ctx.reply(embed=discord.Embed(
            description="Setting up color roles...",
            color=GOLD
        ))
        created = []

        for name, hex_color in DEFAULT_COLORS:
            role = discord.utils.get(ctx.guild.roles, name=f"✦ {name}")
            if not role:
                try:
                    role = await ctx.guild.create_role(
                        name=f"✦ {name}",
                        color=discord.Color(hex_color),
                        reason="Color setup"
                    )
                except discord.HTTPException:
                    continue
            created.append({"name": name, "role_id": str(role.id), "color": hex_color})
            await asyncio.sleep(0.3)

        await color_roles_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"roles": created}},
            upsert=True
        )

        embed = discord.Embed(
            title="Setup Complete",
            description=f"Created {len(created)} color roles successfully.",
            color=GOLD
        )
        await msg.edit(embed=embed)

    @color.command(name="pick")
    async def color_pick(self, ctx, *, name: str = None):
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(
                description="Color roles are a premium feature.",
                color=SILVER
            ))

        if not name:
            return await ctx.reply(embed=discord.Embed(
                description="Provide a color name.",
                color=SILVER
            ))

        doc = await color_roles_col.find_one({"guild_id": str(ctx.guild.id)})
        if not doc or not doc.get("roles"):
            return await ctx.reply(embed=discord.Embed(
                description="Color roles are not set up.",
                color=SILVER
            ))

        match = next((r for r in doc["roles"] if r["name"].lower() == name.lower()), None)
        if not match:
            return await ctx.reply(embed=discord.Embed(
                description="Color not found in the palette.",
                color=SILVER
            ))

        role = ctx.guild.get_role(int(match["role_id"]))
        if not role:
            return await ctx.reply(embed=discord.Embed(
                description="Role no longer exists on this server.",
                color=SILVER
            ))

        if not ctx.guild.me.guild_permissions.manage_roles or ctx.guild.me.top_role <= role:
            return await ctx.reply(embed=discord.Embed(
                description="Cannot assign this role due to permission hierarchy.",
                color=SILVER
            ))

        active_roles = [
            ctx.guild.get_role(int(r["role_id"]))
            for r in doc["roles"]
            if ctx.guild.get_role(int(r["role_id"])) in ctx.author.roles
        ]
        active_roles = [r for r in active_roles if r]

        if active_roles:
            try:
                await ctx.author.remove_roles(*active_roles, reason="Color change")
            except discord.HTTPException:
                pass

        try:
            await ctx.author.add_roles(role, reason=f"Selected color {match['name']}")
            embed = discord.Embed(
                description=f"Color updated to **{match['name']}**.",
                color=match["color"]
            )
            await ctx.reply(embed=embed)
        except discord.HTTPException:
            await ctx.reply(embed=discord.Embed(
                description="Failed to assign role.",
                color=SILVER
            ))

    @color.command(name="remove")
    async def color_remove(self, ctx):
        doc = await color_roles_col.find_one({"guild_id": str(ctx.guild.id)})
        if not doc or not doc.get("roles"):
            return await ctx.reply(embed=discord.Embed(
                description="No color roles configured.",
                color=SILVER
            ))

        to_remove = [
            role for r in doc["roles"]
            if (role := ctx.guild.get_role(int(r["role_id"]))) and role in ctx.author.roles
        ]

        if not to_remove:
            return await ctx.reply(embed=discord.Embed(
                description="No color role active on your account.",
                color=SILVER
            ))

        if not ctx.guild.me.guild_permissions.manage_roles:
            return await ctx.reply(embed=discord.Embed(
                description="Missing permissions to remove roles.",
                color=SILVER
            ))

        try:
            await ctx.author.remove_roles(*to_remove, reason="Removed color role")
            await ctx.reply(embed=discord.Embed(
                description="Color role removed successfully.",
                color=GOLD
            ))
        except discord.HTTPException:
            await ctx.reply(embed=discord.Embed(
                description="Failed to remove color role.",
                color=SILVER
            ))

    @color.command(name="add")
    @ctx_admin()
    async def color_add(self, ctx, name: str = None, hex_color: str = None):
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(description="Premium feature only.", color=SILVER))
        
        if not name or not hex_color:
            return await ctx.reply(embed=discord.Embed(description="Provide both name and hex code.", color=SILVER))

        clean_hex = hex_color.replace("#", "")
        if len(clean_hex) != 6 or not all(c in "0123456789abcdefABCDEF" for c in clean_hex):
            return await ctx.reply(embed=discord.Embed(description="Invalid hex code format.", color=SILVER))

        color_int = int(clean_hex, 16)

        if not ctx.guild.me.guild_permissions.manage_roles:
            return await ctx.reply(embed=discord.Embed(description="Missing permission to create roles.", color=SILVER))

        try:
            role = await ctx.guild.create_role(
                name=f"✦ {name}",
                color=discord.Color(color_int),
                reason="Custom color role"
            )
            await color_roles_col.update_one(
                {"guild_id": str(ctx.guild.id)},
                {"$push": {"roles": {"name": name, "role_id": str(role.id), "color": color_int}}},
                upsert=True
            )
            await ctx.reply(embed=discord.Embed(
                description=f"Added **{name}** to palette.",
                color=color_int
            ))
        except discord.HTTPException:
            await ctx.reply(embed=discord.Embed(description="Failed to create role.", color=SILVER))

    @commands.command(aliases=["scard", "invite"])
    async def servercard(self, ctx):
        if not ctx.guild:
            return

        g = ctx.guild
        is_prem = await is_premium_server(g.id)
        color = GOLD if is_prem else SILVER

        invite_url = None
        if g.me.guild_permissions.create_instant_invite:
            try:
                if g.vanity_url_code:
                    invite_url = f"https://discord.gg/{g.vanity_url_code}"
                else:
                    for ch in g.text_channels:
                        if ch.permissions_for(g.me).create_instant_invite:
                            inv = await ch.create_invite(max_age=0, max_uses=0, reason="Servercard")
                            invite_url = inv.url
                            break
            except discord.HTTPException:
                pass

        bots = sum(1 for m in g.members if m.bot)
        humans = g.member_count - bots
        online = sum(1 for m in g.members if m.status != discord.Status.offline and not m.bot)

        embed = discord.Embed(
            title=g.name,
            description=g.description or "Welcome to our server.",
            color=color
        )
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        if g.banner:
            embed.set_image(url=g.banner.url)

        embed.add_field(name="Members", value=f"{humans:,} humans | {bots} bots", inline=True)
        embed.add_field(name="Online", value=f"{online:,}", inline=True)
        embed.add_field(name="Boost Level", value=f"Level {g.premium_tier}", inline=True)
        embed.add_field(name="Channels", value=str(len(g.channels)), inline=True)
        embed.add_field(name="Roles", value=str(len(g.roles)), inline=True)
        embed.add_field(name="Created", value=f"<t:{int(g.created_at.timestamp())}:D>", inline=True)

        if invite_url:
            embed.add_field(name="Server Link", value=f"[Join Direct]({invite_url})", inline=False)

        await ctx.reply(embed=embed)

    @commands.Cog.listener("on_message")
    async def aesthetic_on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        cfg = await aesthetic_col.find_one({"guild_id": str(message.guild.id)}) or {}

        media_config = cfg.get("media_channel_config")
        if media_config and str(message.channel.id) == media_config.get("channel_id"):
            has_media = message.attachments or any(
                ext in message.content.lower()
                for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", "http://", "https://"]
            )
            if has_media and message.channel.permissions_for(message.guild.me).add_reactions:
                try:
                    await message.add_reaction(media_config.get("emoji", "❤️"))
                except discord.HTTPException:
                    pass

        if await is_premium_server(message.guild.id):
            embed_only = cfg.get("embed_only_channels", [])
            if str(message.channel.id) in embed_only:
                is_admin = message.author.guild_permissions.manage_messages or message.author.id == BOT_OWNER_ID
                if not is_admin and not message.embeds and not message.attachments:
                    if message.channel.permissions_for(message.guild.me).manage_messages:
                        try:
                            await message.delete()
                        except discord.HTTPException:
                            pass

            auto_pin = cfg.get("auto_pin_channels", [])
            if str(message.channel.id) in auto_pin:
                if message.channel.permissions_for(message.guild.me).manage_messages:
                    try:
                        pins = await message.channel.pins()
                        if len(pins) < 50:
                            await message.pin()
                    except discord.HTTPException:
                        pass

    @commands.group(invoke_without_command=True)
    @ctx_admin()
    async def aesthetic(self, ctx):
        if not ctx.guild:
            return

        cfg = await aesthetic_col.find_one({"guild_id": str(ctx.guild.id)}) or {}
        is_prem = await is_premium_server(ctx.guild.id)

        embed = discord.Embed(
            title="Aesthetic Dashboard",
            color=GOLD if is_prem else SILVER
        )
        embed.add_field(name="Premium Status", value="Active" if is_prem else "Inactive", inline=True)
        embed.add_field(name="Auto Role", value=f"<@&{cfg['auto_role_id']}>" if cfg.get("auto_role_id") else "None", inline=True)
        
        media_cfg = cfg.get("media_channel_config")
        media_str = f"<#{media_cfg['channel_id']}> ({media_cfg['emoji']})" if media_cfg else "None"
        embed.add_field(name="Media Channel", value=media_str, inline=True)

        embed.add_field(
            name="Configuration Commands",
            value=(
                "`,aesthetic autorole <role>`\n"
                "`,aesthetic media <channel> <emoji>`\n"
                "`,aesthetic embedonly <channel>`\n"
                "`,aesthetic autopin <channel>`\n"
                "`,aesthetic milestone <channel>`"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @aesthetic.command(name="autorole")
    @ctx_admin()
    async def aesthetic_autorole(self, ctx, role: discord.Role = None):
        if not role:
            await aesthetic_col.update_one(
                {"guild_id": str(ctx.guild.id)},
                {"$unset": {"auto_role_id": ""}},
                upsert=True
            )
            return await ctx.reply(embed=discord.Embed(description="Auto role disabled.", color=GOLD))

        if ctx.guild.me.top_role <= role:
            return await ctx.reply(embed=discord.Embed(description="Role is higher than bot's highest role.", color=SILVER))

        await aesthetic_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"auto_role_id": str(role.id)}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(
            description=f"Auto role set to {role.mention}.",
            color=GOLD
        ))

    @aesthetic.command(name="media")
    @ctx_admin()
    async def aesthetic_media(self, ctx, channel: discord.TextChannel = None, emoji: str = None):
        if not channel or not emoji:
            return await ctx.reply(embed=discord.Embed(description="Specify a text channel and emoji.", color=SILVER))

        await aesthetic_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"media_channel_config": {"channel_id": str(channel.id), "emoji": emoji}}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(
            description=f"Auto reaction configured in {channel.mention}.",
            color=GOLD
        ))

    @aesthetic.command(name="embedonly")
    @ctx_admin()
    async def aesthetic_embedonly(self, ctx, channel: discord.TextChannel = None):
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(description="Premium feature only.", color=SILVER))
        
        if not channel:
            return await ctx.reply(embed=discord.Embed(description="Specify a channel.", color=SILVER))

        cfg = await aesthetic_col.find_one({"guild_id": str(ctx.guild.id)}) or {}
        ids = cfg.get("embed_only_channels", [])
        cid = str(channel.id)

        if cid in ids:
            ids.remove(cid)
            msg = f"Embed-only mode disabled for {channel.mention}."
        else:
            ids.append(cid)
            msg = f"Embed-only mode enabled for {channel.mention}."

        await aesthetic_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"embed_only_channels": ids}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(description=msg, color=GOLD))

    @aesthetic.command(name="autopin")
    @ctx_admin()
    async def aesthetic_autopin(self, ctx, channel: discord.TextChannel = None):
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(description="Premium feature only.", color=SILVER))
        
        if not channel:
            return await ctx.reply(embed=discord.Embed(description="Specify a channel.", color=SILVER))

        cfg = await aesthetic_col.find_one({"guild_id": str(ctx.guild.id)}) or {}
        ids = cfg.get("auto_pin_channels", [])
        cid = str(channel.id)

        if cid in ids:
            ids.remove(cid)
            msg = f"Auto pin disabled for {channel.mention}."
        else:
            ids.append(cid)
            msg = f"Auto pin enabled for {channel.mention}."

        await aesthetic_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"auto_pin_channels": ids}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(description=msg, color=GOLD))

    @aesthetic.command(name="milestone")
    @ctx_admin()
    async def aesthetic_milestone(self, ctx, channel: discord.TextChannel = None):
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(description="Premium feature only.", color=SILVER))
        
        if not channel:
            return await ctx.reply(embed=discord.Embed(description="Specify a channel.", color=SILVER))

        await aesthetic_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"milestone_channel_id": str(channel.id)}},
            upsert=True
        )
        await ctx.reply(embed=discord.Embed(
            description=f"Milestone alerts set to {channel.mention}.",
            color=GOLD
        ))

    @commands.command(aliases=["polish", "glow"])
    @ctx_admin()
    async def serverpolish(self, ctx):
        if not ctx.guild:
            return

        is_prem = await is_premium_server(ctx.guild.id)
        msg = await ctx.reply(embed=discord.Embed(description="Optimizing server settings...", color=GOLD if is_prem else SILVER))

        applied = []

        media_ch = next(
            (ch for ch in ctx.guild.text_channels if any(k in ch.name for k in ["media", "art", "gallery"])),
            None
        )
        if media_ch:
            await aesthetic_col.update_one(
                {"guild_id": str(ctx.guild.id)},
                {"$set": {"media_channel_config": {"channel_id": str(media_ch.id), "emoji": "❤️"}}},
                upsert=True
            )
            applied.append(f"Configured media reactions in {media_ch.mention}")

        if is_prem:
            doc = await color_roles_col.find_one({"guild_id": str(ctx.guild.id)})
            if not doc and ctx.guild.me.guild_permissions.manage_roles:
                created = []
                for name, hex_color in DEFAULT_COLORS[:6]:
                    role = discord.utils.get(ctx.guild.roles, name=f"✦ {name}")
                    if not role:
                        try:
                            role = await ctx.guild.create_role(
                                name=f"✦ {name}",
                                color=discord.Color(hex_color),
                                reason="Server polish setup"
                            )
                        except discord.HTTPException:
                            continue
                    created.append({"name": name, "role_id": str(role.id), "color": hex_color})
                    await asyncio.sleep(0.3)
                
                if created:
                    await color_roles_col.update_one(
                        {"guild_id": str(ctx.guild.id)},
                        {"$set": {"roles": created}},
                        upsert=True
                    )
                    applied.append(f"Setup {len(created)} default color roles")

        embed = discord.Embed(
            title="Optimization Complete",
            description="\n".join(f"• {a}" for a in applied) if applied else "No changes required.",
            color=GOLD if is_prem else SILVER
        )
        await msg.edit(embed=embed)

async def setup(bot):
    await bot.add_cog(Aesthetic(bot))