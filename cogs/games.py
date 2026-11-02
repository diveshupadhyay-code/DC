import asyncio
import logging
import random
import time
from collections import Counter

import discord
from discord.ext import commands, tasks

from utils.db import db

log = logging.getLogger(__name__)

counting_col = db["counting"]

WORDS_POOL = [
    "apple", "baker", "chair", "dance", "eagle", "flame", "grape", "house", "image", "juice",
    "knife", "lemon", "mouse", "night", "ocean", "paper", "queen", "river", "smile", "tiger",
    "unity", "voice", "water", "xenon", "yacht", "zebra", "anchor", "bridge", "candle", "dragon"
]

WORD_PAIRS = [
    ("apple", "pear"), ("tiger", "lion"), ("coffee", "tea"), ("river", "lake"),
    ("guitar", "violin"), ("doctor", "nurse"), ("pizza", "burger"), ("moon", "sun"),
    ("bus", "train"), ("cat", "dog"), ("beach", "desert"), ("king", "prince"),
    ("pencil", "pen"), ("soccer", "rugby"), ("pillow", "blanket"), ("castle", "palace"),
]

COLOR_MAIN = 0x5865F2
COLOR_GOOD = 0x57F287
COLOR_WARN = 0xFEE75C
COLOR_BAD = 0xED4245
COLOR_DARK = 0x2B2D31

NO_MENTIONS = discord.AllowedMentions.none()
MAX_GAMES_PER_SERVER = 5
IDLE_LIMIT = 600
CHAIN_LETTERS = "abcdefghilmnoprstw"


def make_embed(title, description=None, color=COLOR_MAIN, footer=None):
    embed = discord.Embed(title=title, description=description, color=color)
    if footer:
        embed.set_footer(text=footer)
    return embed


async def safe_send(channel, **kwargs):
    try:
        return await channel.send(allowed_mentions=NO_MENTIONS, **kwargs)
    except discord.HTTPException:
        return None


async def safe_reply(message, text, delete_after=None):
    try:
        await message.reply(text, mention_author=False, allowed_mentions=NO_MENTIONS, delete_after=delete_after)
    except discord.HTTPException:
        pass


async def safe_react(message, emoji):
    try:
        await message.add_reaction(emoji)
    except discord.HTTPException:
        pass


async def retire(view, message):
    view.stop()
    if message:
        try:
            await message.edit(view=None)
        except discord.HTTPException:
            pass


def game_command(**kwargs):
    def decorator(func):
        func = commands.cooldown(1, 5, commands.BucketType.user)(func)
        func = commands.bot_has_permissions(send_messages=True, embed_links=True)(func)
        func = commands.guild_only()(func)
        return commands.hybrid_command(**kwargs)(func)
    return decorator


class PickView(discord.ui.View):
    def __init__(self, game, phase, options, handler):
        super().__init__(timeout=60)
        self.game = game
        self.phase = phase
        self.round = game.round
        self.handler = handler
        self.select = discord.ui.Select(placeholder="Choose a player", options=options, min_values=1, max_values=1)
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction):
        g = self.game
        if g.phase != self.phase or g.round != self.round or interaction.user.id not in g.alive:
            return await interaction.response.edit_message(content="This choice has expired.", view=None)
        await self.handler(interaction, int(self.select.values[0]))


class VoteView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=None)
        self.game = game

    @discord.ui.button(label="Cast Vote", style=discord.ButtonStyle.blurple)
    async def vote(self, interaction, button):
        g = self.game
        uid = interaction.user.id
        if uid not in g.players:
            return await interaction.response.send_message("You are not part of this game.", ephemeral=True)
        if uid not in g.alive:
            return await interaction.response.send_message("You are out of the game, so you cannot vote.", ephemeral=True)
        if g.phase != "vote":
            return await interaction.response.send_message("Voting is closed.", ephemeral=True)

        options = g.player_options([u for u in g.alive if u != uid])
        options.append(discord.SelectOption(label="Skip vote", value="0"))

        async def handler(inter, target):
            g.votes[uid] = target
            shown = "Skip vote" if target == 0 else g.name_of(target)
            await inter.response.edit_message(content=f"Your vote is saved: **{shown}**", view=None)
            if len(g.votes) >= len(g.alive):
                g.wake.set()

        await g.ask(interaction, "Who do you suspect?", options, handler)


class NightView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=None)
        self.game = game

    @discord.ui.button(label="Night Action", style=discord.ButtonStyle.blurple)
    async def act(self, interaction, button):
        g = self.game
        uid = interaction.user.id
        if uid not in g.players:
            return await interaction.response.send_message("You are not part of this game.", ephemeral=True)
        if uid not in g.alive:
            return await interaction.response.send_message("You are out of the game.", ephemeral=True)
        if g.phase != "night":
            return await interaction.response.send_message("The night is over.", ephemeral=True)
        await g.night_click(interaction)


class RoleView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=7200)
        self.game = game
        self.show.label = game.role_label

    @discord.ui.button(label="See My Role", style=discord.ButtonStyle.gray)
    async def show(self, interaction, button):
        g = self.game
        if interaction.user.id not in g.players:
            return await interaction.response.send_message("You are not part of this game.", ephemeral=True)
        await interaction.response.send_message(g.role_text(interaction.user.id), ephemeral=True)


class LobbyView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=180)
        self.game = game
        self.message = None

    def build(self):
        g = self.game
        names = "\n".join(f"• {g.name_of(u)}" for u in g.players)
        embed = make_embed(
            f"{g.name} Lobby",
            f"{g.lobby_text}\n\nHost: **{g.name_of(g.host.id)}**",
            COLOR_MAIN,
            f"{len(g.players)}/{g.max_players} players  |  {g.min_players} needed to start"
        )
        embed.add_field(name="Players", value=names, inline=False)
        return embed

    async def close(self, text):
        self.stop()
        if self.message:
            try:
                await self.message.edit(embed=make_embed(f"{self.game.name} Lobby Closed", text, COLOR_DARK), view=None)
            except discord.HTTPException:
                pass

    async def on_timeout(self):
        g = self.game
        if g.phase != "lobby":
            return
        g.phase = "over"
        if g.cog._party_games.get(g.channel.id) is g:
            del g.cog._party_games[g.channel.id]
        await self.close("Nobody started the game in time.")

    @discord.ui.button(label="Join", style=discord.ButtonStyle.green)
    async def join(self, interaction, button):
        g = self.game
        uid = interaction.user.id
        if g.phase != "lobby":
            return await interaction.response.send_message("This lobby is closed.", ephemeral=True)
        if uid in g.players:
            return await interaction.response.send_message("You are already in this game.", ephemeral=True)
        if len(g.players) >= g.max_players:
            return await interaction.response.send_message("This lobby is full.", ephemeral=True)
        g.players[uid] = interaction.user
        await interaction.response.edit_message(embed=self.build(), view=self)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.gray)
    async def leave(self, interaction, button):
        g = self.game
        uid = interaction.user.id
        if g.phase != "lobby":
            return await interaction.response.send_message("This lobby is closed.", ephemeral=True)
        if uid == g.host.id:
            return await interaction.response.send_message("The host cannot leave. Use the endgame command to cancel.", ephemeral=True)
        if uid not in g.players:
            return await interaction.response.send_message("You are not in this game.", ephemeral=True)
        del g.players[uid]
        await interaction.response.edit_message(embed=self.build(), view=self)

    @discord.ui.button(label="Start", style=discord.ButtonStyle.blurple)
    async def start(self, interaction, button):
        g = self.game
        if interaction.user.id != g.host.id:
            return await interaction.response.send_message("Only the host can start the game.", ephemeral=True)
        if g.phase != "lobby":
            return await interaction.response.send_message("This lobby is closed.", ephemeral=True)
        if len(g.players) < g.min_players:
            return await interaction.response.send_message(f"At least {g.min_players} players are needed to start.", ephemeral=True)
        g.assign()
        g.phase = "starting"
        self.stop()
        await interaction.response.edit_message(
            embed=make_embed(f"{g.name} Is Starting", "Get ready. Roles are being handed out.", COLOR_GOOD),
            view=None
        )
        g.task = asyncio.create_task(g.cog.run_party(g))


class PartyGame:
    name = "Game"
    min_players = 4
    max_players = 12
    lobby_text = ""
    role_label = "See My Role"
    role_names = {}

    def __init__(self, cog, channel, host):
        self.cog = cog
        self.channel = channel
        self.host = host
        self.players = {host.id: host}
        self.roles = {}
        self.alive = set()
        self.phase = "lobby"
        self.round = 0
        self.votes = {}
        self.acted = set()
        self.wake = asyncio.Event()
        self.task = None
        self.lobby = None
        self.role_view = None
        self.role_message = None

    def label_of(self, uid):
        return self.players[uid].display_name[:100]

    def name_of(self, uid):
        return discord.utils.escape_markdown(self.players[uid].display_name)

    def player_options(self, ids):
        ordered = sorted(ids, key=lambda u: self.label_of(u).lower())
        return [discord.SelectOption(label=self.label_of(u), value=str(u)) for u in ordered]

    def assign(self):
        raise NotImplementedError

    def role_text(self, uid):
        raise NotImplementedError

    async def run(self):
        raise NotImplementedError

    async def show_roles(self, embed):
        self.role_view = RoleView(self)
        self.role_message = await safe_send(self.channel, embed=embed, view=self.role_view)

    async def ask(self, interaction, prompt, options, handler):
        view = PickView(self, self.phase, options, handler)
        await interaction.response.send_message(prompt, view=view, ephemeral=True)

    async def wait_for_wake(self, seconds):
        try:
            await asyncio.wait_for(self.wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def vote_phase(self, embed, seconds):
        self.phase = "vote"
        self.votes = {}
        self.wake.clear()
        view = VoteView(self)
        message = await safe_send(self.channel, embed=embed, view=view)
        try:
            await self.wait_for_wake(seconds)
        finally:
            self.phase = "closed"
            await retire(view, message)
        counts = Counter(t for t in self.votes.values() if t)
        if not counts:
            return None
        top = max(counts.values())
        leaders = [t for t, c in counts.items() if c == top]
        if len(leaders) != 1:
            return None
        return leaders[0]

    async def cleanup(self):
        if self.role_view:
            await retire(self.role_view, self.role_message)


class NightGame(PartyGame):
    night_seconds = 45
    day_seconds = 90
    night_text = ""
    intro_text = ""
    win_titles = {}
    win_texts = {}

    def reset_night(self):
        pass

    def required_actors(self):
        return set(self.alive)

    def resolve_night(self):
        raise NotImplementedError

    def winner(self):
        raise NotImplementedError

    async def night_click(self, interaction):
        raise NotImplementedError

    def check_night_done(self):
        if self.required_actors() <= self.acted:
            self.wake.set()

    async def run(self):
        await self.show_roles(make_embed(f"{self.name} Begins", self.intro_text, COLOR_MAIN))
        await asyncio.sleep(15)
        while True:
            self.round += 1
            await self.night()
            result = self.winner()
            if result:
                break
            await self.day()
            result = self.winner()
            if result:
                break
        await self.finish(result)

    async def night(self):
        self.phase = "night"
        self.acted = set()
        self.reset_night()
        self.wake.clear()
        view = NightView(self)
        message = await safe_send(
            self.channel,
            embed=make_embed(f"Night {self.round}", self.night_text, COLOR_DARK, f"You have {self.night_seconds} seconds"),
            view=view
        )
        try:
            await self.wait_for_wake(self.night_seconds)
        finally:
            self.phase = "closed"
            await retire(view, message)
        text = self.resolve_night()
        await safe_send(self.channel, embed=make_embed(f"Morning {self.round}", text, COLOR_WARN))

    async def day(self):
        embed = make_embed(
            f"Day {self.round}",
            "Talk it over, then tap **Cast Vote** to remove someone you suspect.",
            COLOR_MAIN,
            f"You have {self.day_seconds} seconds"
        )
        target = await self.vote_phase(embed, self.day_seconds)
        if target is None:
            await safe_send(self.channel, embed=make_embed("Vote Result", "The votes were tied or skipped. Nobody was removed.", COLOR_DARK))
            return
        self.alive.discard(target)
        role = self.role_names[self.roles[target]]
        await safe_send(self.channel, embed=make_embed("Vote Result", f"**{self.name_of(target)}** was removed. Their role was **{role}**.", COLOR_BAD))

    async def finish(self, result):
        lines = []
        for uid, role in self.roles.items():
            status = "" if uid in self.alive else " (out)"
            lines.append(f"• {self.name_of(uid)}: {self.role_names[role]}{status}")
        embed = make_embed(self.win_titles[result], self.win_texts[result], COLOR_GOOD)
        embed.add_field(name="Roles", value="\n".join(lines), inline=False)
        await safe_send(self.channel, embed=embed)


class MafiaGame(NightGame):
    name = "Mafia"
    lobby_text = "Hidden roles, night attacks and daytime votes. The town must find the Mafia before it is too late."
    intro_text = "Tap **See My Role** to check your role in private. The first night starts in 15 seconds."
    night_text = "The town falls asleep. Mafia, Doctor and Detective, tap **Night Action** to make your move."
    role_names = {"mafia": "Mafia", "doctor": "Doctor", "detective": "Detective", "villager": "Villager"}
    win_titles = {"town": "The Town Wins", "mafia": "The Mafia Wins"}
    win_texts = {
        "town": "Every member of the Mafia has been found.",
        "mafia": "The Mafia now equals or outnumbers the town."
    }

    def assign(self):
        ids = list(self.players)
        random.shuffle(ids)
        mafia_count = max(1, len(ids) // 4)
        roles = ["mafia"] * mafia_count + ["doctor", "detective"]
        roles += ["villager"] * (len(ids) - len(roles))
        for uid, role in zip(ids, roles):
            self.roles[uid] = role
        self.alive = set(ids)

    def role_text(self, uid):
        role = self.roles[uid]
        if role == "mafia":
            team = ", ".join(self.name_of(u) for u in self.roles if self.roles[u] == "mafia")
            return f"You are **Mafia**. Each night, pick one person to eliminate.\nYour team: {team}"
        if role == "doctor":
            return "You are the **Doctor**. Each night, pick one person to protect. You may protect yourself."
        if role == "detective":
            return "You are the **Detective**. Each night, pick one person to find out if they are Mafia."
        return "You are a **Villager**. Talk, think and vote out the Mafia."

    def reset_night(self):
        self.kill_votes = {}
        self.saved = None

    def required_actors(self):
        return {u for u in self.alive if self.roles[u] != "villager"}

    def winner(self):
        mafia = sum(1 for u in self.alive if self.roles[u] == "mafia")
        town = len(self.alive) - mafia
        if mafia == 0:
            return "town"
        if mafia >= town:
            return "mafia"
        return None

    async def night_click(self, interaction):
        uid = interaction.user.id
        role = self.roles[uid]
        if role == "villager":
            return await interaction.response.send_message("You have no night action. Stay quiet until morning.", ephemeral=True)
        if uid in self.acted:
            return await interaction.response.send_message("You already made your choice tonight.", ephemeral=True)
        if role == "mafia":
            pool = [u for u in self.alive if self.roles[u] != "mafia"]
            prompt = "Choose someone to eliminate."
        elif role == "doctor":
            pool = list(self.alive)
            prompt = "Choose someone to protect."
        else:
            pool = [u for u in self.alive if u != uid]
            prompt = "Choose someone to investigate."

        async def handler(inter, target):
            if uid in self.acted:
                return await inter.response.edit_message(content="You already made your choice tonight.", view=None)
            self.acted.add(uid)
            who = self.name_of(target)
            if role == "mafia":
                self.kill_votes[uid] = target
                text = f"You chose **{who}**."
            elif role == "doctor":
                self.saved = target
                text = f"You will protect **{who}** tonight."
            else:
                verdict = "is **Mafia**" if self.roles[target] == "mafia" else "is **not Mafia**"
                text = f"**{who}** {verdict}."
            await inter.response.edit_message(content=text, view=None)
            self.check_night_done()

        await self.ask(interaction, prompt, self.player_options(pool), handler)

    def resolve_night(self):
        if not self.kill_votes:
            return "Nobody was attacked. It was a quiet night."
        counts = Counter(self.kill_votes.values())
        top = max(counts.values())
        victim = random.choice([t for t, c in counts.items() if c == top])
        if victim == self.saved:
            return "The Mafia attacked someone, but the Doctor saved them. Nobody died."
        self.alive.discard(victim)
        role = self.role_names[self.roles[victim]]
        return f"**{self.name_of(victim)}** was found dead. Their role was **{role}**."


class FinalNightGame(NightGame):
    name = "Final Night"
    lobby_text = "Survivors hide in three rooms while assassins strike. Find the assassins before they take over."
    intro_text = "Tap **See My Role** to check your role in private. The first night starts in 15 seconds."
    night_text = "Everyone picks a room. Survivors hide. Assassins strike. Tap **Night Action** to choose."
    night_seconds = 40
    role_names = {"assassin": "Assassin", "survivor": "Survivor"}
    win_titles = {"survivors": "The Survivors Win", "assassins": "The Assassins Win"}
    win_texts = {
        "survivors": "Every assassin has been found.",
        "assassins": "The assassins now equal or outnumber the survivors."
    }

    def assign(self):
        ids = list(self.players)
        random.shuffle(ids)
        assassin_count = max(1, len(ids) // 4)
        for index, uid in enumerate(ids):
            self.roles[uid] = "assassin" if index < assassin_count else "survivor"
        self.alive = set(ids)

    def role_text(self, uid):
        if self.roles[uid] == "assassin":
            team = ", ".join(self.name_of(u) for u in self.roles if self.roles[u] == "assassin")
            return f"You are an **Assassin**. Each night, pick a room to strike. One survivor hiding there will be caught.\nYour team: {team}"
        return "You are a **Survivor**. Each night, pick a room to hide in. Stay alive and vote out the assassins."

    def reset_night(self):
        self.rooms = {}

    def winner(self):
        assassins = sum(1 for u in self.alive if self.roles[u] == "assassin")
        survivors = len(self.alive) - assassins
        if assassins == 0:
            return "survivors"
        if assassins >= survivors:
            return "assassins"
        return None

    async def night_click(self, interaction):
        uid = interaction.user.id
        if uid in self.acted:
            return await interaction.response.send_message("You already made your choice tonight.", ephemeral=True)
        options = [discord.SelectOption(label=f"Room {n}", value=str(n)) for n in (1, 2, 3)]
        prompt = "Choose a room to strike." if self.roles[uid] == "assassin" else "Choose a room to hide in."

        async def handler(inter, target):
            if uid in self.acted:
                return await inter.response.edit_message(content="You already made your choice tonight.", view=None)
            self.acted.add(uid)
            self.rooms[uid] = target
            await inter.response.edit_message(content=f"You chose **Room {target}**.", view=None)
            self.check_night_done()

        await self.ask(interaction, prompt, options, handler)

    def resolve_night(self):
        for uid in self.alive:
            if uid not in self.rooms:
                self.rooms[uid] = random.randint(1, 3)
        strikes = [self.rooms[u] for u in self.alive if self.roles[u] == "assassin" and u in self.acted]
        if not strikes:
            return "The night was quiet. Nobody was caught."
        counts = Counter(strikes)
        top = max(counts.values())
        room = random.choice([r for r, c in counts.items() if c == top])
        hiding = [u for u in self.alive if self.roles[u] == "survivor" and self.rooms[u] == room]
        if not hiding:
            return f"The assassins struck **Room {room}**, but nobody was inside."
        victim = random.choice(hiding)
        self.alive.discard(victim)
        return f"The assassins struck **Room {room}**. **{self.name_of(victim)}** was caught."


class AgentGame(PartyGame):
    name = "Guess the Agent"
    min_players = 5
    lobby_text = (
        "Everyone gets a word. **Agent Black** gets the real word, **Agent Red** gets a similar word, "
        "and **Agent White** gets nothing. Give clues, find the agents and vote them out."
    )
    role_label = "See My Word"
    role_names = {"black": "Agent Black", "red": "Agent Red", "white": "Agent White"}

    def assign(self):
        ids = list(self.players)
        random.shuffle(ids)
        red_count = 1 if len(ids) < 8 else 2
        roles = ["white"] + ["red"] * red_count
        roles += ["black"] * (len(ids) - len(roles))
        for uid, role in zip(ids, roles):
            self.roles[uid] = role
        self.word, self.similar = random.choice(WORD_PAIRS)
        self.alive = set(ids)

    def role_text(self, uid):
        role = self.roles[uid]
        if role == "black":
            return f"Your word is **{self.word}**."
        if role == "red":
            return f"Your word is **{self.similar}**."
        return "You have no word. Listen to the clues and blend in."

    def winner(self):
        agents = sum(1 for u in self.alive if self.roles[u] != "black")
        black = len(self.alive) - agents
        if agents == 0:
            return "black"
        if agents >= black:
            return "agents"
        return None

    async def run(self):
        await self.show_roles(make_embed(
            "Guess the Agent Begins",
            "Tap **See My Word** to check your word in private. The first round starts in 15 seconds.",
            COLOR_MAIN
        ))
        await asyncio.sleep(15)
        while True:
            self.round += 1
            self.phase = "clues"
            await safe_send(self.channel, embed=make_embed(
                f"Round {self.round}: Clues",
                "Each player gives one short clue about their word in chat. Do not say the word itself. Voting starts in 60 seconds.",
                COLOR_WARN
            ))
            await asyncio.sleep(60)
            embed = make_embed(
                f"Round {self.round}: Vote",
                "Tap **Cast Vote** to pick who you think is an agent.",
                COLOR_MAIN,
                "You have 60 seconds"
            )
            target = await self.vote_phase(embed, 60)
            if target is None:
                await safe_send(self.channel, embed=make_embed("Vote Result", "The votes were tied or skipped. Nobody was removed.", COLOR_DARK))
            else:
                self.alive.discard(target)
                role = self.role_names[self.roles[target]]
                await safe_send(self.channel, embed=make_embed("Vote Result", f"**{self.name_of(target)}** was removed. Their role was **{role}**.", COLOR_BAD))
            result = self.winner()
            if result:
                break
        await self.finish(result)

    async def finish(self, result):
        if result == "black":
            title, text = "Agent Black Wins", "All the agents were found."
        else:
            title, text = "The Agents Win", "The agents now equal or outnumber the real players."
        lines = []
        for uid, role in self.roles.items():
            status = "" if uid in self.alive else " (out)"
            lines.append(f"• {self.name_of(uid)}: {self.role_names[role]}{status}")
        embed = make_embed(title, f"{text}\nReal word: **{self.word}**\nSimilar word: **{self.similar}**", COLOR_GOOD)
        embed.add_field(name="Roles", value="\n".join(lines), inline=False)
        await safe_send(self.channel, embed=embed)


class Games(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._num_games = {}
        self._word_games = {}
        self._chain_games = {}
        self._party_games = {}
        self._count_locks = {}

    async def cog_load(self):
        self.cleanup.start()

    async def cog_unload(self):
        self.cleanup.cancel()
        for store in (self._num_games, self._word_games, self._chain_games):
            for cid in list(store):
                self._close(store, cid)
        for game in list(self._party_games.values()):
            if game.task:
                game.task.cancel()
        self._party_games.clear()

    async def cog_command_error(self, ctx, error):
        error = getattr(error, "original", error)
        text = None
        if isinstance(error, commands.CommandOnCooldown):
            text = f"Please wait {error.retry_after:.0f} seconds before using this again."
        elif isinstance(error, commands.NoPrivateMessage):
            text = "Games only work inside servers."
        elif isinstance(error, commands.BotMissingPermissions):
            text = "I need permission to send messages and embed links in this channel."
        elif isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            text = "Please enter a number of seconds between 5 and 300."
        if text:
            try:
                await ctx.reply(text, ephemeral=True)
            except discord.HTTPException:
                pass

    @tasks.loop(seconds=30)
    async def cleanup(self):
        now = time.monotonic()
        for store in (self._num_games, self._word_games, self._chain_games):
            for cid, game in list(store.items()):
                if now - game["last"] > IDLE_LIMIT:
                    self._close(store, cid)
                    await safe_send(game["channel"], embed=self._summary(game, "Game Closed", COLOR_DARK, "This game was closed because nobody played for a while.\n"))

    @cleanup.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    def _close(self, store, cid):
        game = store.pop(cid, None)
        if game and game.get("timer"):
            game["timer"].cancel()
        return game

    def _channel_busy(self, cid):
        return (
            cid in self._num_games
            or cid in self._word_games
            or cid in self._chain_games
            or cid in self._party_games
        )

    def _active_in_guild(self, gid):
        total = 0
        for store in (self._num_games, self._word_games, self._chain_games):
            total += sum(1 for g in store.values() if g["guild"] == gid)
        total += sum(1 for g in self._party_games.values() if g.channel.guild.id == gid)
        return total

    def _start_block(self, ctx):
        if self._channel_busy(ctx.channel.id):
            return "A game is already running in this channel."
        if self._active_in_guild(ctx.guild.id) >= MAX_GAMES_PER_SERVER:
            return "This server has too many games running right now. Please wait for one to finish."
        return None

    def _summary(self, game, title, color=COLOR_DARK, note=""):
        kind = game["kind"]
        if kind == "number":
            text = f"The secret number was **{game['number']}**."
        elif kind == "word":
            text = f"The secret word was **{game['word']}**."
        else:
            board = "\n".join(
                f"{i}. <@{uid}>: {score}" for i, (uid, score) in enumerate(game["scores"].most_common(5), 1)
            ) or "No words were played."
            text = f"Words played: **{len(game['words'])}**\n{board}"
        return make_embed(title, note + text, color)

    async def run_party(self, game):
        try:
            await game.run()
        except Exception:
            log.exception("Party game crashed")
            await safe_send(game.channel, embed=make_embed("Game Stopped", "Something went wrong, so the game was closed.", COLOR_BAD))
        finally:
            game.phase = "over"
            if self._party_games.get(game.channel.id) is game:
                del self._party_games[game.channel.id]
            await game.cleanup()

    async def _stop_party(self, game):
        was_lobby = game.phase == "lobby"
        game.phase = "over"
        game.wake.set()
        if self._party_games.get(game.channel.id) is game:
            del self._party_games[game.channel.id]
        if game.task and not game.task.done():
            game.task.cancel()
        if was_lobby and game.lobby:
            await game.lobby.close("The host ended this lobby.")

    async def _open_lobby(self, ctx, game_cls):
        block = self._start_block(ctx)
        if block:
            return await ctx.reply(block, ephemeral=True)
        game = game_cls(self, ctx.channel, ctx.author)
        self._party_games[ctx.channel.id] = game
        view = LobbyView(game)
        game.lobby = view
        try:
            view.message = await ctx.reply(embed=view.build(), view=view)
        except discord.HTTPException:
            self._party_games.pop(ctx.channel.id, None)
            raise

    async def _start_number(self, ctx, top, seconds):
        block = self._start_block(ctx)
        if block:
            return await ctx.reply(block, ephemeral=True)
        game = {
            "kind": "number",
            "channel": ctx.channel,
            "guild": ctx.guild.id,
            "host": ctx.author.id,
            "number": random.randint(1, top),
            "attempts": 0,
            "tries": Counter(),
            "last": time.monotonic(),
            "timer": None,
        }
        self._num_games[ctx.channel.id] = game
        if seconds:
            embed = make_embed(
                f"Timed Number Guess ({seconds}s)",
                f"I picked a number between 1 and {top:,}. You have **{seconds} seconds**. Type your guess in chat.",
                COLOR_WARN
            )
        else:
            embed = make_embed(
                "Number Guessing Game",
                f"I picked a number between 1 and {top:,}. Type your guess in chat.",
                COLOR_MAIN
            )
        await ctx.reply(embed=embed)
        if seconds:
            game["timer"] = asyncio.create_task(self._timed_end(game, seconds))

    async def _timed_end(self, game, seconds):
        await asyncio.sleep(seconds)
        cid = game["channel"].id
        if self._num_games.get(cid) is game:
            self._num_games.pop(cid, None)
            await safe_send(game["channel"], embed=make_embed(
                "Time Is Up",
                f"Nobody found it. The secret number was **{game['number']}**.",
                COLOR_BAD
            ))

    @game_command(name="guess", description="Start a number guessing game (1 to 100,000)")
    async def guess(self, ctx):
        await self._start_number(ctx, 100000, None)

    @game_command(name="guesstime", description="Start a timed number guessing game (1 to 1,000)")
    async def guesstime(self, ctx, seconds: commands.Range[int, 5, 300]):
        await self._start_number(ctx, 1000, seconds)

    @game_command(name="endgame", description="End the game running in this channel")
    async def endgame(self, ctx):
        cid = ctx.channel.id
        found_store = None
        for store in (self._num_games, self._word_games, self._chain_games):
            if cid in store:
                found_store = store
                break
        party = self._party_games.get(cid)
        if not found_store and not party:
            return await ctx.reply("There is no game running in this channel.", ephemeral=True)

        host_id = party.host.id if party else found_store[cid]["host"]
        if ctx.author.id != host_id and not ctx.author.guild_permissions.manage_messages:
            return await ctx.reply("Only the game host or a moderator can end this game.", ephemeral=True)

        if party:
            await self._stop_party(party)
            embed = make_embed("Game Ended", f"The {party.name} game was ended.", COLOR_DARK)
        else:
            game = self._close(found_store, cid)
            embed = self._summary(game, "Game Ended")
        await ctx.reply(embed=embed)

    @game_command(name="guessword", aliases=["gw"], description="Start Guess the Word (the host gets the secret word)")
    async def guessword(self, ctx):
        block = self._start_block(ctx)
        if block:
            return await ctx.reply(block, ephemeral=True)
        word = random.choice(WORDS_POOL)
        try:
            await ctx.author.send(embed=make_embed(
                "Your Secret Word",
                f"The word is **{word}**.\nGive clues in {ctx.channel.mention} without saying the word.",
                COLOR_WARN
            ))
        except discord.HTTPException:
            return await ctx.reply("I could not send you a direct message. Please allow direct messages from this server and try again.", ephemeral=True)
        block = self._start_block(ctx)
        if block:
            return await ctx.reply(block, ephemeral=True)
        self._word_games[ctx.channel.id] = {
            "kind": "word",
            "channel": ctx.channel,
            "guild": ctx.guild.id,
            "host": ctx.author.id,
            "word": word,
            "last": time.monotonic(),
            "timer": None,
        }
        await ctx.reply(embed=make_embed(
            "Guess the Word",
            f"{ctx.author.mention} knows the secret word and will give clues.\nThe word has **{len(word)}** letters. Everyone else, type your guess in chat.",
            COLOR_MAIN
        ))

    @game_command(name="wordchain", aliases=["wc"], description="Start a Word Chain game")
    async def wordchain(self, ctx):
        block = self._start_block(ctx)
        if block:
            return await ctx.reply(block, ephemeral=True)
        letter = random.choice(CHAIN_LETTERS)
        self._chain_games[ctx.channel.id] = {
            "kind": "chain",
            "channel": ctx.channel,
            "guild": ctx.guild.id,
            "host": ctx.author.id,
            "letter": letter,
            "last_user": None,
            "words": set(),
            "scores": Counter(),
            "last": time.monotonic(),
            "timer": None,
        }
        await ctx.reply(embed=make_embed(
            "Word Chain Started",
            f"The first letter is **{letter.upper()}**.\nType a word that starts with it. Each new word must start with the last letter of the word before.",
            COLOR_GOOD,
            "Words need at least 3 letters. No repeats, and no going twice in a row."
        ))

    @game_command(name="mafia", aliases=["ma"], description="Start a Mafia game")
    async def mafia(self, ctx):
        await self._open_lobby(ctx, MafiaGame)

    @game_command(name="guessagent", aliases=["gta"], description="Start Guess the Agent")
    async def guessagent(self, ctx):
        await self._open_lobby(ctx, AgentGame)

    @game_command(name="finalnight", aliases=["fn"], description="Start Final Night")
    async def finalnight(self, ctx):
        await self._open_lobby(ctx, FinalNightGame)

    @game_command(name="stats", description="View server game stats")
    async def stats(self, ctx):
        gid = ctx.guild.id
        cfg = await self._get_counting(str(gid))
        count_val = cfg.get("count", 0) if cfg else 0
        high_score = cfg.get("high_score", 0) if cfg else 0
        number_games = sum(1 for g in self._num_games.values() if g["guild"] == gid)
        chain_games = sum(1 for g in self._chain_games.values() if g["guild"] == gid)

        embed = make_embed("Game Stats", "A quick look at this server.", COLOR_DARK, "Scores come from counting progress and active games.")
        embed.add_field(name="Current Count", value=str(count_val), inline=True)
        embed.add_field(name="Highest Count", value=str(high_score), inline=True)
        embed.add_field(name="Games Running", value=str(self._active_in_guild(gid)), inline=True)
        embed.add_field(name="Number Games", value=str(number_games), inline=True)
        embed.add_field(name="Word Chains", value=str(chain_games), inline=True)
        await ctx.reply(embed=embed)

    async def _get_counting(self, gid):
        try:
            return await counting_col.find_one({"guild_id": gid})
        except Exception:
            log.exception("Counting lookup failed")
            return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        content = message.content.strip()
        if not content or len(content) > 100:
            return

        cid = message.channel.id
        is_number = content.isascii() and content.isdigit()

        game = self._num_games.get(cid)
        if game:
            if is_number and len(content) <= 7:
                await self._handle_number(message, game, int(content))
            return

        game = self._word_games.get(cid)
        if game:
            await self._handle_word(message, game, content)
            return

        game = self._chain_games.get(cid)
        if game:
            await self._handle_chain(message, game, content)
            return

        if cid in self._party_games:
            return

        if is_number and len(content) <= 15:
            await self._handle_counting(message, int(content))

    async def _handle_number(self, message, game, guess):
        cid = message.channel.id
        uid = message.author.id
        game["attempts"] += 1
        game["tries"][uid] += 1
        game["last"] = time.monotonic()
        number = game["number"]

        if guess == number:
            self._close(self._num_games, cid)
            await safe_send(message.channel, embed=make_embed(
                "Correct Number",
                f"{message.author.mention} found **{number}**!\nTheir guesses: **{game['tries'][uid]}**\nTotal guesses: **{game['attempts']}**",
                COLOR_GOOD
            ))
        elif guess < number:
            await safe_reply(message, "Too low. Try a higher number.")
        else:
            await safe_reply(message, "Too high. Try a lower number.")

    async def _handle_word(self, message, game, content):
        if message.author.id == game["host"]:
            return
        game["last"] = time.monotonic()
        if content.lower() != game["word"]:
            return
        cid = message.channel.id
        if self._word_games.get(cid) is not game:
            return
        self._close(self._word_games, cid)
        await safe_send(message.channel, embed=make_embed(
            "Word Guessed",
            f"{message.author.mention} guessed the secret word **{game['word']}**!",
            COLOR_GOOD
        ))

    async def _handle_chain(self, message, game, content):
        word = content.lower()
        if not (word.isascii() and word.isalpha() and 3 <= len(word) <= 25):
            return
        uid = message.author.id
        if word[0] != game["letter"]:
            await safe_react(message, "❌")
            return
        if uid == game["last_user"]:
            await safe_react(message, "❌")
            await safe_reply(message, "Let another player go first.", delete_after=5)
            return
        if word in game["words"]:
            await safe_react(message, "❌")
            await safe_reply(message, "That word was already used.", delete_after=5)
            return
        game["words"].add(word)
        game["letter"] = word[-1]
        game["last_user"] = uid
        game["scores"][uid] += 1
        game["last"] = time.monotonic()
        await safe_react(message, "✅")
        await safe_reply(message, f"Next letter: **{game['letter'].upper()}**")

    async def _handle_counting(self, message, num):
        gid = str(message.guild.id)
        cfg = await self._get_counting(gid)
        if not cfg or str(cfg.get("channel_id")) != str(message.channel.id):
            return

        lock = self._count_locks.setdefault(gid, asyncio.Lock())
        async with lock:
            cfg = await self._get_counting(gid)
            if not cfg:
                return
            expected = cfg.get("count", 0) + 1
            last_user = cfg.get("last_user_id")
            current_high = cfg.get("high_score", 0)
            author = message.author

            if last_user is not None and str(author.id) == str(last_user):
                await counting_col.update_one({"guild_id": gid}, {"$set": {"count": 0, "last_user_id": None}})
                await safe_react(message, "❌")
                await safe_send(message.channel, embed=make_embed(
                    "Chain Broken",
                    f"{author.mention} counted twice in a row. The count is back to **0**.",
                    COLOR_BAD
                ))
                return

            if num != expected:
                await counting_col.update_one({"guild_id": gid}, {"$set": {"count": 0, "last_user_id": None}})
                await safe_react(message, "❌")
                await safe_send(message.channel, embed=make_embed(
                    "Chain Broken",
                    f"{author.mention} typed **{num}**, but the next number was **{expected}**. The count is back to **0**.",
                    COLOR_BAD
                ))
                return

            await counting_col.update_one(
                {"guild_id": gid},
                {"$set": {"count": num, "last_user_id": str(author.id), "high_score": max(current_high, num)}}
            )
            await safe_react(message, "✅")


async def setup(bot):
    await bot.add_cog(Games(bot))