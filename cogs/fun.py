import math
import random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

COLOR = 0x2B2D31
NO_PINGS = discord.AllowedMentions.none()
MAX_TEXT = 200
SHRUG = "¯\\\\\\_(ツ)\\_/¯"

RP_RESPONSES = {
    "hug": [
        "{a} pulls {t} into a warm hug.",
        "{a} wraps their arms around {t} tightly.",
        "{a} gives {t} the biggest hug ever.",
    ],
    "pat": [
        "{a} pats {t} gently on the head.",
        "{a} gives {t} an approving head pat.",
        "{a} keeps patting {t} like a happy puppy.",
    ],
    "slap": [
        "{a} slaps {t} across the face.",
        "{a} gives {t} a mighty slap.",
        "{a} slaps {t} back to reality.",
    ],
    "kiss": [
        "{a} gives {t} a sweet kiss.",
        "{a} kisses {t} on the cheek.",
        "{a} surprises {t} with a kiss.",
    ],
    "poke": [
        "{a} pokes {t} again and again.",
        "{a} jabs {t} with one finger.",
        "{a} just won't stop poking {t}.",
    ],
    "highfive": [
        "{a} gives {t} an epic high-five!",
        "{a} and {t} share a perfect high-five.",
        "{a} leaves {t} hanging for a second... then high-fives.",
    ],
    "bonk": [
        "{a} bonks {t} on the head.",
        "{a} pulls out the bonk hammer for {t}.",
        "{a} sends {t} to bonk jail.",
    ],
    "cuddle": [
        "{a} cuddles up with {t}.",
        "{a} and {t} are now cuddling.",
        "{a} wraps a cozy blanket around {t}.",
    ],
    "boop": [
        "{a} boops {t} on the nose.",
        "{a} gives {t} a gentle nose boop.",
        "Boop! {a} got {t}'s nose.",
    ],
    "wave": [
        "{a} waves at {t}!",
        "{a} gives {t} a friendly wave.",
        "{a} waves at {t} with a big smile.",
    ],
    "stare": [
        "{a} stares at {t} very seriously.",
        "{a} will not stop staring at {t}.",
        "{a} looks {t} in the eyes and never blinks.",
    ],
}

RP_EMOJIS = {
    "hug": "🤗", "pat": "🙂", "slap": "👋", "kiss": "💋",
    "poke": "👉", "highfive": "✋", "bonk": "🔨", "cuddle": "🫂",
    "boop": "👃", "wave": "👋", "stare": "👁️",
}

WYR_QUESTIONS = [
    ("Fight 100 duck-sized horses", "Fight 1 horse-sized duck"),
    ("Always speak in rhymes", "Always speak in questions"),
    ("Have no internet for a month", "Have no music for a month"),
    ("Fly, but only 2 feet off the ground", "Teleport, but only 10 feet at a time"),
    ("Know when you will die", "Know how you will die"),
    ("Be famous but disliked", "Be unknown but loved"),
    ("Give up social media forever", "Give up movies and shows forever"),
    ("Have a perfect memory", "Have a perfect poker face"),
    ("Never use your phone again", "Never watch TV again"),
    ("Talk to animals", "Speak every human language"),
]

EIGHT_BALL = [
    "Definitely yes.", "Without a doubt.", "Most likely.",
    "Signs point to yes.", "Ask again later.", "I can't tell right now.",
    "Don't count on it.", "Very doubtful.", "My answer is no.",
    "Things don't look good.",
]

ROASTS = [
    "You're the human version of a participation trophy.",
    "You have the same effect on people as a Monday morning.",
    "I would roast you, but you're already toast.",
    "You're not slow, you just enjoy taking your time.",
    "You bring so much energy to the room. Mostly when you leave.",
]

COMPLIMENTS = [
    "You light up every room you walk into.",
    "You have a heart of gold.",
    "You make the world better just by being in it.",
    "You are more capable than you realize.",
    "Your smile could cheer up anyone.",
]

SHIP_LEVELS = [
    (15, "Not a great match.", "💔"),
    (35, "Better as friends.", "💛"),
    (55, "There is some hope.", "🧡"),
    (75, "A solid match!", "❤️"),
    (90, "Great together!", "💖"),
    (101, "A perfect match.", "💞"),
]

HOT_LEVELS = [
    (15, "Room temperature at best."),
    (35, "Warming up..."),
    (55, "Pretty good. Could be hotter."),
    (75, "Looking good!"),
    (90, "Very hot!"),
    (101, "Too hot to handle."),
]


def clean(text) -> str:
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(text)))


def meter(pct: int) -> str:
    filled = (pct + 5) // 10
    return "▰" * filled + "▱" * (10 - filled)


def pick(levels, pct: int):
    for level in levels:
        if pct < level[0]:
            return level[1:]
    return levels[-1][1:]


def score(key: str) -> int:
    return random.Random(key).randint(0, 100)


def base_embed(title: Optional[str] = None, description: Optional[str] = None) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=COLOR)


def stamp(embed: discord.Embed, user, label: str = "Requested by") -> discord.Embed:
    embed.set_footer(text=f"{label} {user.display_name}", icon_url=user.display_avatar.url)
    return embed


def slow():
    return commands.cooldown(1, 3, commands.BucketType.user)


def slow_slash():
    return app_commands.checks.cooldown(1, 3.0)


class VoteView(discord.ui.View):
    def __init__(self, option_a: str, option_b: str):
        super().__init__(timeout=300)
        self.option_a = option_a
        self.option_b = option_b
        self.votes = {}
        self.message = None

    def build(self) -> discord.Embed:
        total = len(self.votes)
        count_a = sum(1 for v in self.votes.values() if v == "a")
        count_b = total - count_a
        pct_a = round(100 * count_a / total) if total else 0
        pct_b = round(100 * count_b / total) if total else 0

        embed = base_embed("Would You Rather?")
        embed.add_field(
            name="🅰️  Option A",
            value=f"{self.option_a}\n{meter(pct_a)}  **{pct_a}%**  ·  {count_a} vote{'s' if count_a != 1 else ''}",
            inline=False,
        )
        embed.add_field(
            name="🅱️  Option B",
            value=f"{self.option_b}\n{meter(pct_b)}  **{pct_b}%**  ·  {count_b} vote{'s' if count_b != 1 else ''}",
            inline=False,
        )
        embed.set_footer(text="Tap a button to vote. You can change your vote.")
        return embed

    async def cast(self, interaction: discord.Interaction, choice: str):
        self.votes[interaction.user.id] = choice
        await interaction.response.edit_message(embed=self.build(), view=self)

    @discord.ui.button(label="Option A", emoji="🅰️", style=discord.ButtonStyle.secondary)
    async def vote_a(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cast(interaction, "a")

    @discord.ui.button(label="Option B", emoji="🅱️", style=discord.ButtonStyle.secondary)
    async def vote_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cast(interaction, "b")

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        return ctx.guild is not None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.guild is not None

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            message = f"Please wait {math.ceil(error.retry_after)} seconds and try again."
        elif isinstance(error, commands.MemberNotFound):
            message = "I couldn't find that person in this server."
        elif isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            message = "That doesn't look right. Please check the command and try again."
        elif isinstance(error, commands.CheckFailure):
            message = "This only works inside a server."
        else:
            message = "Something went wrong. Please try again later."
        try:
            await ctx.reply(message, mention_author=False, allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            pass

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"Please wait {math.ceil(error.retry_after)} seconds and try again."
        elif isinstance(error, app_commands.CheckFailure):
            message = "This only works inside a server."
        else:
            message = "Something went wrong. Please try again later."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass

    def _rp_embed(self, action: str, author, target) -> discord.Embed:
        text = random.choice(RP_RESPONSES[action]).format(
            a=f"**{clean(author.display_name)}**",
            t=f"**{clean(target.display_name)}**",
        )
        embed = base_embed(description=f"{RP_EMOJIS[action]}  {text}")
        return stamp(embed, author)

    async def _handle_rp(self, ctx, action: str, target):
        if target is None:
            return await ctx.reply(
                f"Please mention someone. Example: `{ctx.clean_prefix}{action} @name`",
                mention_author=False,
                allowed_mentions=NO_PINGS,
            )
        if target.id == ctx.author.id:
            return await ctx.reply(
                "You need to pick someone else for that.",
                mention_author=False,
                allowed_mentions=NO_PINGS,
            )
        await ctx.reply(
            embed=self._rp_embed(action, ctx.author, target),
            mention_author=False,
            allowed_mentions=NO_PINGS,
        )

    @commands.command()
    @slow()
    async def hug(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "hug", target)

    @commands.command()
    @slow()
    async def pat(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "pat", target)

    @commands.command()
    @slow()
    async def slap(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "slap", target)

    @commands.command()
    @slow()
    async def kiss(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "kiss", target)

    @commands.command()
    @slow()
    async def poke(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "poke", target)

    @commands.command()
    @slow()
    async def highfive(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "highfive", target)

    @commands.command()
    @slow()
    async def bonk(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "bonk", target)

    @commands.command()
    @slow()
    async def cuddle(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "cuddle", target)

    @commands.command()
    @slow()
    async def boop(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "boop", target)

    @commands.command()
    @slow()
    async def wave(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "wave", target)

    @commands.command()
    @slow()
    async def stare(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "stare", target)

    def _ship_embed(self, user1, user2, requester) -> discord.Embed:
        low, high = sorted((user1.id, user2.id))
        pct = score(f"ship:{low}:{high}")
        verdict, heart = pick(SHIP_LEVELS, pct)

        name1 = user1.display_name
        name2 = user2.display_name
        ship_name = name1[:max(1, len(name1) // 2)] + name2[len(name2) // 2:]

        embed = base_embed(
            f"{heart}  Love Meter",
            f"**{clean(name1)}**  ×  **{clean(name2)}**\n\n{meter(pct)}  **{pct}%**\n*{verdict}*",
        )
        embed.add_field(name="Ship Name", value=f"`{clean(ship_name)}`", inline=False)
        return stamp(embed, requester)

    @commands.command(aliases=["love", "match"])
    @slow()
    async def ship(self, ctx, user1: discord.Member = None, user2: discord.Member = None):
        if user1 is None or user2 is None:
            return await ctx.reply(
                f"Please mention two people. Example: `{ctx.clean_prefix}ship @name1 @name2`",
                mention_author=False,
                allowed_mentions=NO_PINGS,
            )
        if user1.id == user2.id:
            return await ctx.reply(
                "Please pick two different people.",
                mention_author=False,
                allowed_mentions=NO_PINGS,
            )
        await ctx.reply(
            embed=self._ship_embed(user1, user2, ctx.author),
            mention_author=False,
            allowed_mentions=NO_PINGS,
        )

    @app_commands.command(name="ship", description="Check the love match between two members")
    @slow_slash()
    async def slash_ship(self, interaction: discord.Interaction, user1: discord.Member, user2: discord.Member):
        if user1.id == user2.id:
            return await interaction.response.send_message("Please pick two different people.", ephemeral=True)
        await interaction.response.send_message(
            embed=self._ship_embed(user1, user2, interaction.user),
            allowed_mentions=NO_PINGS,
        )

    def _hot_embed(self, member, requester) -> discord.Embed:
        pct = score(f"hot:{member.id}")
        (verdict,) = pick(HOT_LEVELS, pct)
        embed = base_embed(
            "🔥  Hotness Meter",
            f"**{clean(member.display_name)}**\n\n{meter(pct)}  **{pct}%**\n*{verdict}*",
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        return stamp(embed, requester, "Checked by")

    @commands.command(aliases=["hotness"])
    @slow()
    async def hot(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        await ctx.reply(
            embed=self._hot_embed(member, ctx.author),
            mention_author=False,
            allowed_mentions=NO_PINGS,
        )

    @app_commands.command(name="hot", description="Check someone's hotness level")
    @slow_slash()
    async def slash_hot(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        member = member or interaction.user
        await interaction.response.send_message(
            embed=self._hot_embed(member, interaction.user),
            allowed_mentions=NO_PINGS,
        )

    def _ball_embed(self, question: str, requester) -> discord.Embed:
        embed = base_embed("🎱  Magic 8-Ball")
        embed.add_field(name="Question", value=clean(question), inline=False)
        embed.add_field(name="Answer", value=f"**{random.choice(EIGHT_BALL)}**", inline=False)
        return stamp(embed, requester, "Asked by")

    @commands.command(name="8ball", aliases=["eightball"])
    @slow()
    async def eight_ball(self, ctx, *, question: str = None):
        if not question:
            return await ctx.reply(
                f"Please ask a question. Example: `{ctx.clean_prefix}8ball will I win?`",
                mention_author=False,
                allowed_mentions=NO_PINGS,
            )
        if len(question) > MAX_TEXT:
            return await ctx.reply(
                f"Please keep your question under {MAX_TEXT} letters.",
                mention_author=False,
                allowed_mentions=NO_PINGS,
            )
        await ctx.reply(
            embed=self._ball_embed(question, ctx.author),
            mention_author=False,
            allowed_mentions=NO_PINGS,
        )

    @app_commands.command(name="8ball", description="Ask the magic 8-ball")
    @slow_slash()
    async def slash_8ball(self, interaction: discord.Interaction, question: app_commands.Range[str, 1, MAX_TEXT]):
        await interaction.response.send_message(
            embed=self._ball_embed(question, interaction.user),
            allowed_mentions=NO_PINGS,
        )

    def _dice_embed(self, sides: int, requester) -> discord.Embed:
        embed = base_embed("🎲  Dice Roll", f"# {random.randint(1, sides)}")
        embed.set_footer(text=f"d{sides}  ·  Rolled by {requester.display_name}", icon_url=requester.display_avatar.url)
        return embed

    @commands.command(aliases=["roll"])
    @slow()
    async def dice(self, ctx, sides: int = 6):
        if sides < 2 or sides > 1000:
            return await ctx.reply(
                "Please pick a number from 2 to 1000.",
                mention_author=False,
                allowed_mentions=NO_PINGS,
            )
        await ctx.reply(
            embed=self._dice_embed(sides, ctx.author),
            mention_author=False,
            allowed_mentions=NO_PINGS,
        )

    @app_commands.command(name="dice", description="Roll a dice")
    @slow_slash()
    async def slash_dice(self, interaction: discord.Interaction, sides: app_commands.Range[int, 2, 1000] = 6):
        await interaction.response.send_message(
            embed=self._dice_embed(sides, interaction.user),
            allowed_mentions=NO_PINGS,
        )

    @commands.command(aliases=["wyr"])
    @slow()
    async def wouldyourather(self, ctx):
        option_a, option_b = random.choice(WYR_QUESTIONS)
        view = VoteView(option_a, option_b)
        view.message = await ctx.reply(
            embed=view.build(),
            view=view,
            mention_author=False,
            allowed_mentions=NO_PINGS,
        )

    @app_commands.command(name="wouldyourather", description="Get a would-you-rather question")
    @slow_slash()
    async def slash_wouldyourather(self, interaction: discord.Interaction):
        option_a, option_b = random.choice(WYR_QUESTIONS)
        view = VoteView(option_a, option_b)
        await interaction.response.send_message(embed=view.build(), view=view)
        view.message = await interaction.original_response()

    @commands.command()
    @slow()
    async def shrug(self, ctx, *, message: str = None):
        if message and len(message) > 1800:
            return await ctx.reply(
                "That message is too long.",
                mention_author=False,
                allowed_mentions=NO_PINGS,
            )
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        text = f"{discord.utils.escape_mentions(message)} {SHRUG}" if message else SHRUG
        await ctx.send(text, allowed_mentions=NO_PINGS)

    @commands.command()
    @slow()
    async def roast(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        embed = base_embed("🔥  Roast", f"{member.mention}\n\n> {random.choice(ROASTS)}")
        embed.set_footer(text="All in good fun!")
        await ctx.reply(embed=embed, mention_author=False, allowed_mentions=NO_PINGS)

    @app_commands.command(name="roast", description="Gently roast someone")
    @slow_slash()
    async def slash_roast(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        member = member or interaction.user
        embed = base_embed("🔥  Roast", f"{member.mention}\n\n> {random.choice(ROASTS)}")
        embed.set_footer(text="All in good fun!")
        await interaction.response.send_message(embed=embed, allowed_mentions=NO_PINGS)

    @commands.command(aliases=["compliment"])
    @slow()
    async def praise(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        embed = base_embed("💖  Compliment", f"{member.mention}\n\n> {random.choice(COMPLIMENTS)}")
        await ctx.reply(embed=embed, mention_author=False, allowed_mentions=NO_PINGS)

    @app_commands.command(name="praise", description="Send a kind word to someone")
    @slow_slash()
    async def slash_praise(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        member = member or interaction.user
        embed = base_embed("💖  Compliment", f"{member.mention}\n\n> {random.choice(COMPLIMENTS)}")
        await interaction.response.send_message(embed=embed, allowed_mentions=NO_PINGS)


async def setup(bot):
    await bot.add_cog(Fun(bot))