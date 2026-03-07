# 🤖 Happy Bot - Global Friend

**Happy** ek professional Discord bot hai jo India ki street vibe aur global standards ko mix karke banaya gaya hai. Ye bot sirf chatting nahi karta, balki server ko manage (Mod) aur members ka swagat (Welcome/Bye) bhi karta hai.

---

## ✨ Features

* **🧠 AI Chatting (Gemini 2.5 Flash):** Happy se @mention karke baat karo, wo ek dum Hinglish style mein reply dega.
* **🌍 Global Welcome & Bye:** National aur International members ke liye glassy glass-effect embeds.
* **🛡️ Power Moderation:** Kick, Ban, Mute (Timeout), aur Role management — sirf Admins ke liye.
* **💤 AFK System:** Jab kaam pe ho, toh `/afk` set karo. Koi mention karega toh Happy sambhaal lega.
* **🎭 Dynamic Status:** "Watching Masti with Bhai Log" status ke saath 24/7 online.
* **❤️ Auto Reactions:** Greetings (GM, GN, HBD) par auto heart reactions.

---

## 🚀 Commands List

### 🛠️ Admin Commands (Administrator Only)
| Command | Description |
| :--- | :--- |
| `/setwelcome` | Welcome message ka channel set karo. |
| `/setbye` | Server chhodne waale messages ka channel set karo. |
| `/kick` | Kisi member ko server se bahar nikaalo. |
| `/ban` | Kisi ko hamesha ke liye ban karo. |
| `/mute` | Member ko specific minutes ke liye timeout do. |
| `/role` | Role add ya remove karo. |
| `/warn` | Member ko warning embed bhejo. |

### 👤 Member Commands (For Everyone)
| Command | Description |
| :--- | :--- |
| `/afk` | Apna AFK status set karo (Reason ke saath). |
| `/userinfo` | Kisi bhi member ki details nikaalo. |
| `/avatar` | Kisi ka profile picture (DP) badi karke dekho. |
| `/stats` | Server ki stats (Members, Boosts) dekho. |
| `/ping` | Bot ki speed check karo. |
| `/help` | Saari commands ki list dekho. |

---

## 🛠️ Setup & Installation

1.  **Clone the Repo:**
    ```bash
    git clone [[https://github.com/diveshupadhyay-code/DC.git](https://github.com/diveshupadhyay-code/DC.git)]
    ```
2.  **Install Dependencies:**
    ```bash
    pip install discord.py google-genai flask python-dotenv
    ```
3.  **Environment Variables (.env):**
    Ek `.env` file banao aur ye details daalo:
    ```env
    DISCORD_TOKEN=your_bot_token_here
    GEMINI_API_KEY=your_google_api_key_here
    ```
4.  **Run the Bot:**
    ```bash
    python happy.py
    ```

---

## ⚠️ Important Note
Bot ko **Moderation** commands chalane ke liye Server Settings mein **Happy Role** ko baaki roles ke upar rakhna zaroori hai. Saath hi, Discord Developer Portal par **Server Members Intent** aur **Message Content Intent** ON hona chahiye.

---

## 🤝 Support
Agar koi locha ho ya bot "short circuit" ho jaye, toh Admin ko contact karo!

*Made with ❤️ for the Global Desi Community.*
