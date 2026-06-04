"""
cogs/ai_chat.py — AI chat via Groq.

Controls (in order of priority):
  1. Global kill switch  — ,aimode off          (owner, stored in bot.ai_enabled)
  2. Server-level toggle — ,aiserver off        (admin, stored in DB per guild)
  3. Premium gate        — server or user must have premium
  4. Mention / reply / active session trigger
"""

import discord
from discord.ext import commands
import asyncio, random, os, pytz, time
from datetime import datetime

from groq import Groq
from utils.db import settings_col, premium_col
from utils.helpers import BOT_OWNER_ID, is_premium_user, is_premium_server, ctx_owner, ctx_admin

GROQ_API_KEY = os.getenv("GROQ_API_KEY")


class AIChat(commands.Cog):
    def __init__(self, bot):
        self.bot      = bot
        self.client   = Groq(api_key=GROQ_API_KEY)
        self.memories = {}   # {user_id: [messages]}

        # Ensure global flag exists
        if not hasattr(self.bot, "ai_enabled"):
            self.bot.ai_enabled = True

    # ══════════════════════════════════════════════════════════════════════════
    #  OWNER — Global AI toggle
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @ctx_owner()
    async def aimode(self, ctx, status: str = None):
        """
        Toggle AI chat globally across ALL servers.
        Usage: ,aimode on/off
        """
        if not status:
            state = "ON" if self.bot.ai_enabled else "OFF"
            return await ctx.reply(embed=discord.Embed(
                title="AI Mode — Global",
                description=(
                    f"AI chat is currently **{state}** globally.\n\n"
                    "`,aimode on` — enable for all servers\n"
                    "`,aimode off` — disable for all servers\n"
                    "`,aiserver` — per-server control (admin)"
                ),
                color=0x57F287 if self.bot.ai_enabled else 0xED4245
            ))

        self.bot.ai_enabled = status.lower() in ("on", "true", "1", "yes")
        state = "ON" if self.bot.ai_enabled else "OFF"
        await ctx.reply(embed=discord.Embed(
            title="AI Mode Updated",
            description=(
                f"AI chat globally: **{state}**\n"
                + ("All premium servers can now use AI." if self.bot.ai_enabled
                   else "AI disabled on ALL servers until you turn it back on.")
            ),
            color=0x57F287 if self.bot.ai_enabled else 0xED4245
        ))

    # ══════════════════════════════════════════════════════════════════════════
    #  ADMIN — Per-server AI toggle
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def aiserver(self, ctx, status: str = None):
        """
        Enable or disable AI chat for THIS server only.
        Premium servers can turn it off if they don't want it.
        Usage: ,aiserver on/off
        """
        gs      = await settings_col.find_one({"_id": str(ctx.guild.id)}) or {}
        current = gs.get("ai_enabled", True)   # default: on

        if not status:
            is_prem = await is_premium_server(ctx.guild.id)
            state   = "ON" if current else "OFF"
            embed   = discord.Embed(
                title="AI Chat — Server Settings",
                color=0x57F287 if current else 0xED4245
            )
            embed.add_field(name="Status",  value=f"**{state}**",                                 inline=True)
            embed.add_field(name="Premium", value="Yes" if is_prem else "No (required for AI)",   inline=True)
            embed.add_field(name="Global",  value="ON" if self.bot.ai_enabled else "OFF (owner)", inline=True)
            embed.add_field(
                name="Commands",
                value="`,aiserver on` — enable AI for this server\n`,aiserver off` — disable AI for this server",
                inline=False
            )
            if not is_prem:
                embed.set_footer(text="AI chat requires Happy Premium. Contact the bot owner.")
            return await ctx.reply(embed=embed)

        new_state = status.lower() in ("on", "true", "1", "yes")
        await settings_col.update_one(
            {"_id": str(ctx.guild.id)},
            {"$set": {"ai_enabled": new_state}},
            upsert=True
        )
        state = "ON" if new_state else "OFF"
        await ctx.reply(embed=discord.Embed(
            description=f"AI chat for **{ctx.guild.name}**: **{state}**",
            color=0x57F287 if new_state else 0xED4245
        ))

    # ══════════════════════════════════════════════════════════════════════════
    #  OWNER — Remove a server's AI access (moderation)
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command()
    @ctx_owner()
    async def aiblock(self, ctx, guild_id: str = None):
        """Block AI for a specific server (owner only). Use guild ID."""
        if not guild_id:
            return await ctx.reply("Usage: `,aiblock <guild_id>`")
        await settings_col.update_one(
            {"_id": guild_id},
            {"$set": {"ai_enabled": False}},
            upsert=True
        )
        guild = self.bot.get_guild(int(guild_id))
        name  = guild.name if guild else guild_id
        await ctx.reply(embed=discord.Embed(
            description=f"AI chat blocked for **{name}** (`{guild_id}`).",
            color=0xED4245
        ))

    # ══════════════════════════════════════════════════════════════════════════
    #  CORE — handle_message (called by core.py)
    # ══════════════════════════════════════════════════════════════════════════

    async def handle_message(self, message: discord.Message, sessions: dict, timeout: int):
        """Called by Core cog for every human guild message."""
        channel_id   = message.channel.id
        current_time = time.time()

        # ── Trigger detection ─────────────────────────────────────────────────
        # Never respond to @everyone or @here
        is_mentioned = (
            self.bot.user in message.mentions and
            not message.mention_everyone
        )
        is_reply_to_bot = False
        if message.reference and message.reference.message_id:
            try:
                ref = await message.channel.fetch_message(message.reference.message_id)
                if ref.author.id == self.bot.user.id:
                    is_reply_to_bot = True
            except:
                pass

        is_session = (
            channel_id in sessions and
            current_time - sessions[channel_id] < timeout
        )

        if not (is_mentioned or is_reply_to_bot or is_session):
            return

        # ── 1. Global kill switch ─────────────────────────────────────────────
        if not self.bot.ai_enabled:
            if is_mentioned:
                await message.reply(
                    "AI chat is currently disabled globally. Contact the bot owner.",
                    mention_author=False
                )
            return

        # ── 2. Per-server toggle ──────────────────────────────────────────────
        gs           = await settings_col.find_one({"_id": str(message.guild.id)}) or {}
        server_ai_on = gs.get("ai_enabled", True)   # default on
        if not server_ai_on:
            if is_mentioned:
                await message.reply(
                    "AI chat is disabled on this server. A server admin can enable it with `,aiserver on`.",
                    mention_author=False
                )
            return

        # ── 3. Premium gate ───────────────────────────────────────────────────
        is_owner   = message.author.id == BOT_OWNER_ID
        user_prem  = await is_premium_user(message.author.id)
        srv_prem   = await is_premium_server(message.guild.id)

        # Also check if server has premium role and user has it
        prem_role_id = gs.get("premium_role_id")
        has_prem_role = False
        if prem_role_id:
            role = message.guild.get_role(int(prem_role_id))
            has_prem_role = role in message.author.roles if role else False

        can_use = is_owner or user_prem or srv_prem or has_prem_role

        if not can_use:
            if is_mentioned:
                await message.reply(
                    "AI chat is a **Happy Premium** feature.\n"
                    "Ask the server owner to activate Premium, or contact the bot owner.",
                    mention_author=False
                )
            return

        # ── 4. Start/extend session ───────────────────────────────────────────
        sessions[channel_id] = current_time

        uid          = message.author.id
        clean_prompt = (
            message.content
            .replace(f"<@!{self.bot.user.id}>", "")
            .replace(f"<@{self.bot.user.id}>",  "")
            .strip()
        )
        if not clean_prompt:
            return

        if uid not in self.memories:
            self.memories[uid] = []

        # ── 5. Build context ──────────────────────────────────────────────────
        IST           = pytz.timezone("Asia/Kolkata")
        dt_now        = datetime.now(IST)
        readable_time = dt_now.strftime("%I:%M %p")
        readable_date = dt_now.strftime("%d %B %Y")

        system = (
            "You are Happy, an Indian guy.\n"
            "1. Language: Natural Hinglish (Mix of Hindi/English). No forced slangs.\n"
            "2. Rule: Give logical, helpful, and sensible answers only.\n"
            "3. Style: Keep it very short (1 line). Chat like a normal person on Discord.\n"
            "4. Persona: Friendly but not stupid. If a question is serious, answer simply.\n"
            "5. No AI behavior: Don't say 'As an AI' or 'I'm here to help.'\n"
            "Emojis: Use rarely (1-2 max). No bot-like sparkles.\n"
            f"Current Date: {readable_date}\n"
            f"Current Time: {readable_time}"
        )

        msgs = [{"role": "system", "content": system}]
        for h in self.memories[uid][-6:]:
            msgs.append(h)
        msgs.append({"role": "user", "content": clean_prompt})

        # ── 6. Generate response ──────────────────────────────────────────────
        try:
            await asyncio.sleep(random.uniform(1.0, 2.0))
            async with message.channel.typing():
                wait = min(random.uniform(2.0, 4.5) + len(clean_prompt) / 10, 7.0)
                await asyncio.sleep(wait)

                resp  = self.client.chat.completions.create(
                    messages=msgs,
                    model="llama-3.3-70b-versatile",
                    max_tokens=50,
                    temperature=0.7
                )
                reply = resp.choices[0].message.content

                self.memories[uid].append({"role": "user",      "content": clean_prompt})
                self.memories[uid].append({"role": "assistant", "content": reply})

                # Keep memory trim
                if len(self.memories[uid]) > 20:
                    self.memories[uid] = self.memories[uid][-20:]

                await message.reply(reply, mention_author=False)

        except Exception as e:
            print(f"[AIChat] Groq error: {e}")


async def setup(bot):
    await bot.add_cog(AIChat(bot))