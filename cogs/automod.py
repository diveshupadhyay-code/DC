import re
import time
import typing
import unicodedata
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord.ext import commands

from utils.db import automod_col
from utils.helpers import ctx_admin, log_event
from utils import secui

CONFIG_SECONDS = 10
STRIKE_SECONDS = 600
MAX_WORDS = 200

DEFAULTS = {
    "enabled": False,
    "invites": True,
    "spam": True,
    "phishing": True,
    "words": True,
    "leave_ban": True,
    "punishment": "timeout",
    "phishing_action": "ban",
    "spam_limit": 5,
    "dup_limit": 3,
    "mention_limit": 6,
}

PUNISHMENTS = {
    "timeout": ("Timeout 1 hour", "Mutes them after repeated rule breaks"),
    "kick": ("Kick", "Removes them after repeated rule breaks"),
    "ban": ("Ban", "Bans them after repeated rule breaks"),
}

ZERO = re.compile("[\u200b-\u200f\u2060\ufeff\u00ad]")
LEET = str.maketrans({"@": "a", "4": "a", "0": "o", "1": "i", "3": "e", "$": "s", "5": "s", "7": "t"})

INVITE = re.compile(
    r"(?:discord(?:app)?\s*\.\s*(?:gg|io|me|li|link|com\s*/\s*invite)|dsc\s*\.\s*gg|invite\s*\.\s*gg)\s*/\s*([a-z0-9-]+)"
)
URL = re.compile(r"(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,24})(?::\d+)?(?:/[^\s]*)?", re.I)

OFFICIAL = {
    "discord.com", "discord.gg", "discordapp.com", "discordapp.net", "discord.media",
    "discordstatus.com", "discord.gift", "discord.new", "discord.dev",
    "steampowered.com", "steamcommunity.com", "steamstatic.com", "roblox.com", "epicgames.com",
}
BLOCKED = {
    "discord-gift.com", "discordgift.site", "discord-nitro.gift", "discordnitro.gift",
    "dlscord.gift", "discord-airdrop.com", "nitro-discord.com", "steam-gift.com",
    "steamcommunlty.com", "steamcomminuty.com", "steamcornmunity.com",
}
BRAND = re.compile(r"d[il]sc[ck]?o?r[dt]|dicsord|discrod|steam|roblox|epicgames")
BAIT_WORDS = re.compile(r"nitro|gift|free|promo|claim|airdrop|giveaway|verify|login|auth|drop|bonus")
BAIT_PHRASES = (
    "free nitro", "nitro gift", "claim your nitro", "nitro for free", "steam gift",
    "free steam", "free robux", "discord gift", "airdrop",
)
SUSPICIOUS_TLDS = {
    "xyz", "top", "click", "gift", "ru", "tk", "ml", "ga", "cf", "gq", "icu",
    "buzz", "shop", "live", "site", "online", "link", "club", "rest", "cyou", "cam", "sbs",
}


def clean(text):
    return ZERO.sub("", text)


def normalize(text):
    text = unicodedata.normalize("NFKD", ZERO.sub("", text.lower()))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.translate(LEET)


def build_pattern(words):
    parts = []
    for word in words:
        partial = word.endswith("*")
        base = re.sub(r"\s+", " ", normalize(word.rstrip("*"))).strip()
        if not base:
            continue
        if " " in base:
            core = r"\s+".join(re.escape(p) for p in base.split(" "))
        elif len(base) >= 3:
            core = r"[\W_]*".join(re.escape(c) for c in base)
        else:
            core = re.escape(base)
        right = "" if partial else r"(?![a-z0-9])"
        parts.append(rf"(?<![a-z0-9]){core}{right}")
    return re.compile("|".join(parts)) if parts else None


def registrable(host):
    labels = host.split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def in_domains(host, domains):
    return any(host == d or host.endswith("." + d) for d in domains)


def phishing_reason(text, custom, safe):
    hosts = {m.group(1).lower().strip(".") for m in URL.finditer(text)}
    bait = any(phrase in text for phrase in BAIT_PHRASES)
    foreign = False
    for host in hosts:
        reg = registrable(host)
        if host in OFFICIAL or reg in OFFICIAL or in_domains(host, safe):
            continue
        foreign = True
        if host in BLOCKED or reg in BLOCKED or in_domains(host, custom):
            return "Known scam website"
        flat = host.translate(LEET)
        if BRAND.search(flat):
            tld = host.rsplit(".", 1)[-1]
            if bait or "-" in host or tld in SUSPICIOUS_TLDS or BAIT_WORDS.search(host):
                return "Fake lookalike website"
    if bait and foreign:
        return "Free gift scam message"
    return None


class AutoMod(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._cache = {}
        self._patterns = {}
        self._own = {}
        self._recent = defaultdict(deque)
        self._strikes = defaultdict(deque)
        self._acted = {}

    def forget(self, guild_id):
        self._cache.pop(guild_id, None)

    async def _cfg(self, guild_id):
        now = time.monotonic()
        hit = self._cache.get(guild_id)
        if hit and hit[1] > now:
            return hit[0]
        doc = await automod_col.find_one({"guild_id": str(guild_id)}) or {}
        cfg = {**DEFAULTS, **doc}
        self._cache[guild_id] = (cfg, now + CONFIG_SECONDS)
        return cfg

    async def _save(self, guild_id, update):
        await automod_col.update_one({"guild_id": str(guild_id)}, update, upsert=True)
        self.forget(guild_id)

    def _strike(self, guild_id, user_id, weight):
        now = time.monotonic()
        queue = self._strikes[(guild_id, user_id)]
        while queue and now - queue[0][0] > STRIKE_SECONDS:
            queue.popleft()
        if weight:
            queue.append((now, weight))
        return sum(w for _, w in queue)

    async def _own_codes(self, guild):
        now = time.monotonic()
        hit = self._own.get(guild.id)
        if hit and hit[1] > now:
            return hit[0]
        codes = set()
        if guild.vanity_url_code:
            codes.add(guild.vanity_url_code.lower())
        if guild.me.guild_permissions.manage_guild:
            try:
                codes.update(i.code.lower() for i in await guild.invites())
            except discord.HTTPException:
                pass
        self._own[guild.id] = (codes, now + 60)
        return codes

    async def _foreign_invite(self, guild, text):
        codes = INVITE.findall(text.lower())
        if not codes:
            return False
        own = await self._own_codes(guild)
        return any(code not in own for code in codes)

    def _blocked_word(self, guild_id, words, text):
        key = tuple(words)
        hit = self._patterns.get(guild_id)
        if not hit or hit[0] != key:
            hit = (key, build_pattern(words))
            self._patterns[guild_id] = hit
        pattern = hit[1]
        return bool(pattern and pattern.search(normalize(text)))

    def _spam(self, message, cfg, text):
        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        queue = self._recent[key]
        content = re.sub(r"\s+", " ", text.lower()).strip()
        queue.append((now, content, message))
        while queue and now - queue[0][0] > 15:
            queue.popleft()
        fast = [m for t, _, m in queue if now - t <= 5]
        same = [m for _, c, m in queue if content and c == content]
        mentions = len({u.id for u in message.mentions}) + len(message.role_mentions)

        reason = None
        if len(fast) >= cfg["spam_limit"]:
            reason = f"{len(fast)} messages in 5 seconds"
        elif len(same) >= cfg["dup_limit"]:
            reason = f"Same message sent {len(same)} times"
        elif mentions >= cfg["mention_limit"]:
            reason = f"{mentions} mentions in one message"
        if not reason:
            if len(self._recent) > 5000:
                for stale in [k for k, q in self._recent.items() if not q]:
                    self._recent.pop(stale, None)
            return None, []
        offenders = {m.id: m for m in fast + same}
        offenders[message.id] = message
        queue.clear()
        return reason, list(offenders.values())

    async def _exempt(self, message, cfg):
        author = message.author
        if await secui.is_trusted(self.bot, message.guild, author.id):
            return True
        if str(message.channel.id) in cfg.get("ignored_channels", []):
            return True
        category = getattr(message.channel, "category_id", None)
        if category and str(category) in cfg.get("ignored_channels", []):
            return True
        ignored = set(cfg.get("ignored_roles", []))
        return any(str(r.id) in ignored for r in author.roles)

    async def _delete(self, messages):
        grouped = defaultdict(list)
        for m in messages:
            grouped[m.channel].append(m)
        for channel, items in grouped.items():
            try:
                if len(items) == 1:
                    await items[0].delete()
                else:
                    await channel.delete_messages(items)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    async def _apply(self, guild, member, action):
        kind, minutes = action
        if kind == "ban":
            return await secui.ban_id(guild, member.id, "Auto-Mod: repeated rule breaking")
        if not secui.can_act(guild, member):
            return False
        try:
            if kind == "kick":
                await member.kick(reason="Auto-Mod: repeated rule breaking")
            else:
                await member.timeout(timedelta(minutes=minutes), reason="Auto-Mod: rule broken")
        except discord.HTTPException:
            return False
        return True

    async def _violate(self, message, cfg, title, detail, weight, notice, immediate=None, batch=None):
        guild, member = message.guild, message.author
        await self._delete(batch or [message])
        strikes = self._strike(guild.id, member.id, weight)

        action = None
        if immediate == "timeout":
            action = ("timeout", 1440)
        elif immediate:
            action = (immediate, 0)
        elif strikes >= 4:
            final = cfg["punishment"]
            action = ("timeout", 60) if final == "timeout" else (final, 0)
        elif strikes >= 2:
            action = ("timeout", 10)

        result = "Message removed"
        if action:
            done = await self._apply(guild, member, action)
            names = {"ban": "Banned", "kick": "Kicked", "timeout": f"Timed out for {action[1]} min"}
            result = names[action[0]] if done else f"{names[action[0]]}  (failed)"
            if done and action[0] in ("ban", "kick"):
                self._acted[(guild.id, member.id)] = time.monotonic()

        if not (action and action[0] in ("ban", "kick")):
            line = f"{member.mention}  {notice}"
            if action:
                line += f"\nYou are now in timeout for {action[1]} minutes."
            try:
                await message.channel.send(
                    embed=secui.make(description=line, color=secui.BAD),
                    delete_after=7,
                    allowed_mentions=discord.AllowedMentions(users=[member]),
                )
            except discord.HTTPException:
                pass

        embed = secui.make(f"Auto-Mod  ·  {title}", color=secui.ALERT if action else secui.NEUTRAL)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Person", value=f"{member.mention}\n`{member.id}`", inline=True)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        embed.add_field(name="Result", value=result, inline=True)
        embed.add_field(name="Details", value=detail, inline=False)
        if message.content and title != "Blocked word":
            snippet = message.content.replace("```", "'''")[:300]
            embed.add_field(name="Message", value=f"```{snippet}```", inline=False)
        secui.footer(embed, guild)
        await secui.post(guild, cfg.get("log_channel_id"), embed)
        await log_event(self.bot, guild, "automod", f"{member} - {title} - {result}")

    async def _scan(self, message, edited=False):
        author = message.author
        if author.bot or message.guild is None or not isinstance(author, discord.Member):
            return
        cfg = await self._cfg(message.guild.id)
        if not cfg["enabled"]:
            return
        if await self._exempt(message, cfg):
            return
        text = clean(message.content or "")

        if cfg["phishing"]:
            found = phishing_reason(
                text.lower(), cfg.get("custom_domains", []), cfg.get("safe_domains", [])
            )
            if found:
                return await self._violate(
                    message, cfg, "Phishing link", found, 0,
                    "A dangerous link was removed.", immediate=cfg["phishing_action"],
                )

        if cfg["invites"] and await self._foreign_invite(message.guild, text):
            return await self._violate(
                message, cfg, "Invite link", "Posted an invite to another server", 1,
                "Invite links are not allowed here.",
            )

        if cfg["words"] and self._blocked_word(message.guild.id, cfg.get("blocked_words", []), text):
            return await self._violate(
                message, cfg, "Blocked word", "Used a word on the blocked list", 1,
                "That language is not allowed here.",
            )

        if cfg["spam"] and not edited:
            reason, batch = self._spam(message, cfg, text)
            if reason:
                await self._violate(
                    message, cfg, "Spam", reason, 2,
                    "Please slow down. Spamming is not allowed.", batch=batch,
                )

    @commands.Cog.listener()
    async def on_message(self, message):
        await self._scan(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.content != after.content:
            await self._scan(after, edited=True)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        guild = member.guild
        cfg = await self._cfg(guild.id)
        if not cfg["enabled"] or not cfg["leave_ban"]:
            return
        last = self._acted.get((guild.id, member.id))
        if last and time.monotonic() - last < 60:
            return
        if self._strike(guild.id, member.id, 0) < 2:
            return
        if await secui.ban_id(guild, member.id, "Auto-Mod: left right after breaking the rules"):
            embed = secui.make("Auto-Mod  ·  Banned after leaving", color=secui.ALERT)
            embed.add_field(name="Person", value=f"{member}\n`{member.id}`", inline=True)
            embed.add_field(name="Why", value="Left the server right after spamming or breaking rules", inline=True)
            secui.footer(embed, guild)
            await secui.post(guild, cfg.get("log_channel_id"), embed)

    async def _panel_embed(self, guild):
        self.forget(guild.id)
        cfg = await self._cfg(guild.id)
        log_id = cfg.get("log_channel_id")
        embed = secui.make(
            "🧹  Auto-Mod",
            "Keeps chat clean by removing invites, spam, scam links and bad words, then stepping up the punishment if someone keeps going.",
            secui.ALERT if cfg["enabled"] else secui.NEUTRAL,
        )
        embed.add_field(name="Status", value=secui.state(cfg["enabled"]), inline=True)
        embed.add_field(name="Final punishment", value=PUNISHMENTS[cfg["punishment"]][0], inline=True)
        embed.add_field(name="Log channel", value=f"<#{log_id}>" if log_id else "Not set", inline=True)
        embed.add_field(
            name="Filters",
            value=(
                f"Invite links  ·  {secui.state(cfg['invites'])}\n"
                f"Spam  ·  {secui.state(cfg['spam'])}\n"
                f"Phishing  ·  {secui.state(cfg['phishing'])}\n"
                f"Blocked words  ·  {secui.state(cfg['words'])}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Spam limits",
            value=(
                f"**{cfg['spam_limit']}** messages in 5 seconds\n"
                f"**{cfg['dup_limit']}** repeated messages\n"
                f"**{cfg['mention_limit']}** mentions in one message"
            ),
            inline=True,
        )
        embed.add_field(
            name="Lists",
            value=(
                f"Blocked words  **{len(cfg.get('blocked_words', []))}**\n"
                f"Blocked sites  **{len(cfg.get('custom_domains', []))}**\n"
                f"Ignored places  **{len(cfg.get('ignored_channels', [])) + len(cfg.get('ignored_roles', []))}**"
            ),
            inline=True,
        )
        embed.add_field(name="Scam link action", value=cfg["phishing_action"].title(), inline=True)
        embed.add_field(name="Ban if they leave after spamming", value=secui.state(cfg["leave_ban"]), inline=True)
        embed.add_field(
            name="My access",
            value=secui.access_line(guild, "manage_messages", "moderate_members", "ban_members"),
            inline=True,
        )
        return secui.footer(embed, guild)

    @commands.group(name="automod", aliases=["am"], invoke_without_command=True)
    @ctx_admin()
    async def automod(self, ctx):
        cfg = await self._cfg(ctx.guild.id)
        gid = ctx.guild.id
        panel = secui.Panel(ctx.author.id, self._panel_embed)
        after = self.forget
        panel.add_item(secui.Toggle(automod_col, gid, "enabled", "Auto-Mod", cfg["enabled"], False, 0, after))
        panel.add_item(secui.Toggle(automod_col, gid, "invites", "Invites", cfg["invites"], True, 0, after))
        panel.add_item(secui.Toggle(automod_col, gid, "spam", "Spam", cfg["spam"], True, 0, after))
        panel.add_item(secui.Toggle(automod_col, gid, "phishing", "Phishing", cfg["phishing"], True, 0, after))
        panel.add_item(secui.Toggle(automod_col, gid, "words", "Words", cfg["words"], True, 0, after))
        panel.add_item(secui.Toggle(automod_col, gid, "leave_ban", "Ban on leave", cfg["leave_ban"], True, 1, after))
        panel.add_item(
            secui.Choice(
                automod_col, gid, "punishment", "Choose the final punishment",
                [(k, v[0], v[1]) for k, v in PUNISHMENTS.items()], cfg["punishment"], 2, after,
            )
        )
        panel.message = await secui.send(ctx, await self._panel_embed(ctx.guild), view=panel)

    @automod.command(name="enable")
    @ctx_admin()
    async def am_enable(self, ctx):
        await self._save(ctx.guild.id, {"$set": {"enabled": True}})
        await secui.send(ctx, secui.ok("Auto-Mod is now **on**."))

    @automod.command(name="disable")
    @ctx_admin()
    async def am_disable(self, ctx):
        await self._save(ctx.guild.id, {"$set": {"enabled": False}})
        await secui.send(ctx, secui.ok("Auto-Mod is now **off**."))

    @automod.command(name="logs")
    @ctx_admin()
    async def am_logs(self, ctx, channel: discord.TextChannel = None):
        if channel is None:
            return await secui.send(ctx, secui.fail(f"Use `{ctx.clean_prefix}automod logs #channel`."))
        await self._save(ctx.guild.id, {"$set": {"log_channel_id": str(channel.id)}})
        await secui.send(ctx, secui.ok(f"Auto-Mod alerts will be sent to {channel.mention}."))

    @automod.command(name="punishment")
    @ctx_admin()
    async def am_punishment(self, ctx, mode: str = None):
        if mode is None or mode.lower() not in PUNISHMENTS:
            return await secui.send(ctx, secui.fail(f"Use `{ctx.clean_prefix}automod punishment timeout`, `kick` or `ban`."))
        await self._save(ctx.guild.id, {"$set": {"punishment": mode.lower()}})
        await secui.send(ctx, secui.ok(f"Final punishment is now **{PUNISHMENTS[mode.lower()][0]}**."))

    @automod.command(name="limit")
    @ctx_admin()
    async def am_limit(self, ctx, messages: int = None, repeats: int = None, mentions: int = None):
        if (
            messages is None or repeats is None or mentions is None
            or not 3 <= messages <= 20 or not 2 <= repeats <= 10 or not 3 <= mentions <= 30
        ):
            return await secui.send(
                ctx,
                secui.fail(
                    f"Use `{ctx.clean_prefix}automod limit <messages 3-20> <repeats 2-10> <mentions 3-30>`.\n"
                    f"Example: `{ctx.clean_prefix}automod limit 5 3 6`"
                ),
            )
        await self._save(
            ctx.guild.id,
            {"$set": {"spam_limit": messages, "dup_limit": repeats, "mention_limit": mentions}},
        )
        await secui.send(ctx, secui.ok("Spam limits updated."))

    @automod.command(name="ignore")
    @ctx_admin()
    async def am_ignore(self, ctx, target: typing.Union[discord.TextChannel, discord.CategoryChannel, discord.Role] = None):
        if target is None:
            return await secui.send(ctx, secui.fail(f"Use `{ctx.clean_prefix}automod ignore #channel` or `@role`. Run it again to undo."))
        field = "ignored_roles" if isinstance(target, discord.Role) else "ignored_channels"
        cfg = await self._cfg(ctx.guild.id)
        if str(target.id) in cfg.get(field, []):
            await self._save(ctx.guild.id, {"$pull": {field: str(target.id)}})
            return await secui.send(ctx, secui.ok(f"{target.mention} is no longer ignored."))
        await self._save(ctx.guild.id, {"$addToSet": {field: str(target.id)}})
        await secui.send(ctx, secui.ok(f"Auto-Mod will ignore {target.mention}."))

    @automod.group(name="words", invoke_without_command=True)
    @ctx_admin()
    async def am_words(self, ctx):
        cfg = await self._cfg(ctx.guild.id)
        words = cfg.get("blocked_words", [])
        embed = secui.make("Blocked words", color=secui.NEUTRAL)
        embed.description = "  ".join(f"||{w}||" for w in words)[:3900] or "No blocked words yet."
        embed.add_field(
            name="Commands",
            value=(
                f"`{ctx.clean_prefix}automod words add <word or phrase>`\n"
                f"`{ctx.clean_prefix}automod words remove <word>`\n"
                f"`{ctx.clean_prefix}automod words clear`\n"
                "End a word with `*` to also catch longer words, like `scam*`."
            ),
            inline=False,
        )
        await secui.send(ctx, embed)

    @am_words.command(name="add")
    @ctx_admin()
    async def am_words_add(self, ctx, *, word: str = None):
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        if not word:
            return await secui.send(ctx, secui.fail(f"Use `{ctx.clean_prefix}automod words add <word or phrase>`."))
        word = " ".join(word.lower().split())
        if len(word) > 40:
            return await secui.send(ctx, secui.fail("Keep each word under 40 characters."))
        cfg = await self._cfg(ctx.guild.id)
        current = cfg.get("blocked_words", [])
        if word in current:
            return await secui.send(ctx, secui.fail("That word is already blocked."))
        if len(current) >= MAX_WORDS:
            return await secui.send(ctx, secui.fail(f"The list is full ({MAX_WORDS} words)."))
        await self._save(ctx.guild.id, {"$addToSet": {"blocked_words": word}})
        await secui.send(ctx, secui.ok("Word added to the blocked list."), delete_after=8)

    @am_words.command(name="remove")
    @ctx_admin()
    async def am_words_remove(self, ctx, *, word: str = None):
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        if not word:
            return await secui.send(ctx, secui.fail(f"Use `{ctx.clean_prefix}automod words remove <word>`."))
        word = " ".join(word.lower().split())
        cfg = await self._cfg(ctx.guild.id)
        if word not in cfg.get("blocked_words", []):
            return await secui.send(ctx, secui.fail("That word is not on the list."), delete_after=8)
        await self._save(ctx.guild.id, {"$pull": {"blocked_words": word}})
        await secui.send(ctx, secui.ok("Word removed from the blocked list."), delete_after=8)

    @am_words.command(name="clear")
    @ctx_admin()
    async def am_words_clear(self, ctx):
        await self._save(ctx.guild.id, {"$set": {"blocked_words": []}})
        await secui.send(ctx, secui.ok("The blocked word list is now empty."))

    @automod.group(name="phishing", invoke_without_command=True)
    @ctx_admin()
    async def am_phishing(self, ctx):
        cfg = await self._cfg(ctx.guild.id)
        blocked = cfg.get("custom_domains", [])
        safe = cfg.get("safe_domains", [])
        embed = secui.make("Phishing protection", color=secui.NEUTRAL)
        embed.description = "Catches fake Discord, Steam and Roblox sites, free gift scams and known scam websites."
        embed.add_field(name="Your blocked sites", value="\n".join(f"• `{d}`" for d in blocked[:25]) or "None", inline=True)
        embed.add_field(name="Your safe sites", value="\n".join(f"• `{d}`" for d in safe[:25]) or "None", inline=True)
        embed.add_field(
            name="Commands",
            value=(
                f"`{ctx.clean_prefix}automod phishing block <site>`\n"
                f"`{ctx.clean_prefix}automod phishing unblock <site>`\n"
                f"`{ctx.clean_prefix}automod phishing safe <site>`\n"
                f"`{ctx.clean_prefix}automod phishing unsafe <site>`\n"
                f"`{ctx.clean_prefix}automod phishing action ban|kick|timeout`"
            ),
            inline=False,
        )
        await secui.send(ctx, embed)

    @staticmethod
    def _domain(raw):
        raw = raw.lower().strip().strip("/")
        raw = re.sub(r"^https?://", "", raw).split("/")[0]
        return raw if re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,24}", raw) else None

    async def _domain_edit(self, ctx, raw, field, add, label):
        domain = self._domain(raw or "")
        if not domain:
            return await secui.send(ctx, secui.fail("Enter a site like `example.com`."))
        op = "$addToSet" if add else "$pull"
        await self._save(ctx.guild.id, {op: {field: domain}})
        verb = "added to" if add else "removed from"
        await secui.send(ctx, secui.ok(f"`{domain}` {verb} the {label} list."))

    @am_phishing.command(name="block")
    @ctx_admin()
    async def am_phishing_block(self, ctx, site: str = None):
        await self._domain_edit(ctx, site, "custom_domains", True, "blocked sites")

    @am_phishing.command(name="unblock")
    @ctx_admin()
    async def am_phishing_unblock(self, ctx, site: str = None):
        await self._domain_edit(ctx, site, "custom_domains", False, "blocked sites")

    @am_phishing.command(name="safe")
    @ctx_admin()
    async def am_phishing_safe(self, ctx, site: str = None):
        await self._domain_edit(ctx, site, "safe_domains", True, "safe sites")

    @am_phishing.command(name="unsafe")
    @ctx_admin()
    async def am_phishing_unsafe(self, ctx, site: str = None):
        await self._domain_edit(ctx, site, "safe_domains", False, "safe sites")

    @am_phishing.command(name="action")
    @ctx_admin()
    async def am_phishing_action(self, ctx, mode: str = None):
        if mode is None or mode.lower() not in ("ban", "kick", "timeout"):
            return await secui.send(ctx, secui.fail(f"Use `{ctx.clean_prefix}automod phishing action ban`, `kick` or `timeout`."))
        await self._save(ctx.guild.id, {"$set": {"phishing_action": mode.lower()}})
        await secui.send(ctx, secui.ok(f"Scam link posters will now get: **{mode.title()}**."))


async def setup(bot):
    await bot.add_cog(AutoMod(bot))