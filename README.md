# 🤖 Happy Bot - The Ultimate Desi AI Friend

**Happy** ek next-level Discord bot hai jo India ki street vibe aur high-end AI technology (Llama 3.3) ko mix karke banaya gaya hai. Ye sirf chatting nahi karta, balki server connection, global matchmaking aur power moderation bhi sambhalta hai.

---

## ✨ Key Features

* **🧠 Brainy AI Chatting:** Llama-3.3-70b ka use karke ek dum natural Hinglish replies. Ye purani baatein yaad rakhta hai (Memory) aur typing effect ke saath reply deta hai.
* **📞 Global Call Matchmaking:** `/call` command se apne server ke channel ko kisi dusre random server se connect karo aur anjan logon se baatein karo!
* **🎭 Mimic (Webhook Magic):** `/mimic` command se kisi bhi member ka roop dhaar lo (Avatar + Name) aur unke naam se message bhejo.
* **🎁 Giveaway & Announcements:** Professional embeds ke saath giveaways aur `@everyone` pings ke saath announcements karo.
* **🛡️ Power Moderation:** Kick, Ban, Mute (Timeout), Warn, aur Role management commands.
* **💤 AFK System:** Jab kaam pe ho, toh `/afk` set karo. Happy sabko bata dega tum busy ho.

---

## 🚀 Commands List

### 🛠️ Admin & Setup (High-Level Control)
| Command | Description |
| :--- | :--- |
| `/setwelcome` | Welcome messages ke liye channel set karo. |
| `/setbye` | Goodbye messages ke liye channel set karo. |
| `/echo` | Happy ke zariye message bhejo (AI auto-off ho jati hai). |
| `/call` | Dusre server ke saath connection (Matchmaking) shuru karo. |
| `/hangup` | Active call ko cut karo ya waiting list se hato. |
| `/ai_mode` | Poore server ke liye AI Chatting ON/OFF karo. |
| `/mimic` | Kisi member ko copy karke message bhejo (Webhook logic). |
| `/giveaway` | Prize aur time set karke giveaway start karo. |

### 👤 Member Commands (For Everyone)
| Command | Description |
| :--- | :--- |
| `/afk` | Apna AFK status set karo (Reason ke saath). |
| `/userinfo` | Kisi bhi member ki "Kundli" (Details) nikaalo. |
| `/avatar` | Kisi ki DP (Avatar) churao ya badi karke dekho. |
| `/stats` | Server ka haal-chaal (Members, Boosts) dekho. |
| `/ping` | Happy ki speed (Latency) check karo. |

---

## 🛠️ Setup & Installation
1.  **Clone the Repo:**
    ```bash
    git clone https://github.com/diveshupadhyay-code/DC.git
    ```
2.  **Install Dependencies:**
    ```bash
    pip install discord.py groq pymongo flask python-dotenv pytz
    ```
3.  **Environment Variables (.env):**
    Ek `.env` file banao aur ye chaar cheezein zaroor daalo:
    ```env
    DISCORD_TOKEN=your_bot_token
    GROQ_API_KEY=your_groq_api_key
    MONGO_URL=your_mongodb_connection_string
    ```
4.  **Run the Bot:**
    ```bash
    python happy.py
    ```

---

## ⚠️ Important Configuration
* **Intents:** Discord Developer Portal par **Server Members Intent** aur **Message Content Intent** ka ON hona compulsory hai.
* **Role Hierarchy:** Moderation commands ke liye **Happy Role** ko baki roles se upar rakhein.
* **AI Session:** AI tabhi reply dega jab aap use `@mention` karenge, ya uske message ka `Reply` denge, ya fir active session (5 mins) ke beech mein baat karenge.

---

## 🤝 Support & Contribution
Agar bot "hang" ho jaye ya koi naya feature chahiye, toh admin ko poke karo! 

*Developed with 💖 for the Global Desi Community.*

---
