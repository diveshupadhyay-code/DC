import discord
from discord.ext import commands
from discord import app_commands
import random

RP_RESPONSES = {
    "hug": [
        "{a} pulls {t} into a warm hug.",
        "{a} wraps their arms around {t} tightly.",
        "{a} gives {t} the biggest hug ever.",
    ],
    "pat": [
        "{a} pats {t} gently on the head.",
        "{a} gives {t} an approving head pat.",
        "{a} repeatedly pats {t} like a golden retriever.",
    ],
    "slap": [
        "{a} slaps {t} across the face.",
        "{a} delivers a mighty slap to {t}.",
        "{a} slaps {t} back to reality.",
    ],
    "kiss": [
        "{a} gives {t} a sweet kiss.",
        "{a} plants a kiss on {t}'s cheek.",
        "{a} surprised {t} with a kiss.",
    ],
    "poke": [
        "{a} pokes {t} repeatedly.",
        "{a} jabs {t} with one finger.",
        "{a} won't stop poking {t}.",
    ],
    "highfive": [
        "{a} gives {t} an epic high-five!",
        "{a} and {t} exchange a perfect high-five.",
        "{a} leaves {t} hanging for a sec... then high-fives.",
    ],
    "bonk": [
        "{a} bonks {t} on the head.",
        "{a} pulls out the bonk hammer for {t}.",
        "{a} sends {t} to bonk jail.",
    ],
    "cuddle": [
        "{a} cuddles up with {t}.",
        "{a} and {t} are now cuddling.",
        "{a} wraps a blanket around {t} and cuddles.",
    ],
    "boop": [
        "{a} boops {t} on the nose.",
        "{a} gives {t} a gentle nose boop.",
        "boop! {a} got {t}'s nose.",
    ],
    "wave": [
        "{a} waves at {t}!",
        "{a} gives {t} a friendly wave.",
        "{a} enthusiastically waves at {t}.",
    ],
    "stare": [
        "{a} stares intensely at {t}.",
        "{a} won't stop staring at {t}.",
        "{a} locks eyes with {t} and doesn't blink.",
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
    ("Be able to fly but only 2 feet off the ground", "Be able to teleport but only 10 feet at a time"),
    ("Know when you'll die", "Know how you'll die"),
    ("Be famous but hated", "Be unknown but loved"),
    ("Give up social media forever", "Give up watching movies/shows forever"),
    ("Have a photographic memory", "Have a perfect poker face"),
    ("Never use your phone again", "Never watch TV again"),
    ("Be able to talk to animals", "Be able to speak all human languages"),
]

EIGHT_BALL = [
    "Definitely yes.", "Without a doubt.", "Most likely.",
    "Signs point to yes.", "Ask again later.", "Cannot predict now.",
    "Don't count on it.", "Very doubtful.", "My sources say no.",
    "Outlook not so good.",
]

ROASTS = [
    "You're the human equivalent of a participation trophy.",
    "You have the same effect on people as a Monday morning.",
    "I'd roast you but my mom said I'm not allowed to burn trash.",
    "You're not stupid, you just have bad luck thinking.",
    "If you were any more inbred, you'd be a sandwich.",
]

COMPLIMENTS = [
    "You light up every room you walk into.",
    "You have a heart of gold.",
    "You make the world a better place just by being in it.",
    "You're more capable than you realize.",
    "Your smile could end wars.",
]


class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _rp_embed(self, action: str, author: discord.Member, target: discord.Member) -> discord.Embed:
        responses = RP_RESPONSES.get(action, [f"{{a}} does {action} to {{t}}."])
        text = random.choice(responses).format(a=f"**{author.display_name}**", t=f"**{target.display_name}**")
        emoji = RP_EMOJIS.get(action, "✨")
        
        embed = discord.Embed(description=f"{emoji} {text}", color=0x2B2D31)
        embed.set_footer(text=f"Requested by {author.display_name}", icon_url=author.display_avatar.url)
        return embed

    async def _handle_rp(self, ctx, action: str, target: discord.Member):
        target = target or ctx.author
        await ctx.reply(embed=self._rp_embed(action, ctx.author, target))

    @commands.command()
    async def hug(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "hug", target)

    @commands.command()
    async def pat(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "pat", target)

    @commands.command()
    async def slap(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "slap", target)

    @commands.command()
    async def kiss(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "kiss", target)

    @commands.command()
    async def poke(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "poke", target)

    @commands.command()
    async def highfive(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "highfive", target)

    @commands.command()
    async def bonk(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "bonk", target)

    @commands.command()
    async def cuddle(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "cuddle", target)

    @commands.command()
    async def boop(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "boop", target)

    @commands.command()
    async def wave(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "wave", target)

    @commands.command()
    async def stare(self, ctx, target: discord.Member = None):
        await self._handle_rp(ctx, "stare", target)

    @commands.command(aliases=["love", "match"])
    async def ship(self, ctx, user1: discord.Member = None, user2: discord.Member = None):
        if not user1 or not user2:
            return await ctx.reply("Usage: `,ship @user1 @user2`")
        if user1.id == user2.id:
            return await ctx.reply("You can't ship someone with themselves.")

        random.seed(min(user1.id, user2.id) * max(user1.id, user2.id))
        pct = random.randint(0, 100)
        random.seed()

        if pct < 15:    verdict, heart = "Terrible match.", "💔"
        elif pct < 35:  verdict, heart = "Friendzone material.", "💔"
        elif pct < 55:  verdict, heart = "There's potential.", "💛"
        elif pct < 75:  verdict, heart = "Solid match!", "🧡"
        elif pct < 90:  verdict, heart = "Great chemistry!", "❤️"
        else:            verdict, heart = "Soulmates.", "💖"

        filled = round(pct / 10)
        bar = "🟥" * filled + "⬛" * (10 - filled)
        combined = user1.display_name[:len(user1.display_name)//2] + user2.display_name[len(user2.display_name)//2:]

        embed = discord.Embed(title=f"{heart} {user1.display_name} + {user2.display_name}", color=0x2B2D31)
        embed.add_field(name="Ship Name", value=f"`{combined}`", inline=True)
        embed.add_field(name="Compatibility", value=f"**{pct}%**", inline=True)
        embed.add_field(name="Verdict", value=verdict, inline=True)
        embed.add_field(name="Meter", value=bar, inline=False)
        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.reply(embed=embed)

    @app_commands.command(name="ship", description="Check love compatibility between two members")
    async def slash_ship(self, interaction: discord.Interaction, user1: discord.Member, user2: discord.Member):
        if user1.id == user2.id:
            return await interaction.response.send_message("Can't ship someone with themselves.", ephemeral=True)
        
        random.seed(min(user1.id, user2.id) * max(user1.id, user2.id))
        pct = random.randint(0, 100)
        random.seed()
        
        bar = "🟥" * round(pct/10) + "⬛" * (10 - round(pct/10))
        embed = discord.Embed(title=f"❤️ {user1.display_name} + {user2.display_name}", color=0x2B2D31)
        embed.add_field(name="Compatibility", value=f"**{pct}%**", inline=True)
        embed.add_field(name="Meter", value=bar, inline=False)
        await interaction.response.send_message(embed=embed)

    @commands.command(aliases=["hotness"])
    async def hot(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        random.seed(member.id)
        pct = random.randint(0, 100)
        random.seed()

        if pct < 15:    verdict = "Room temperature at best."
        elif pct < 35:  verdict = "Warming up..."
        elif pct < 55:  verdict = "Decent. Could be hotter."
        elif pct < 75:  verdict = "Looking good!"
        elif pct < 90:  verdict = "Hot! Check your DMs."
        else:            verdict = "Dangerous levels of heat detected."

        bar = "🔥" * round(pct/10) + "⬛" * (10 - round(pct/10))
        embed = discord.Embed(title="Hotness Meter", color=0x2B2D31)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name=f"{member.display_name}", value=f"**{pct}%**\n{bar}\n{verdict}", inline=False)
        embed.set_footer(text=f"Checked by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.reply(embed=embed)

    @app_commands.command(name="hot", description="Check someone's hotness level")
    async def slash_hot(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        random.seed(member.id)
        pct = random.randint(0, 100)
        random.seed()

        if pct < 15:    verdict = "Room temperature at best."
        elif pct < 35:  verdict = "Warming up..."
        elif pct < 55:  verdict = "Decent. Could be hotter."
        elif pct < 75:  verdict = "Looking good!"
        elif pct < 90:  verdict = "Hot! Check your DMs."
        else:            verdict = "Dangerous levels of heat detected."

        bar = "🔥" * round(pct/10) + "⬛" * (10 - round(pct/10))
        embed = discord.Embed(title="Hotness Meter", color=0x2B2D31)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name=f"{member.display_name}", value=f"**{pct}%**\n{bar}\n{verdict}", inline=False)
        await interaction.response.send_message(embed=embed)

    @commands.command(name="8ball", aliases=["eightball"])
    async def eight_ball(self, ctx, *, question: str = None):
        if not question:
            return await ctx.reply("Ask a question: `,8ball will I win?`")
        answer = random.choice(EIGHT_BALL)
        embed = discord.Embed(color=0x2B2D31)
        embed.add_field(name="Question", value=question, inline=False)
        embed.add_field(name="Answer", value=f"**{answer}**", inline=False)
        embed.set_footer(text="Magic 8-Ball", icon_url=ctx.author.display_avatar.url)
        await ctx.reply(embed=embed)

    @app_commands.command(name="8ball", description="Ask the magic 8-ball")
    async def slash_8ball(self, interaction: discord.Interaction, question: str):
        answer = random.choice(EIGHT_BALL)
        embed = discord.Embed(color=0x2B2D31)
        embed.add_field(name="Question", value=question, inline=False)
        embed.add_field(name="Answer", value=f"**{answer}**", inline=False)
        await interaction.response.send_message(embed=embed)

    @commands.command(aliases=["roll"])
    async def dice(self, ctx, sides: int = 6):
        if sides < 2 or sides > 1000:
            return await ctx.reply("Dice sides must be between 2 and 1000.")
        result = random.randint(1, sides)
        embed = discord.Embed(description=f"🎲 You rolled a **{result}** (d{sides})", color=0x2B2D31)
        await ctx.reply(embed=embed)

    @app_commands.command(name="dice", description="Roll a dice")
    async def slash_dice(self, interaction: discord.Interaction, sides: int = 6):
        if sides < 2 or sides > 1000:
            return await interaction.response.send_message("Between 2 and 1000 sides only.", ephemeral=True)
        result = random.randint(1, sides)
        await interaction.response.send_message(
            embed=discord.Embed(description=f"🎲 You rolled **{result}** (d{sides})", color=0x2B2D31)
        )

    @commands.command(aliases=["wyr"])
    async def wouldyourather(self, ctx):
        a, b = random.choice(WYR_QUESTIONS)
        embed = discord.Embed(title="Would You Rather...", color=0x2B2D31)
        embed.add_field(name="Option A 🅰️", value=a, inline=True)
        embed.add_field(name="Option B 🅱️", value=b, inline=True)
        embed.set_footer(text="React with 🅰️ or 🅱️ to vote!")
        msg = await ctx.reply(embed=embed)
        await msg.add_reaction("🅰️")
        await msg.add_reaction("🅱️")

    @app_commands.command(name="wouldyourather", description="Get a would-you-rather question")
    async def slash_wouldyourather(self, interaction: discord.Interaction):
        a, b = random.choice(WYR_QUESTIONS)
        embed = discord.Embed(title="Would You Rather...", color=0x2B2D31)
        embed.add_field(name="Option A 🅰️", value=a, inline=True)
        embed.add_field(name="Option B 🅱️", value=b, inline=True)
        await interaction.response.send_message(embed=embed)

    @commands.command()
    async def shrug(self, ctx, *, message: str = None):
        try:
            await ctx.message.delete()
        except:
            pass
        await ctx.send(f"{message} ¯\\_(ツ)_/¯" if message else "¯\\_(ツ)_/¯")

    @commands.command()
    async def roast(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        roast = random.choice(ROASTS)
        embed = discord.Embed(description=f"🔥 {member.mention}... {roast}", color=0x2B2D31)
        embed.set_footer(text="All in good fun!")
        await ctx.reply(embed=embed)

    @app_commands.command(name="roast", description="Gently roast someone")
    async def slash_roast(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        roast = random.choice(ROASTS)
        embed = discord.Embed(description=f"🔥 {member.mention}... {roast}", color=0x2B2D31)
        await interaction.response.send_message(embed=embed)

    @commands.command(aliases=["compliment"])
    async def praise(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        comp = random.choice(COMPLIMENTS)
        embed = discord.Embed(description=f"💖 {member.mention}, {comp}", color=0x2B2D31)
        await ctx.reply(embed=embed)

    @app_commands.command(name="praise", description="Send a compliment to someone")
    async def slash_praise(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        comp = random.choice(COMPLIMENTS)
        embed = discord.Embed(description=f"💖 {member.mention}, {comp}", color=0x2B2D31)
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Fun(bot))