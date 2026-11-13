<div align="center">

# Happy Bot

**A friendly Discord bot with moderation, games, an economy, server safety tools and AI chat.**

Default command symbol: `,` · Type `,help` or `/help` to start

</div>

---

## What it does

| | |
|---|---|
| **Moderation** | Kick, ban, mute, warn, jail, lock channels and clear messages. Every action is saved as a case. |
| **Safety** | Auto-Mod, Anti-Nuke, Anti-Raid and Fake Perms keep your server protected. |
| **Fun** | Games, levels, birthdays, profiles, giveaways and roleplay actions. |
| **Economy** | Earn, save, borrow, trade and invest HC. |
| **Server tools** | Welcome and goodbye messages, tickets, roles, logs and counters. |
| **Premium** | Global calls, color roles, private voice rooms, bump reminders and AI chat. |

---

## Get started

**1. Download the project**

```
git clone https://github.com/diveshupadhyay-code/dc.git
cd happybot
```

**2. Install what the bot needs**

```
pip install -r requirements.txt
```

**3. Add your keys**

Copy `.env.example` to `.env`, then fill in the values.

| Key | What it is |
|-----|------------|
| `DISCORD_TOKEN` | Your bot token from the Discord Developer Portal |
| `MONGO_URL` | The link to your database |
| `GROQ_API_KEY` | The key for AI chat |
| `GROQ_MODEL` | Optional. Choose which AI model to use |

**4. Start the bot**

```
python main.py
```

**5. Set up your server**

Type `,quicksetup` in your server. It creates the basic channels and roles for you.

### Host it on Render

| Setting | Value |
|---------|-------|
| Build command | `pip install -r requirements.txt` |
| Start command | `python main.py` |
| Environment keys | `DISCORD_TOKEN`, `MONGO_URL`, `GROQ_API_KEY` |

> The free plan can be slow to wake up after a quiet period. A small web page keeps the bot awake.

---

## Keep it safe

- Never share your bot token, database link or AI key with anyone.
- Never upload your `.env` file. Add it to `.gitignore` so it stays on your computer.
- If a key is ever shown in public, reset it right away in its own website and use the new one.
- Give the bot only the permissions it needs.
- Keep the bot's role high in your role list so it can act.
- Only trust people you know well. Trusted people are never stopped by Anti-Nuke.
- Choose a log channel with `,logs set #channel` so you can see what happened.

---

## Premium

Only the bot owner can give or take Premium.

| Command | What it does |
|---------|--------------|
| `,premium` | See everyone who has Premium |
| `,premium add server <id>` | Give a server Premium |
| `,premium add user <id>` | Give a user Premium |
| `,premium remove server <id>` | Take Premium from a server |
| `,premium remove user <id>` | Take Premium from a user |
| `,premium info <server id>` | See Premium details for a server |

**For members:** AI chat, a Premium badge before your name, and half-price Catch.

**For servers:** Global Call, channel rules, auto reactions, color roles, private voice rooms, bump reminders, button roles, a custom bot status and Glow Up.

> AI chat needs Premium on the member **and** on the server.

Members can type `,perks` to see their status.

---

## Commands

Commands start with `,` unless your server picks another symbol. Items in `[ ]` are optional. Items in `< >` are needed.

For the full list, type `,help`. To search, type `,help <word>`.

### Moderation

| Command | What it does |
|---------|--------------|
| `,kick <@user> [reason]` | Remove a member |
| `,ban <@user> [reason]` | Ban a member |
| `,tempban <@user> <time>` | Ban for a set time, like `30m`, `2h` or `7d` |
| `,unban <user id>` | Unban someone |
| `,mute <@user> [time]` | Stop a member from chatting |
| `,unmute <@user>` | Let them chat again |
| `,warn <@user> [reason]` | Give a warning |
| `,warnings [@user]` | See warnings |
| `,clearwarns <@user>` | Delete all warnings |
| `,softban <@user>` | Ban, then unban, to wipe recent messages |
| `,nickname <@user> [name]` | Change or reset a nickname |
| `,purge <amount>` | Delete recent messages |
| `,purge bots/links/images <amount>` | Delete only one kind of message |
| `,purge @user <amount>` | Delete messages from one member |

### Locks and jail

| Command | What it does |
|---------|--------------|
| `,lock [#channel]` / `,unlock [#channel]` | Stop or allow chatting in a channel |
| `,vclock` / `,vcunlock` | Lock or unlock a voice channel |
| `,lockdown [reason]` | Lock every channel in an emergency |
| `,unlockdown` | End the lockdown |
| `,jailsetup` | Create the jail role and channel |
| `,jail <@user> [reason]` | Send a member to jail |
| `,unjail <@user>` | Free them |

### Safety

| Command | What it does |
|---------|--------------|
| `,automod` | Open the Auto-Mod panel |
| `,antinuke` | Open the Anti-Nuke panel |
| `,antiraid` | Open the Anti-Raid panel |
| `,fakeperms` | See roles that have fake permissions |

### Roles

| Command | What it does |
|---------|--------------|
| `,role add/remove <@user> <@role>` | Give or take a role |
| `,massrole add/remove <everyone/bots> <@role>` | Change roles for many members |
| `,reactionrole add <link> <emoji> <@role>` | Give a role when people react |
| `,buttonrole <@role> Label \| ...` | A panel where buttons give roles (Premium) |
| `,boosterrole <@role>` | A reward role for boosters |

### Server setup

| Command | What it does |
|---------|--------------|
| `,quicksetup` | Create basic channels and roles |
| `,settings` | See all server settings |
| `,prefix set <symbol>` | Change the command symbol |
| `,welcome set <#channel>` | Choose the welcome channel |
| `,bye set <#channel>` | Choose the goodbye channel |
| `,logs set <#channel>` | Choose where logs go |
| `,setupmute` | Create the mute roles |
| `,counter create <type> <#vc>` | Show a live count in a voice channel |
| `,ticket setup` | Post the ticket panel |
| `,announce [#channel] <text>` | Post an announcement |
| `,giveaway <time> <winners> <prize>` | Start a giveaway |

Welcome and goodbye messages are off until you turn them on.

### For everyone

| Command | What it does |
|---------|--------------|
| `,level [@user]` / `,leaderboard` | See levels and the top members |
| `,profile [@user]` | See a profile card |
| `,birthday set DD/MM` | Set your birthday |
| `,userinfo` / `,avatar` / `,serverinfo` | See details |
| `,balance` / `,daily` / `,work` | Earn and check your HC |
| `,afk [reason]` | Tell others you are away |
| `,translate <lang> <text>` | Turn text into another language |
| `,ship` / `,hot` / `,8ball` / `,hug` | Fun commands |

### Premium

| Command | What it does |
|---------|--------------|
| `,perks` | See your Premium status |
| `,call` / `,hangup` / `,callinfo` | Talk live with another server |
| `,vcsetup create` | Let members make private voice rooms |
| `,bumpreminder on/off` | Remind your server after a DISBOARD bump |
| `,setstatus <text>` | Add your own text to the bot's status |
| `,color [name]` | Pick a color role for your name |
| `,studio` | Open Server Studio |
| `,glowup` | Make your server look nice in one click |
| `,prefix self <symbol>` | Pick your own command symbol |

### AI chat

Mention **@Happy** or reply to a message from Happy. Admins choose where Happy chats.

| Command | What it does |
|---------|--------------|
| `,aiserver on/off` | Turn AI chat on or off in your server |
| `,aichannel add <#channel>` | Pick a channel for AI chat. You can pick up to 5 |
| `,aichannel remove <#channel>` | Stop AI chat in a channel |
| `,aichannel reset` | Go back to channels named `happy`, `bot` or `commands` |

### Bot owner

| Command | What it does |
|---------|--------------|
| `,aimode on/off` | Turn AI chat on or off everywhere |
| `,aiblock <server id> [remove]` | Block or allow AI chat in one server |
| `,maintenance on/off` | Turn maintenance mode on or off |

### Slash commands

Many commands also work with `/`, such as `/help`, `/ping`, `/userinfo`, `/avatar`, `/kick`, `/ban`, `/warn`, `/mute`, `/clear`, `/announce`, `/afk` and `/level`.

---

## Good to know

- The bot owner can use every command in any server.
- Each member can pick their own command symbol with Premium.
- Premium and AI chat can be turned off for a server by the bot owner at any time.

---

<div align="center">

Made with care. If Happy helps your server, share it with a friend.

</div>
