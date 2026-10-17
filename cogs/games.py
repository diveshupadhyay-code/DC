import discord
from discord.ext import commands
import asyncio
import random
from utils.db import db
from utils.helpers import ctx_mod

counting_col = db["counting"]

WORDS_POOL = [
    "apple", "baker", "chair", "dance", "eagle", "flame", "grape", "house", "image", "juice",
    "knife", "lemon", "mouse", "night", "ocean", "paper", "queen", "river", "smile", "tiger",
    "unity", "voice", "water", "xenon", "yacht", "zebra", "anchor", "bridge", "candle", "dragon"
]

class Games(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._num_games = {}
        self._timed_num_games = {}
        self._word_games = {}
        self._word_chain_games = {}
        self._mafia_games = {}
        self._final_night_games = {}

    @commands.hybrid_command(name="guess", description="Start a number guessing game (1 to 100,000)")
    async def guess(self, ctx):
        cid = ctx.channel.id
        if cid in self._num_games or cid in self._timed_num_games:
            return await ctx.reply("A number game is already running in this channel.")
        
        number = random.randint(1, 100000)
        self._num_games[cid] = {
            "number": number,
            "attempts": 0,
            "players": set()
        }
        
        embed = discord.Embed(
            title="Number Guessing Game",
            description="I have picked a number between 1 and 100,000. Type your guess in chat!",
            color=0x5865F2
        )
        await ctx.reply(embed=embed)

    @commands.hybrid_command(name="guesstime", description="Start a timed number guessing game (1 to 1,000)")
    async def guesstime(self, ctx, seconds: int):
        if seconds <= 0 or seconds > 300:
            return await ctx.reply("Please enter time between 1 and 300 seconds.")
        
        cid = ctx.channel.id
        if cid in self._num_games or cid in self._timed_num_games:
            return await ctx.reply("A number game is already running in this channel.")
        
        number = random.randint(1, 1000)
        self._timed_num_games[cid] = {
            "number": number,
            "attempts": 0,
            "players": set()
        }
        
        embed = discord.Embed(
            title=f"Timed Number Guess ({seconds}s)",
            description=f"I have picked a number between 1 and 1,000. You have {seconds} seconds!",
            color=0xFEE75C
        )
        await ctx.reply(embed=embed)
        
        async def timer_task():
            await asyncio.sleep(seconds)
            if cid in self._timed_num_games:
                game = self._timed_num_games.pop(cid)
                await ctx.channel.send(embed=discord.Embed(
                    title="Time Up!",
                    description=f"Time ran out! The secret number was **{game['number']}**.",
                    color=0xED4245
                ))
        
        self.bot.loop.create_task(timer_task())

    @commands.hybrid_command(name="endgame", description="End active Number Guess and Timed Guess games")
    async def endgame(self, ctx):
        cid = ctx.channel.id
        num_game = self._num_games.pop(cid, None)
        timed_game = self._timed_num_games.pop(cid, None)
        
        if not num_game and not timed_game:
            return await ctx.reply("No active number guessing games in this channel.")
        
        ans = num_game["number"] if num_game else timed_game["number"]
        await ctx.reply(embed=discord.Embed(
            title="Game Ended",
            description=f"Active number game ended. The secret number was **{ans}**.",
            color=0x2B2D31
        ))

    @commands.hybrid_command(name="guessword", aliases=["gw"], description="Start Guess the Word (one player gets the secret word)")
    async def guessword(self, ctx):
        cid = ctx.channel.id
        if cid in self._word_games:
            return await ctx.reply("A guess word game is already active in this channel.")
        
        word = random.choice(WORDS_POOL)
        self._word_games[cid] = {
            "word": word,
            "host": ctx.author.id,
            "players": set()
        }
        
        try:
            await ctx.author.send(f"Your secret word for Guess the Word in {ctx.channel.mention} is: **{word}**")
            await ctx.reply("I have sent you the secret word in your direct messages. Everyone else, start guessing in this channel!")
        except discord.Forbidden:
            await ctx.reply(f"I couldn't DM you. The secret word is: **{word}**. (Please enable DMs for future games!)")

    @commands.hybrid_command(name="wordchain", aliases=["wc"], description="Start a Word Chain game")
    async def wordchain(self, ctx):
        cid = ctx.channel.id
        if cid in self._word_chain_games:
            return await ctx.reply("Word chain is already active in this channel.")
        
        start_letter = random.choice("abcdefghijklmnopqrstuvwxyz")
        self._word_chain_games[cid] = {
            "last_letter": start_letter,
            "last_user": None,
            "words": set()
        }
        
        embed = discord.Embed(
            title="Word Chain Started!",
            description=f"The starting letter is **{start_letter.upper()}**. Type a word starting with this letter!",
            color=0x57F287
        )
        await ctx.reply(embed=embed)

    @commands.hybrid_command(name="mafia", aliases=["ma"], description="Start a Mafia game")
    async def mafia(self, ctx):
        cid = ctx.channel.id
        if cid in self._mafia_games:
            return await ctx.reply("A Mafia game lobby is already open here.")
        
        self._mafia_games[cid] = {
            "host": ctx.author.id,
            "players": [ctx.author],
            "started": False
        }
        
        view = MafiaJoinView(self, cid)
        embed = discord.Embed(
            title="Mafia Game Lobby",
            description=f"{ctx.author.mention} started a Mafia game! Click Join below to participate.",
            color=0x5865F2
        )
        await ctx.reply(embed=embed, view=view)

    @commands.hybrid_command(name="guessagent", aliases=["gta"], description="Guide for Guess the Agent game")
    async def guessagent(self, ctx):
        embed = discord.Embed(
            title="Guess the Agent Guide",
            description="How to play Guess the Agent:",
            color=0x2B2D31
        )
        embed.add_field(name="Agent Black", value="Gets the real secret word.", inline=False)
        embed.add_field(name="Agent Red", value="Gets a similar but different word.", inline=False)
        embed.add_field(name="Agent White", value="Gets no word and must pretend to know it.", inline=False)
        embed.add_field(name="Gameplay", value="Players describe their words one by one, discuss who is suspicious, and vote to find the agents.", inline=False)
        await ctx.reply(embed=embed)

    @commands.hybrid_command(name="finalnight", aliases=["fn"], description="Start Final Night game")
    async def finalnight(self, ctx):
        cid = ctx.channel.id
        if cid in self._final_night_games:
            return await ctx.reply("Final Night is already running here.")
        
        self._final_night_games[cid] = {
            "players": {ctx.author.id: [random.randint(1, 3), random.randint(1, 3), random.randint(1, 3)]},
            "assassins": {ctx.author.id}
        }
        
        embed = discord.Embed(
            title="Final Night Started",
            description=f"{ctx.author.mention} started Final Night. Rooms assigned secretly!",
            color=0x9b59b6
        )
        await ctx.reply(embed=embed)

    @commands.hybrid_command(name="stats", description="View server and global game stats")
    async def stats(self, ctx):
        guild_id = str(ctx.guild.id)
        cfg = await counting_col.find_one({"guild_id": guild_id})
        count_val = cfg.get("count", 0) if cfg else 0
        high_score = cfg.get("high_score", 0) if cfg else 0
        
        embed = discord.Embed(
            title="Game Stats",
            description="Overview of server and global statistics:",
            color=0x2B2D31
        )
        embed.add_field(name="Current Counting Score", value=str(count_val), inline=True)
        embed.add_field(name="Counting High Score", value=str(high_score), inline=True)
        embed.add_field(name="Active Number Games", value=str(len(self._num_games) + len(self._timed_num_games)), inline=True)
        embed.add_field(name="Active Word Chains", value=str(len(self._word_chain_games)), inline=True)
        embed.set_footer(text="Scores reflect counting progress and active game sessions in this server.")
        await ctx.reply(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        
        cid = message.channel.id
        content = message.content.strip()
        
        if cid in self._num_games and content.isdigit():
            game = self._num_games[cid]
            guess = int(content)
            game["attempts"] += 1
            number = game["number"]
            
            if guess == number:
                self._num_games.pop(cid, None)
                await message.channel.send(embed=discord.Embed(
                    title="Correct Number!",
                    description=f"{message.author.mention} guessed **{number}** in {game['attempts']} attempts!",
                    color=0x57F287
                ))
            elif guess < number:
                await message.reply("Too low!", mention_author=False)
            else:
                await message.reply("Too high!", mention_author=False)
        
        if cid in self._timed_num_games and content.isdigit():
            game = self._timed_num_games[cid]
            guess = int(content)
            game["attempts"] += 1
            number = game["number"]
            
            if guess == number:
                self._timed_num_games.pop(cid, None)
                await message.channel.send(embed=discord.Embed(
                    title="Correct Timed Number!",
                    description=f"{message.author.mention} guessed **{number}** correctly just in time!",
                    color=0x57F287
                ))
            elif guess < number:
                await message.reply("Too low!", mention_author=False)
            else:
                await message.reply("Too high!", mention_author=False)
        
        if cid in self._word_games:
            game = self._word_games[cid]
            if content.lower() == game["word"].lower():
                self._word_games.pop(cid, None)
                await message.channel.send(embed=discord.Embed(
                    title="Word Guessed!",
                    description=f"{message.author.mention} correctly guessed the secret word **{game['word']}**!",
                    color=0x57F287
                ))
        
        if cid in self._word_chain_games:
            game = self._word_chain_games[cid]
            word_lower = content.lower()
            if word_lower.isalpha() and len(word_lower) > 1:
                if word_lower[0] == game["last_letter"] and word_lower not in game["words"]:
                    game["words"].add(word_lower)
                    game["last_letter"] = word_lower[-1]
                    game["last_user"] = message.author.id
                    await message.add_reaction("✅")
                    await message.channel.send(f"Good word! Next word must start with **{game['last_letter'].upper()}**.")
                elif word_lower[0] != game["last_letter"]:
                    await message.add_reaction("❌")
                    await message.reply(f"Word must start with **{game['last_letter'].upper()}**!", delete_after=5, mention_author=False)

        cfg = await counting_col.find_one({"guild_id": str(message.guild.id)})
        if cfg and cfg.get("channel_id") and str(cid) == str(cfg["channel_id"]):
            await self._handle_counting(message, cfg)

    async def _handle_counting(self, message: discord.Message, cfg: dict):
        content = message.content.strip()
        if not content.isdigit():
            return
        
        num = int(content)
        expected = cfg.get("count", 0) + 1
        last_user = cfg.get("last_user_id")
        current_high = cfg.get("high_score", 0)
        
        if str(message.author.id) == str(last_user):
            await counting_col.update_one({"guild_id": str(message.guild.id)}, {"$set": {"count": 0, "last_user_id": None}})
            await message.add_reaction("❌")
            await message.channel.send(embed=discord.Embed(description=f"{message.author.mention} counted twice! Count reset to 0.", color=0xED4245))
            return
        
        if num != expected:
            await counting_col.update_one({"guild_id": str(message.guild.id)}, {"$set": {"count": 0, "last_user_id": None}})
            await message.add_reaction("❌")
            await message.channel.send(embed=discord.Embed(description=f"{message.author.mention} got it wrong! Expected {expected}, got {num}. Reset to 0.", color=0xED4245))
            return
        
        new_high = max(current_high, num)
        await counting_col.update_one({"guild_id": str(message.guild.id)}, {"$set": {"count": num, "last_user_id": str(message.author.id), "high_score": new_high}})
        await message.add_reaction("✅")

class MafiaJoinView(discord.ui.View):
    def __init__(self, cog, cid):
        super().__init__(timeout=60)
        self.cog = cog
        self.cid = cid

    @discord.ui.button(label="Join Mafia", style=discord.ButtonStyle.green)
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = self.cog._mafia_games.get(self.cid)
        if not game:
            return await interaction.response.send_message("No active lobby.", ephemeral=True)
        if interaction.user in game["players"]:
            return await interaction.response.send_message("You are already in!", ephemeral=True)
        
        game["players"].append(interaction.user)
        await interaction.response.send_message("You joined the Mafia game!", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Games(bot))