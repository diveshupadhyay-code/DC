import discord
from discord.ext import commands

from utils.db import fake_perms_col as fake_col
from utils import secui

ALLOWED = {
    "ban_members": "Ban",
    "kick_members": "Kick",
    "moderate_members": "Timeout",
    "manage_messages": "Delete messages",
    "manage_nicknames": "Change nicknames",
    "manage_channels": "Lock channels",
    "view_audit_log": "View audit log",
}

ALIASES = {
    "ban": "ban_members",
    "kick": "kick_members",
    "timeout": "moderate_members",
    "mute": "moderate_members",
    "messages": "manage_messages",
    "purge": "manage_messages",
    "delete": "manage_messages",
    "nick": "manage_nicknames",
    "nicknames": "manage_nicknames",
    "channels": "manage_channels",
    "lock": "manage_channels",
    "audit": "view_audit_log",
}

_fake = {}


def resolve(name):
    key = (name or "").lower().strip().replace(" ", "_")
    key = ALIASES.get(key, key)
    return key if key in ALLOWED else None


class FakeContext(commands.Context):
    @property
    def has_fake_perms(self):
        if self.guild is None:
            return False
        roles = _fake.get(self.guild.id)
        if not roles:
            return False
        return any(roles.get(r.id) for r in getattr(self.author, "roles", []))

    @property
    def permissions(self):
        base = commands.Context.permissions.function(self)
        if self.guild is None:
            return base
        roles = _fake.get(self.guild.id)
        if not roles:
            return base
        merged = discord.Permissions(base.value)
        for role in getattr(self.author, "roles", []):
            for name in roles.get(role.id, ()):
                setattr(merged, name, True)
        return merged


class FakePerms(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._original = None

    async def cog_load(self):
        await self._reload()
        self._original = self.bot.get_context

        async def get_context(origin, *, cls=commands.Context):
            use = FakeContext if cls is commands.Context else cls
            return await self._original(origin, cls=use)

        self.bot.get_context = get_context

    def cog_unload(self):
        if self._original is not None:
            self.bot.get_context = self._original
        _fake.clear()

    async def _reload(self):
        _fake.clear()
        async for doc in fake_col.find({}):
            _fake.setdefault(int(doc["guild_id"]), {})[int(doc["role_id"])] = set(doc.get("perms", []))

    async def _store(self, guild_id, role_id, perms):
        gid, rid = str(guild_id), str(role_id)
        if perms:
            await fake_col.update_one(
                {"guild_id": gid, "role_id": rid},
                {"$set": {"perms": sorted(perms)}},
                upsert=True,
            )
            _fake.setdefault(guild_id, {})[role_id] = set(perms)
        else:
            await fake_col.delete_one({"guild_id": gid, "role_id": rid})
            _fake.get(guild_id, {}).pop(role_id, None)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        await self._store(role.guild.id, role.id, set())

    @commands.group(name="fakeperms", aliases=["fp", "fakepermissions"], invoke_without_command=True)
    @secui.trusted_only()
    async def fakeperms(self, ctx):
        roles = _fake.get(ctx.guild.id, {})
        lines = []
        for rid, perms in roles.items():
            role = ctx.guild.get_role(rid)
            if role and perms:
                names = "  ·  ".join(ALLOWED[p] for p in sorted(perms))
                lines.append(f"{role.mention}\n{names}")

        embed = secui.make(
            "🎭  Fake Permissions",
            "Lets a role use the bot's moderation commands without giving it real Discord permissions. "
            "Your moderators can help, and nobody gets dangerous power.",
            secui.NEUTRAL,
        )
        embed.add_field(name="Roles with fake permissions", value="\n\n".join(lines) or "None yet", inline=False)
        embed.add_field(
            name="What you can give",
            value="  ·  ".join(f"`{k}`" for k in ("ban", "kick", "timeout", "messages", "nicknames", "channels", "audit")),
            inline=False,
        )
        embed.add_field(
            name="Commands",
            value=(
                f"`{ctx.clean_prefix}fakeperms grant @role <permission>`\n"
                f"`{ctx.clean_prefix}fakeperms revoke @role <permission>`\n"
                f"`{ctx.clean_prefix}fakeperms reset @role`"
            ),
            inline=False,
        )
        embed.set_footer(text="Admin, Manage Server and Manage Roles can never be given this way")
        await secui.send(ctx, embed)

    @fakeperms.command(name="grant", aliases=["add"])
    @secui.trusted_only()
    async def fp_grant(self, ctx, role: discord.Role = None, permission: str = None):
        name = resolve(permission)
        if role is None or name is None:
            return await secui.send(
                ctx,
                secui.fail(f"Use `{ctx.clean_prefix}fakeperms grant @role ban`.\nOptions: ban, kick, timeout, messages, nicknames, channels, audit"),
            )
        if role.is_default():
            return await secui.send(ctx, secui.fail("You cannot use the everyone role. Pick a staff role."))
        perms = set(_fake.get(ctx.guild.id, {}).get(role.id, set()))
        perms.add(name)
        await self._store(ctx.guild.id, role.id, perms)
        await secui.send(ctx, secui.ok(f"{role.mention} can now use **{ALLOWED[name]}** commands."))

    @fakeperms.command(name="revoke", aliases=["remove"])
    @secui.trusted_only()
    async def fp_revoke(self, ctx, role: discord.Role = None, permission: str = None):
        name = resolve(permission)
        if role is None or name is None:
            return await secui.send(ctx, secui.fail(f"Use `{ctx.clean_prefix}fakeperms revoke @role ban`."))
        perms = set(_fake.get(ctx.guild.id, {}).get(role.id, set()))
        if name not in perms:
            return await secui.send(ctx, secui.fail(f"{role.mention} does not have that fake permission."))
        perms.discard(name)
        await self._store(ctx.guild.id, role.id, perms)
        await secui.send(ctx, secui.ok(f"{role.mention} can no longer use **{ALLOWED[name]}** commands."))

    @fakeperms.command(name="reset", aliases=["clear"])
    @secui.trusted_only()
    async def fp_reset(self, ctx, role: discord.Role = None):
        if role is None:
            return await secui.send(ctx, secui.fail(f"Use `{ctx.clean_prefix}fakeperms reset @role`."))
        await self._store(ctx.guild.id, role.id, set())
        await secui.send(ctx, secui.ok(f"All fake permissions removed from {role.mention}."))


async def setup(bot):
    await bot.add_cog(FakePerms(bot))