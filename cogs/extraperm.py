"""
cogs/extraperm.py — Extra Perm Roles System (Standalone, Premium Feature)

Completely separate from the level roles system.
Admins run ONE command to create all extra perm roles in their server.
Then they can assign/remove those roles to any member whenever they want.

Extra perm roles created:
  Gif      — embed_links + attach_files  (post GIFs via Tenor / Giphy)
  React    — add_reactions
  Media    — attach_files + embed_links
  Ext      — use_external_emojis + use_external_stickers
  Speak    — speak in voice channels
  Stream   — stream / go live in voice
  Thread   — create_public_threads + send_messages_in_threads
  Mention  — mention_everyone (use carefully)
  Nick     — change_nickname
  Invite   — create_instant_invite

Commands:
  ,extraperm setup          — create all extra perm roles in one go
  ,extraperm                — dashboard: list all roles + who has them
  ,extraperm give @member <role>   — assign a perm role to a member
  ,extraperm take @member <role>   — remove a perm role from a member
  ,extraperm list [@member]        — see which perm roles a member has
  ,extraperm info <rolename>       — what permissions a role grants
  ,extraperm teardown       — delete all created extra perm roles (confirmation required)

No emojis in role names.
"""

import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from datetime import datetime, timezone

from utils.db import db
from utils.helpers import BOT_OWNER_ID, ctx_admin, is_premium_server

# ── Collection ─────────────────────────────────────────────────────────────────
ep_config_col = db["extraperm_config"]
# Schema: {guild_id, roles: {name: role_id}, members: {user_id: [role_names]}}

# ── Role definitions ───────────────────────────────────────────────────────────
# name → {description, perms: {discord_perm_field: bool}, color}
EXTRA_PERM_ROLES: dict[str, dict] = {
    "Gif": {
        "description": "Post GIFs via Tenor/Giphy (embed links + attach files)",
        "perms":       {"embed_links": True, "attach_files": True},
        "color":       0xF97316,  # orange
    },
    "React": {
        "description": "Add reactions to messages",
        "perms":       {"add_reactions": True},
        "color":       0xFBBF24,  # yellow
    },
    "Media": {
        "description": "Attach files and embed links in messages",
        "perms":       {"attach_files": True, "embed_links": True},
        "color":       0x38BDF8,  # sky blue
    },
    "Ext": {
        "description": "Use external emojis and stickers from other servers",
        "perms":       {"use_external_emojis": True, "use_external_stickers": True},
        "color":       0xA78BFA,  # lavender
    },
    "Speak": {
        "description": "Speak in voice channels",
        "perms":       {"speak": True},
        "color":       0x34D399,  # mint
    },
    "Stream": {
        "description": "Go live / screen share in voice channels",
        "perms":       {"stream": True},
        "color":       0xE8425A,  # rose
    },
    "Thread": {
        "description": "Create public threads and participate in threads",
        "perms":       {"create_public_threads": True, "send_messages_in_threads": True},
        "color":       0x64748B,  # steel
    },
    "Mention": {
        "description": "Use @everyone and @here mentions (use carefully)",
        "perms":       {"mention_everyone": True},
        "color":       0xDC2626,  # crimson
    },
    "Nick": {
        "description": "Change their own nickname",
        "perms":       {"change_nickname": True},
        "color":       0x10B981,  # emerald
    },
    "Invite": {
        "description": "Create invite links for the server",
        "perms":       {"create_instant_invite": True},
        "color":       0x3B82F6,  # sapphire
    },
}

# Aliases so members can type natural names
ROLE_ALIASES: dict[str, str] = {
    "gif":     "Gif",
    "react":   "React",
    "reaction":"React",
    "media":   "Media",
    "ext":     "Ext",
    "external":"Ext",
    "speak":   "Speak",
    "voice":   "Speak",
    "stream":  "Stream",
    "live":    "Stream",
    "thread":  "Thread",
    "threads": "Thread",
    "mention": "Mention",
    "ping":    "Mention",
    "nick":    "Nick",
    "nickname":"Nick",
    "invite":  "Invite",
    "inv":     "Invite",
}


def _resolve_name(raw: str) -> str | None:
    """Resolve a user-typed name/alias to the canonical role name."""
    clean = raw.strip().lower()
    # Direct match (case-insensitive)
    for name in EXTRA_PERM_ROLES:
        if name.lower() == clean:
            return name
    # Alias match
    return ROLE_ALIASES.get(clean)


class ExtraPerm(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Helpers ────────────────────────────────────────────────────────────────
    async def _cfg(self, guild_id: int) -> dict:
        return await ep_config_col.find_one({"guild_id": str(guild_id)}) or {}

    def _get_role(self, guild: discord.Guild, cfg: dict, name: str) -> discord.Role | None:
        rid = cfg.get("roles", {}).get(name)
        return guild.get_role(int(rid)) if rid else None

    def _all_roles_exist(self, guild: discord.Guild, cfg: dict) -> int:
        """Return count of extra perm roles that exist in the guild."""
        return sum(
            1 for name in EXTRA_PERM_ROLES
            if self._get_role(guild, cfg, name) is not None
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  DASHBOARD  ,extraperm
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(name="extraperm", aliases=["ep", "xperm"], invoke_without_command=True)
    @ctx_admin()
    async def extraperm(self, ctx):
        """Extra perm roles dashboard."""
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(
                description="Extra Perm Roles is a **Happy Premium** feature.",
                color=0xC0C0C0
            ))

        cfg    = await self._cfg(ctx.guild.id)
        exists = self._all_roles_exist(ctx.guild, cfg)

        embed = discord.Embed(
            title="Extra Perm Roles — Dashboard",
            color=0xF0C040,
            timestamp=datetime.now(timezone.utc)
        )

        # Role status table
        role_lines = []
        for name, data in EXTRA_PERM_ROLES.items():
            role = self._get_role(ctx.guild, cfg, name)
            if role:
                # Count members who have this role
                count = len([m for m in ctx.guild.members if role in m.roles])
                role_lines.append(
                    f"{role.mention} — {data['description']}"
                    f"\n  `{count}` member(s) assigned"
                )
            else:
                role_lines.append(f"`{name}` (not created) — {data['description']}")

        embed.add_field(
            name=f"Roles ({exists}/{len(EXTRA_PERM_ROLES)} active)",
            value="\n\n".join(role_lines),
            inline=False
        )

        if exists < len(EXTRA_PERM_ROLES):
            embed.add_field(
                name="Setup Required",
                value="Run `,extraperm setup` to create all extra perm roles.",
                inline=False
            )

        embed.add_field(
            name="Commands",
            value=(
                "`,extraperm setup` — create all 10 extra perm roles\n"
                "`,extraperm give @member <role>` — assign a perm role\n"
                "`,extraperm take @member <role>` — remove a perm role\n"
                "`,extraperm list [@member]` — view a member's perm roles\n"
                "`,extraperm info <rolename>` — what a role grants\n"
                "`,extraperm teardown` — delete all created roles\n\n"
                "Role names: `"
                + "`, `".join(EXTRA_PERM_ROLES.keys())
                + "`"
            ),
            inline=False
        )
        embed.set_footer(text="Happy Premium — Extra Perm Roles")
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  SETUP — create all extra perm roles
    # ══════════════════════════════════════════════════════════════════════════

    @extraperm.command(name="setup")
    @ctx_admin()
    async def ep_setup(self, ctx):
        """
        Create all 10 extra perm roles in this server.
        Each role is created with its specific Discord permissions already set.
        Roles that already exist are skipped.
        """
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply(embed=discord.Embed(
                description="Extra Perm Roles requires **Happy Premium**.",
                color=0xC0C0C0
            ))

        if not ctx.guild.me.guild_permissions.manage_roles:
            return await ctx.reply("I need **Manage Roles** permission to create roles.")

        msg = await ctx.reply(embed=discord.Embed(
            title="Extra Perm Roles — Setup",
            description="Creating extra perm roles...",
            color=0xF0C040
        ))

        cfg          = await self._cfg(ctx.guild.id)
        saved_roles  = dict(cfg.get("roles", {}))
        created      = []
        skipped      = []
        failed       = []

        for name, data in EXTRA_PERM_ROLES.items():
            # Already tracked in DB and still alive in guild?
            existing_rid = saved_roles.get(name)
            if existing_rid:
                existing_role = ctx.guild.get_role(int(existing_rid))
                if existing_role:
                    skipped.append(name)
                    continue

            # Check by name in guild
            by_name = discord.utils.get(ctx.guild.roles, name=name)
            if by_name:
                saved_roles[name] = str(by_name.id)
                skipped.append(name)
                continue

            # Create it
            try:
                new_role = await ctx.guild.create_role(
                    name=name,
                    permissions=discord.Permissions(**data["perms"]),
                    color=discord.Color(data["color"]),
                    reason="Extra Perm Roles setup — Happy Bot"
                )
                saved_roles[name] = str(new_role.id)
                created.append(name)
            except discord.Forbidden:
                failed.append(name)
            except discord.HTTPException as e:
                failed.append(f"{name} ({e.text})")

            await asyncio.sleep(0.4)

        # Save to DB
        await ep_config_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {
                "guild_id": str(ctx.guild.id),
                "roles":    saved_roles,
            }},
            upsert=True
        )

        # Reload cfg for the result embed
        cfg = await self._cfg(ctx.guild.id)

        # Build result rows
        role_lines = []
        for name, data in EXTRA_PERM_ROLES.items():
            role    = self._get_role(ctx.guild, cfg, name)
            status  = "created" if name in created else ("existed" if name in skipped else "FAILED")
            perm_str = ", ".join(f"`{k}`" for k in data["perms"])
            role_lines.append(
                f"{role.mention if role else f'`{name}`'} [{status}]\n"
                f"  Perms: {perm_str}\n"
                f"  {data['description']}"
            )

        result = discord.Embed(
            title="Extra Perm Roles — Setup Complete",
            color=0x57F287 if not failed else 0xFEE75C,
            timestamp=datetime.now(timezone.utc)
        )
        result.add_field(
            name="Summary",
            value=(
                f"Created: **{len(created)}**\n"
                f"Already existed: **{len(skipped)}**"
                + (f"\nFailed: **{len(failed)}**" if failed else "")
            ),
            inline=False
        )
        result.add_field(
            name="Roles",
            value="\n\n".join(role_lines),
            inline=False
        )
        result.add_field(
            name="Next Steps",
            value=(
                "`,extraperm give @member Gif` — give Gif role to a member\n"
                "`,extraperm give @member React` — give React role\n"
                "`,extraperm list @member` — see a member's perm roles\n"
                "`,extraperm` — open full dashboard"
            ),
            inline=False
        )
        result.set_footer(text="Happy Premium — Extra Perm Roles")
        await msg.edit(embed=result)

    # ══════════════════════════════════════════════════════════════════════════
    #  GIVE — assign a perm role to a member
    # ══════════════════════════════════════════════════════════════════════════

    @extraperm.command(name="give", aliases=["add", "grant"])
    @ctx_admin()
    async def ep_give(self, ctx, member: discord.Member = None, *, role_name: str = None):
        """
        Assign an extra perm role to a member.
        Usage: ,extraperm give @member Gif
        Role names: Gif, React, Media, Ext, Speak, Stream, Thread, Mention, Nick, Invite
        """
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply("Extra Perm Roles requires **Happy Premium**.")

        if not member or not role_name:
            return await ctx.reply(
                "Usage: `,extraperm give @member <rolename>`\n"
                "Role names: `" + "`, `".join(EXTRA_PERM_ROLES.keys()) + "`"
            )

        canonical = _resolve_name(role_name)
        if not canonical:
            return await ctx.reply(
                f"Unknown role `{role_name}`.\n"
                "Available: `" + "`, `".join(EXTRA_PERM_ROLES.keys()) + "`"
            )

        cfg  = await self._cfg(ctx.guild.id)
        role = self._get_role(ctx.guild, cfg, canonical)
        if not role:
            return await ctx.reply(
                f"The `{canonical}` role hasn't been created yet.\n"
                "Run `,extraperm setup` first."
            )

        if ctx.guild.me.top_role <= role:
            return await ctx.reply("My role is below that role. Move Happy higher in the role list.")

        if role in member.roles:
            return await ctx.reply(
                f"**{member.display_name}** already has the **{canonical}** role."
            )

        await member.add_roles(role, reason=f"ExtraPerm: granted by {ctx.author}")

        embed = discord.Embed(
            title="Extra Perm Role Assigned",
            color=discord.Color(EXTRA_PERM_ROLES[canonical]["color"])
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Member",      value=member.mention,                                inline=True)
        embed.add_field(name="Role",        value=role.mention,                                  inline=True)
        embed.add_field(name="Granted by",  value=ctx.author.mention,                           inline=True)
        embed.add_field(
            name="What this unlocks",
            value=EXTRA_PERM_ROLES[canonical]["description"],
            inline=False
        )
        embed.set_footer(text="Use ,extraperm take @member to remove it")
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  TAKE — remove a perm role from a member
    # ══════════════════════════════════════════════════════════════════════════

    @extraperm.command(name="take", aliases=["remove", "revoke"])
    @ctx_admin()
    async def ep_take(self, ctx, member: discord.Member = None, *, role_name: str = None):
        """
        Remove an extra perm role from a member.
        Usage: ,extraperm take @member Gif
        """
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply("Extra Perm Roles requires **Happy Premium**.")

        if not member or not role_name:
            return await ctx.reply(
                "Usage: `,extraperm take @member <rolename>`\n"
                "Role names: `" + "`, `".join(EXTRA_PERM_ROLES.keys()) + "`"
            )

        canonical = _resolve_name(role_name)
        if not canonical:
            return await ctx.reply(
                f"Unknown role `{role_name}`.\n"
                "Available: `" + "`, `".join(EXTRA_PERM_ROLES.keys()) + "`"
            )

        cfg  = await self._cfg(ctx.guild.id)
        role = self._get_role(ctx.guild, cfg, canonical)
        if not role:
            return await ctx.reply(
                f"The `{canonical}` role doesn't exist in this server.\n"
                "Run `,extraperm setup` first."
            )

        if role not in member.roles:
            return await ctx.reply(
                f"**{member.display_name}** doesn't have the **{canonical}** role."
            )

        await member.remove_roles(role, reason=f"ExtraPerm: removed by {ctx.author}")

        embed = discord.Embed(
            title="Extra Perm Role Removed",
            color=0x2B2D31
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Member",     value=member.mention,   inline=True)
        embed.add_field(name="Role",       value=role.mention,     inline=True)
        embed.add_field(name="Removed by", value=ctx.author.mention, inline=True)
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  LIST — see which perm roles a member has
    # ══════════════════════════════════════════════════════════════════════════

    @extraperm.command(name="list", aliases=["check", "view"])
    async def ep_list(self, ctx, member: discord.Member = None):
        """
        See which extra perm roles a member has.
        Usage: ,extraperm list @member
        """
        member = member or ctx.author
        cfg    = await self._cfg(ctx.guild.id)

        has_roles   = []
        lacks_roles = []

        for name in EXTRA_PERM_ROLES:
            role = self._get_role(ctx.guild, cfg, name)
            if role:
                if role in member.roles:
                    has_roles.append(f"{role.mention} — {EXTRA_PERM_ROLES[name]['description']}")
                else:
                    lacks_roles.append(f"`{name}`")

        embed = discord.Embed(
            title=f"Extra Perm Roles — {member.display_name}",
            color=0xF0C040 if has_roles else 0x2B2D31
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        embed.add_field(
            name=f"Has ({len(has_roles)})",
            value="\n".join(has_roles) if has_roles else "None",
            inline=False
        )
        if lacks_roles:
            embed.add_field(
                name=f"Does not have ({len(lacks_roles)})",
                value=", ".join(lacks_roles),
                inline=False
            )
        embed.set_footer(text=f"ID: {member.id}")
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  INFO — what a role grants
    # ══════════════════════════════════════════════════════════════════════════

    @extraperm.command(name="info")
    async def ep_info(self, ctx, *, role_name: str = None):
        """
        View what permissions a specific extra perm role grants.
        Usage: ,extraperm info Gif
        """
        if not role_name:
            lines = []
            for name, data in EXTRA_PERM_ROLES.items():
                perm_str = ", ".join(f"`{k}`" for k in data["perms"])
                lines.append(f"**{name}** — {data['description']}\n  Perms: {perm_str}")
            embed = discord.Embed(
                title="Extra Perm Roles — All Roles",
                description="\n\n".join(lines),
                color=0xF0C040
            )
            embed.set_footer(text="Use ,extraperm info <rolename> for detail on one role")
            return await ctx.reply(embed=embed)

        canonical = _resolve_name(role_name)
        if not canonical:
            return await ctx.reply(
                f"Unknown role `{role_name}`.\n"
                "Available: `" + "`, `".join(EXTRA_PERM_ROLES.keys()) + "`"
            )

        data = EXTRA_PERM_ROLES[canonical]
        cfg  = await self._cfg(ctx.guild.id)
        role = self._get_role(ctx.guild, cfg, canonical)

        # Members who have this role
        members_with = []
        if role:
            members_with = [m for m in ctx.guild.members if role in m.roles]

        embed = discord.Embed(
            title=f"Extra Perm Role — {canonical}",
            description=data["description"],
            color=discord.Color(data["color"])
        )
        embed.add_field(
            name="Discord Permissions",
            value="\n".join(f"`{k}` = `{v}`" for k, v in data["perms"].items()),
            inline=False
        )
        embed.add_field(
            name="Role in Server",
            value=role.mention if role else "Not created. Run `,extraperm setup`.",
            inline=True
        )
        embed.add_field(
            name="Members Assigned",
            value=str(len(members_with)),
            inline=True
        )
        if members_with:
            preview = ", ".join(m.display_name for m in members_with[:8])
            if len(members_with) > 8:
                preview += f" +{len(members_with) - 8} more"
            embed.add_field(name="Who has it", value=preview, inline=False)

        embed.set_footer(
            text=f"Assign: ,extraperm give @member {canonical} | Remove: ,extraperm take @member {canonical}"
        )
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  TEARDOWN — delete all created extra perm roles
    # ══════════════════════════════════════════════════════════════════════════

    @extraperm.command(name="teardown", aliases=["reset", "deleteall"])
    @ctx_admin()
    async def ep_teardown(self, ctx):
        """
        Delete all extra perm roles created by this system.
        Requires confirmation. This cannot be undone.
        """
        if not await is_premium_server(ctx.guild.id):
            return await ctx.reply("Extra Perm Roles requires **Happy Premium**.")

        cfg = await self._cfg(ctx.guild.id)
        roles_to_delete = []
        for name in EXTRA_PERM_ROLES:
            role = self._get_role(ctx.guild, cfg, name)
            if role:
                roles_to_delete.append((name, role))

        if not roles_to_delete:
            return await ctx.reply(
                "No extra perm roles found in this server to delete."
            )

        confirm_embed = discord.Embed(
            title="Teardown Confirmation",
            description=(
                f"This will permanently delete **{len(roles_to_delete)}** extra perm role(s):\n"
                + ", ".join(f"`{n}`" for n, _ in roles_to_delete)
                + "\n\nType `confirm teardown` within 30 seconds to proceed."
            ),
            color=0xED4245
        )
        await ctx.reply(embed=confirm_embed)

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            reply = await self.bot.wait_for("message", check=check, timeout=30)
        except asyncio.TimeoutError:
            return await ctx.send("Teardown cancelled — timed out.")

        if reply.content.strip().lower() != "confirm teardown":
            return await ctx.send("Teardown cancelled.")

        msg     = await ctx.send(embed=discord.Embed(
            description="Deleting extra perm roles...", color=0xED4245
        ))
        deleted = 0
        failed  = 0

        for name, role in roles_to_delete:
            try:
                await role.delete(reason="ExtraPerm teardown")
                deleted += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.4)

        # Clear DB config
        await ep_config_col.delete_one({"guild_id": str(ctx.guild.id)})

        result = discord.Embed(
            title="Teardown Complete",
            description=(
                f"Deleted **{deleted}** role(s)."
                + (f"\nFailed to delete **{failed}** (already gone or permission issue)." if failed else "")
                + "\n\nAll extra perm role data cleared from database."
            ),
            color=0x2B2D31
        )
        await msg.edit(embed=result)

    # ══════════════════════════════════════════════════════════════════════════
    #  SLASH — info / give / take
    # ══════════════════════════════════════════════════════════════════════════

    @app_commands.command(name="extraperm", description="View or manage extra perm roles")
    @app_commands.describe(member="Member to check (leave blank for dashboard)")
    async def slash_extraperm(
        self,
        interaction: discord.Interaction,
        member: discord.Member = None
    ):
        if not await is_premium_server(interaction.guild.id):
            return await interaction.response.send_message(
                "Extra Perm Roles requires **Happy Premium**.", ephemeral=True
            )

        cfg = await self._cfg(interaction.guild.id)
        target = member or interaction.user

        has_roles = []
        for name in EXTRA_PERM_ROLES:
            role = self._get_role(interaction.guild, cfg, name)
            if role and role in target.roles:
                has_roles.append(role.mention)

        embed = discord.Embed(
            title=f"Extra Perm Roles — {target.display_name}",
            description=(
                "\n".join(has_roles) if has_roles
                else "No extra perm roles assigned."
            ),
            color=0xF0C040 if has_roles else 0x2B2D31
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text="Admins: use ,extraperm give/take to manage")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(ExtraPerm(bot))
