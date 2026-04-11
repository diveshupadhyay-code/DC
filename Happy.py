import discord
from discord.ext import commands, tasks
from discord import app_commands
from flask import Flask, render_template, request, redirect, url_for
from threading import Thread
import os, json
from dotenv import load_dotenv
import pytz # timezone ke liye
import time  # Isse time.time() chalega
import datetime
from datetime import datetime, timedelta, timezone 
import asyncio 
import random
from groq import Groq
from motor.motor_asyncio import AsyncIOMotorClient

# MongoDB collection variable
user_memories = {}
active_calls = {}  # {server_id: {'partner_id': id, 'channel_id': id}}
waiting_list = []  # List of dicts: [{'server_id': id, 'channel_id': id}]
ai_enabled = True  # By default AI on rahega
# Dictionary to track active sessions {channel_id: last_interaction_timestamp}
active_sessions = {} # {channel_id: last_active_timestamp}
SESSION_TIMEOUT = 300 # 5 minutes (seconds mein)


# --- MongoDB Setup ---
# Render ke Environment Variables mein MONGO_URL set karna (Tera link password ke saath)
MONGO_URL = os.getenv("MONGO_URL") 
cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["HappyBotDB"]
settings_col = db["server_settings"] # Collection for Welcome/Bye IDs
warns_col = db["warnings"] 
afk_col = db["afk_users"] 
sticky_col = db["sticky_messages"]
sticky_counter = {}

async def get_server_data(server_id):
    # .find_one() ke pehle await zaroori hai
    data = await settings_col.find_one({"_id": str(server_id)})
    return data if data else {}

async def update_server_data(server_id, key, value):
    # .update_one() ke pehle bhi await lagao
    await settings_col.update_one(
        {"_id": str(server_id)},
        {"$set": {key: value}},
        upsert=True
    )

ADMIN_PASS = "happydc"    
# --- Flask & AI Setup (Tera Original) ---
app = Flask('')
@app.route('/')
def home():
    # Dashboard pe dikhane ke liye data pack kar rahe hain
    stats = {
        "servers": len(bot.guilds),
        "users": len(bot.users),
        "ai_status": "ON" if ai_enabled else "OFF",
        "latency": round(bot.latency * 1000)
    }
    return render_template('dashboard.html', stats=stats)

@app.route('/toggle-ai', methods=['POST'])
def toggle_ai():
    global ai_enabled
    password = request.form.get("password")
    if password == ADMIN_PASS:
        ai_enabled = not ai_enabled
    return redirect(url_for('home'))

@app.route('/broadcast', methods=['POST'])
def broadcast():
    password = request.form.get("password")
    msg = request.form.get("msg")
    if password == ADMIN_PASS and msg:
        for guild in bot.guilds:
            if guild.system_channel:
                bot.loop.create_task(guild.system_channel.send(f"{msg}"))
    return redirect(url_for('home'))

def run():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
groq_client = Groq(api_key=GROQ_API_KEY)
MODEL_ID = "gemini-2.5-flash"
# Model name: llama-3.3-70b-versatile (Best balance of speed & smartness)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True 
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Status Loop ---
import random # Isko file ke ekdum upar rakhna top pe

@tasks.loop(seconds=15)
async def change_status():
    await bot.wait_until_ready()
    
    # Servers aur Users ka real count nikal lo
    guild_count = len(bot.guilds)
    member_count = sum(guild.member_count for guild in bot.guilds) # Zyada accurate count
    
    if ai_enabled:
        status_list = [
            f"👀 {member_count} Launde-Lapaate",
            f"🏢 {guild_count} Servers mein Raaj",
            "👂 Listening to @Happy",
            "✨ Type /help for Masti",
            "🤖 AI Mode: Full Power ✅",
            f"🚀 Latency: {round(bot.latency * 1000)}ms"
        ]
    else:
        status_list = [
            "🎤 Mod is chatting via Echo",
            "😴 AI Mode: Chilling/Off",
            "🛡️ Owner Control: ON",
            "👀 Watching you quietly...",
            f"📊 Monitoring {guild_count} Guilds"
        ]
    
    new_status = random.choice(status_list)
    
    # Activity type bhi random kar sakte hain thoda variation ke liye
    # Par tune 'watching' bola hai toh wahi rakhte hain with a twist
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching, 
            name=new_status
        )
    )
# --- Loop ko Start karne ke liye ---
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    if not change_status.is_running():
        change_status.start() # Loop shuru ho jayega
    
    # Slash commands sync karne ke liye
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands!")
    except Exception as e:
        print(e)
#functions
def get_color(color_str):
    try:
        # Agar user ne Hex code diya hai (e.g. #00ff00)
        return discord.Color.from_str(color_str)
    except:
        # Default color agar code galat ho
        return discord.Color.blue()
    
# --- ADMIN COMMANDS: Channel Choose Karne Ke Liye ---
def is_admin_or_owner():
    async def predicate(interaction: discord.Interaction):
        owner_id = 876629015144828939  
        # Agar user owner hai OR uske paas admin permission hai
        return interaction.user.id == owner_id or interaction.user.guild_permissions.administrator
    return app_commands.check(predicate)
    
def owner_is_present():
    async def predicate(interaction: discord.Interaction):
        owner_id = 876629015144828939  
        
        # Server ke members mein tumhe dhund raha hai
        owner_in_server = interaction.guild.get_member(owner_id)
        
        if owner_in_server:
            return True
        else:
            # Agar tum server mein nahi ho, toh ye error fekega
            raise app_commands.AppCommandError("Bhai, mere asli maalik is server mein nahi hain, toh main kaam nahi karunga!")
            
    return app_commands.check(predicate)
# --- Ye naya logic har server ka data alag rakhega ---

# --- Updated Admin Commands ---
@bot.tree.command(name="setwelcome", description="Is server ka welcome channel set karo")
@is_admin_or_owner() # Ab ye custom check kaam karega
# @app_commands.checks.has_permissions(administrator=True)
async def setwelcome(interaction: discord.Interaction, channel: discord.TextChannel):
    # 1. Sabse pehle ye line dalo (Ye 3 second ki limit ko 15 mins kar degi)
    await interaction.response.defer(ephemeral=True) 

    server_id = interaction.guild.id
    
    # 2. Ab MongoDB ka slow kaam hone do
    await update_server_data(server_id, "welcome_channel", channel.id)
    
    # 3. Ab response bhejne ke liye followup use karo (kyunki defer ho chuka hai)
    await interaction.followup.send(f"✅ Done bhai! Welcome messages ab {channel.mention} mein aayenge.")

@bot.tree.command(name="setbye", description="Is server ka bye channel set karo")
@is_admin_or_owner() # Ab ye custom check kaam karega
# @app_commands.checks.has_permissions(administrator=True)
async def setbye(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    server_id = interaction.guild.id
    await update_server_data(server_id, "bye_channel", channel.id)
    await interaction.followup.send(f"✅ Done! Bye messages {channel.mention} mein set ho gaye hain.")

# 1. KICK MEMBER
@bot.tree.command(name="kick", description="Kisi ko dhakke maar ke nikaalo")
@commands.has_permissions(administrator=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "Nuksan pahuncha raha tha"):
    await member.kick(reason=reason)
    await interaction.response.send_message(f"✅ {member.name} ko tata-bye-bye kar diya! Reason: {reason}")

# 2. BAN MEMBER
@bot.tree.command(name="ban", description="Hamesha ke liye block")
@commands.has_permissions(administrator=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "Galti ki saza"):
    await member.ban(reason=reason)
    await interaction.response.send_message(f"🚫 {member.name} ko ban kar diya! Ab ye wapas nahi aayega.")

# 3. MUTE (TIMEOUT)
@bot.tree.command(name="mute", description="Member ka munh band karo")
@commands.has_permissions(administrator=True)
async def mute(interaction: discord.Interaction, member: discord.Member, minutes: int):
    if member.top_role >= interaction.guild.me.top_role:
        return await interaction.response.send_message("Bhai, ye banda mujhse upar hai, main isse mute nahi kar sakta! 😂", ephemeral=True)
    
    try:
        # Naya logic: Utcnow use karke duration set karna
        duration = datetime.timedelta(minutes=minutes)
        await member.timeout(duration, reason="Admin Order")
        await interaction.response.send_message(f"🤐 {member.mention} ko {minutes} minute ke liye 'Kone' mein bhej diya gaya hai!")
    except Exception as e:
        await interaction.response.send_message(f"Arre yaar, error aa gaya: {e}", ephemeral=True)

# 4. ASSIGN/REMOVE ROLE
@bot.tree.command(name="role", description="Role dena ya lena")
@is_admin_or_owner() # Ab ye custom check kaam karega
# @commands.has_permissions(administrator=True)
async def role(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if role in member.roles:
        await member.remove_roles(role)
        await interaction.response.send_message(f"❌ {role.name} role {member.name} se le liya gaya.")
    else:
        await member.add_roles(role)
        await interaction.response.send_message(f"✅ {role.name} role {member.name} ko de diya gaya.")

# 5. WARNING (Simple message warning)
@bot.tree.command(name="warn", description="Kisi member ko warning do")
@is_admin_or_owner()
# @app_commands.checks.has_permissions(administrator=True)
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str = "Koi reason nahi diya"):
    await interaction.response.defer() # Slow network ke liye safety
    
    server_id = str(interaction.guild.id)
    user_id = str(member.id)

    # 1. Database se purani warnings nikalo (Yahan MongoDB use ho raha hai)
    # Humein ek naya collection chahiye hoga, let's call it 'warns_col'
    user_data = await warns_col.find_one({"server_id": server_id, "user_id": user_id})
    
    if user_data:
        new_count = user_data["count"] + 1
        await warns_col.update_one({"_id": user_data["_id"]}, {"$set": {"count": new_count}})
    else:
        new_count = 1
        await warns_col.insert_one({"server_id": server_id, "user_id": user_id, "count": 1})

    # 2. Ek Kadak Embed banao
    embed = discord.Embed(
        title="⚠️ Official Warning Issued",
        description=f"Oye {member.mention}, teri harkatein theek nahi lag rahi!",
        color=discord.Color.from_rgb(255, 0, 0), # Pure Red
        timestamp=datetime.now()
    )
    embed.add_field(name="👤 Member", value=member.name, inline=True)
    embed.add_field(name="👮 Warned By", value=interaction.user.name, inline=True)
    embed.add_field(name="📊 Total Warnings", value=f"**{new_count}**", inline=False)
    embed.add_field(name="📄 Reason", value=f"```{reason}```", inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Sudhar jao warna sidha BAN milega! 😎")

    # 3. Chat mein reply do
    await interaction.followup.send(content=f"Dhyan se dekhle {member.mention}!", embed=embed)

    # 4. Bande ko DM karo (Private Message)
    try:
        dm_embed = discord.Embed(
            title=f"Tumhe {interaction.guild.name} mein Warn kiya gaya hai!",
            description=f"Sudhar jao bhai, ye tumhari **Warning #{new_count}** hai.\n**Reason:** {reason}",
            color=discord.Color.orange()
        )
        await member.send(embed=dm_embed)
    except:
        # Agar bande ke DM band hain toh bot crash nahi hoga
        pass

# --- CALL COMMAND (Matchmaking) ---
@bot.tree.command(name="call", description="Isi channel ko dusre server se connect karein")
@is_admin_or_owner() # Ab ye custom check kaam karega
# @commands.has_permissions(administrator=True)
async def call(interaction: discord.Interaction):
    global waiting_list
    server_id = interaction.guild.id
    channel_id = interaction.channel.id # Jis channel mein command chali

    if server_id in active_calls:
        await interaction.response.send_message("❌ Bhai, aap pehle se call par ho!", ephemeral=True)
        return

    # Check agar ye server pehle se wait kar raha hai
    if any(d['server_id'] == server_id for d in waiting_list):
        await interaction.response.send_message("⏳ Waiting list mein ho bhai, thoda sabar!", ephemeral=True)
        return

    if waiting_list:
        # Match mil gaya!
        partner_data = waiting_list.pop(0)
        p_server_id = partner_data['server_id']
        p_channel_id = partner_data['channel_id']

        # Dono ko aapas mein connect karo
        # Ismein hum 'partner_channel' aur 'my_channel' dono save kar rahe hain
        active_calls[server_id] = {'partner_channel': p_channel_id, 'my_channel': channel_id}
        active_calls[p_server_id] = {'partner_channel': channel_id, 'my_channel': p_channel_id}

        await interaction.response.send_message("☎️ **Call Connected!** Ab aap is channel mein baatein kar sakte hain.")
        
        # Dusre server ke usi channel mein message bhejna
        partner_channel = bot.get_channel(p_channel_id)
        if partner_channel:
            await partner_channel.send("☎️ **Call Connected!** Ek partner mil gaya hai. Shuru ho jao!")
    else:
        # Waiting list mein daal do channel ID ke saath
        waiting_list.append({'server_id': server_id, 'channel_id': channel_id})
        await interaction.response.send_message("📡 **Waiting...** Jaise hi koi aur server call karega, main isi channel ko connect kar doonga.", ephemeral=True)

# --- HANGUP COMMAND (Error Proof) ---
@bot.tree.command(name="hangup", description="Call khatam karein ya waiting list se hatein")
@is_admin_or_owner() # Ab ye custom check kaam karega
# @commands.has_permissions(administrator=True)
async def hangup(interaction: discord.Interaction):
    global waiting_list
    server_id = interaction.guild.id
    
    # 1. Check karo: Kya server Waiting List mein hai?
    # Hum list comprehension use karenge server_id dhoondne ke liye
    server_in_waiting = next((d for d in waiting_list if d['server_id'] == server_id), None)
    
    if server_in_waiting:
        waiting_list.remove(server_in_waiting)
        await interaction.response.send_message("📴 Aap waiting list se hat gaye hain.", ephemeral=True)
        return

    # 2. Check karo: Kya server Active Call par hai?
    if server_id in active_calls:
        data = active_calls[server_id]
        partner_id = data.get('partner_id') or data.get('partner_server') # Safe get
        partner_channel_id = data.get('partner_channel')
        
        # Connection delete karo (Dono side se)
        if server_id in active_calls:
            del active_calls[server_id]
        if partner_id and partner_id in active_calls:
            del active_calls[partner_id]
            
        await interaction.response.send_message("📴 Call cut kar di gayi hai.")
        
        # Partner ko khabar kar do
        if partner_channel_id:
            partner_channel = bot.get_channel(partner_channel_id)
            if partner_channel:
                try:
                    await partner_channel.send("📴 **Partner ne call cut kar di hai.**")
                except:
                    pass
    else:
        # Kuch bhi nahi mila
        await interaction.response.send_message("Bhai, koi active call ya waiting request nahi mili!", ephemeral=True)



# ANNOUNCEMENT & EVENT PING COMMAND
@bot.tree.command(name="announce", description="Server mein event ya koi bada announcement karo")
@is_admin_or_owner() # Ab ye custom check kaam karega
# @commands.has_permissions(administrator=True)
async def announce(
    interaction: discord.Interaction, 
    title: str, 
    description: str, 
    ping: bool = False, 
    channel: discord.TextChannel = None
):
    # Agar channel select nahi kiya, toh current channel mein bhejega
    target_channel = channel or interaction.channel
    
    embed = discord.Embed(
        title=f"📢 {title}",
        description=description,
        color=0x2B2D31 # Wahi glassy theme
    )
    embed.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
    embed.set_footer(text=f"Announced by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()

    # Agar ping True hai, toh @everyone ke saath bhejega
    content = "@everyone" if ping else None

    await target_channel.send(content=content, embed=embed)
    await interaction.response.send_message(f"✅ Announcement {target_channel.mention} mein bhej di gayi hai!", ephemeral=True)

@bot.tree.command(name="giveaway", description="Server mein professional giveaway start karo")
@is_admin_or_owner()
# @app_commands.checks.has_permissions(administrator=True)
async def giveaway(
    interaction: discord.Interaction, 
    prize: str, 
    duration_minutes: int, 
    winners_count: int = 1
):
    # 1. Countdown Timer (Discord style: <t:timestamp:R>)
    end_time = datetime.now() + timedelta(minutes=duration_minutes)
    timestamp = int(end_time.timestamp())
    
    embed = discord.Embed(
        title="🎁 NEW GIVEAWAY! 🎁",
        description=(
            f"Bhaiyo aur unki Behno, **{prize}** ka giveaway shuru ho gaya hai!\n\n"
            f"🎉 React karo enter karne ke liye!\n"
            f"🏆 Winners: **{winners_count}**\n"
            f"⏰ Khatam hoga: <t:{timestamp}:R> (<t:{timestamp}:f>)"
        ),
        color=0x00FF00 # Green for start
    )
    embed.set_thumbnail(url="https://media.discordapp.net/attachments/1487601910465953965/1490827440178598090/Giveaway_Design_Template_Pink_Neon_Light_Background_Vector_Illustration_Advert_Coupon_Deal_Background_Image_And_Wallpaper_for_Free_Download.jpg?ex=69d578bf&is=69d4273f&hm=235fcd14cceb85238793605d5ad1b6982cd946f4e8c80079485fc43652f6fef1&=&format=webp") # Giveaway icon
    embed.set_footer(text=f"Hosted by {interaction.user.name}")

    # 2. Giveaway Message bhejna
    await interaction.response.send_message(f"✅ Giveaway for **{prize}** is live!", ephemeral=True)
    msg = await interaction.channel.send(embed=embed)
    await msg.add_reaction("🎉")

    # 3. Database mein save karo (Taaki restart par yaad rahe)
    # giveaway_data = {"msg_id": msg.id, "channel_id": msg.channel.id, "end_time": end_time, "prize": prize, "winners": winners_count}
    # await db.giveaways.insert_one(giveaway_data)

    # 4. Timer (Abhi ke liye sleep use kar rahe hain, par long term ke liye 'tasks' best hain)
    await asyncio.sleep(duration_minutes * 60)

    # 5. Winner Selection Logic
    try:
        new_msg = await interaction.channel.fetch_message(msg.id)
        reaction = next((r for r in new_msg.reactions if str(r.emoji) == "🎉"), None)
        
        if not reaction:
            return await interaction.channel.send(f"❌ Giveaway for **{prize}** cancel ho gaya, reaction nahi mila.")

        users = [u async for u in reaction.users() if not u.bot]

        if len(users) >= winners_count:
            winners = random.sample(users, winners_count)
            winner_mentions = ", ".join([w.mention for w in winners])

            win_embed = discord.Embed(
                title="🎊 GIVEAWAY ENDED 🎊",
                description=f"Prize: **{prize}**\n\nWinner(s): {winner_mentions}\nCongrats bhaiyo! 🏆",
                color=0xFFFF00 # Gold
            )
            await interaction.channel.send(f"Mubarak ho {winner_mentions}! Tumne **{prize}** jeet liya!", embed=win_embed)
            
            # Original embed update karo
            embed.description = f"Giveaway khatam! Winners: {winner_mentions}"
            embed.color = discord.Color.greyple()
            await msg.edit(embed=embed)
        else:
            await interaction.channel.send(f"😥 Giveaway for **{prize}** mein kafi log nahi aaye. (Min {winners_count} required)")
            
    except Exception as e:
        print(f"Giveaway Error: {e}")

#/ping speed
@bot.tree.command(name="ping", description="Bot ki speed check karo")
async def ping(interaction: discord.Interaction):
    # Bot ki latency milliseconds (ms) mein calculate hoti hai
    latency = round(bot.latency * 1000) 
    
    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"Happy ki speed: **{latency}ms**",
        color=0x2B2D31
    )
    
    # Speed ke hisaab se reaction
    if latency < 150:
        embed.set_footer(text="Bhai, internet ek dum 5G chal raha hai! 🚀")
    else:
        embed.set_footer(text="Thoda slow hai, par kaam chal jayega. 🐢")

    await interaction.response.send_message(embed=embed)

#sticky msg
@bot.tree.command(name="sticky", description="Channel mein multi-line sticky message set karo")
@app_commands.checks.has_permissions(manage_messages=True)
@is_admin_or_owner()
@app_commands.describe(
    text="Message likho (New line ke liye Shift+Enter ya \\n use karo)",
    color="Hex code dalo (e.g. #ff5500) ya khali chhodo"
)
async def sticky(interaction: discord.Interaction, text: str, color: str = "#0000ff"):
    await interaction.response.defer(ephemeral=True)
    
    # \n ko real new line mein badlo (agar user string mein bhej raha hai)
    clean_text = text.replace("\\n", "\n")
    
    # Purana sticky delete karne ka logic
    old_data = await sticky_col.find_one({"channel_id": interaction.channel.id})
    if old_data:
        try:
            old_msg = await interaction.channel.fetch_message(old_data["message_id"])
            await old_msg.delete()
        except:
            pass

    # Naya Embed banna
    chosen_color = get_color(color)
    embed = discord.Embed(description=clean_text, color=chosen_color)
    embed.set_footer(text="📌 Sticky Message")
    
    msg = await interaction.channel.send(embed=embed)
    
    # MongoDB mein text aur color dono save karo
    await sticky_col.update_one(
        {"channel_id": interaction.channel.id},
        {"$set": {
            "message_id": msg.id, 
            "content": clean_text, 
            "color": color # Color save kar rahe hain taaki move hote waqt wahi rahe
        }},
        upsert=True
    )
    
    await interaction.followup.send(f"✅ Sticky set ho gaya `{color}` color mein!")

@bot.tree.command(name="unsticky", description="Is channel se sticky message hata do")
@app_commands.checks.has_permissions(manage_messages=True)
@is_admin_or_owner()
async def unsticky(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    channel_id = interaction.channel.id
    
    # 1. Database mein check karo
    sticky_data = await sticky_col.find_one({"channel_id": channel_id})
    
    if not sticky_data:
        return await interaction.followup.send("❌ Is channel mein koi sticky message set nahi hai!")

    # 2. Jo purana message channel mein hai use delete karne ki koshish karo
    try:
        old_msg = await interaction.channel.fetch_message(sticky_data["message_id"])
        await old_msg.delete()
    except Exception:
        # Agar message pehle hi delete ho gaya ho toh ignore
        pass

    # 3. Database se entry uda do
    await sticky_col.delete_one({"channel_id": channel_id})
    
    await interaction.followup.send("✅ Sticky message hata diya gaya hai. Happy ab shant rahega! 🤫")

#echoooo
@bot.tree.command(name="echo", description="Happy ki awaaz mein baat karein (AI apne aap band ho jayegi)")
@is_admin_or_owner() # Ab ye custom check kaam karega
# @commands.has_permissions(administrator=True)
async def echo(interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
    global ai_enabled # Ye zaroori hai taaki hum global switch ko chhed sakein
    
    target_channel = channel or interaction.channel
    
    try:
        # 1. Sabse pehle AI band kar dete hain
        ai_enabled = False
        
        # 2. Bot message bhej dega
        await target_channel.send(message)
        
        # 3. Admin ko confirmation (Sirf tujhe dikhega)
        await interaction.response.send_message(
            f"✅ Message bhej diya gaya hai {target_channel.mention} mein!\n🤖 **Happy ki AI Chat ab BAND hai.** (Wapas on karne ke liye `/ai_mode` use karein)", 
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(f"❌ Kuch locha ho gaya: {e}", ephemeral=True)

# --- MIMIC / TUPPERBOX COMMAND ---
@bot.tree.command(name="mimic", description="Kisi aur ke naam se message bhejein (Admin Only)")
@app_commands.describe(user="Kise copy karna hai?", message="Kya bulwana hai?")
@is_admin_or_owner()
# @app_commands.checks.has_permissions(administrator=True) # Sirf Admins ke liye
async def mimic(interaction: discord.Interaction, user: discord.Member, message: str):
    # Pehle response ko defer kar do taaki 'Bot is thinking' dikhe aur gayab ho jaye
    await interaction.response.defer(ephemeral=True)

    channel = interaction.channel
    
    # 1. Check karo ki kya channel mein pehle se hamara koi webhook hai
    webhooks = await channel.webhooks()
    webhook = discord.utils.get(webhooks, name="HappyMimic")
    
    # 2. Agar nahi hai, toh naya banao
    if webhook is None:
        webhook = await channel.create_webhook(name="HappyMimic")

    # 3. Webhook ke zariye message bhejo (User ki PFP aur Name ke saath)
    await webhook.send(
        content=message,
        username=user.display_name,
        avatar_url=user.display_avatar.url
    )

    # 4. Moderator ko confirmation de do
    await interaction.followup.send(f"Done! {user.display_name} ban kar message bhej diya.", ephemeral=True)

# Error Handling (Agar koi bina permission ke chalaye)
@mimic.error
async def mimic_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("Bhai, ye bade logon ka kaam hai. Aapke paas permissions nahi hain! ❌", ephemeral=True)

# 1. USER INFO: Kisi bhi member ki detail nikalne ke liye
@bot.tree.command(name="userinfo", description="Kisi bhi bande ki kundli nikaalo")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"👤 User Info: {member.name}", color=0x2B2D31)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID", value=member.id, inline=True)
    embed.add_field(name="Joined Server", value=member.joined_at.strftime("%d-%m-%Y"), inline=True)
    embed.add_field(name="Top Role", value=member.top_role.mention, inline=True)
    await interaction.response.send_message(embed=embed)

# 2. AVATAR: Kisi ki profile picture badi karke dekhne ke liye
@bot.tree.command(name="avatar", description="Kisi ki DP churao (badi karke dekho)")
async def avatar(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"🖼️ {member.name}'s Avatar", color=0x2B2D31)
    embed.set_image(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

# 3. SERVER STATS: Server mein kitne log hain
@bot.tree.command(name="stats", description="Server ka haal-chaal")
async def stats(interaction: discord.Interaction):
    guild = interaction.guild
    embed = discord.Embed(title=f"📊 {guild.name} Stats", color=0x2B2D31)
    embed.add_field(name="Total Members", value=guild.member_count)
    embed.add_field(name="Boost Level", value=f"Level {guild.premium_tier}")
    embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
    await interaction.response.send_message(embed=embed)

# purge messages
@bot.tree.command(name="clear", description="Chat saaf karo")
@app_commands.checks.has_permissions(manage_messages=True)
@is_admin_or_owner()
async def clear(interaction: discord.Interaction, amount: int):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"✅ {len(deleted)} messages uda diye gaye!")

# 4. HELP: Bot kya-kya kar sakta hai (Updated & Clean)
@bot.tree.command(name="help", description="Happy ki saari shaktiyon ki list")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 Happy - Help Menu",
        description="Oye! Main Happy hoon, tera AI dost. Mere paas kaafi saari powers hain, dekh lo:",
        color=0x2B2D31
    )
    
    # AI Chatting
    embed.add_field(
        name="🧠 AI Chatting", 
        value="Mujhe mention karo (`@Happy`) ya reply do.", 
        inline=False
    )
    
    # Utility & Fun
    embed.add_field(
        name="🌍 Global & Fun", 
        value="`/afk` - Break pe jao\n`/call` - Dusre server se connect karo ☎️\n`/userinfo` - Kundli nikaalo\n`/avatar` - DP dekho\n`/ping` - Speed check karo", 
        inline=False
    )
    
    # Moderation & Admin (Added Announcement & Mimic)
    embed.add_field(
        name="🛡️ Admin Only", 
        value="`/announce` - Server mein bada elaan karo 📢\n`/giveaway` - Prize baanto 🎁\n`/mimic` - Kisi ka roop dharo 🎭\n`/kick`, `/ban`, `/mute`, `/warn` - Server control karo\n`/setwelcome` & `/setbye` - Channels set karo", 
        inline=False
    )

    # Footer
    embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.set_footer(text="Made with ❤️ | Use / commands to start")
    
    await interaction.response.send_message(embed=embed)

# --- WELCOME EVENT (Glassy Embed) ---

@bot.event
async def on_member_join(member):
    data = await get_server_data(member.guild.id)
    channel_id = data.get("welcome_channel")
    channel = bot.get_channel(channel_id) or member.guild.system_channel
    
    if channel:
        # Tera pasandida minimalist banner link
        welcome_banner = "https://media.discordapp.net/attachments/1487601910465953965/1488790037398032476/you_can_use_this_as_a_discord_welcome_image_i_dont_really_care_anymore.jpg?ex=69ce0f45&is=69ccbdc5&hm=4f5e1d0eab273e555d20807cf97ddfca511c877d1ebbe247462c393a2a382d46&=&format=webp&width=583&height=561" 

        # --- MINIMALIST EMBED ---
        embed = discord.Embed(color=0x2b2d31) # Dark Aesthetic Theme
        embed.set_image(url=welcome_banner)
        
        # Mention aur message embed ke upar aayega
        await channel.send(
            content=f"Oye hoye! Swagat karo **{member.mention}** ka! 🔥\n", 
            embed=embed
        )        

@bot.event
async def on_member_remove(member):
    data = await get_server_data(member.guild.id)
    channel_id = data.get("bye_channel")
    channel = bot.get_channel(channel_id)
    
    if channel:
        # Ek cool "Sad/Bye" banner link (Tu ise replace kar sakta hai)
        bye_banner = "https://media.discordapp.net/attachments/1487601910465953965/1488803367655575642/fa5ae4001ba27d38.jpg?ex=69ce1baf&is=69ccca2f&hm=7f155ea09d332daeb8e081bc5d4691775ef8f67f3534c10e357124f0e4ba1a6a&=&format=webp"

        # --- MINIMALIST BYE EMBED ---
        embed = discord.Embed(color=0x2b2d31) # Dark Theme match
        embed.set_image(url=bye_banner)
        
        # Footer mein total members dikhayega taaki pata chale ab kitne bache hain
        embed.set_footer(text=f"Total Members Left: {member.guild.member_count}")

        # Text content embed ke upar (Tag nahi kar rahe kyunki wo ja chuka hai)
        await channel.send(
            content=f"Alvida **{member.name}**! 👋\nUmeed hai phir milenge... (Ya phir nahi? 😂)", 
            embed=embed
        )

@bot.tree.command(name="afk", description="AFK set karo")
async def afk(interaction: discord.Interaction, reason: str = "Break le raha hoon!"):
    # 1. Discord ko bolo ki "Wait karo, main kaam kar raha hoon"
    await interaction.response.defer(ephemeral=True) 
    
    user_id = interaction.user.id
    guild_id = interaction.guild.id

    # 2. MongoDB wala kaam (isme time lag sakta hai)
    await afk_col.update_one(
        {"user_id": user_id, "guild_id": guild_id},
        {"$set": {"reason": reason, "time": datetime.now(timezone.utc)}},
        upsert=True
    )

    # 3. Nickname change logic
    try:
        if interaction.guild.me.guild_permissions.manage_nicknames:
            if not interaction.user.display_name.startswith("[AFK]"):
                await interaction.user.edit(nick=f"[AFK] {interaction.user.display_name[:25]}")
    except:
        pass

    # 4. Ab response bhejo (defer use kiya hai isliye followup use hoga)
    await interaction.followup.send(f"✅ {interaction.user.mention}, ab aap AFK ho!")

# --- AI CHAT LOGIC (Tera Pura Original Instruction) ---
@bot.tree.command(name="ai_mode", description="AI Chat ko ON ya OFF karo")
@owner_is_present()
@is_admin_or_owner() # Ab ye custom check kaam karega
# @commands.has_permissions(administrator=True)
async def ai_mode(interaction: discord.Interaction, status: bool):
    global ai_enabled
    ai_enabled = status
    state = "CHALU (ON) ✅" if ai_enabled else "BAND (OFF) ❌"
    
    embed = discord.Embed(
        title="Chat Status",
        description=f"Bhaiyo, Happy ki Chat ab **{state}** hai.",
        color=0x2B2D31
    )
    await interaction.response.send_message(embed=embed)

@bot.event
async def on_message(message):
    if message.author.bot: return
    if message.mention_everyone: return
    if len(message.content) < 2: return
    if message.content.startswith(('!', '.', '?', '/', '$','@')): return
    if "http" in message.content.lower() or "discord.gg" in message.content.lower(): return
    if "discord.gg/" in message.content.lower().replace(" ", ""):
        if not message.author.guild_permissions.administrator:
            try:
                await message.delete()
                await message.channel.send(f"🚫 {message.author.mention}, Invites allow nahi hain!", delete_after=5)
            except discord.Forbidden:
                print("Bhai, mere paas message delete karne ki permission nahi hai!")
            except discord.NotFound:
                pass # Message pehle hi delete ho gaya shayad
        return
    
    # Check karo kya is channel mein koi sticky message hai
    sticky_data = await sticky_col.find_one({"channel_id": message.channel.id})
    
    if sticky_data:
        chan_id = message.channel.id
        sticky_counter[chan_id] = sticky_counter.get(chan_id, 0) + 1
        
        # Rate limit safety: Har 3 messages ke baad hi move hoga
        if sticky_counter[chan_id] >= 1:
            sticky_counter[chan_id] = 0
            
            try:
                # Purana delete
                old_msg = await message.channel.fetch_message(sticky_data["message_id"])
                await old_msg.delete()
                
                # Naya send (Same content aur color)
                color_hex = sticky_data.get("color", "#0000ff")
                embed = discord.Embed(description=sticky_data["content"], color=get_color(color_hex))
                embed.set_footer(text="📌 Sticky Message")
                
                new_sticky = await message.channel.send(embed=embed)
                
                # DB mein naya message ID update
                await sticky_col.update_one(
                    {"channel_id": chan_id},
                    {"$set": {"message_id": new_sticky.id}}
                )
            except:
                pass

    # --- Yahan fresh time nikaalo ---
    IST = pytz.timezone('Asia/Kolkata')
    dt_now = datetime.now(IST)
    readable_time = dt_now.strftime("%I:%M %p")
    readable_date = dt_now.strftime("%d %B %Y")

    user_afk = await afk_col.find_one({"user_id": message.author.id, "guild_id": message.guild.id})
    
    if user_afk:
        await afk_col.delete_one({"_id": user_afk["_id"]})
        
        # Nickname wapas theek karna (Sirf agar permission ho)
        if message.guild.me.guild_permissions.manage_nicknames:
            try:
                new_nick = message.author.display_name.replace("[AFK] ", "")
                await message.author.edit(nick=new_nick)
            except:
                pass
        
        await message.channel.send(f"Welcome back {message.author.mention}! Aapka AFK hata diya gaya.", delete_after=5)

    # --- LOGIC 2: Mention Check (Koi AFK bande ko tag kare) ---
    if message.mentions:
        for mention in message.mentions:
            target_afk = await afk_col.find_one({"user_id": mention.id, "guild_id": message.guild.id})
            if target_afk:
                reason = target_afk["reason"]
                await message.reply(f"🚫 **{mention.name}** abhi AFK hai.\n**Reason:** {reason}", delete_after=10)
    
    # --- 2. HEART REACTION LOGIC (Tera purana logic) ---
    greetings = ["good morning", "gm", "good night", "gn", "happy birthday", "hbd", "hello", "hi", "welcome"]
    if any(word in message.content.lower().split() for word in greetings):
        try:
            await asyncio.sleep(random.uniform(0.2, 0.8)) 
            await message.add_reaction("💖")
        except: pass

    # --- 3. AI SESSION LOGIC (NEW HUMAN BEHAVIOR) ---
    channel_id = message.channel.id
    current_time = time.time()
    
    # Check: Kya bot ko mention kiya ya reply kiya?
    is_mentioned = bot.user.mentioned_in(message)
    is_reply_to_bot = False
    if message.reference and message.reference.message_id:
        try:
            replied_msg = await message.channel.fetch_message(message.reference.message_id)
            if replied_msg.author.id == bot.user.id:
                is_reply_to_bot = True
        except: pass

    # Check: Kya session active hai?
    is_session_active = (channel_id in active_sessions and 
                         current_time - active_sessions[channel_id] < SESSION_TIMEOUT)

    # Trigger: AI tab chalega jab (Mention) OR (Reply) OR (Active Session)
    if ai_enabled and (is_mentioned or is_reply_to_bot or is_session_active):
        
        # Update session time
        active_sessions[channel_id] = current_time
        
        user_id = message.author.id
        clean_prompt = message.content.replace(f'<@!{bot.user.id}>', '').replace(f'<@{bot.user.id}>', '').strip()
        
        if clean_prompt:
            if user_id not in user_memories: user_memories[user_id] = []

            instruction = f"""You are Happy, a Indian guy. 
1. Language: Natural Hinglish (Mix of Hindi/English). No forced slangs.
2. Rule: Give logical, helpful, and sensible answers only. 
3. Style: Keep it very short (1 line). Chat like a normal person on discord.
4. Persona: Friendly but not stupid. If a question is serious, answer it simply. 
5. No AI behavior: Don't say "As an AI" or "I'm here to help.
# Emojis: Use rarely (1-2 max),  No bot-like sparkles.
# Current Date: {readable_date}
Current Time: {readable_time}"""

            # Build Context
            messages_to_send = [{"role": "system", "content": instruction}]
            for hist in user_memories[user_id][-6:]:
                messages_to_send.append(hist)
            messages_to_send.append({"role": "user", "content": clean_prompt})

            try:
                await asyncio.sleep(random.uniform(1.0, 2.0))
                # --- TYPING EFFECT ---
                async with message.channel.typing():
                    typing_speed = len(clean_prompt) / 10
                    wait_time = min(random.uniform(2.0, 4.5) + typing_speed, 7.0) # Max 7 sec tak wait karega
                    await asyncio.sleep(wait_time)

                    chat_completion = groq_client.chat.completions.create(
                        messages=messages_to_send,
                        model="llama-3.3-70b-versatile",
                        max_tokens=100,
                        temperature=0.7
                    )
                    reply = chat_completion.choices[0].message.content
                    
                    user_memories[user_id].append({"role": "user", "content": clean_prompt})
                    user_memories[user_id].append({"role": "assistant", "content": reply})
                    
                    await message.reply(reply)

            except Exception as e:
                print(f"Groq Error: {e}")

    # --- 4. GLOBAL CALL RELAY (Tera purana logic) ---
    server_id = message.guild.id
    if server_id in active_calls:
        data = active_calls[server_id]
        if message.channel.id == data['my_channel'] and not is_mentioned:
            target_channel = bot.get_channel(data['partner_channel'])
            if target_channel:
                try:
                    await target_channel.send(f"☎️ **{message.author.name}**: {message.content}")
                except: pass

    await bot.process_commands(message)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("❌ Bhai, Admin permission chahiye iske liye!", ephemeral=True)
    else:
        # Agar koi aur error hai toh uska msg dikhao
        print(f"Log Error: {error}") # Logs mein bhi dikhega par bot crash nahi hoga
        if not interaction.response.is_done():
            await interaction.response.send_message(f"⚠️ Error: `{error}`", ephemeral=True)

if __name__ == "__main__":
    Thread(target=run).start()
    bot.run(TOKEN)
