# 🤖 Happy Bot - The Ultimate Desi AI Friend & Server Manager

**Happy** ek next-level hybrid Discord bot hai jo India ki street vibe, high-end AI technology (Llama 3.3 via Groq), aur power-packed moderation features ko mix karke banaya gaya hai. 

Ye sirf chatting nahi karta, balki server connection, persistent ticket system, interactive embed building, dynamic profile cards aur server management bhi ekdum smooth sambhalta hai.

---

## ✨ Key Features

* **🧠 Brainy AI Chatting:** Llama-3.3-70b ka use karke ek dum natural Hinglish replies. Ye purani baatein yaad rakhta hai (Memory) aur typing effect ke saath reply deta hai.
* **🎫 Persistent Ticket System:** MongoDB-backed Discord UI buttons jo bot restart hone par bhi dead nahi hote. Single-click support channels banata hai!
* **🛡️ Power Moderation:** Bulk message deleting (Purge), Role management, Channel locking, Voice controls aur Softbans with hierarchy checks.
* **🌐 Translation & Slangs:** Google translate aur Urban Dictionary street lingo search direct chat ke andar asynchronously bina kisi lag ke.
* **📞 Global Call Matchmaking:** `/call` command se apne server ke channel ko kisi dusre random server se connect karo aur anjan logon se baatein karo!
* **🎭 Mimic (Webhook Magic):** `/mimic` command se kisi bhi member ka roop dhaar lo (Avatar + Name) aur unke naam se message bhejo.
* **💳 VIP Profile Cards:** Server ke members ke liye dynamic, editable profile identity cards (Bio + Location) jo server stats show karte hain.

---

## 🚀 Commands List

Bot dono **Slash Commands (`/`)** aur **Prefix Commands (`,` ya server custom prefix)** ko support karta hai.

### 🛡️ Moderation & Server Settings (Admins/Mods Only)
| Command | Format | Description |
| :--- | :--- | :--- |
| `,settings` | `,settings` | Server ke saare active configurations aur settings ek hi screen par dekho. |
| `,command` | `,command disable/enable [cmd]` | Kisi bhi command ko server ke liye ban ya unban karo. |
| `,softban` | `,softban @user [reason]` | User ko ban karke pichle 7 days ke messages clear karke instant unban karna. |
| `,role add` | `,role add @user @role` | User ko role assign karega (Hierarchy protected). |
| `,role remove` | `,role remove @user @role` | User se role remove karega (Hierarchy protected). |
| `,lock` | `,lock [#channel] [reason]` | Kisi bhi text channel ko locked/mute karne ke liye. |
| `,unlock` | `,unlock [#channel] [reason]` | Locked channel ko wapas normal/open karne ke liye. |
| `,vclock` | `,vclock [vc_channel]` | Voice channel ko lock karna taaki naye members join na kar sakein. |
| `,vcunlock` | `,vcunlock [vc_channel]` | Voice channel ko wapas unlock karna. |
| `,purge` | `,purge [100] / bots [50] / @user [20]` | Target filtering ke sath messages bulk-delete karna. |
| `,clearwarns`| `,clearwarns @user` | Database se user ki saari warnings/records delete karna. |
| `,nickname` | `,nickname @user [name]` | Kisi ka sasta name set karna, ya blank chhod kar default reset karna. |

### 🎫 Support Tickets & Embed Builder
| Command | Format | Description |
| :--- | :--- | :--- |
| `,ticket setup` | `,ticket setup` | Green button wala ticket support panel channel mein send karega. |
| `,ticket add` | `,ticket add @user` | Ticket channel ke andar kisi member ko add karne ke liye. |
| `,ticket remove`| `,ticket remove @user`| Ticket channel se kisi member ko remove karne ke liye. |
| `,ticket close`| `,ticket close` | Active support ticket channel ko delete aur wipe out karna. |
| `,embed` | `,embed create` | Naya empty custom embed draft start karna. |
| `,embed title` | `,embed title [text]` | Active draft ka main title/heading set karna. |
| `,embed description` | `,embed description [text]` | Active draft ka description/body set karna. |
| `,embed color` | `,embed color [hex_code]` | Embed ki side-line ka hexadecimal color set karna (e.g., `#FF0000`). |
| `,embed send` | `,embed send [#channel]` | Final designed embed ko target channel mein chipkana. |

### 🌍 Utilities & Fun (Everyone)
| Command | Format | Description |
| :--- | :--- | :--- |
| `,profile` | `,profile / set bio / set location` | Apna VIP identity card dekhna ya bio aur city set karna. |
| `,urban` | `,urban [word]` | Urban Dictionary se street slangs aur definitions search karna. |
| `,translate` | `,translate [lang] [text]` | Kisi bhi videshi message ko instant translated text mein badalna. |
| `,membercount`| `,membercount` | Server ke Humans aur Bots ka dynamic breakdown stats dekhna. |
| `,ship` | `,ship @user1 @user2` | Do dosto ke beech constant love matches check karna (Bavaal guarantee!). |
| `,hot` | `,hot @user` | Apne kisi dost ka 'hotness meter' visual progress bar ke sath nikalna. |
| `,shrug` | `,shrug [message]` | Message ke aage instant `¯\_(ツ)_/¯` shrug emoji lagana. |

### 🧠 Legacy Slash Commands (AI & Matchmaking)
| Command | Description |
| :--- | :--- |
| `/call` | Dusre random server ke call-matchmaking session se connect hona. |
| `/hangup` | Active call ko cut karna ya waiting list se hatna. |
| `/ai_mode` | AI engine ko poore server ke liye ON ya OFF karna. |
| `/mimic` | Webhook magic ke zariye kisi member ka roop lekar message bhejna. |
| `/afk` | Reason ke sath AFK status set karna. |
| `/userinfo` | Kisi bhi member ki "Kundli" (Details) nikalna. |
| `/avatar` | Kisi member ki avatar image ko badi karke dekhna. |
| `/giveaway`| Prize aur time set karke premium giveaway panel start karna. |

---

## 🛠️ Setup & Installation

1. **Clone the Repo:**
   ```bash
   git clone [https://github.com/diveshupadhyay-code/DC.git](https://github.com/diveshupadhyay-code/DC.git)
   cd DC
