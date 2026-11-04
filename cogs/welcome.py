import asyncio

import discord
from discord.ext import commands

from utils.db import settings_col, embed_col
from utils.helpers import update_server_data
from utils import embeds as style

LABELS = {"welcome": "Welcome", "bye": "Leave", "boost": "Boost"}
MAX_TEXT = 1000
MAX_POJ_CHANNELS = 5
SWITCH_ON = {"on", "yes", "true", "enable", "enabled"}
SWITCH_OFF = {"off", "no", "false", "disable", "disabled"}


class Greeting(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.running = set()

    def cog_unload(self):
        for task in list(self.running):
            task.cancel()

    async def cog_check(self, ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if ctx.command.qualified_name == "variables":
            return True
        if not ctx.author.guild_permissions.administrator:
            raise commands.MissingPermissions(["administrator"])
        return True

    async def _settings(self, guild_id):
        return await settings_col.find_one({"_id": str(guild_id)}) or {}

    def _default_embed(self, kind, member, guild, message, picture):
        embed = discord.Embed(color=style.NEUTRAL)
        icon = guild.icon.url if guild.icon else None
        name = style.clean_name(member.display_name)

        if kind == "welcome":
            embed.set_author(name=f"Welcome to {guild.name}", icon_url=icon)
            fallback = f"Hey {member.mention}, we are happy to have you here.\nYou are member **#{guild.member_count}**."
            footer = f"ID: {member.id}"
        else:
            embed.set_author(name=f"{member.display_name} has left", icon_url=member.display_avatar.url)
            fallback = f"**{name}** has left the server.\nWe now have **{guild.member_count}** members."
            joined = member.joined_at.strftime("%d %b %Y") if member.joined_at else "Unknown"
            footer = f"ID: {member.id}  •  Joined {joined}"

        embed.description = style.trim(style.render(message, member, guild), 4000) if message else fallback
        embed.set_thumbnail(url=member.display_avatar.url)
        if picture and style.is_link(picture):
            embed.set_image(url=picture)
        embed.set_footer(text=footer)
        return embed

    def _event_embed(self, kind, member, guild, settings):
        draft = settings.get(f"{kind}_embed")
        if isinstance(draft, dict) and style.has_content(draft):
            return style.build_embed(draft, member, guild)
        return self._default_embed(kind, member, guild, settings.get(f"{kind}_custom_msg"), settings.get(f"{kind}_gif"))

    def _look(self, kind, settings):
        if isinstance(settings.get(f"{kind}_embed"), dict):
            return f"Saved embed {style.code(settings.get(f'{kind}_embed_name') or 'draft', 30)}"
        if settings.get(f"{kind}_custom_msg"):
            return "Custom message"
        return "Default look"

    async def _panel(self, ctx, kind):
        settings = await self._settings(ctx.guild.id)
        label = LABELS[kind]
        p = ctx.clean_prefix
        channel_id = settings.get(f"{kind}_channel")
        status = "🟢 On" if settings.get(f"{kind}_enabled") else "🔴 Off"
        message = settings.get(f"{kind}_custom_msg")
        picture = settings.get(f"{kind}_gif")

        embed = style.card(f"{label} Messages", "Choose where members are greeted and how the message looks.")
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Channel", value=f"<#{channel_id}>" if channel_id else "Not set", inline=True)
        embed.add_field(name="Look", value=self._look(kind, settings), inline=True)
        embed.add_field(name="Text message", value=style.code(message, 120) if message else "None", inline=False)
        embed.add_field(name="Picture", value=f"[Open picture]({picture})" if picture else "None", inline=True)
        if kind == "welcome":
            embed.add_field(name="Mention member", value="Yes" if settings.get("welcome_ping") else "No", inline=True)

        lines = [
            f"`{p}{kind} set #channel`  Pick the channel",
            f"`{p}{kind} on` / `{p}{kind} off`  Turn it on or off",
            f"`{p}{kind} message <text>`  Write a text message",
            f"`{p}{kind} embed <draft>`  Use a saved embed",
            f"`{p}{kind} embed clear`  Stop using the embed",
            f"`{p}{kind} picture <link>`  Add a picture",
        ]
        if kind == "welcome":
            lines.append(f"`{p}{kind} ping on` / `off`  Mention the member above the message")
        lines += [
            f"`{p}{kind} reset`  Go back to the default look",
            f"`{p}{kind} test`  See a preview",
        ]
        embed.add_field(name="Commands", value="\n".join(lines), inline=False)
        embed.set_footer(text=f"Make an embed first with {p}embed create <name>")
        await style.respond(ctx, embed=embed)

    async def _set_channel(self, ctx, kind, channel):
        if not channel:
            return await style.respond(ctx, embed=style.usage(f"{ctx.clean_prefix}{kind} set #channel"))
        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            return await style.respond(ctx, embed=style.failure(f"I need permission to see, send messages and add embeds in {channel.mention}."))
        await update_server_data(ctx.guild.id, f"{kind}_channel", channel.id)
        await update_server_data(ctx.guild.id, f"{kind}_enabled", True)
        await style.respond(ctx, embed=style.success(f"{LABELS[kind]} messages will now go to {channel.mention}."))

    async def _remove_channel(self, ctx, kind):
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$unset": {f"{kind}_channel": ""}, "$set": {f"{kind}_enabled": False}},
            upsert=True,
        )
        await style.respond(ctx, embed=style.success(f"The {LABELS[kind].lower()} channel was removed and the feature is off."))

    async def _turn(self, ctx, kind, on):
        if on:
            settings = await self._settings(ctx.guild.id)
            if not settings.get(f"{kind}_channel"):
                return await style.respond(ctx, embed=style.failure(f"Pick a channel first with `{ctx.clean_prefix}{kind} set #channel`."))
        await update_server_data(ctx.guild.id, f"{kind}_enabled", on)
        state = "on" if on else "off"
        await style.respond(ctx, embed=style.success(f"{LABELS[kind]} messages are now {state}."))

    async def _set_message(self, ctx, kind, text):
        if not text:
            return await style.respond(ctx, embed=style.usage(f"{ctx.clean_prefix}{kind} message <text>", f"**Words you can use**\n{style.variables_text()}"))
        text = text.strip()
        if len(text) > MAX_TEXT:
            return await style.respond(ctx, embed=style.failure(f"That message is too long. Please keep it under {MAX_TEXT} characters."))
        await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$set": {f"{kind}_custom_msg": text}}, upsert=True)
        settings = await self._settings(ctx.guild.id)
        note = ""
        if isinstance(settings.get(f"{kind}_embed"), dict):
            note = f"\n\nA saved embed is still in use, so this text is ignored. Run `{ctx.clean_prefix}{kind} embed clear` to use the text."
        await style.respond(ctx, embed=style.success(f"Your new message:\n{style.code(text, 300)}{note}", "Saved"))

    async def _set_picture(self, ctx, kind, link):
        if not link or not style.is_link(link):
            return await style.respond(ctx, embed=style.failure("Please send a picture link that starts with `https://`."))
        await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$set": {f"{kind}_gif": link.strip()}}, upsert=True)
        await style.respond(ctx, embed=style.success("The picture was saved.", "Saved"))

    async def _use_embed(self, ctx, kind, name):
        p = ctx.clean_prefix
        if not name:
            return await style.respond(ctx, embed=style.usage(f"{p}{kind} embed <draft name>", f"Make a draft with `{p}embed create <name>`. To stop using an embed, type `{p}{kind} embed clear`."))

        if name.lower() == "clear":
            await settings_col.update_one(
                {"_id": str(ctx.guild.id)},
                {"$unset": {f"{kind}_embed": "", f"{kind}_embed_name": ""}},
                upsert=True,
            )
            return await style.respond(ctx, embed=style.success(f"The {LABELS[kind].lower()} message no longer uses an embed."))

        if not style.NAME_PATTERN.match(name):
            return await style.respond(ctx, embed=style.failure("A draft name can only use letters, numbers, dashes and underscores."))

        draft = await embed_col.find_one({"guild_id": str(ctx.guild.id), "author_id": str(ctx.author.id), "draft_name": name})
        if not draft:
            return await style.respond(ctx, embed=style.failure(f"You have no draft named `{name}`. Make one with `{p}embed create {name}`."))

        clean, error = style.clean_draft(draft)
        if error:
            return await style.respond(ctx, embed=style.failure(error))
        if not style.has_content(clean):
            return await style.respond(ctx, embed=style.failure("That draft is empty. Add some text or a picture first."))

        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {f"{kind}_embed": clean, f"{kind}_embed_name": name}},
            upsert=True,
        )
        preview = style.build_embed(clean, ctx.author, ctx.guild)
        await style.respond(
            ctx,
            content=f"✓  **{LABELS[kind]} embed saved.** It was copied from `{name}`. If you change the draft later, run this command again.",
            embed=preview,
            allowed_mentions=style.NO_PINGS,
        )

    async def _reset(self, ctx, kind):
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$unset": {
                f"{kind}_custom_msg": "",
                f"{kind}_gif": "",
                f"{kind}_embed": "",
                f"{kind}_embed_name": "",
            }},
            upsert=True,
        )
        await style.respond(ctx, embed=style.success(f"The {LABELS[kind].lower()} message is back to the default look."))

    async def _test(self, ctx, kind):
        settings = await self._settings(ctx.guild.id)
        embed = self._event_embed(kind, ctx.author, ctx.guild, settings)
        await style.respond(
            ctx,
            content=f"**Preview**  ·  this is how the {LABELS[kind].lower()} message will look.",
            embed=embed,
            allowed_mentions=style.NO_PINGS,
        )

    @commands.group(name="welcome", invoke_without_command=True)
    async def welcome(self, ctx):
        await self._panel(ctx, "welcome")

    @welcome.command(name="set", aliases=["setchannel"])
    async def welcome_set(self, ctx, channel: discord.TextChannel = None):
        await self._set_channel(ctx, "welcome", channel)

    @welcome.command(name="removechannel")
    async def welcome_removechannel(self, ctx):
        await self._remove_channel(ctx, "welcome")

    @welcome.command(name="enable", aliases=["on"])
    async def welcome_enable(self, ctx):
        await self._turn(ctx, "welcome", True)

    @welcome.command(name="disable", aliases=["off"])
    async def welcome_disable(self, ctx):
        await self._turn(ctx, "welcome", False)

    @welcome.command(name="message", aliases=["setmessage", "msg"])
    async def welcome_message(self, ctx, *, text: str = None):
        await self._set_message(ctx, "welcome", text)

    @welcome.command(name="gif", aliases=["picture", "image"])
    async def welcome_gif(self, ctx, link: str = None):
        await self._set_picture(ctx, "welcome", link)

    @welcome.command(name="embed")
    async def welcome_embed(self, ctx, name: str = None):
        await self._use_embed(ctx, "welcome", name)

    @welcome.command(name="ping")
    async def welcome_ping(self, ctx, state: str = None):
        value = (state or "").lower()
        if value not in SWITCH_ON | SWITCH_OFF:
            return await style.respond(ctx, embed=style.usage(f"{ctx.clean_prefix}welcome ping on", "Use `on` or `off`."))
        await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$set": {"welcome_ping": value in SWITCH_ON}}, upsert=True)
        text = "The new member will be mentioned above the welcome message." if value in SWITCH_ON else "The new member will not be mentioned."
        await style.respond(ctx, embed=style.success(text))

    @welcome.command(name="reset")
    async def welcome_reset(self, ctx):
        await self._reset(ctx, "welcome")

    @welcome.command(name="test")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def welcome_test(self, ctx):
        await self._test(ctx, "welcome")

    @commands.group(name="bye", aliases=["leave"], invoke_without_command=True)
    async def bye(self, ctx):
        await self._panel(ctx, "bye")

    @bye.command(name="set", aliases=["setchannel"])
    async def bye_set(self, ctx, channel: discord.TextChannel = None):
        await self._set_channel(ctx, "bye", channel)

    @bye.command(name="removechannel")
    async def bye_removechannel(self, ctx):
        await self._remove_channel(ctx, "bye")

    @bye.command(name="enable", aliases=["on"])
    async def bye_enable(self, ctx):
        await self._turn(ctx, "bye", True)

    @bye.command(name="disable", aliases=["off"])
    async def bye_disable(self, ctx):
        await self._turn(ctx, "bye", False)

    @bye.command(name="message", aliases=["setmessage", "msg"])
    async def bye_message(self, ctx, *, text: str = None):
        await self._set_message(ctx, "bye", text)

    @bye.command(name="gif", aliases=["picture", "image"])
    async def bye_gif(self, ctx, link: str = None):
        await self._set_picture(ctx, "bye", link)

    @bye.command(name="embed")
    async def bye_embed(self, ctx, name: str = None):
        await self._use_embed(ctx, "bye", name)

    @bye.command(name="reset")
    async def bye_reset(self, ctx):
        await self._reset(ctx, "bye")

    @bye.command(name="test")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def bye_test(self, ctx):
        await self._test(ctx, "bye")

    @commands.group(name="boost", invoke_without_command=True)
    async def boost(self, ctx):
        settings = await self._settings(ctx.guild.id)
        p = ctx.clean_prefix
        channel_id = settings.get("boost_channel")
        message = settings.get("boost_custom_msg")

        embed = style.card("Boost Messages", "Say thank you when someone boosts the server.")
        embed.add_field(name="Status", value="🟢 On" if settings.get("boost_enabled") else "🔴 Off", inline=True)
        embed.add_field(name="Channel", value=f"<#{channel_id}>" if channel_id else "Not set", inline=True)
        embed.add_field(name="Message", value=style.code(message, 120) if message else "Default message", inline=False)
        embed.add_field(
            name="Commands",
            value=(
                f"`{p}boost set #channel`  Pick the channel\n"
                f"`{p}boost on` / `{p}boost off`  Turn it on or off\n"
                f"`{p}boost message <text>`  Write a message\n"
                f"`{p}boost removechannel`  Clear the channel\n"
                f"`{p}boost test`  See a preview"
            ),
            inline=False,
        )
        await style.respond(ctx, embed=embed)

    @boost.command(name="set", aliases=["setchannel"])
    async def boost_set(self, ctx, channel: discord.TextChannel = None):
        await self._set_channel(ctx, "boost", channel)

    @boost.command(name="removechannel")
    async def boost_removechannel(self, ctx):
        await self._remove_channel(ctx, "boost")

    @boost.command(name="enable", aliases=["on"])
    async def boost_enable(self, ctx):
        await self._turn(ctx, "boost", True)

    @boost.command(name="disable", aliases=["off"])
    async def boost_disable(self, ctx):
        await self._turn(ctx, "boost", False)

    @boost.command(name="message", aliases=["setmessage"])
    async def boost_message(self, ctx, *, text: str = None):
        if not text:
            return await style.respond(ctx, embed=style.usage(f"{ctx.clean_prefix}boost message <text>", f"**Words you can use**\n{style.variables_text()}"))
        text = text.strip()
        if len(text) > MAX_TEXT:
            return await style.respond(ctx, embed=style.failure(f"That message is too long. Please keep it under {MAX_TEXT} characters."))
        await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$set": {"boost_custom_msg": text}}, upsert=True)
        await style.respond(ctx, embed=style.success("The boost message was updated.", "Saved"))

    @boost.command(name="test")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def boost_test(self, ctx):
        settings = await self._settings(ctx.guild.id)
        await style.respond(
            ctx,
            content="**Preview**  ·  this is how the boost message will look.",
            embed=self._boost_embed(ctx.author, ctx.guild, settings),
            allowed_mentions=style.NO_PINGS,
        )

    def _boost_embed(self, member, guild, settings):
        text = settings.get("boost_custom_msg") or "{user} just boosted **{server}**. Thank you so much!"
        embed = style.card("Server Boost", style.trim(style.render(text, member, guild), 4000), style.ACCENT)
        embed.set_thumbnail(url=member.display_avatar.url)
        return embed

    @commands.group(name="joindm", invoke_without_command=True)
    async def joindm(self, ctx):
        settings = await self._settings(ctx.guild.id)
        p = ctx.clean_prefix
        message = settings.get("joindm_msg")

        embed = style.card("Welcome Direct Message", "Send a private message to every new member.")
        embed.add_field(name="Status", value="🟢 On" if settings.get("joindm_enabled") else "🔴 Off", inline=True)
        embed.add_field(name="Message", value=style.code(message, 120) if message else "Default message", inline=False)
        embed.add_field(
            name="Commands",
            value=(
                f"`{p}joindm message <text>`  Write the private message\n"
                f"`{p}joindm on` / `{p}joindm off`  Turn it on or off\n"
                f"`{p}joindm test`  Send yourself a test"
            ),
            inline=False,
        )
        await style.respond(ctx, embed=embed)

    @joindm.command(name="message", aliases=["setmessage"])
    async def joindm_message(self, ctx, *, text: str = None):
        if not text:
            return await style.respond(ctx, embed=style.usage(f"{ctx.clean_prefix}joindm message <text>", f"**Words you can use**\n{style.variables_text()}"))
        text = text.strip()
        if len(text) > MAX_TEXT:
            return await style.respond(ctx, embed=style.failure(f"That message is too long. Please keep it under {MAX_TEXT} characters."))
        await settings_col.update_one({"_id": str(ctx.guild.id)}, {"$set": {"joindm_msg": text, "joindm_enabled": True}}, upsert=True)
        await style.respond(ctx, embed=style.success("The private message was saved and turned on.", "Saved"))

    @joindm.command(name="enable", aliases=["on"])
    async def joindm_enable(self, ctx):
        await update_server_data(ctx.guild.id, "joindm_enabled", True)
        await style.respond(ctx, embed=style.success("Private messages are now on."))

    @joindm.command(name="disable", aliases=["off"])
    async def joindm_disable(self, ctx):
        await update_server_data(ctx.guild.id, "joindm_enabled", False)
        await style.respond(ctx, embed=style.success("Private messages are now off."))

    @joindm.command(name="test")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def joindm_test(self, ctx):
        settings = await self._settings(ctx.guild.id)
        text = settings.get("joindm_msg") or "Welcome to **{server}**, {username}!"
        try:
            await ctx.author.send(style.trim(style.render(text, ctx.author, ctx.guild), 2000), allowed_mentions=style.NO_PINGS)
        except discord.HTTPException:
            return await style.respond(ctx, embed=style.failure("I could not message you. Please open your private messages and try again."))
        await style.respond(ctx, embed=style.success("Check your private messages for the test."))

    @commands.group(name="poj", invoke_without_command=True)
    async def poj(self, ctx):
        p = ctx.clean_prefix
        embed = style.card("Ping On Join", "Mention new members in a channel, then remove the mention right away.")
        embed.add_field(
            name="Commands",
            value=(
                f"`{p}poj start #channel [seconds]`  Start pinging in a channel\n"
                f"`{p}poj stop #channel`  Stop pinging in a channel\n"
                f"`{p}poj list`  See all active channels"
            ),
            inline=False,
        )
        embed.set_footer(text=f"You can use up to {MAX_POJ_CHANNELS} channels. The wait can be 0 to 300 seconds.")
        await style.respond(ctx, embed=embed)

    @poj.command(name="start")
    async def poj_start(self, ctx, channel: discord.TextChannel = None, delay: int = 0):
        if not channel:
            return await style.respond(ctx, embed=style.usage(f"{ctx.clean_prefix}poj start #channel [seconds]"))
        if delay < 0 or delay > 300:
            return await style.respond(ctx, embed=style.failure("The wait must be between 0 and 300 seconds."))
        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.view_channel and perms.send_messages):
            return await style.respond(ctx, embed=style.failure(f"I cannot send messages in {channel.mention}."))

        settings = await self._settings(ctx.guild.id)
        current = settings.get("poj_channels", {})
        if str(channel.id) not in current and len(current) >= MAX_POJ_CHANNELS:
            return await style.respond(ctx, embed=style.failure(f"You can only use {MAX_POJ_CHANNELS} channels. Stop one first."))

        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {f"poj_channels.{channel.id}": {"delay": delay}}},
            upsert=True,
        )
        await style.respond(ctx, embed=style.success(f"New members will be mentioned in {channel.mention} after {delay} seconds.", "Saved"))

    @poj.command(name="stop")
    async def poj_stop(self, ctx, channel: discord.TextChannel = None):
        if not channel:
            return await style.respond(ctx, embed=style.usage(f"{ctx.clean_prefix}poj stop #channel"))
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$unset": {f"poj_channels.{channel.id}": ""}},
            upsert=True,
        )
        await style.respond(ctx, embed=style.success(f"Stopped pinging in {channel.mention}.", "Saved"))

    @poj.command(name="list")
    async def poj_list(self, ctx):
        settings = await self._settings(ctx.guild.id)
        data = settings.get("poj_channels", {})
        if not data:
            return await style.respond(ctx, embed=style.card("Ping On Join", "No channels are set up yet."))
        lines = [f"<#{channel_id}>  ·  waits `{config.get('delay', 0)}s`" for channel_id, config in data.items()]
        await style.respond(ctx, embed=style.card("Ping On Join", "\n".join(lines)))

    @commands.command(name="variables", aliases=["vars"])
    async def variables(self, ctx):
        embed = style.card("Words You Can Use", style.variables_text())
        embed.set_footer(text="Put these inside your message or embed. They change for each member.")
        await style.respond(ctx, private=False, embed=embed)

    async def _announce(self, kind, member, settings):
        if not settings.get(f"{kind}_enabled"):
            return
        try:
            channel = member.guild.get_channel(int(settings.get(f"{kind}_channel")))
        except (TypeError, ValueError):
            return
        if not isinstance(channel, discord.TextChannel):
            return
        perms = channel.permissions_for(member.guild.me)
        if not (perms.send_messages and perms.embed_links):
            return

        embed = self._event_embed(kind, member, member.guild, settings)
        ping = kind == "welcome" and settings.get("welcome_ping")
        try:
            await channel.send(
                content=member.mention if ping else None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=[member]) if ping else style.NO_PINGS,
            )
        except discord.HTTPException:
            pass

    async def _send_private(self, member, settings):
        if member.bot or not settings.get("joindm_enabled") or not settings.get("joindm_msg"):
            return
        text = style.trim(style.render(settings["joindm_msg"], member, member.guild), 2000)
        try:
            await member.send(text, allowed_mentions=style.NO_PINGS)
        except discord.HTTPException:
            pass

    async def _ping_later(self, member, channel_id, delay):
        if delay > 0:
            await asyncio.sleep(delay)
        guild = member.guild
        if guild.get_member(member.id) is None:
            return
        try:
            channel = guild.get_channel(int(channel_id))
        except (TypeError, ValueError):
            return
        if not isinstance(channel, discord.TextChannel):
            return
        if not channel.permissions_for(guild.me).send_messages:
            return
        try:
            await channel.send(member.mention, delete_after=1, allowed_mentions=discord.AllowedMentions(users=[member]))
        except discord.HTTPException:
            pass

    def _start_pings(self, member, settings):
        if member.bot:
            return
        data = settings.get("poj_channels", {})
        for channel_id, config in list(data.items())[:MAX_POJ_CHANNELS]:
            try:
                delay = max(0, min(int(config.get("delay", 0)), 300))
            except (TypeError, ValueError, AttributeError):
                delay = 0
            task = asyncio.create_task(self._ping_later(member, channel_id, delay))
            self.running.add(task)
            task.add_done_callback(self.running.discard)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        try:
            settings = await self._settings(member.guild.id)
        except Exception:
            return
        await self._announce("welcome", member, settings)
        await self._send_private(member, settings)
        self._start_pings(member, settings)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        try:
            settings = await self._settings(member.guild.id)
        except Exception:
            return
        await self._announce("bye", member, settings)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.premium_since is not None or after.premium_since is None:
            return
        try:
            settings = await self._settings(after.guild.id)
        except Exception:
            return
        if not settings.get("boost_enabled"):
            return
        try:
            channel = after.guild.get_channel(int(settings.get("boost_channel")))
        except (TypeError, ValueError):
            return
        if not isinstance(channel, discord.TextChannel):
            return
        perms = channel.permissions_for(after.guild.me)
        if not (perms.send_messages and perms.embed_links):
            return
        try:
            await channel.send(embed=self._boost_embed(after, after.guild, settings), allowed_mentions=style.NO_PINGS)
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Greeting(bot))