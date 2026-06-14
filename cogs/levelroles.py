"""
cogs/levelroles.py — Level Role System (Premium Feature)

ONE command sets up everything:
  ,levelroles setup
    → Creates Lvl 1 … Lvl 100 roles (plain text, no emojis)
    → Creates extra perm roles: Gif, React, Media, Ext, Speak, Stream, Thread
    → Saves all role IDs to DB
    → Each Lvl role inherits the Discord permissions of all roles below it
      (cascade is applied at assignment time via channel overwrites)

How permissions work:
  - Every 10 levels unlocks a new base permission tier.
  - Channel overwrites are applied only in channels marked as "managed".
  - Extra perm roles grant specific channel permissions on top, for any member
    the admin trusts regardless of level.

Key commands:
  ,levelroles setup               — auto-create all 100 level roles + 7 extra perm roles
  ,levelroles                     — dashboard
  ,levelroles managechannel #ch   — mark/unmark a channel for perm management
  ,levelroles sync                — re-apply roles/perms to all current members
  ,levelroles info <level>        — detail view for one level
  ,levelroles grant @member @role — give an extra perm role to a member
  ,levelroles revoke @member @role— remove it
  ,levelroles grants [@member]    — list extra roles for a member
  ,levelroles teardown            — delete all created roles (confirmation required)

No emojis in any role names.
"""

import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from datetime import datetime, timezone

from utils.db import db, levels_col, settings_col
from utils.helpers import BOT_OWNER_ID, ctx_admin, is_premium_server

# ── Collections ────────────────────────────────────────────────────────────────
lr_config_col   = db["lr_config"]     # {guild_id, level_role_ids:{}, extra_role_ids:{}, managed_ch_ids:[]}
lr_extra_col    = db["lr_extra"]      # {guild_id, user_id, role_ids:[]}

# ── Extra perm role definitions ────────────────────────────────────────────────
# name → list of discord.Permissions field names that are set True on the role itself
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

# ── Level → permission tier (what channel perms open up at each milestone) ────
# Keys are level numbers at which new perms unlock.
# Each tier is CUMULATIVE — level 20 gets tier-10 AND tier-20 perms.
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
    100: {"administrator": False},  # 100 does NOT grant admin — reserved slot only
}

# Role colour gradient from grey → gold across 100 levels
# We compute it in the setup function.
def _level_color(level: int) -> int:
    """Interpolate from dark grey (1) to gold (100)."""
    t = (level - 1) / 99  # 0.0 … 1.0
    # grey  0x36393F  →  gold  0xF0C040
    r = int(0x36 + (0xF0 - 0x36) * t)
    g = int(0x39 + (0xC0 - 0x39) * t)
    b = int(0x3F + (0x40 - 0x3F) * t)
    return (r << 16) | (g << 8) | b


def _cumulative_perms(level: int) -> discord.Permissions:
    """Return the cumulative Discord Permissions for a given level."""
    p = discord.Permissions.none()
    for threshold in sorted(LEVEL_PERM_TIERS):
        if level >= threshold:
            tier = LEVEL_PERM_TIERS[threshold]
            for field, val in tier.items():
                if val:
                    setattr(p, field, True)
    return p


def _cumulative_overwrite(level: int) -> discord.PermissionOverwrite:
    """Build a PermissionOverwrite from the cumulative perms up to `level`."""
    ow = discord.PermissionOverwrite()
    for threshold in sorted(LEVEL_PERM_TIERS):
        if level >= threshold:
            for field, val in LEVEL_PERM_TIERS[threshold].items():
                setattr(ow, field, val)
    return ow


# ── COG ────────────────────────────────────────────────────────────────────────

class LevelRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Internal: fetch config ─────────────────────────────────────────────────
    async def _cfg(self, guild_id: int) -> dict:
        return await lr_config_col.find_one({"guild_id": str(guild_id)}) or {}

    # ── Internal: resolve level role ───────────────────────────────────────────
    def _get_level_role(self, guild: discord.Guild, cfg: dict, level: int) -> discord.Role | None:
        rid = cfg.get("level_role_ids", {}).get(str(level))
        return guild.get_role(int(rid)) if rid else None

    def _get_extra_role(self, guild: discord.Guild, cfg: dict, name: str) -> discord.Role | None:
        rid = cfg.get("extra_role_ids", {}).get(name)
        return guild.get_role(int(rid)) if rid else None

    async def _managed_channels(self, guild: discord.Guild, cfg: dict) -> list:
        ids = cfg.get("managed_ch_ids", [])
        return [guild.get_channel(int(i)) for i in ids if guild.get_channel(int(i))]

    # ── on_message: assign level role + channel perms when level changes ───────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if not await is_premium_server(message.guild.id):
            return

        # Small sleep so core.py writes XP first
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
            return  # setup not run yet

        await self._apply_member(message.guild, message.author, current_level, cfg)

    # ── Core apply: give correct level role + channel overwrites ──────────────
    async def _apply_member(
        self,
        guild: discord.Guild,
        member: discord.Member,
        level: int,
        cfg: dict
    ):
        """
        1. Remove any level roles the member shouldn't have.
        2. Add the correct level role for their current level.
        3. Apply cumulative channel permission overwrites in managed channels.
        """
        role_ids = cfg.get("level_role_ids", {})
        managed  = await self._managed_channels(guild, cfg)

        # Determine which level role to assign (exact match only — one role per member)
        target_role_id  = role_ids.get(str(level))
        target_role     = guild.get_role(int(target_role_id)) if target_role_id else None

        # Collect all level roles that exist in the guild
        all_level_roles = []
        for lvl_str, rid in role_ids.items():
            r = guild.get_role(int(rid))
            if r:
                all_level_roles.append(r)

        # Remove level roles the member has that aren't the target
        to_remove = [r for r in all_level_roles if r in member.roles and r != target_role]
        if to_remove:
            try:
                await member.remove_roles(*to_remove, reason="LevelRoles: level update")
            except discord.Forbidden:
                pass

        # Add target role if missing
        if target_role and target_role not in member.roles:
            try:
                await member.add_roles(target_role, reason=f"LevelRoles: reached level {level}")
            except discord.Forbidden:
                pass

        # Apply channel overwrites
        if managed:
            ow = _cumulative_overwrite(level)
            has_any = any(v is not None for _, v in ow)
            if has_any:
                for ch in managed:
                    try:
                        await ch.set_permissions(
                            member, overwrite=ow,
                            reason=f"LevelRoles: level {level}"
                        )
                        await asyncio.sleep(0.25)
                    except discord.Forbidden:
                        pass
                    except Exception:
                        pass

    # ══════════════════════════════════════════════════════════════════════════
    #  ,levelroles setup  — THE BIG ONE
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(name="levelroles", aliases=["lvlroles", "lr"], invoke_without_command=True)
    @ctx_admin()
    async def levelroles(self, ctx):
        """Level role system dashboard."""
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(
                description="Level Roles is a **Happy Premium** feature.",
                color=0xC0C0C0
            ))

        cfg     = await self._cfg(ctx.guild.id)
        managed = await self._managed_channels(ctx.guild, cfg)
        role_ids = cfg.get("level_role_ids", {})
        extra_ids = cfg.get("extra_role_ids", {})

        embed = discord.Embed(
            title="Level Roles — Dashboard",
            color=0xF0C040,
            timestamp=datetime.now(timezone.utc)
        )

        # Setup status
        total_roles = sum(1 for rid in role_ids.values() if ctx.guild.get_role(int(rid)))
        if total_roles:
            embed.add_field(
                name="Level Roles",
                value=(
                    f"**{total_roles}** level roles active (Lvl 1 – Lvl 100).\n"
                    "Members are auto-assigned their exact level role when they level up."
                ),
                inline=False
            )
        else:
            embed.add_field(
                name="Level Roles",
                value="Not set up yet. Run `,levelroles setup` to create all 100 level roles.",
                inline=False
            )

        # Extra perm roles
        extra_lines = []
        for name in EXTRA_ROLES:
            rid  = extra_ids.get(name)
            role = ctx.guild.get_role(int(rid)) if rid else None
            extra_lines.append(f"{'[OK]' if role else '[missing]'} {role.mention if role else name}")
        embed.add_field(
            name="Extra Perm Roles",
            value="\n".join(extra_lines) if extra_lines else "None",
            inline=False
        )

        # Managed channels
        if managed:
            embed.add_field(
                name=f"Managed Channels ({len(managed)})",
                value=" ".join(ch.mention for ch in managed[:15]),
                inline=False
            )
        else:
            embed.add_field(
                name="Managed Channels",
                value="None. Add with `,levelroles managechannel #ch`",
                inline=False
            )

        # Permission tiers
        tier_lines = []
        for lvl, perms in sorted(LEVEL_PERM_TIERS.items()):
            keys = [k for k, v in perms.items() if v]
            tier_lines.append(f"Level **{lvl}+** — `{'`, `'.join(keys)}`")
        embed.add_field(
            name="Permission Tiers (cumulative)",
            value="\n".join(tier_lines),
            inline=False
        )

        embed.add_field(
            name="Commands",
            value=(
                "`,levelroles setup` — create all 100 level roles + 7 extra perm roles\n"
                "`,levelroles managechannel #ch` — toggle managed channel\n"
                "`,levelroles sync` — re-apply roles/perms to all members\n"
                "`,levelroles info <level>` — detail view for a level\n"
                "`,levelroles grant @member @role` — give extra perm role\n"
                "`,levelroles revoke @member @role` — remove extra perm role\n"
                "`,levelroles grants [@member]` — list a member's extra roles\n"
                "`,levelroles teardown` — delete all created roles"
            ),
            inline=False
        )
        embed.set_footer(text="Happy Premium — Level Roles System")
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  SETUP — create Lvl 1–100 + extra perm roles
    # ══════════════════════════════════════════════════════════════════════════

    @levelroles.command(name="setup")
    @ctx_admin()
    async def lr_setup(self, ctx):
        """
        Auto-create all 100 level roles (Lvl 1 – Lvl 100) and 7 extra perm roles.
        Roles are created with appropriate Discord permissions at each tier.
        This may take up to 2–3 minutes due to Discord rate limits.
        """
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(
                description="Level Roles requires **Happy Premium**.",
                color=0xC0C0C0
            ))

        # Check bot hierarchy
        if not ctx.guild.me.guild_permissions.manage_roles:
            return await ctx.reply("I need the **Manage Roles** permission to create roles.")

        msg = await ctx.reply(embed=discord.Embed(
            title="Level Roles Setup",
            description=(
                "Starting setup...\n"
                "Creating **100 level roles** + **7 extra perm roles**.\n\n"
                "This may take 2–3 minutes. Please wait."
            ),
            color=0xF0C040
        ))

        cfg = await self._cfg(ctx.guild.id)
        existing_level_ids = cfg.get("level_role_ids", {})
        existing_extra_ids = cfg.get("extra_role_ids", {})

        created_levels = 0
        skipped_levels = 0
        level_role_ids: dict[str, str] = dict(existing_level_ids)

        # ── Step 1: Create Lvl 1–100 roles ─────────────────────────────────
        await msg.edit(embed=discord.Embed(
            title="Level Roles Setup",
            description="Step 1/2 — Creating level roles (Lvl 1 – Lvl 100)...",
            color=0xF0C040
        ))

        for level in range(1, 101):
            # Check if role already exists from a previous setup
            existing_rid = existing_level_ids.get(str(level))
            if existing_rid:
                existing_role = ctx.guild.get_role(int(existing_rid))
                if existing_role:
                    skipped_levels += 1
                    continue  # Already exists, skip

            role_name = f"Lvl {level}"
            perms     = _cumulative_perms(level)
            color     = discord.Color(_level_color(level))

            # Check if a role with this name already exists in the server
            existing_by_name = discord.utils.get(ctx.guild.roles, name=role_name)
            if existing_by_name:
                level_role_ids[str(level)] = str(existing_by_name.id)
                skipped_levels += 1
            else:
                try:
                    new_role = await ctx.guild.create_role(
                        name=role_name,
                        permissions=perms,
                        color=color,
                        reason="LevelRoles setup — auto created by Happy Bot"
                    )
                    level_role_ids[str(level)] = str(new_role.id)
                    created_levels += 1
                except discord.Forbidden:
                    await msg.edit(embed=discord.Embed(
                        description="Missing permission to create roles. Aborting.",
                        color=0xED4245
                    ))
                    return
                except discord.HTTPException:
                    pass  # skip silently, hit role limit or rate limit

            # Rate limit safety: small sleep every role
            await asyncio.sleep(0.35)

            # Progress update every 10 levels
            if level % 10 == 0:
                await msg.edit(embed=discord.Embed(
                    title="Level Roles Setup",
                    description=(
                        f"Step 1/2 — Creating level roles...\n"
                        f"Progress: **{level}/100** done"
                        f" ({created_levels} created, {skipped_levels} already existed)"
                    ),
                    color=0xF0C040
                ))

        # ── Step 2: Create extra perm roles ───────────────────────────────
        await msg.edit(embed=discord.Embed(
            title="Level Roles Setup",
            description="Step 2/2 — Creating extra perm roles...",
            color=0xF0C040
        ))

        extra_role_ids: dict[str, str] = dict(existing_extra_ids)
        created_extras = 0
        skipped_extras = 0

        for extra_name, extra_data in EXTRA_ROLES.items():
            existing_rid = existing_extra_ids.get(extra_name)
            if existing_rid:
                existing_role = ctx.guild.get_role(int(existing_rid))
                if existing_role:
                    skipped_extras += 1
                    continue

            role_name = extra_name  # plain name, no emojis, no prefix
            # Check by name first
            existing_by_name = discord.utils.get(ctx.guild.roles, name=role_name)
            if existing_by_name:
                extra_role_ids[extra_name] = str(existing_by_name.id)
                skipped_extras += 1
            else:
                perms_kwargs = extra_data["perms"]
                try:
                    new_role = await ctx.guild.create_role(
                        name=role_name,
                        permissions=discord.Permissions(**perms_kwargs),
                        color=discord.Color(0x2B2D31),
                        reason=f"LevelRoles setup — extra perm role: {extra_name}"
                    )
                    extra_role_ids[extra_name] = str(new_role.id)
                    created_extras += 1
                except (discord.Forbidden, discord.HTTPException):
                    pass

            await asyncio.sleep(0.4)

        # ── Save to DB ────────────────────────────────────────────────────
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

        # ── Final result embed ────────────────────────────────────────────
        extra_lines = []
        for name, data in EXTRA_ROLES.items():
            rid  = extra_role_ids.get(name)
            role = ctx.guild.get_role(int(rid)) if rid else None
            extra_lines.append(
                f"{role.mention if role else f'`{name}`'} — {data['description']}"
            )

        result = discord.Embed(
            title="Level Roles Setup Complete",
            color=0x57F287,
            timestamp=datetime.now(timezone.utc)
        )
        result.add_field(
            name="Level Roles",
            value=(
                f"Created: **{created_levels}**\n"
                f"Already existed: **{skipped_levels}**\n"
                f"Range: Lvl 1 – Lvl 100"
            ),
            inline=True
        )
        result.add_field(
            name="Extra Perm Roles",
            value=(
                f"Created: **{created_extras}**\n"
                f"Already existed: **{skipped_extras}**"
            ),
            inline=True
        )
        result.add_field(
            name="Extra Perm Roles Created",
            value="\n".join(extra_lines),
            inline=False
        )
        result.add_field(
            name="Permission Tiers",
            value=(
                "Lvl 1+ — read, send messages\n"
                "Lvl 10+ — reactions, embed links\n"
                "Lvl 20+ — attach files\n"
                "Lvl 30+ — external emojis\n"
                "Lvl 40+ — external stickers, threads\n"
                "Lvl 50+ — thread messages, stream\n"
                "Lvl 60+ — voice speak\n"
                "Lvl 70+ — voice connect\n"
                "Lvl 80+ — manage messages\n"
                "Lvl 90+ — move members"
            ),
            inline=False
        )
        result.add_field(
            name="Next Steps",
            value=(
                "1. `,levelroles managechannel #channel` — mark channels for perm management\n"
                "2. `,levelroles sync` — apply roles to all existing members\n"
                "3. `,levelroles grant @member @Gif` — give a member an extra perm role"
            ),
            inline=False
        )
        result.set_footer(text="Happy Premium — Level Roles System")
        await msg.edit(embed=result)

    # ══════════════════════════════════════════════════════════════════════════
    #  MANAGE CHANNEL
    # ══════════════════════════════════════════════════════════════════════════

    @levelroles.command(name="managechannel", aliases=["mc"])
    @ctx_admin()
    async def lr_managechannel(self, ctx, channel: discord.abc.GuildChannel = None):
        """Toggle a channel as managed — level perms are applied here automatically."""
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply("Level Roles requires **Happy Premium**.")

        cfg = await self._cfg(ctx.guild.id)

        if not channel:
            managed = await self._managed_channels(ctx.guild, cfg)
            if not managed:
                return await ctx.reply(
                    "No managed channels yet.\n"
                    "Usage: `,levelroles managechannel #channel`"
                )
            embed = discord.Embed(
                title="Managed Channels",
                description="\n".join(ch.mention for ch in managed),
                color=0xF0C040
            )
            embed.set_footer(text="Run command again with a channel to toggle it on/off")
            return await ctx.reply(embed=embed)

        ids = list(cfg.get("managed_ch_ids", []))
        cid = str(channel.id)

        if cid in ids:
            ids.remove(cid)
            action = "removed from"
        else:
            ids.append(cid)
            action = "added to"

        await lr_config_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"managed_ch_ids": ids}},
            upsert=True
        )
        await ctx.reply(
            f"**{channel.name}** {action} managed channels. "
            "Run `,levelroles sync` to apply permission overwrites to existing members."
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  INFO
    # ══════════════════════════════════════════════════════════════════════════

    @levelroles.command(name="info")
    async def lr_info(self, ctx, level: int = None):
        """View configuration and permissions for a specific level."""
        if level is None or not 1 <= level <= 100:
            return await ctx.reply("Usage: `,levelroles info <1-100>`")

        cfg  = await self._cfg(ctx.guild.id)
        role = self._get_level_role(ctx.guild, cfg, level)

        # Figure out which perm tiers apply
        active_tiers = {t: p for t, p in LEVEL_PERM_TIERS.items() if level >= t}
        all_perms: dict[str, bool] = {}
        for tier_perms in active_tiers.values():
            all_perms.update(tier_perms)

        granted = [k for k, v in all_perms.items() if v]
        # What tier did this level unlock?
        tier_unlocked = max((t for t in LEVEL_PERM_TIERS if t <= level), default=1)
        new_at_tier   = [k for k, v in LEVEL_PERM_TIERS.get(tier_unlocked, {}).items() if v]

        # How many members at this level
        count = await levels_col.count_documents({
            "guild_id": str(ctx.guild.id),
            "level": level
        })

        embed = discord.Embed(
            title=f"Level {level} — Info",
            color=discord.Color(_level_color(level))
        )
        embed.add_field(
            name="Role",
            value=role.mention if role else "Not created yet. Run `,levelroles setup`",
            inline=True
        )
        embed.add_field(name="Members at this level", value=str(count), inline=True)
        embed.add_field(
            name="Cumulative Permissions",
            value=", ".join(f"`{p}`" for p in granted) or "none",
            inline=False
        )
        if new_at_tier == tier_unlocked or level == tier_unlocked:
            embed.add_field(
                name=f"Newly unlocked at level {tier_unlocked}",
                value=", ".join(f"`{p}`" for p in new_at_tier) or "none",
                inline=False
            )
        embed.set_footer(text="Permissions apply in managed channels only")
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  SYNC
    # ══════════════════════════════════════════════════════════════════════════

    @levelroles.command(name="sync")
    @ctx_admin()
    async def lr_sync(self, ctx):
        """
        Re-apply level roles and channel permissions to every member in the server.
        Run this after setup, or after adding new managed channels.
        """
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply("Level Roles requires **Happy Premium**.")

        cfg = await self._cfg(ctx.guild.id)
        if not cfg.get("level_role_ids"):
            return await ctx.reply(
                "Level roles not set up yet. Run `,levelroles setup` first."
            )

        managed = await self._managed_channels(ctx.guild, cfg)
        msg     = await ctx.reply(embed=discord.Embed(
            description="Syncing level roles and permissions to all members...",
            color=0xF0C040
        ))

        cursor  = levels_col.find({"guild_id": str(ctx.guild.id)})
        xp_docs = await cursor.to_list(None)

        done   = 0
        errors = 0

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
                errors += 1

            if (i + 1) % 20 == 0:
                await msg.edit(embed=discord.Embed(
                    title="Syncing...",
                    description=f"Processed **{i+1}/{len(xp_docs)}** members.",
                    color=0xF0C040
                ))

        embed = discord.Embed(title="Sync Complete", color=0x57F287)
        embed.add_field(name="Members processed", value=str(done),          inline=True)
        embed.add_field(name="Managed channels",  value=str(len(managed)),  inline=True)
        if errors:
            embed.add_field(name="Errors", value=str(errors), inline=True)
        embed.set_footer(text="Members with no XP data were skipped")
        await msg.edit(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  GRANT / REVOKE extra perm roles
    # ══════════════════════════════════════════════════════════════════════════

    @levelroles.command(name="grant")
    @ctx_admin()
    async def lr_grant(self, ctx, member: discord.Member = None, role: discord.Role = None):
        """
        Give a member an extra perm role regardless of their level.
        The role must be one of the extra perm roles created during setup
        (Gif, React, Media, Ext, Speak, Stream, Thread) or any other role.
        Usage: ,levelroles grant @member @Gif
        """
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply("Level Roles requires **Happy Premium**.")

        if not member or not role:
            return await ctx.reply(
                "Usage: `,levelroles grant @member @role`\n"
                "Extra perm roles: Gif, React, Media, Ext, Speak, Stream, Thread"
            )

        if ctx.guild.me.top_role <= role:
            return await ctx.reply("My role is below that role. Move Happy's role higher first.")

        if ctx.author.id != BOT_OWNER_ID and ctx.author.top_role <= role:
            return await ctx.reply("You cannot grant a role equal to or higher than your own.")

        # Give role
        if role not in member.roles:
            await member.add_roles(role, reason=f"LevelRoles extra grant by {ctx.author}")

        # Track in DB
        await lr_extra_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(member.id)},
            {"$addToSet": {"role_ids": str(role.id)}},
            upsert=True
        )

        embed = discord.Embed(
            title="Extra Role Granted",
            description=(
                f"**{role.name}** granted to {member.mention}.\n\n"
                "This is tracked separately from level requirements.\n"
                "Use `,levelroles revoke @member @role` to remove it."
            ),
            color=0x57F287
        )
        embed.set_footer(text=f"Granted by {ctx.author.display_name}")
        await ctx.reply(embed=embed)

    @levelroles.command(name="revoke")
    @ctx_admin()
    async def lr_revoke(self, ctx, member: discord.Member = None, role: discord.Role = None):
        """Remove an extra perm role from a member."""
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply("Level Roles requires **Happy Premium**.")

        if not member or not role:
            return await ctx.reply("Usage: `,levelroles revoke @member @role`")

        if role in member.roles:
            await member.remove_roles(role, reason=f"LevelRoles extra revoke by {ctx.author}")

        await lr_extra_col.update_one(
            {"guild_id": str(ctx.guild.id), "user_id": str(member.id)},
            {"$pull": {"role_ids": str(role.id)}}
        )

        await ctx.reply(embed=discord.Embed(
            description=f"**{role.name}** removed from {member.mention}.",
            color=0x2B2D31
        ))

    @levelroles.command(name="grants")
    async def lr_grants(self, ctx, member: discord.Member = None):
        """List all extra perm roles granted to a member."""
        member = member or ctx.author
        doc    = await lr_extra_col.find_one({
            "guild_id": str(ctx.guild.id),
            "user_id":  str(member.id)
        })
        if not doc or not doc.get("role_ids"):
            return await ctx.reply(
                f"**{member.display_name}** has no extra perm roles on record."
            )

        lines = []
        for rid in doc["role_ids"]:
            role = ctx.guild.get_role(int(rid))
            lines.append(role.mention if role else f"`deleted role ({rid})`")

        embed = discord.Embed(
            title=f"Extra Roles — {member.display_name}",
            description="\n".join(lines),
            color=0xF0C040
        )
        embed.set_footer(text="Granted manually, outside of level requirements")
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  TEARDOWN — delete all created roles
    # ══════════════════════════════════════════════════════════════════════════

    @levelroles.command(name="teardown")
    @ctx_admin()
    async def lr_teardown(self, ctx):
        """
        Delete ALL level roles and extra perm roles created by this system.
        Requires confirmation. This cannot be undone.
        """
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply("Level Roles requires **Happy Premium**.")

        embed = discord.Embed(
            title="Teardown Confirmation",
            description=(
                "This will **permanently delete** all 100 level roles and all 7 extra perm roles "
                "from this server.\n\n"
                "Type `confirm teardown` in the next 30 seconds to proceed, "
                "or anything else to cancel."
            ),
            color=0xED4245
        )
        await ctx.reply(embed=embed)

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            reply = await self.bot.wait_for("message", check=check, timeout=30)
        except asyncio.TimeoutError:
            return await ctx.send("Teardown cancelled — timed out.")

        if reply.content.strip().lower() != "confirm teardown":
            return await ctx.send("Teardown cancelled.")

        cfg = await self._cfg(ctx.guild.id)
        msg = await ctx.send(embed=discord.Embed(
            description="Deleting level roles...", color=0xED4245
        ))

        deleted = 0
        failed  = 0

        # Delete level roles
        for rid in cfg.get("level_role_ids", {}).values():
            role = ctx.guild.get_role(int(rid))
            if role:
                try:
                    await role.delete(reason="LevelRoles teardown")
                    deleted += 1
                except Exception:
                    failed += 1
                await asyncio.sleep(0.35)

        # Delete extra perm roles
        for rid in cfg.get("extra_role_ids", {}).values():
            role = ctx.guild.get_role(int(rid))
            if role:
                try:
                    await role.delete(reason="LevelRoles teardown — extra perm role")
                    deleted += 1
                except Exception:
                    failed += 1
                await asyncio.sleep(0.35)

        # Clear DB
        await lr_config_col.delete_one({"guild_id": str(ctx.guild.id)})
        await lr_extra_col.delete_many({"guild_id": str(ctx.guild.id)})

        embed = discord.Embed(
            title="Teardown Complete",
            description=(
                f"Deleted **{deleted}** roles.\n"
                + (f"Failed to delete **{failed}** roles (already deleted or permissions issue).\n" if failed else "")
                + "\nAll level role data cleared from database."
            ),
            color=0x2B2D31
        )
        await msg.edit(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  PUBLIC HOOK — called from core.py on level-up
    # ══════════════════════════════════════════════════════════════════════════

    async def on_level_up(self, guild: discord.Guild, member: discord.Member, new_level: int):
        """Called by core.py's _award_xp when a member levels up."""
        if not await is_premium_server(guild.id):
            return
        cfg = await self._cfg(guild.id)
        if not cfg.get("level_role_ids"):
            return
        await self._apply_member(guild, member, new_level, cfg)

    # ══════════════════════════════════════════════════════════════════════════
    #  SLASH — dashboard
    # ══════════════════════════════════════════════════════════════════════════

    @app_commands.command(name="levelroles", description="View the level role system dashboard")
    async def slash_levelroles(self, interaction: discord.Interaction):
        if not await is_premium_server(interaction.guild.id):
            return await interaction.response.send_message(
                "Level Roles requires **Happy Premium**.", ephemeral=True
            )
        cfg      = await self._cfg(interaction.guild.id)
        role_ids = cfg.get("level_role_ids", {})
        total    = sum(1 for rid in role_ids.values() if interaction.guild.get_role(int(rid)))
        embed    = discord.Embed(title="Level Roles", color=0xF0C040)
        embed.add_field(
            name="Level Roles Active",
            value=f"**{total}/100** roles exist in this server.",
            inline=True
        )
        extra_ids = cfg.get("extra_role_ids", {})
        extra_lines = []
        for name in EXTRA_ROLES:
            rid  = extra_ids.get(name)
            role = interaction.guild.get_role(int(rid)) if rid else None
            extra_lines.append(role.mention if role else f"`{name}` (missing)")
        embed.add_field(
            name="Extra Perm Roles",
            value="\n".join(extra_lines) or "None",
            inline=True
        )
        embed.set_footer(text="Use ,levelroles setup to create all roles | ,levelroles for full dashboard")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(LevelRoles(bot))