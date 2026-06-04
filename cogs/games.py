"""
cogs/games.py — Server games: Number Guess, Counting, Word Guess.
All games are channel-based and multiplayer-friendly.
"""

import discord
from discord.ext import commands
import asyncio, random
from datetime import datetime, timezone

from utils.db import db
from utils.helpers import ctx_mod

# ── Collections ───────────────────────────────────────────────────────────────
counting_col  = db["counting"]    # {guild_id, channel_id, count, last_user_id}
wordguess_col = db["word_guess"]  # {channel_id, word, guessed, wrong, players}
numguess_col  = db["num_guess"]   # {channel_id, number, attempts, players}

# ── Word list (common 4-8 letter words) ───────────────────────────────────────
WORDS = [
    "python","discord","server","gaming","friend","castle","dragon","planet",
    "bridge","candle","flower","garden","hammer","island","jungle","knight",
    "lemon","mirror","napkin","orange","pepper","rabbit","silver","sunset",
    "tennis","umbrella","valley","window","yellow","zipper","anchor","button",
    "circle","donkey","engine","falcon","goblin","hunter","igloo","jacket",
    "kitten","lantern","mango","needle","oyster","pillow","quartz","rocket",
    "salmon","tunnel","violet","walnut","xylem","yogurt","zenith","alpine",
    "breeze","chrome","divine","echoes","frozen","gravel","hollow","ignite",
    "jasper","kindle","legend","mystic","nebula","oracle","portal","quiver",
    "radiant","scarlet","throne","ultra","vortex","winter","xylophone",
]


class Games(commands.Cog):
    def __init__(self, bot):
        self.bot          = bot
        self._num_games   = {}   # {channel_id: {number, attempts, max_attempts, players}}
        self._word_games  = {}   # {channel_id: {word, guessed: set, wrong: set, players}}

    # ══════════════════════════════════════════════════════════════════════════
    #  NUMBER GUESSING GAME
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(invoke_without_command=True)
    async def numguess(self, ctx):
        """Number guessing game. Sub-commands: start, stop"""
        embed = discord.Embed(title="Number Guess", color=0x2B2D31)
        embed.add_field(
            name="Commands",
            value=(
                "`,numguess start [max]` — start a game (default max: 100)\n"
                "`,numguess stop` — end the current game"
            ),
            inline=False
        )
        embed.add_field(name="How to play", value="Bot picks a number. Type your guess in chat.", inline=False)
        await ctx.reply(embed=embed)

    @numguess.command(name="start")
    async def numguess_start(self, ctx, max_num: int = 100):
        cid = ctx.channel.id
        if cid in self._num_games:
            return await ctx.reply("A game is already running in this channel. Use `,numguess stop` to end it.")

        if max_num < 5 or max_num > 10000:
            return await ctx.reply("Max number must be between 5 and 10,000.")

        number       = random.randint(1, max_num)
        max_attempts = min(10, max(5, max_num // 10))

        self._num_games[cid] = {
            "number":       number,
            "attempts":     0,
            "max_attempts": max_attempts,
            "players":      set(),
            "host":         ctx.author.id,
        }

        embed = discord.Embed(
            title="Number Guess — Started!",
            description=(
                f"I'm thinking of a number between **1** and **{max_num}**.\n"
                f"You have **{max_attempts}** attempts. Type your guess in this channel!"
            ),
            color=0x5865F2
        )
        embed.set_footer(text=f"Started by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @numguess.command(name="stop")
    @ctx_mod()
    async def numguess_stop(self, ctx):
        cid  = ctx.channel.id
        game = self._num_games.pop(cid, None)
        if not game:
            return await ctx.reply("No active number guess game in this channel.")
        await ctx.reply(embed=discord.Embed(
            description=f"Game ended. The number was **{game['number']}**.",
            color=0x2B2D31
        ))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        cid = message.channel.id

        # ── Number guessing ───────────────────────────────────────────────────
        if cid in self._num_games:
            game = self._num_games[cid]

            # Only process if message is a plain number
            if message.content.strip().lstrip("-").isdigit():
                guess = int(message.content.strip())
                game["attempts"] += 1
                game["players"].add(message.author.id)
                number  = game["number"]
                left    = game["max_attempts"] - game["attempts"]

                if guess == number:
                    del self._num_games[cid]
                    embed = discord.Embed(
                        title="Correct!",
                        description=(
                            f"{message.author.mention} guessed **{number}** correctly!\n"
                            f"Solved in **{game['attempts']}** attempt(s) with "
                            f"**{len(game['players'])}** player(s)."
                        ),
                        color=0x57F287
                    )
                    await message.channel.send(embed=embed)

                elif game["attempts"] >= game["max_attempts"]:
                    del self._num_games[cid]
                    embed = discord.Embed(
                        title="Game Over",
                        description=f"Out of attempts! The number was **{number}**.",
                        color=0xED4245
                    )
                    await message.channel.send(embed=embed)

                else:
                    hint  = "Too high!" if guess > number else "Too low!"
                    bar   = "🟩" * (game["attempts"]) + "⬜" * left
                    embed = discord.Embed(
                        description=f"**{hint}** {bar} — {left} attempt(s) left.",
                        color=0xFEE75C
                    )
                    await message.reply(embed=embed, mention_author=False)

        # ── Counting game ─────────────────────────────────────────────────────
        cfg = await counting_col.find_one({"guild_id": str(message.guild.id)})
        if cfg and cfg.get("channel_id") and str(cid) == str(cfg["channel_id"]):
            await self._handle_counting(message, cfg)

        # ── Word guess ────────────────────────────────────────────────────────
        if cid in self._word_games:
            await self._handle_word_guess(message)

    # ══════════════════════════════════════════════════════════════════════════
    #  COUNTING GAME
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(invoke_without_command=True)
    async def counting(self, ctx):
        """Counting channel game. Sub-commands: setup, reset, stats"""
        cfg = await counting_col.find_one({"guild_id": str(ctx.guild.id)})
        if not cfg:
            return await ctx.reply(
                "Counting not set up. Admin can run `,counting setup #channel`."
            )
        ch  = ctx.guild.get_channel(int(cfg["channel_id"]))
        embed = discord.Embed(title="Counting Game", color=0x2B2D31)
        embed.add_field(name="Channel",     value=ch.mention if ch else "Unknown",  inline=True)
        embed.add_field(name="Current",     value=f"**{cfg.get('count', 0)}**",     inline=True)
        embed.add_field(name="High Score",  value=f"**{cfg.get('high_score', 0)}**",inline=True)
        embed.set_footer(text="Count one by one — no two consecutive counts from the same person!")
        await ctx.reply(embed=embed)

    @counting.command(name="setup")
    @ctx_mod()
    async def counting_setup(self, ctx, channel: discord.TextChannel = None):
        """Set up the counting channel."""
        channel = channel or ctx.channel
        await counting_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {
                "channel_id":   str(channel.id),
                "count":        0,
                "last_user_id": None,
                "high_score":   0,
            }},
            upsert=True
        )
        embed = discord.Embed(
            title="Counting Channel Set",
            description=(
                f"Count in {channel.mention}!\n\n"
                "**Rules:**\n"
                "— Type the next number (1, 2, 3...)\n"
                "— You cannot count twice in a row\n"
                "— Wrong number resets the count to 0\n"
                "— Try to beat the high score together!"
            ),
            color=0x2B2D31
        )
        await ctx.reply(embed=embed)

    @counting.command(name="reset")
    @ctx_mod()
    async def counting_reset(self, ctx):
        """Reset the count back to 0."""
        await counting_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"count": 0, "last_user_id": None}},
        )
        await ctx.reply("Count reset to 0.")

    @counting.command(name="stats")
    async def counting_stats(self, ctx):
        """View counting stats for this server."""
        cfg = await counting_col.find_one({"guild_id": str(ctx.guild.id)})
        if not cfg:
            return await ctx.reply("Counting is not set up on this server.")
        embed = discord.Embed(title="Counting Stats", color=0x2B2D31)
        embed.add_field(name="Current Count", value=f"**{cfg.get('count', 0)}**",    inline=True)
        embed.add_field(name="High Score",    value=f"**{cfg.get('high_score', 0)}**", inline=True)
        await ctx.reply(embed=embed)

    async def _handle_counting(self, message: discord.Message, cfg: dict):
        """Process a message in the counting channel."""
        content = message.content.strip()
        if not content.isdigit():
            return

        num            = int(content)
        expected       = cfg.get("count", 0) + 1
        last_user      = cfg.get("last_user_id")
        current_high   = cfg.get("high_score", 0)

        # Same person counted twice
        if str(message.author.id) == str(last_user):
            await counting_col.update_one(
                {"guild_id": str(message.guild.id)},
                {"$set": {"count": 0, "last_user_id": None}}
            )
            await message.add_reaction("❌")
            await message.channel.send(
                embed=discord.Embed(
                    description=(
                        f"{message.author.mention} counted twice in a row! "
                        f"Count reset to **0**. The count was at **{cfg['count']}**."
                    ),
                    color=0xED4245
                )
            )
            return

        # Wrong number
        if num != expected:
            await counting_col.update_one(
                {"guild_id": str(message.guild.id)},
                {"$set": {"count": 0, "last_user_id": None}}
            )
            await message.add_reaction("❌")
            await message.channel.send(
                embed=discord.Embed(
                    description=(
                        f"{message.author.mention} ruined it! "
                        f"Expected **{expected}**, got **{num}**. "
                        f"Count reset to **0**."
                    ),
                    color=0xED4245
                )
            )
            return

        # Correct — update count
        new_high = max(current_high, num)
        await counting_col.update_one(
            {"guild_id": str(message.guild.id)},
            {"$set": {
                "count":        num,
                "last_user_id": str(message.author.id),
                "high_score":   new_high,
            }}
        )
        await message.add_reaction("✅")

        # Milestone reactions
        if num % 100 == 0:
            await message.channel.send(
                embed=discord.Embed(
                    description=f"**{num}!** Amazing teamwork! Keep going!",
                    color=0xffd700
                )
            )
        elif num % 50 == 0:
            await message.add_reaction("🎉")

        # New high score
        if num > current_high and num > 1:
            await message.channel.send(
                embed=discord.Embed(
                    description=f"New high score: **{num}**!",
                    color=0x57F287
                ),
                delete_after=5
            )

    # ══════════════════════════════════════════════════════════════════════════
    #  WORD GUESS  (Hangman-style)
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(invoke_without_command=True)
    async def wordguess(self, ctx):
        """Word guessing game (Hangman-style). Sub-commands: start, stop"""
        embed = discord.Embed(title="Word Guess", color=0x2B2D31)
        embed.add_field(
            name="Commands",
            value=(
                "`,wordguess start` — start a new game\n"
                "`,wordguess stop` — end the current game"
            ),
            inline=False
        )
        embed.add_field(
            name="How to play",
            value="Bot picks a secret word. Type single letters to guess it. 6 wrong guesses and it's over.",
            inline=False
        )
        await ctx.reply(embed=embed)

    @wordguess.command(name="start")
    async def wordguess_start(self, ctx):
        cid = ctx.channel.id
        if cid in self._word_games:
            return await ctx.reply("A word guess game is already running. Use `,wordguess stop` to end it.")

        word = random.choice(WORDS).lower()
        self._word_games[cid] = {
            "word":    word,
            "guessed": set(),   # correct letters guessed
            "wrong":   set(),   # wrong letters guessed
            "players": set(),
            "host":    ctx.author.id,
        }

        await ctx.send(embed=self._word_embed(cid))

    @wordguess.command(name="stop")
    @ctx_mod()
    async def wordguess_stop(self, ctx):
        cid  = ctx.channel.id
        game = self._word_games.pop(cid, None)
        if not game:
            return await ctx.reply("No active word guess game in this channel.")
        await ctx.reply(embed=discord.Embed(
            description=f"Game ended. The word was **{game['word']}**.",
            color=0x2B2D31
        ))

    def _word_embed(self, cid: int) -> discord.Embed:
        game    = self._word_games[cid]
        word    = game["word"]
        guessed = game["guessed"]
        wrong   = game["wrong"]

        # Build display: "_ _ e _ _ _"
        display = " ".join(c if c in guessed else r"\_" for c in word)

        # Hangman stages
        stage = len(wrong)
        lives_left = 6 - stage
        lives_bar  = "❤️" * lives_left + "🖤" * stage

        color = 0x57F287 if lives_left > 3 else (0xFEE75C if lives_left > 1 else 0xED4245)

        embed = discord.Embed(
            title="Word Guess",
            description=f"```{display}```",
            color=color
        )
        embed.add_field(
            name="Wrong guesses",
            value=" ".join(f"`{l}`" for l in sorted(wrong)) or "None",
            inline=True
        )
        embed.add_field(name="Lives", value=lives_bar, inline=True)
        embed.add_field(name="Letters", value=f"{len(word)} letters", inline=True)
        embed.set_footer(text="Type a single letter to guess · ,wordguess stop to end")
        return embed

    async def _handle_word_guess(self, message: discord.Message):
        cid     = message.channel.id
        game    = self._word_games.get(cid)
        if not game:
            return

        content = message.content.strip().lower()

        # Must be a single letter
        if len(content) != 1 or not content.isalpha():
            # Check if they guessed the full word
            if content == game["word"]:
                del self._word_games[cid]
                embed = discord.Embed(
                    title="Correct!",
                    description=(
                        f"{message.author.mention} guessed the word **{game['word']}**!\n"
                        f"Wrong guesses: {len(game['wrong'])}"
                    ),
                    color=0x57F287
                )
                await message.channel.send(embed=embed)
            return

        letter  = content
        word    = game["word"]
        already = game["guessed"] | game["wrong"]

        if letter in already:
            await message.reply(f"`{letter}` was already guessed.", delete_after=4, mention_author=False)
            return

        game["players"].add(message.author.id)

        if letter in word:
            game["guessed"].add(letter)
            await message.add_reaction("✅")

            # Check win
            if all(c in game["guessed"] for c in word):
                del self._word_games[cid]
                embed = discord.Embed(
                    title="Word Solved!",
                    description=(
                        f"The word was **{word}**!\n"
                        f"{message.author.mention} got the last letter.\n"
                        f"Wrong guesses: {len(game['wrong'])} | "
                        f"Players: {len(game['players'])}"
                    ),
                    color=0x57F287
                )
                await message.channel.send(embed=embed)
            else:
                await message.channel.send(embed=self._word_embed(cid))

        else:
            game["wrong"].add(letter)
            await message.add_reaction("❌")

            if len(game["wrong"]) >= 6:
                word_val = game["word"]
                del self._word_games[cid]
                embed = discord.Embed(
                    title="Game Over",
                    description=f"Too many wrong guesses! The word was **{word_val}**.",
                    color=0xED4245
                )
                await message.channel.send(embed=embed)
            else:
                await message.channel.send(embed=self._word_embed(cid))


async def setup(bot):
    await bot.add_cog(Games(bot))