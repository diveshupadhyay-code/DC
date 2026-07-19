import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from datetime import datetime, timezone
from utils.db import db, levels_col, settings_col
from utils.helpers import BOT_OWNER_ID, ctx_admin, is_premium_server

lr_config_col = db["lr_config"]
lr_extra_col = db["lr_extra"]

EXTRA_ROLES: dict[str, dict] = {
    "Gif": {
        "description": "Can post GIFs (embed links + attach files)",
        "perms": {"embed_links": True, "attach_files": True},
    },
    "React": {
        "description": "Can add reactions",
        "perms": {"add_reactions": True},
    },
    "Media": {
        "description": "Can attach files and embed links",
        "perms": {"attach_files": True, "embed_links": True},
    },
    "Ext": {
        "description": "Can use external emojis and stickers",
        "perms": {"use_external_emojis": True, "use_external_stickers": True},
    },
    "Speak": {
        "description": "Can speak in voice channels",
        "perms": {"speak": True},
    },
    "Stream": {
        "description": "Can go live / stream in voice channels",
        "perms": {"stream": True},
    },
    "Thread": {
        "description": "Can create and participate in threads",
        "perms": {"create_public_threads": True, "send_messages_in_threads": True},
    },
}

LEVEL_PERM_TIERS: dict[int, dict[str, bool]] = {
    1:  {"read_messages": True, "read_message_history": True, "send_messages": True},
    10: {"add_reactions": True, "embed_links": True},
    20: {"attach_files": True},
    30: {"use_external_emojis": True},
    40: {"use_external_stickers": True, "create_public_threads": True},
    50: {"send_messages_in_threads": True, "stream": True},
    60: {"speak": True},
    70: {"connect": True, "use_voice_activation": True},
    80: {"manage_messages": True},
    90: {"move_members": True},
    100: {"administrator": False},
}

def _level_color(level: int) -> int:
    t = (level - 1) / 99
    r = int(0x36 + (0xF0 - 0x36) * t)
    g = int(0x39 + (0xC0 - 0x39) * t)
    b = int(0x3F + (0x40 - 0x3F) * t)
    return (r << 16) | (g << 8) | b

def _cumulative_perms(level: int) -> discord.Permissions:
    p = discord.Permissions.none()
    for threshold in sorted(LEVEL_PERM_TIERS):
        if level >= threshold:
            for field, val in LEVEL_PERM_TIERS[threshold].items():
                if val:
                    setattr(p, field, True)
    return p

def _cumulative_overwrite(level: int) -> discord.PermissionOverwrite:
    ow = discord.PermissionOverwrite()
    for threshold in sorted(LEVEL_PERM_TIERS):
        if level >= threshold:
            for field, val in LEVEL_PERM_TIERS[threshold].items():
                setattr(ow, field, val)
    return ow


class LevelRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _cfg(self, guild_id: int) -> dict:
        return await lr_config_col.find_one({"guild_id": str(guild_id)}) or {}

    def _get_level_role(self, guild: discord.Guild, cfg: dict, level: int) -> discord.Role | None:
        rid = cfg.get("level_role_ids", {}).get(str(level))
        return guild.get_role(int(rid)) if rid else None

    def _get_extra_role(self, guild: discord.Guild, cfg: dict, name: str) -> discord.Role | None:
        rid = cfg.get("extra_role_ids", {}).get(name)
        return guild.get_role(int(rid)) if rid else None

    async def _managed_channels(self, guild: discord.Guild, cfg: dict) -> list:
        ids = cfg.get("managed_ch_ids", [])
        return [guild.get_channel(int(i)) for i in ids if guild.get_channel(int(i))]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if not await is_premium_server(message.guild.id):
            return

        await asyncio.sleep(0.15)

        doc = await levels_col.find_one({
            "guild_id": str(message.guild.id),
            "user_id":  str(message.author.id)
        })
        if not doc:
            return

        current_level = doc.get("level", 0)
        if current_level < 1:
            return

        cfg = await self._cfg(message.guild.id)
        if not cfg.get("level_role_ids"):
            return

        await self._apply_member(message.guild, message.author, current_level, cfg)

    async def _apply_member(self, guild: discord.Guild, member: discord.Member, level: int, cfg: dict):
        role_ids = cfg.get("level_role_ids", {})
        managed  = await self._managed_channels(guild, cfg)

        target_role_id  = role_ids.get(str(level))
        target_role     = guild.get_role(int(target_role_id)) if target_role_id else None

        all_level_roles = []
        for lvl_str, rid in role_ids.items():
            r = guild.get_role(int(rid))
            if r:
                all_level_roles.append(r)

        to_remove = [r for r in all_level_roles if r in member.roles and r != target_role]
        if to_remove:
            try:
                await member.remove_roles(*to_remove, reason="LevelRoles: level update")
            except discord.Forbidden:
                pass

        if target_role and target_role not in member.roles:
            try:
                await member.add_roles(target_role, reason=f"LevelRoles: reached level {level}")
            except discord.Forbidden:
                pass

        if managed:
            ow = _cumulative_overwrite(level)
            for ch in managed:
                try:
                    await ch.set_permissions(member, overwrite=ow, reason=f"LevelRoles: level {level}")
                    await asyncio.sleep(0.25)
                except (discord.Forbidden, Exception):
                    pass

    @commands.group(name="levelroles", aliases=["lvlroles", "lr"], invoke_without_command=True)
    @ctx_admin()
    async def levelroles(self, ctx):
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(
                description="✨ **Premium Feature**\n\nThis setup needs **Happy Premium**.",
                color=0x2B2D31
            ))

        cfg = await self._cfg(ctx.guild.id)
        managed = await self._managed_channels(ctx.guild, cfg)
        role_ids = cfg.get("level_role_ids", {})
        extra_ids = cfg.get("extra_role_ids", {})

        embed = discord.Embed(
            title="⚡ Level Roles Main Menu",
            color=0x2B2D31,
            timestamp=datetime.now(timezone.utc)
        )

        total_roles = sum(1 for rid in role_ids.values() if ctx.guild.get_role(int(rid)))
        status_val = f"🟢 **{total_roles}/100** Roles Active" if total_roles else "🔴 **Not Ready**\nType `,levelroles setup` to start."
        embed.add_field(name="Current Status", value=status_val, inline=False)

        extra_lines = []
        for name in EXTRA_ROLES:
            rid = extra_ids.get(name)
            role = ctx.guild.get_role(int(rid)) if rid else None
            extra_lines.append(f"🔹 {role.mention if role else f'`{name}` (Missing)'}")
        embed.add_field(name="Special VIP Roles", value="\n".join(extra_lines), inline=True)

        ch_val = " ".join(ch.mention for ch in managed[:15]) if managed else "None added yet.\nType `,levelroles managechannel #ch`"
        embed.add_field(name="Locked Channels", value=ch_val, inline=True)

        cmd_list = (
            "⚙️ `,lr setup` • Create all roles automatically\n"
            "📺 `,lr managechannel #ch` • Lock or unlock a channel\n"
            "🔄 `,lr sync` • Update permissions for everyone\n"
            "📊 `,lr info <level>` • Check perks for a level\n"
            "➕ `,lr grant @user @role` • Give someone a VIP role\n"
            "➖ `,lr revoke @user @role` • Take back a VIP role\n"
            "🔍 `,lr grants [@user]` • See someone's VIP roles\n"
            "❌ `,lr teardown` • Delete all system roles"
        )
        embed.add_field(name="Available Commands", value=cmd_list, inline=False)
        embed.set_footer(text=f"Premium System • {ctx.guild.name}")
        await ctx.reply(embed=embed)

    @levelroles.command(name="setup")
    @ctx_admin()
    async def lr_setup(self, ctx):
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(
                description="✨ This setup needs **Happy Premium**.",
                color=0x2B2D31
            ))

        if not ctx.guild.me.guild_permissions.manage_roles:
            return await ctx.reply("❌ I am missing the `Manage Roles` permission.")

        msg = await ctx.reply(embed=discord.Embed(
            title="🚀 Setup Started",
            description="Creating 100 level roles and special VIP roles.\nThis will take 2-3 minutes. Please wait...",
            color=0x2B2D31
        ))

        cfg = await self._cfg(ctx.guild.id)
        existing_level_ids = cfg.get("level_role_ids", {})
        existing_extra_ids = cfg.get("extra_role_ids", {})

        level_role_ids: dict[str, str] = dict(existing_level_ids)

        for level in range(1, 101):
            existing_rid = existing_level_ids.get(str(level))
            if existing_rid and ctx.guild.get_role(int(existing_rid)):
                continue

            role_name = f"Lvl {level}"
            perms = _cumulative_perms(level)
            color = discord.Color(_level_color(level))

            existing_by_name = discord.utils.get(ctx.guild.roles, name=role_name)
            if existing_by_name:
                level_role_ids[str(level)] = str(existing_by_name.id)
            else:
                try:
                    new_role = await ctx.guild.create_role(
                        name=role_name,
                        permissions=perms,
                        color=color,
                        reason="LevelRoles Setup"
                    )
                    level_role_ids[str(level)] = str(new_role.id)
                except discord.Forbidden:
                    await msg.edit(embed=discord.Embed(description="❌ Cannot create roles. Permission denied.", color=0x2B2D31))
                    return
                except discord.HTTPException:
                    pass

            await asyncio.sleep(0.35)

            if level % 20 == 0:
                await msg.edit(embed=discord.Embed(
                    title="🚀 Creating Roles",
                    description=f"Working on it...\nProgress: **{level}/100** roles finished.",
                    color=0x2B2D31
                ))

        extra_role_ids: dict[str, str] = dict(existing_extra_ids)

        for extra_name, extra_data in EXTRA_ROLES.items():
            existing_rid = existing_extra_ids.get(extra_name)
            if existing_rid and ctx.guild.get_role(int(existing_rid)):
                continue

            existing_by_name = discord.utils.get(ctx.guild.roles, name=extra_name)
            if existing_by_name:
                extra_role_ids[extra_name] = str(existing_by_name.id)
            else:
                try:
                    new_role = await ctx.guild.create_role(
                        name=extra_name,
                        permissions=discord.Permissions(**extra_data["perms"]),
                        color=discord.Color(0x2B2D31),
                        reason=f"LevelRoles VIP Role: {extra_name}"
                    )
                    extra_role_ids[extra_name] = str(new_role.id)
                except (discord.Forbidden, discord.HTTPException):
                    pass

            await asyncio.sleep(0.4)

        await lr_config_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {
                "guild_id":       str(ctx.guild.id),
                "level_role_ids": level_role_ids,
                "extra_role_ids": extra_role_ids,
                "managed_ch_ids": cfg.get("managed_ch_ids", []),
            }},
            upsert=True
        )

        res_embed = discord.Embed(
            title="✅ Setup Finished",
            description="All roles and permissions have been created successfully.",
            color=0x2B2D31
        )
        res_embed.add_field(name="What was created", value=f"• **100** Level Roles\n• **{len(EXTRA_ROLES)}** Special VIP Roles", inline=False)
        res_embed.add_field(name="Next Steps", value="1. Pick which channels to manage using `,lr managechannel #channel`.\n2. Type `,lr sync` to update existing server members.", inline=False)
        await msg.edit(res_embed)

    @levelroles.command(name="managechannel", aliases=["mc"])
    @ctx_admin()
    async def lr_managechannel(self, ctx, channel: discord.abc.GuildChannel = None):
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply("✨ This feature needs **Happy Premium**.")

        cfg = await self._cfg(ctx.guild.id)

        if not channel:
            managed = await self._managed_channels(ctx.guild, cfg)
            if not managed:
                return await ctx.reply("❌ No channels setup yet.\nUsage: `,levelroles managechannel #channel`")
            
            embed = discord.Embed(
                title="⚙️ Current Managed Channels",
                description="\n".join(f"• {ch.mention}" for ch in managed),
                color=0x2B2D31
            )
            return await ctx.reply(embed=embed)

        ids = list(cfg.get("managed_ch_ids", []))
        cid = str(channel.id)

        if cid in ids:
            ids.remove(cid)
            status = "removed from the list"
        else:
            ids.append(cid)
            status = "added to the list"

        await lr_config_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"managed_ch_ids": ids}},
            upsert=True
        )
        
        await ctx.reply(embed=discord.Embed(
            description=f"✅ **{channel.name}** has been {status}.\nType `,lr sync` to update the channel settings now.",
            color=0x2B2D31
        ))

    @levelroles.command(name="info")
    async def lr_info(self, ctx, level: int = None):
        if level is None or not 1 <= level <= 100:
            return await ctx.reply("❌ Please pick a number from 1 to 100.\nUsage: `,levelroles info <1-100>`")

        cfg = await self._cfg(ctx.guild.id)
        role = self._get_level_role(ctx.guild, cfg, level)

        active_tiers = {t: p for t, p in LEVEL_PERM_TIERS.items() if level >= t}
        all_perms: dict[str, bool] = {}
        for tier_perms in active_tiers.values():
            all_perms.update(tier_perms)

        granted = [k for k, v in all_perms.items() if v]
        
        count = await levels_col.count_documents({
            "guild_id": str(ctx.guild.id),
            "level": level
        })

        embed = discord.Embed(
            title=f"📊 Level {level} Details",
            color=discord.Color(_level_color(level))
        )
        embed.add_field(name="Role Perks", value=role.mention if role else "Not Created Yet", inline=True)
        embed.add_field(name="Members at this level", value=f"**{count}** users", inline=True)
        embed.add_field(name="Unlocked Permissions", value=", ".join(f"`{p}`" for p in granted) if granted else "`None`", inline=False)
        
        await ctx.reply(embed=embed)

    @levelroles.command(name="sync")
    @ctx_admin()
    async def lr_sync(self, ctx):
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply("✨ This feature needs **Happy Premium**.")

        cfg = await self._cfg(ctx.guild.id)
        if not cfg.get("level_role_ids"):
            return await ctx.reply("❌ Setup is not done yet. Please type `,levelroles setup` first.")

        msg = await ctx.reply(embed=discord.Embed(
            description="🔄 Updating roles and permissions for everyone in the server...",
            color=0x2B2D31
        ))

        cursor = levels_col.find({"guild_id": str(ctx.guild.id)})
        xp_docs = await cursor.to_list(None)

        done = 0
        for i, doc in enumerate(xp_docs):
            member = ctx.guild.get_member(int(doc["user_id"]))
            if not member:
                continue
            level = doc.get("level", 0)
            if level < 1:
                continue
            
            try:
                await self._apply_member(ctx.guild, member, level, cfg)
                done += 1
            except Exception:
                pass

            if (i + 1) % 20 == 0:
                await msg.edit(embed=discord.Embed(
                    title="🔄 Syncing Members",
                    description=f"Checking profiles...\nProgress: Finished **{i+1}/{len(xp_docs)}** members.",
                    color=0x2B2D31
                ))

        await msg.edit(embed=discord.Embed(
            title="✅ Sync Finished",
            description=f"Successfully updated **{done}** members.",
            color=0x2B2D31
        ))

    @levelroles.command(name="grant")
    @ctx_admin()
    async def lr_grant(self, ctx, member: discord.Member = None, role: discord.Role = None):
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply("✨ This feature needs **Happy Premium**.")

        if not member or not role:
            return await ctx.reply("❌ Missing details.\nUsage: `,levelroles grant @user @role`")

        if ctx.guild.me.top_role <= role:
            return await ctx.reply("❌ I cannot give this role because it is higher than my own role.")

        if ctx.author.id != BOT_OWNER_ID and ctx.author.top_role <= role:
            return await ctx.reply("❌ You cannot give this role because it is higher than your own role.")

        if role not in member.roles:
            await member.add_roles(role, reason="LevelRoles Manual Grant")

        await lr_extra_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(member.id)},
            {"$addToSet": {"role_ids": str(role.id)}},
            upsert=True
        )

        await ctx.reply(embed=discord.Embed(
            description=f"✅ Gave the special role {role.mention} to {member.mention}.",
            color=0x2B2D31
        ))

    @levelroles.command(name="revoke")
    @ctx_admin()
    async def lr_revoke(self, ctx, member: discord.Member = None, role: discord.Role = None):
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply("✨ This feature needs **Happy Premium**.")

        if not member or not role:
            return await ctx.reply("❌ Missing details.\nUsage: `,levelroles revoke @user @role`")

        if role in member.roles:
            await member.remove_roles(role, reason="LevelRoles Manual Revoke")

        await lr_extra_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(member.id)},
            {"$pull": {"role_ids": str(role.id)}}
        )

        await ctx.reply(embed=discord.Embed(
            description=f"✅ Removed the special role {role.mention} from {member.mention}.",
            color=0x2B2D31
        ))

    @levelroles.command(name="grants")
    async def lr_grants(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        doc = await lr_extra_col.find_one({
            "guild_id": str(ctx.guild.id),
            "user_id":  str(member.id)
        })
        
        if not doc or not doc.get("role_ids"):
            return await ctx.reply(embed=discord.Embed(
                description=f"🔍 {member.mention} has no active special roles.",
                color=0x2B2D31
            ))

        lines = []
        for rid in doc["role_ids"]:
            role = ctx.guild.get_role(int(rid))
            lines.append(role.mention if role else f"`Deleted Role ({rid})`")

        embed = discord.Embed(
            title=f"🛡️ Special Roles for {member.display_name}",
            description="\n".join(f"• {l}" for l in lines),
            color=0x2B2D31
        )
        await ctx.reply(embed=embed)

    @levelroles.command(name="teardown")
    @ctx_admin()
    async def lr_teardown(self, ctx):
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply("✨ This feature needs **Happy Premium**.")

        await ctx.reply(embed=discord.Embed(
            title="⚠️ Warning: Delete Everything?",
            description="This will permanently delete all 100 level roles and special VIP roles from the server.\nType `confirm teardown` within 30 seconds to proceed.",
            color=0x2B2D31
        ))

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            reply = await self.bot.wait_for("message", check=check, timeout=30)
        except asyncio.TimeoutError:
            return await ctx.send("❌ Cancelled. You took too long to reply.")

        if reply.content.strip().lower() != "confirm teardown":
            return await ctx.send("❌ Cancelled. Confirmation phrase was incorrect.")

        cfg = await self._cfg(ctx.guild.id)
        msg = await ctx.send(embed=discord.Embed(description="⚙️ Deleting roles from the server...", color=0x2B2D31))

        deleted = 0
        for rid in cfg.get("level_role_ids", {}).values():
            role = ctx.guild.get_role(int(rid))
            if role:
                try:
                    await role.delete(reason="LevelRoles Teardown")
                    deleted += 1
                except Exception:
                    pass
                await asyncio.sleep(0.35)

        for rid in cfg.get("extra_role_ids", {}).values():
            role = ctx.guild.get_role(int(rid))
            if role:
                try:
                    await role.delete(reason="LevelRoles Teardown")
                    deleted += 1
                except Exception:
                    pass
                await asyncio.sleep(0.35)

        await lr_config_col.delete_one({"guild_id": str(ctx.guild.id)})
        await lr_extra_col.delete_many({"guild_id": str(ctx.guild.id)})

        await msg.edit(embed=discord.Embed(
            title="💥 System Cleaned",
            description=f"Wiped all saved data and deleted **{deleted}** roles from the server.",
            color=0x2B2D31
        ))

    async def on_level_up(self, guild: discord.Guild, member: discord.Member, new_level: int):
        if not await is_premium_server(guild.id):
            return
        cfg = await self._cfg(guild.id)
        if not cfg.get("level_role_ids"):
            return
        await self._apply_member(guild, member, new_level, cfg)

    @app_commands.command(name="levelroles", description="View the level role system dashboard")
    async def slash_levelroles(self, interaction: discord.Interaction):
        if not await is_premium_server(interaction.guild.id):
            return await interaction.response.send_message("✨ This feature needs **Happy Premium**.", ephemeral=True)
        
        cfg = await self._cfg(interaction.guild.id)
        role_ids = cfg.get("level_role_ids", {})
        total = sum(1 for rid in role_ids.values() if interaction.guild.get_role(int(rid)))
        
        embed = discord.Embed(title="⚡ Level Roles Status", color=0x2B2D31)
        embed.add_field(name="Roles Setup", value=f"**{total}/100** system roles are ready.", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(LevelRoles(bot))