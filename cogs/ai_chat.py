"""
cogs/ai_chat.py — AI chat via Groq (Premium only).
The Core cog calls handle_message(); this cog owns the AI state.
"""

import discord
from discord.ext import commands
import asyncio, random, os, pytz
from datetime import datetime
from groq import Groq

from utils.helpers import BOT_OWNER_ID, is_premium_user, is_premium_server

GROQ_API_KEY = os.getenv("GROQ_API_KEY")


class AIChat(commands.Cog):
    def __init__(self, bot):
        self.bot     = bot
        self.client  = Groq(api_key=GROQ_API_KEY)
        self.memories = {}   # {user_id: [messages]}

    async def handle_message(self, message: discord.Message, sessions: dict, timeout: int):
        """Called by Core cog for every human message."""
        channel_id   = message.channel.id
        current_time = __import__("time").time()

        # Only respond to direct mentions or replies — never @everyone / @here
        is_mentioned = (
            self.bot.user in message.mentions and
            not message.mention_everyone  # blocks @everyone and @here
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

        # Premium gate
        can_use = (
            message.author.id == BOT_OWNER_ID or
            await is_premium_user(message.author.id) or
            await is_premium_server(message.guild.id)
        )

        if not can_use:
            if is_mentioned:
                await message.reply(
                    "AI chat is a **Happy Premium** feature. "
                    "Contact the bot owner to activate it for this server.",
                    mention_author=False
                )
            return

        sessions[channel_id] = current_time

        uid          = message.author.id
        clean_prompt = (
            message.content
            .replace(f"<@!{self.bot.user.id}>", "")
            .replace(f"<@{self.bot.user.id}>", "")
            .strip()
        )
        if not clean_prompt:
            return

        if uid not in self.memories:
            self.memories[uid] = []

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
            "Emojis: Use rarely (1–2 max). No bot-like sparkles.\n"
            f"Current Date: {readable_date}\n"
            f"Current Time: {readable_time}"
        )

        msgs = [{"role": "system", "content": system}]
        for h in self.memories[uid][-6:]:
            msgs.append(h)
        msgs.append({"role": "user", "content": clean_prompt})

        try:
            await asyncio.sleep(random.uniform(1.0, 2.0))
            async with message.channel.typing():
                wait = min(random.uniform(2.0, 4.5) + len(clean_prompt) / 10, 7.0)
                await asyncio.sleep(wait)
                resp  = self.client.chat.completions.create(
                    messages=msgs,
                    model="llama-3.3-70b-versatile",
                    max_tokens=100,
                    temperature=0.7
                )
                reply = resp.choices[0].message.content
                self.memories[uid].append({"role": "user",      "content": clean_prompt})
                self.memories[uid].append({"role": "assistant", "content": reply})
                if len(self.memories[uid]) > 20:
                    self.memories[uid] = self.memories[uid][-20:]
                await message.reply(reply, mention_author=False)
        except Exception as e:
            print(f"[AIChat] Groq error: {e}")

    # aimode and maintenance commands live in cogs/admin.py


async def setup(bot):
    await bot.add_cog(AIChat(bot))