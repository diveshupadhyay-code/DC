# Happy Bot — Premium Edition

A feature-rich Discord bot with Indian vibe, premium system, and comprehensive moderation.

---

## Setup

1. Clone/upload to Render (or any Node host)
2. Copy `.env.example` to `.env` and fill in values
3. `pip install -r requirements.txt`
4. `python main.py`

### Render Setup
- Build Command: `pip install -r requirements.txt`
- Start Command: `python main.py`
- Environment Variables: `DISCORD_TOKEN`, `GROQ_API_KEY`, `MONGO_URL`

---

## Premium System

Premium is managed by the bot owner only.

```
,premium add server <guild_id>     — Activate premium for a server
,premium add user <user_id>        — Activate premium for a user  
,premium remove server <guild_id>  — Remove server premium
,premium remove user <user_id>     — Remove user premium
,premium list                      — List all premium entries
```

### Premium Features
- AI Chat (mention @Happy)
- Global Call between servers
- Button Roles
- VoiceMaster (temp VCs)
- Bump Reminder
- Custom Bot Status (per server)
- Personal Prefix (per user, across servers)

---

## Command Reference

### Prefix Commands (default `,`)

#### Prefix Management
| Command | Description |
|---------|-------------|
| `,prefix` | View current prefix |
| `,prefix set <symbol>` | Set server prefix (Admin) |
| `,prefix remove` | Reset to default `,` (Admin) |
| `,prefix self <symbol>` | Personal prefix — all servers (Premium) |
| `,prefix selfremove` | Remove personal prefix |

#### Moderation
| Command | Description |
|---------|-------------|
| `,kick @user [reason]` | Kick a member |
| `,ban @user [reason]` | Ban a member |
| `,unban <user_id>` | Unban a user |
| `,mute @user <minutes> [reason]` | Timeout a member |
| `,unmute @user` | Remove timeout |
| `,warn @user [reason]` | Warn a member |
| `,warnings [@user]` | View warning count |
| `,clearwarns @user` | Clear all warnings |
| `,softban @user [reason]` | Ban + unban (clears messages) |
| `,nickname @user [name]` | Change/reset nickname |

#### Lock System
| Command | Description |
|---------|-------------|
| `,lock [#channel]` | Lock text/thread/voice channel |
| `,unlock [#channel]` | Unlock channel |
| `,lockdown [reason]` | Lock ALL channels (emergency) |
| `,unlockdown` | Lift server lockdown |
| `,vclock [#vc]` | Lock voice channel |
| `,vcunlock [#vc]` | Unlock voice channel |

#### Jail
| Command | Description |
|---------|-------------|
| `,jailsetup` | Create jail role + channel (Admin) |
| `,jail @user [reason]` | Jail a member |
| `,unjail @user` | Release from jail |

#### Purge
| Command | Description |
|---------|-------------|
| `,purge <amount>` | Delete N messages |
| `,purge bots <amount>` | Delete bot messages |
| `,purge @user <amount>` | Delete user's messages |
| `,purge links <amount>` | Delete messages with links |

#### Roles
| Command | Description |
|---------|-------------|
| `,role add @user @role` | Add role to member |
| `,role remove @user @role` | Remove role from member |
| `,massrole add @everyone @role` | Add role to all members |
| `,massrole add bots @role` | Add role to all bots |
| `,reactionrole add <msg_link> <emoji> @role` | Reaction role |
| `,buttonrole @role Label \| @role2 Label2` | Button roles (Premium) |
| `,boosterrole @role` | Reward role for boosters |

#### Server Setup
| Command | Description |
|---------|-------------|
| `,quicksetup` | Auto-create channels, roles, categories |
| `,settings` | View server configuration dashboard |
| `,premiumrole @role` | Set premium members role |
| `,setupmute` | Create Muted/Image Muted/Reaction Muted roles |
| `,setstatus <text>` | Custom bot status for your server (Premium) |

#### Welcome / Bye
| Command | Description |
|---------|-------------|
| `,welcome set #channel` | Set welcome channel |
| `,welcome enable` | Enable welcome messages |
| `,welcome disable` | Disable welcome messages |
| `,setbye #channel` | Set + enable bye messages |

#### Logging
| Command | Description |
|---------|-------------|
| `,logs set #channel` | Set log channel |
| `,logs disable` | Disable logging |

#### Tickets
| Command | Description |
|---------|-------------|
| `,ticket setup` | Send ticket creation panel |
| `,ticket close` | Close current ticket |
| `,ticket add @user` | Add user to ticket |
| `,ticket remove @user` | Remove user from ticket |
| `,ticket staffrole @role` | Set staff role for ticket access |

Ticket types: General Help, Report a User, Join the Staff, Server Event

#### Leveling
| Command | Description |
|---------|-------------|
| `,level [@user]` | View level and XP |
| `,leaderboard` | Top 10 members by level |

#### Birthday
| Command | Description |
|---------|-------------|
| `,birthday [@user]` | View birthday |
| `,birthday set DD/MM` | Set your birthday |

#### Counters
| Command | Description |
|---------|-------------|
| `,counter create members #vc` | Member count VC |
| `,counter create bots #vc` | Bot count VC |
| `,counter create channels #vc` | Channel count VC |

#### Utility
| Command | Description |
|---------|-------------|
| `,userinfo [@user]` | User information |
| `,avatar [@user]` | User avatar |
| `,serverinfo` | Server stats |
| `,ping` | Bot latency |
| `,membercount` | Member/bot/human count |
| `,shrug [text]` | Send shrug |
| `,translate <lang> <text>` | Translate text |
| `,urban <word>` | Urban Dictionary lookup |

#### Fun / Roleplay
| Command | Description |
|---------|-------------|
| `,ship @user1 @user2` | Love match percentage |
| `,hot [@user]` | Hotness meter |
| `,hug/pat/slap/kiss/poke/highfive/bonk/cuddle @user` | Roleplay actions |

#### Profile
| Command | Description |
|---------|-------------|
| `,profile [@user]` | View profile card |
| `,profile bio <text>` | Set bio |
| `,profile location <city>` | Set location |

#### Announcement / Giveaway
| Command | Description |
|---------|-------------|
| `,announce [#channel] <text>` | Send announcement |
| `,giveaway <minutes> <winners> <prize>` | Start giveaway |

#### AFK
| Command | Description |
|---------|-------------|
| `,afk [reason]` | Set AFK status |

#### Sticky
| Command | Description |
|---------|-------------|
| `,sticky <text>` | Set sticky message |
| `,unsticky` | Remove sticky message |

#### Embed Builder
| Command | Description |
|---------|-------------|
| `,embed create` | Start new embed draft |
| `,embed title <text>` | Set title |
| `,embed description <text>` | Set description |
| `,embed color #hex` | Set border color |
| `,embed thumbnail <url>` | Set thumbnail |
| `,embed send [#channel]` | Send the embed |

#### Mimic / Echo
| Command | Description |
|---------|-------------|
| `,mimic @user <message>` | Send message as user (via webhook) |
| `,echo [#channel] <message>` | Send message as bot |

#### AutoMod
| Command | Description |
|---------|-------------|
| `,automod invite on/off` | Toggle anti-invite link |

#### Voice (Premium)
| Command | Description |
|---------|-------------|
| `,vcsetup` | Setup VoiceMaster temp VCs (Premium) |
| `,call` | Connect to another server (Premium) |
| `,hangup` | End the cross-server call (Premium) |

#### Bump Reminder (Premium)
| Command | Description |
|---------|-------------|
| `,bumpreminder on/off` | Toggle DISBOARD bump reminder (Premium) |

#### Owner Only
| Command | Description |
|---------|-------------|
| `,premium add/remove server/user <id>` | Manage premium |
| `,premium list` | List premium entries |
| `,aimode on/off` | Toggle AI chat globally |
| `,maintenance on/off` | Toggle maintenance mode |

---

## Slash Commands
All major commands also have `/` slash equivalents:
`/ping`, `/userinfo`, `/avatar`, `/kick`, `/ban`, `/warn`, `/mute`, `/clear`, `/announce`, `/afk`, `/level`, `/help`

---

## Notes
- Bot owner (hardcoded ID) can use ALL commands in any server without having any permissions
- AI chat requires Premium (server or user)
- Default prefix is `,` — can be changed per server or per user (premium)
- Welcome/bye messages are **opt-in** — disabled by default
- Render free tier may have cold starts; keep-alive Flask server handles this
