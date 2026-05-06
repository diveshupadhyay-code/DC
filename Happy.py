import discord
from discord.ext import commands, tasks
from discord import app_commands
from flask import Flask
from threading import Thread
import os, json
from dotenv import load_dotenv
import pytz # timezone ke liye
import time  # Isse time.time() chalega
import datetime
from datetime import datetime, timedelta, timezone 
import asyncio 
from discord.ui import Button, View
import random
from groq import Groq
from motor.motor_asyncio import AsyncIOMotorClient
import aiohttp
import urllib.parse

load_dotenv()

# MongoDB collection variable
user_memories = {}
active_calls = {}  # {server_id: {'partner_id': id, 'channel_id': id}}
waiting_list = []  # List of dicts: [{'server_id': id, 'channel_id': id}]
ai_enabled = True  # By default AI on rahega
maintenance_mode = False
# Dictionary to track active sessions {channel_id: last_interaction_timestamp}
active_sessions = {} # {channel_id: last_active_timestamp}
SESSION_TIMEOUT = 300 # 5 minutes (seconds mein)


# --- MongoDB Setup ---
# Render ke Environment Variables mein MONGO_URL set karna (Tera link password ke saath)
MONGO_URL = os.getenv("MONGO_URL") 
if not MONGO_URL:
    raise ValueError("❌ Error: MONGO_URL nahi mila! .env file check karo ya environment variable set karo.")
cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["HappyBotDB"]
settings_col = db["server_settings"] # Collection for Welcome/Bye IDs
warns_col = db["warnings"] 
afk_col = db["afk_users"] 
sticky_col = db["sticky_messages"]
sticky_counter = {}
reaction_roles_col = db["reaction_roles"]
tickets_col = db["tickets"]
disabled_commands_col = db["disabled_commands"] 
profiles_col = db["user_profiles"]

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

    
# --- Flask & AI Setup (Tera Original) ---
app = Flask('')
@app.route('/')
def home(): return "Happy is Online!"

def run():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
groq_client = Groq(api_key=GROQ_API_KEY)
MODEL_ID = "gemini-2.5-flash"
# Model name: llama-3.3-70b-versatile (Best balance of speed & smartness)


# --- Dynamic Prefix Setup ---
async def get_prefix(bot, message):
    if not message.guild:
        return "!" # Direct Message (DM) mein default "!" rahega
        
    # Database se server ka custom prefix uthayenge
    data = await settings_col.find_one({"_id": str(message.guild.id)})
    if data and "prefix" in data:
        return data["prefix"]
    
    return "," 

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True 
bot = commands.Bot(command_prefix=get_prefix, intents=intents)

# --- Status Loop ---
@tasks.loop(seconds=15) # Har 15 second mein badlega
async def change_status():
    await bot.wait_until_ready()
    
    # Check karenge ki AI on hai ya off taaki status uske hisaab se dikhe
    if ai_enabled:
        status_list = [
            f"Watching {len(bot.users)} members",
            "Listening to @Happy",
            "Type /help for masti",
            "AI Mode: ON ✅"
        ]
    else:
        status_list = [
            "Mod is chatting via Echo 🎤",
            "AI Mode: Sleeping 😴",
            "Watching the conversation",
            "Owner is in control"
        ]
    
    # Randomly ek status uthayenge
    import random
    new_status = random.choice(status_list)
    
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=new_status))

# --- Loop ko Start karne ke liye ---
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    bot.add_view(TicketCreateView())
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
@bot.tree.command(name="pingpong", description="Bot ki speed check karo")
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

# ==========================================
#        PREFIX COMMANDS ( , prefix )
# ==========================================

# 1. ,help Command
@bot.command(name="info")
async def prefix_help(ctx):
    # Context (ctx) hume message ka poora details deta hai
    embed = discord.Embed(
        title="🤖 Happy - Prefix Help Menu",
        description="Oye! Main Happy hoon. Tu prefix `,` use karke bhi mere commands chala sakta hai:",
        color=0x2B2D31
    )
    
    embed.add_field(
        name="🌍 General Commands",
        value="`,help` - Ye menu dekhne ke liye\n`,ping` - Bot ki speed check karo",
        inline=False
    )
    
    embed.add_field(
        name="🛠️ Setup Commands",
        value="`,welcome set #channel` - Welcome channel set karein",
        inline=False
    )
    
    embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.set_footer(text="Bura na mano, bot hoon! 😉")
    
    await ctx.reply(embed=embed)  # User ke message pe reply karega


# 2. ,welcome Group Command (For ',welcome set #channel')
@bot.group(name="welcome", invoke_without_command=True)
async def welcome_group(ctx):
    # Agar user ne khali ',welcome' likha bina 'set' ke
    await ctx.reply("❌ Oye, adha-adhura command mat maar! Setup karne ke liye `,welcome set #channel` likh.")


@welcome_group.command(name="set")
@commands.has_permissions(administrator=True) # Sirf admins ke liye
async def welcome_set(ctx, channel: discord.TextChannel):
    server_id = ctx.guild.id
    
    # MongoDB mein update karega (Tere existing function ko call kar rahe hain)
    await update_server_data(server_id, "welcome_channel", channel.id)
    
    await ctx.reply(f"✅ Done bhaya! Ab se naye laundo ka swagat {channel.mention} mein hoga.")

# Error handling agar kisi ne bina channel mention kiye command maara
@welcome_set.error
async def welcome_set_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply("❌ Arre, channel mention karna bhool gaya kya? Jaise: `,welcome set #welcome`")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.reply("❌ Hat ja bhai! Iske liye Admin permissions chahiye.")

# --- PREFIX PING COMMAND (English) ---
@bot.command(name="ping")
async def prefix_ping(ctx):
    # Latency calculation in milliseconds
    latency = round(bot.latency * 1000)
    
    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"Happy's current latency: **{latency}ms**",
        color=0x2B2D31 # Dark aesthetic color
    )
    
    # Custom message based on speed
    if latency < 150:
        embed.set_footer(text="Connection is lightning fast! 🚀")
    else:
        embed.set_footer(text="Slightly delayed, but keeping up. 🐢")

    # Replying directly to the user's message
    await ctx.reply(embed=embed)

# ====================================================================
#                     SETUP MUTE SYSTEM
# ====================================================================

@bot.command(name="setupmute")
@commands.has_permissions(administrator=True)
async def setup_mute_system(ctx):
    # Ek initial message bhejenge taaki user ko pata chale process shuru ho gaya hai
    status_msg = await ctx.reply("⚙️ *Setting up mute roles and configuring channel permissions... Please wait!*")

    guild = ctx.guild
    roles_to_create = {
        "Muted": discord.Color.dark_grey(),
        "Image Muted": discord.Color.blue(),
        "Reaction Muted": discord.Color.orange()
    }
    
    created_roles = {}

    # 1. Roles Create/Fetch karne ka logic
    for role_name, role_color in roles_to_create.items():
        # Check agar role pehle se bana hua hai
        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            try:
                role = await guild.create_role(
                    name=role_name, 
                    color=role_color, 
                    reason="Happy Bot Mute System Setup"
                )
                created_roles[role_name] = f"✅ Created new **{role_name}** role."
            except discord.Forbidden:
                return await status_msg.edit(content="❌ *Mujhe roles create karne ki permission nahi hai! Mera role hierarchy mein sabse upar hona zaroori hai.*")
        else:
            created_roles[role_name] = f"ℹ️ Found existing **{role_name}** role."
        
        roles_to_create[role_name] = role # Store actual role object for permission setup

    # Roles objects nikalna override permissions ke liye
    muted_role = roles_to_create["Muted"]
    image_muted_role = roles_to_create["Image Muted"]
    reaction_muted_role = roles_to_create["Reaction Muted"]

    # 2. Saare Channels mein Override Permissions config karna
    text_channels_configured = 0
    voice_channels_configured = 0

    for channel in guild.channels:
        try:
            if isinstance(channel, discord.TextChannel):
                # Text channels ke liye permissions setup
                await channel.set_permissions(muted_role, 
                    send_messages=False, 
                    add_reactions=False, 
                    send_voice_messages=False,
                    reason="Muted role setup"
                )
                await channel.set_permissions(image_muted_role, 
                    attach_files=False, 
                    embed_links=False, 
                    reason="Image Muted role setup"
                )
                await channel.set_permissions(reaction_muted_role, 
                    add_reactions=False, 
                    reason="Reaction Muted role setup"
                )
                text_channels_configured += 1

            elif isinstance(channel, discord.VoiceChannel):
                # Voice channels ke liye permissions setup
                await channel.set_permissions(muted_role, 
                    speak=False, 
                    send_messages=False, 
                    reason="Muted role setup"
                )
                voice_channels_configured += 1
        except Exception:
            # Agar kisi specific channel mein permissions manage karne ki permission bot ko nahi hai toh skip karega
            continue

    # 3. Final Success Embed Response
    success_embed = discord.Embed(
        title="🔒 Mute System Configured!",
        description="Happy has successfully initialized and secured the mute roles across all channels.",
        color=0x2B2D31
    )
    
    # Roles Status grid
    roles_status_text = "\n".join(created_roles.values())
    success_embed.add_field(name="🛡️ Role Status", value=roles_status_text, inline=False)
    
    # Channels Configured grid
    success_embed.add_field(
        name="📁 Channels Secured", 
        value=f"📝 Text Channels: **{text_channels_configured}**\n🔊 Voice Channels: **{voice_channels_configured}**", 
        inline=False
    )
    
    success_embed.set_footer(text="Make sure to keep Happy's role above these roles in Server Settings!")
    
    await status_msg.delete() # Purana status message delete karenge
    await ctx.reply(embed=success_embed)

@setup_mute_system.error
async def setup_mute_system_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("❌ *Bhaya, is command ko chalane ke liye aapke paas `Administrator` permission honi chahiye!*")


# --- PREFIX AFK COMMAND (English) ---
@bot.command(name="afk")
async def prefix_afk(ctx, *, reason: str = "Away from keyboard"):
    user_id = ctx.author.id
    guild_id = ctx.guild.id

    # 1. MongoDB collection mein entry save karenge
    await afk_col.update_one(
        {"user_id": user_id, "guild_id": guild_id},
        {"$set": {"reason": reason, "time": datetime.now(timezone.utc)}},
        upsert=True
    )

    # 2. Nickname update logic (Only if bot has permission and user isn't already [AFK])
    try:
        if ctx.guild.me.guild_permissions.manage_nicknames:
            if not ctx.author.display_name.startswith("[AFK]"):
                # Discord nickname limit of 32 characters handling
                new_nick = f"[AFK] {ctx.author.display_name[:25]}"
                await ctx.author.edit(nick=new_nick)
    except Exception:
        pass

    # 3. Dynamic Prefix fetch karenge response ke liye
    current_prefix = await get_prefix(bot, ctx.message)

    # 4. Clean Bleed-Style Aesthetic Embed Response
    embed = discord.Embed(
        description=f"💤 {ctx.author.mention}, I have set your AFK status.",
        color=0x2B2D31
    )
    embed.add_field(name="Reason", value=f"```{reason}```", inline=False)
    embed.set_footer(text=f"Type anything in chat or use {current_prefix}afk to remove it.")

    await ctx.reply(embed=embed)

@bot.group(name="prefix", invoke_without_command=True)
async def prefix_group(ctx):
    # Agar user sirf ",prefix" likhta hai, toh use current prefix batayega
    current_prefix = await get_prefix(bot, ctx.message)
    embed = discord.Embed(
        title="🔧 Prefix Settings",
        description=f"Is server ka current prefix **`{current_prefix}`** hai.\n\n"
                    f"Naya prefix set karne ke liye use karein:\n"
                    f"`{current_prefix}prefix set [symbol]`",
        color=0x2B2D31
    )
    await ctx.reply(embed=embed)

@prefix_group.command(name="set")
@commands.has_permissions(administrator=True)
async def prefix_set(ctx, new_prefix: str):
    # Length check: Prefix 1 se 3 characters se bada nahi hona chahiye (Safety ke liye)
    if len(new_prefix) > 3:
        return await ctx.reply("❌ Bhai, prefix thoda chota rakho (Maximum 3 characters)!")

    server_id = str(ctx.guild.id)
    
    # MongoDB settings_col database mein save karna
    await settings_col.update_one(
        {"_id": server_id},
        {"$set": {"prefix": new_prefix}},
        upsert=True
    )
    
    embed = discord.Embed(
        title="✅ Prefix Updated!",
        description=f"Is server ke liye prefix badal kar ab **`{new_prefix}`** kar diya gaya hai.\n"
                    f"Ab saare prefix commands `{new_prefix}` se chalenge!",
        color=0x00FF00
    )
    await ctx.reply(embed=embed)

@prefix_set.error
async def prefix_set_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("❌ Bhaiya, iske liye aapke paas `Administrator` permission honi chahiye.")

@bot.event
async def on_message(message):
    if message.author.bot: return

@bot.event
async def on_message(message):
    # 1. Sabse pehle bot ke messages ko ignore karo
    if message.author.bot: 
        return

    # 2. PRIORITY 1: ANTI-INVITE SCAN (Isse sabse upar rakha hai taaki bypass na ho)
    message_content_clean = message.content.lower().replace(" ", "")
    if "discord.gg/" in message_content_clean or "discord.com/invite/" in message_content_clean:
        # Strictly check if the user is NOT an Administrator
        if not message.author.guild_permissions.administrator:
            try:
                # Instant delete
                await message.delete()
                
                # Send warning message
                warn_embed = discord.Embed(
                    description=f"⚠️ {message.author.mention}, discord invite links are not allowed here!",
                    color=0xff0000
                )
                await message.channel.send(embed=warn_embed, delete_after=5)
                return  # Message delete ho gaya, aage ka code stop (Important!)
                
            except discord.Forbidden:
                print("[AutoMod Error] Happy bot doesn't have 'Manage Messages' or Administrator permission!")
            except discord.NotFound:
                pass
            except Exception as e:
                print(f"[AutoMod Error] Unexpected crash: {e}")

    # 3. PRIORITY 2: AFK Checks, Prefix checks aur baaki ka code iske niche aane do...
    # (Yahan se aapka normal code shuru hoga, jaise below lines:)
    # --- LOGIC 1: WELCOME BACK (AFK REMOVAL) ---
    user_afk = await afk_col.find_one({"user_id": message.author.id, "guild_id": message.guild.id})
    if user_afk:
        away_time = ""
        if "time" in user_afk:
            afk_time = user_afk["time"]
            if afk_time.tzinfo is None:
                afk_time = afk_time.replace(tzinfo=timezone.utc)
            duration = datetime.now(timezone.utc) - afk_time
            minutes = int(duration.total_seconds() / 60)
            if minutes > 0:
                away_time = f" after being away for **{minutes}m**"

        await afk_col.delete_one({"_id": user_afk["_id"]})
        
        # Nickname wapas theek karna (Only if bot has permission)
        if message.guild.me.guild_permissions.manage_nicknames:
            try:
                new_nick = message.author.display_name.replace("[AFK] ", "")
                await message.author.edit(nick=new_nick)
            except Exception:
                pass
        
        # Sleek English Welcome Back Embed (Auto-delete after 5 seconds)
        back_embed = discord.Embed(
            description=f"👋 Welcome back {message.author.mention}! I've removed your AFK{away_time}.",
            color=0x2B2D31
        )
        await message.channel.send(embed=back_embed)

    # --- LOGIC 2: MENTION CHECK (Tagging an AFK user) ---
    if message.mentions:
        for mention in message.mentions:
            if mention.id == message.author.id:
                continue # Agar khud ko tag kare toh ignore karo
                
            target_afk = await afk_col.find_one({"user_id": mention.id, "guild_id": message.guild.id})
            if target_afk:
                reason = target_afk.get("reason", "Away from keyboard")
                afk_time = target_afk.get("time")
                
                time_str = ""
                if afk_time:
                    if afk_time.tzinfo is None:
                        afk_time = afk_time.replace(tzinfo=timezone.utc)
                    time_str = f" (<t:{int(afk_time.timestamp())}:R>)"

                # Aesthetic English Alert Embed (Auto-delete after 10 seconds)
                mention_embed = discord.Embed(
                    description=f"💤 **{mention.name}** is currently AFK{time_str}.",
                    color=0x2B2D31
                )
                mention_embed.add_field(name="Reason", value=f"```{reason}```", inline=False)
                
                await message.reply(embed=mention_embed)

    # --- PREFIXES & SYSTEM BYPASS CHECKS ---
    # Database se server ka dynamic prefix fetch karo (Default '!' to bypass command scans)
    current_prefix = "!"
    try:
        data = await settings_col.find_one({"_id": str(message.guild.id)})
        if data and "prefix" in data:
            current_prefix = data["prefix"]
    except Exception:
        pass

    # Agar message kisi command system, bot prefix ya links se shuru ho toh AI block karo
    if message.content.startswith(('!', '.', '?', '/', '$','@', ',')):# <-- Isme ',' (comma) bhi add kar diya!
    # if message.content.startswith((current_prefix, '!', '.', '?', '/', '$', '@')): 
        await bot.process_commands(message)
        return
        
    if len(message.content) < 2: 
        return
        
    if "http" in message.content.lower() or "discord.gg" in message.content.lower(): 
        return
        
    
    # Check karo kya is channel mein koi sticky message hai
    sticky_data = await sticky_col.find_one({"channel_id": message.channel.id})
    
    # --- MAINTENANCE BYPASS ---
    if maintenance_mode:
        # Agar tum khud (Owner) message kar rahe ho, toh bot reply karega (testing ke liye)
        owner_id = 876629015144828939
        if message.author.id == owner_id:
            await bot.process_commands(message)
            return
            
        
        if bot.user.mentioned_in(message) or message.content.startswith(','):
            await message.reply("🚧 **Shanti bhai shanti!** Happy abhi thoda maintenance par hai. Backend pe tel-paani daal raha hoon, thodi der mein ekdum raapchik banke wapas aayega! 😉")
        return

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


# ====================================================================
#                  REACTION ROLE SYSTEM (Bleed Style)
# ====================================================================

@bot.command(name="reactionrole", aliases=["rr"])
@commands.has_permissions(administrator=True)
async def reaction_role_add(ctx, action: str = None, message_link: str = None, emoji: str = None, role: discord.Role = None):
    if not action or action.lower() != "add" or not message_link or not emoji or not role:
        embed_help = discord.Embed(
            title="⚙️ Reaction Role Setup Help",
            description=(
                "Setup a reaction role using a message link, emoji, and role.\n\n"
                "**Format:**\n"
                "`,reactionrole add [message_link] [emoji] [@role]`\n\n"
                "**Example:**\n"
                "`,reactionrole add https://discord.com/channels/123/456/789 🙂 @Member`"
            ),
            color=0x2B2D31
        )
        return await ctx.reply(embed=embed_help)

    # Message Link parsing
    try:
        link_parts = message_link.strip().split('/')
        channel_id = int(link_parts[-2])
        message_id = int(link_parts[-1])
    except Exception:
        return await ctx.reply("❌ **Invalid Message Link!** Sahi Discord message link copy karke dalo.")

    # Fetch Channel & Message
    try:
        channel = bot.get_channel(channel_id)
        if not channel:
            channel = await bot.fetch_channel(channel_id)
        target_message = await channel.fetch_message(message_id)
    except Exception:
        return await ctx.reply("❌ **Error:** Message nahi mila! Check karo kya bot ke paas us channel ko dekhne ki permission hai.")

    # Add reaction to the target message
    try:
        await target_message.add_reaction(emoji)
    except Exception as e:
        return await ctx.reply(f"❌ **Reaction Error:** {e}\nCheck karo kya bot ke paas reactions add karne ki permission hai.")

    # MongoDB Saving (Dono ko string mein save karenge safely)
    await reaction_roles_col.update_one(
        {"message_id": str(message_id), "emoji": str(emoji)},
        {"$set": {
            "channel_id": str(channel_id),
            "guild_id": str(ctx.guild.id),
            "role_id": str(role.id)
        }},
        upsert=True
    )

    success_embed = discord.Embed(
        title="✅ Reaction Role Added!",
        description=f"Reaction role successfully bound to [target message]({message_link})",
        color=0x2B2D31
    )
    success_embed.add_field(name="Emoji", value=emoji, inline=True)
    success_embed.add_field(name="Role Assigned", value=role.mention, inline=True)
    
    await ctx.reply(embed=success_embed)


# --- DETECT REACTION ADD EVENT ---
@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return

    emoji_str = str(payload.emoji)
    message_id_str = str(payload.message_id)

    db_entry = await reaction_roles_col.find_one({
        "message_id": message_id_str, 
        "emoji": emoji_str
    })
    
    if db_entry:
        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
            
        role = guild.get_role(int(db_entry["role_id"]))
        member = payload.member
        
        if not member:
            try:
                member = await guild.fetch_member(payload.user_id)
            except Exception:
                return

        if role and member:
            try:
                await member.add_roles(role, reason="Happy Reaction Role Auto-Assign")
                # Failsafe DM message
                try:
                    await member.send(f"✅ **{guild.name}** mein aapko **{role.name}** role de diya gaya hai!")
                except Exception:
                    pass
            except discord.Forbidden:
                print(f"[RR Error] Bot cannot assign role {role.name}. Role Hierarchy check karo!")
            except Exception as e:
                print(f"[RR Error] Error adding role: {e}")


# --- DETECT REACTION REMOVE EVENT ---
@bot.event
async def on_raw_reaction_remove(payload):
    emoji_str = str(payload.emoji)
    message_id_str = str(payload.message_id)

    db_entry = await reaction_roles_col.find_one({
        "message_id": message_id_str, 
        "emoji": emoji_str
    })
    
    if db_entry:
        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
            
        role = guild.get_role(int(db_entry["role_id"]))
        
        try:
            member = await guild.fetch_member(payload.user_id)
        except Exception:
            return
            
        if role and member:
            try:
                await member.remove_roles(role, reason="Happy Reaction Role Auto-Remove")
                # Failsafe DM message
                try:
                    await member.send(f"❌ **{guild.name}** mein aapka **{role.name}** role hata diya gaya hai.")
                except Exception:
                    pass
            except discord.Forbidden:
                print(f"[RR Error] Bot cannot remove role {role.name}. Role Hierarchy check karo!")
            except Exception as e:
                print(f"[RR Error] Error removing role: {e}")

# ====================================================================
#                  AUTOMOD INVITE BLOCK SYSTEM
# ====================================================================

@bot.group(name="automod", invoke_without_command=True)
# @is_admin_or_owner()
@commands.has_permissions(administrator=True)
async def automod_group(ctx):
    # Dynamic prefix fetch
    data = await settings_col.find_one({"_id": str(ctx.guild.id)})
    current_prefix = data["prefix"] if data and "prefix" in data else "!"
    
    embed = discord.Embed(
        title="🛡️ AutoMod Settings",
        description=(
            "Configure server automod settings to prevent spam and link invites.\n\n"
            "**Command:**\n"
            f"`{current_prefix}automod invite [on/off]`"
        ),
        color=0x2B2D31
    )
    await ctx.reply(embed=embed)

@automod_group.command(name="invite")
# @is_admin_or_owner()
@commands.has_permissions(administrator=True)
async def automod_invite(ctx, status: str = None):
    if not status or status.lower() not in ["on", "off"]:
        current_prefix = await get_prefix(bot, ctx.message)
        return await ctx.reply(f"❌ **Invalid Status!** Use `{current_prefix}automod invite on` or `{current_prefix}automod invite off`.")

    guild_id = str(ctx.guild.id)
    state = status.lower() == "on"

    # MongoDB database mein setting save karenge
    await settings_col.update_one(
        {"_id": guild_id},
        {"$set": {"invite_block": state}},
        upsert=True
    )

    embed = discord.Embed(
        description=f"✅ **AutoMod Invite Blocker** has been turned **{status.upper()}**.",
        color=0x2B2D31
    )
    await ctx.reply(embed=embed)

# ====================================================================
#                      SOFTBAN COMMAND (Moderators)
# ====================================================================

@bot.command(name="softban", aliases=["sb"])
@commands.has_permissions(kick_members=True) # Mod permission required
async def softban_user(ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
    # Dynamic prefix fetch
    data = await settings_col.find_one({"_id": str(ctx.guild.id)})
    current_prefix = data["prefix"] if data and "prefix" in data else ","

    # Agar moderator ne user mention nahi kiya
    if not member:
        embed_help = discord.Embed(
            title="🔨 Softban Command",
            description=(
                "Bans a user and immediately unbans them to clear their recent messages.\n\n"
                "**Format:**\n"
                f"`{current_prefix}softban [@user] [reason]`\n\n"
                "**Example:**\n"
                f"`{current_prefix}softban @Spammer123 Spamming links`"
            ),
            color=0x2B2D31
        )
        return await ctx.reply(embed=embed_help)

    # Permission check: Mod ki hierarchy target member se upar honi chahiye
    if ctx.author.top_role <= member.top_role and ctx.author.id != ctx.guild.owner_id:
        return await ctx.reply("❌ **Error:** You cannot softban this user because they have an equal or higher role than you.")

    # Bot ki hierarchy target member se upar honi chahiye
    if ctx.guild.me.top_role <= member.top_role:
        return await ctx.reply("❌ **Error:** I cannot softban this user. Please move my role higher in the server settings.")

    try:
        # 1. DM the user first before ban (failsafe)
        try:
            dm_embed = discord.Embed(
                description=f"⚠️ You have been softbanned from **{ctx.guild.name}**.\n**Reason:** {reason}",
                color=0xff0000
            )
            await member.send(embed=dm_embed)
        except Exception:
            pass # DM blocked hone par command crash nahi hogi

        # 2. Ban the user (deleting 7 days of messages)
        await ctx.guild.ban(member, reason=f"Softban by {ctx.author}: {reason}", delete_message_days=7)

        # 3. Immediately Unban the user
        await ctx.guild.unban(member, reason="Softban complete (automatic unban)")

        # 4. Clean Success Response
        success_embed = discord.Embed(
            description=f"🧹 **Successfully softbanned {member.mention}**",
            color=0x2B2D31
        )
        success_embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        success_embed.add_field(name="Reason", value=reason, inline=True)
        success_embed.set_footer(text="User was banned, messages cleared, and unbanned.")
        
        await ctx.reply(embed=success_embed)

    except discord.Forbidden:
        await ctx.reply("❌ **Error:** I don't have enough permissions to ban/unban members.")
    except Exception as e:
        await ctx.reply(f"❌ **An unexpected error occurred:** {e}")

# ====================================================================
#                      ROLE MANAGEMENT SYSTEM (Add / Remove)
# ====================================================================

@bot.group(name="role", invoke_without_command=True)
@commands.has_permissions(manage_roles=True) # Mod with Manage Roles permission required
async def role_group(ctx):
    # Dynamic prefix fetch
    data = await settings_col.find_one({"_id": str(ctx.guild.id)})
    current_prefix = data["prefix"] if data and "prefix" in data else ","
    
    embed_help = discord.Embed(
        title="🛡️ Role Management",
        description=(
            "Quickly assign or remove roles from server members.\n\n"
            "**Commands:**\n"
            f"`{current_prefix}role add [@user] [@role]`\n"
            f"`{current_prefix}role remove [@user] [@role]`\n\n"
            "**Examples:**\n"
            f"`{current_prefix}role add @Rohan @Member`\n"
            f"`{current_prefix}role remove @Rohan @Muted`"
        ),
        color=0x2B2D31
    )
    await ctx.reply(embed=embed_help)


# --- SUB-COMMAND: ADD ROLE ---
@role_group.command(name="add")
@commands.has_permissions(manage_roles=True)
async def role_add(ctx, member: discord.Member = None, role: discord.Role = None):
    if not member or not role:
        current_prefix = await get_prefix(bot, ctx.message)
        return await ctx.reply(f"❌ **Invalid Format!** Use `{current_prefix}role add [@user] [@role]`")

    # 1. Mod Permission Hierarchy Check
    if ctx.author.top_role <= role and ctx.author.id != ctx.guild.owner_id:
        return await ctx.reply("❌ **Error:** You cannot assign a role that is equal to or higher than your highest role.")

    # 2. Bot Permission Hierarchy Check
    if ctx.guild.me.top_role <= role:
        return await ctx.reply("❌ **Error:** I cannot assign this role because it is higher than my highest integration role. Drag my role up in settings!")

    # 3. Check if user already has the role
    if role in member.roles:
        return await ctx.reply(f"ℹ️ **{member.name}** already has the {role.mention} role.")

    try:
        await member.add_roles(role, reason=f"Role added by {ctx.author}")
        
        success_embed = discord.Embed(
            description=f"✅ Added {role.mention} to **{member.mention}**",
            color=0x2B2D31
        )
        await ctx.reply(embed=success_embed)
        
    except discord.Forbidden:
        await ctx.reply("❌ **Error:** I don't have enough permissions to manage roles.")
    except Exception as e:
        await ctx.reply(f"❌ **An unexpected error occurred:** {e}")


# --- SUB-COMMAND: REMOVE ROLE ---
@role_group.command(name="remove")
@commands.has_permissions(manage_roles=True)
async def role_remove(ctx, member: discord.Member = None, role: discord.Role = None):
    if not member or not role:
        current_prefix = await get_prefix(bot, ctx.message)
        return await ctx.reply(f"❌ **Invalid Format!** Use `{current_prefix}role remove [@user] [@role]`")

    # 1. Mod Permission Hierarchy Check
    if ctx.author.top_role <= role and ctx.author.id != ctx.guild.owner_id:
        return await ctx.reply("❌ **Error:** You cannot remove a role that is equal to or higher than your highest role.")

    # 2. Bot Permission Hierarchy Check
    if ctx.guild.me.top_role <= role:
        return await ctx.reply("❌ **Error:** I cannot remove this role because it is higher than my highest integration role. Drag my role up in settings!")

    # 3. Check if user has the role
    if role not in member.roles:
        return await ctx.reply(f"ℹ️ **{member.name}** does not have the {role.mention} role.")

    try:
        await member.remove_roles(role, reason=f"Role removed by {ctx.author}")
        
        success_embed = discord.Embed(
            description=f"❌ Removed {role.mention} from **{member.mention}**",
            color=0x2B2D31
        )
        await ctx.reply(embed=success_embed)
        
    except discord.Forbidden:
        await ctx.reply("❌ **Error:** I don't have enough permissions to manage roles.")
    except Exception as e:
        await ctx.reply(f"❌ **An unexpected error occurred:** {e}")

# ====================================================================
#                     CLEAR WARNS COMMAND (Moderators)
# ====================================================================

@bot.command(name="clearwarns", aliases=["cw", "rwarns"])
@commands.has_permissions(kick_members=True) # Requires Mod permissions
async def clear_warnings(ctx, member: discord.Member = None):
    # Dynamic prefix fetch
    data = await settings_col.find_one({"_id": str(ctx.guild.id)})
    current_prefix = data["prefix"] if data and "prefix" in data else "!"

    # Agar moderator ne user mention nahi kiya, toh help embed dikhao
    if not member:
        embed_help = discord.Embed(
            title="🧹 Clear Warnings",
            description=(
                "Clears all the warning history of a specific server member.\n\n"
                "**Format:**\n"
                f"`{current_prefix}clearwarns [@user]`\n\n"
                "**Example:**\n"
                f"`{current_prefix}clearwarns @Rohan`"
            ),
            color=0x2B2D31
        )
        return await ctx.reply(embed=embed_help)

    # Permission check: Mod ki hierarchy target member se upar honi chahiye (Self-protection)
    if ctx.author.top_role <= member.top_role and ctx.author.id != ctx.guild.owner_id:
        return await ctx.reply("❌ **Error:** You cannot clear warnings for someone with an equal or higher role than you.")

    guild_id = str(ctx.guild.id)
    user_id = str(member.id)

    # Database query: Check karenge ki kya user ke paas sach mein koi warns hain bhi ya nahi
    # (Note: warns_col ko apne actual warning collection variable name se match kar lena)
    user_warns = await warns_col.find_one({"guild_id": guild_id, "user_id": user_id})

    if not user_warns or not user_warns.get("warns"):
        return await ctx.reply(f"ℹ️ **{member.name}** has a clean record! There are no warnings to clear.")

    try:
        # User ke saare warns database se delete karenge
        await warns_col.delete_one({"guild_id": guild_id, "user_id": user_id})

        # Aesthetic success message
        success_embed = discord.Embed(
            description=f"🧹 **Successfully cleared all warnings for {member.mention}**",
            color=0x2B2D31
        )
        success_embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        success_embed.add_field(name="Status", value="Database Cleared ✅", inline=True)
        success_embed.set_footer(text="User warning history has been reset to zero.")
        
        await ctx.reply(embed=success_embed)

    except Exception as e:
        await ctx.reply(f"❌ **Database Error:** Could not clear warnings. Reason: {e}")

# ====================================================================
#                  CHANNEL LOCK/UNLOCK SYSTEM (Moderators)
# ====================================================================

@bot.command(name="lock")
@commands.has_permissions(manage_channels=True)
async def lock_channel(ctx, channel: discord.TextChannel = None, *, reason: str = "No reason provided"):
    channel = channel or ctx.channel
    
    # Check if channel is already locked
    overwrite = channel.overwrites_for(ctx.guild.default_role)
    if overwrite.send_messages is False:
        return await ctx.reply(f"ℹ️ {channel.mention} is already locked!")

    try:
        # standard text channel locking
        overwrite.send_messages = False
        overwrite.send_voice_messages = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Locked by {ctx.author}: {reason}")
        
        lock_embed = discord.Embed(
            description=f"🔒 **Locked {channel.mention}**",
            color=0x2B2D31
        )
        lock_embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        lock_embed.add_field(name="Reason", value=reason, inline=True)
        lock_embed.set_footer(text="Members can no longer send messages in this channel.")
        
        await ctx.reply(embed=lock_embed)
        
    except discord.Forbidden:
        await ctx.reply("❌ **Error:** I don't have permission to manage permissions for this channel.")
    except Exception as e:
        await ctx.reply(f"❌ **An error occurred:** {e}")


@bot.command(name="unlock")
@commands.has_permissions(manage_channels=True)
async def unlock_channel(ctx, channel: discord.TextChannel = None, *, reason: str = "No reason provided"):
    channel = channel or ctx.channel
    
    # Check if channel is already unlocked
    overwrite = channel.overwrites_for(ctx.guild.default_role)
    if overwrite.send_messages is None or overwrite.send_messages is True:
        return await ctx.reply(f"ℹ️ {channel.mention} is already unlocked!")

    try:
        # Reset permission to default/neutral (None)
        overwrite.send_messages = None
        overwrite.send_voice_messages = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Unlocked by {ctx.author}: {reason}")
        
        unlock_embed = discord.Embed(
            description=f"🔓 **Unlocked {channel.mention}**",
            color=0x2B2D31
        )
        unlock_embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        unlock_embed.add_field(name="Reason", value=reason, inline=True)
        unlock_embed.set_footer(text="Members can now send messages again.")
        
        await ctx.reply(embed=unlock_embed)
        
    except discord.Forbidden:
        await ctx.reply("❌ **Error:** I don't have permission to manage permissions for this channel.")
    except Exception as e:
        await ctx.reply(f"❌ **An error occurred:** {e}")


# ====================================================================
#                  VOICE LOCK/UNLOCK SYSTEM (Moderators)
# ====================================================================

@bot.command(name="vclock", aliases=["vlock"])
@commands.has_permissions(manage_channels=True)
async def voice_lock(ctx, channel: discord.VoiceChannel = None, *, reason: str = "No reason provided"):
    # Agar channel mention nahi kiya toh mod jis VC mein betha hai use lock karo
    if not channel:
        if ctx.author.voice and ctx.author.voice.channel:
            channel = ctx.author.voice.channel
        else:
            return await ctx.reply("❌ **Error:** Please mention a voice channel or join one to lock it!")

    overwrite = channel.overwrites_for(ctx.guild.default_role)
    if overwrite.connect is False:
        return await ctx.reply(f"ℹ️ **{channel.name}** is already locked!")

    try:
        # Members can no longer connect/join the VC
        overwrite.connect = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Voice locked by {ctx.author}: {reason}")
        
        vclock_embed = discord.Embed(
            description=f"🔒 **Locked Voice Channel: {channel.name}**",
            color=0x2B2D31
        )
        vclock_embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        vclock_embed.add_field(name="Reason", value=reason, inline=True)
        vclock_embed.set_footer(text="New members can no longer join this Voice Channel.")
        
        await ctx.reply(embed=vclock_embed)
        
    except discord.Forbidden:
        await ctx.reply("❌ **Error:** I don't have permission to manage permissions for this voice channel.")
    except Exception as e:
        await ctx.reply(f"❌ **An error occurred:** {e}")


@bot.command(name="vcunlock", aliases=["vunlock"])
@commands.has_permissions(manage_channels=True)
async def voice_unlock(ctx, channel: discord.VoiceChannel = None, *, reason: str = "No reason provided"):
    if not channel:
        if ctx.author.voice and ctx.author.voice.channel:
            channel = ctx.author.voice.channel
        else:
            return await ctx.reply("❌ **Error:** Please mention a voice channel or join one to unlock it!")

    overwrite = channel.overwrites_for(ctx.guild.default_role)
    if overwrite.connect is None or overwrite.connect is True:
        return await ctx.reply(f"ℹ️ **{channel.name}** is already unlocked!")

    try:
        # Reset permissions so members can connect again
        overwrite.connect = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Voice unlocked by {ctx.author}: {reason}")
        
        vcunlock_embed = discord.Embed(
            description=f"🔓 **Unlocked Voice Channel: {channel.name}**",
            color=0x2B2D31
        )
        vcunlock_embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        vcunlock_embed.add_field(name="Reason", value=reason, inline=True)
        vcunlock_embed.set_footer(text="Members can now join this Voice Channel again.")
        
        await ctx.reply(embed=vcunlock_embed)
        
    except discord.Forbidden:
        await ctx.reply("❌ **Error:** I don't have permission to manage permissions for this voice channel.")
    except Exception as e:
        await ctx.reply(f"❌ **An error occurred:** {e}")

# ====================================================================
#                     MEMBER COUNT COMMAND (Everyone)
# ====================================================================

@bot.command(name="membercount", aliases=["mc"])
async def member_count(ctx):
    guild = ctx.guild
    
    # Calculating stats
    total_members = guild.member_count
    bots = sum(1 for member in guild.members if member.bot)
    humans = total_members - bots

    # Premium Aesthetic Embed
    embed = discord.Embed(
        title=f"📊 {guild.name} Stats",
        color=0x2B2D31
    )
    
    embed.add_field(
        name="👥 Total Members", 
        value=f"**{total_members}**", 
        inline=False
    )
    embed.add_field(
        name="🧑 Humans", 
        value=f"**{humans}**", 
        inline=True
    )
    embed.add_field(
        name="🤖 Bots", 
        value=f"**{bots}**", 
        inline=True
    )
    
    # Server icon as thumbnail if available
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
        
    embed.set_footer(
        text=f"Requested by {ctx.author.name}", 
        icon_url=ctx.author.display_avatar.url
    )
    
    await ctx.reply(embed=embed)

# ====================================================================
#                      ADVANCED PURGE SYSTEM (Moderators)
# ====================================================================

@bot.command(name="purge", aliases=["clear", "c"])
@commands.has_permissions(manage_messages=True)
async def purge_messages(ctx, target: str = None, limit: int = None):
    # Dynamic prefix fetch safely
    data = await settings_col.find_one({"_id": str(ctx.guild.id)})
    current_prefix = data["prefix"] if data and "prefix" in data else ","

    # 1. Agar user ne command bina arguments ke chalayi (Help Embed)
    if target is None:
        embed_help = discord.Embed(
            title="🧹 Purge Commands",
            description=(
                "Bulk delete messages from the current channel easily.\n\n"
                "**Formats:**\n"
                f"`{current_prefix}purge [amount]` — Deletes recent messages.\n"
                f"`{current_prefix}purge bots [amount]` — Deletes only bot messages.\n"
                f"`{current_prefix}purge [@user] [amount]` — Deletes specific user messages.\n\n"
                "**Examples:**\n"
                f"`{current_prefix}purge 100`\n"
                f"`{current_prefix}purge bots 50`\n"
                f"`{current_prefix}purge @Spammer 20`"
            ),
            color=0x2B2D31
        )
        return await ctx.reply(embed=embed_help)

    # Pehle message ko delete kar dete hain taaki bot ka command trigger message purge list mein na aaye
    try:
        await ctx.message.delete()
    except Exception:
        pass

    deleted = 0

    # CASE A: ,purge bots [amount]
    if target.lower() == "bots":
        if limit is None:
            return await ctx.send("❌ **Error:** Please specify the number of bot messages to delete!", delete_after=5)
        
        def is_bot(m):
            return m.author.bot
            
        try:
            deleted_msgs = await ctx.channel.purge(limit=limit, check=is_bot)
            deleted = len(deleted_msgs)
        except discord.Forbidden:
            return await ctx.send("❌ **Error:** I don't have permission to manage/delete messages in this channel!", delete_after=5)

    # CASE B: ,purge @user [amount]
    elif target.startswith("<@") and target.endswith(">"):
        if limit is None:
            return await ctx.send("❌ **Error:** Please specify the number of messages to delete for this user!", delete_after=5)
        
        # Extracting user ID from mention
        try:
            user_id = int(target.replace("<@", "").replace(">", "").replace("!", ""))
            member = ctx.guild.get_member(user_id)
        except Exception:
            return await ctx.send("❌ **Error:** Invalid member mention!", delete_after=5)

        if not member:
            return await ctx.send("❌ **Error:** Member not found in this server!", delete_after=5)

        def is_user(m):
            return m.author.id == member.id

        try:
            deleted_msgs = await ctx.channel.purge(limit=limit, check=is_user)
            deleted = len(deleted_msgs)
        except discord.Forbidden:
            return await ctx.send("❌ **Error:** I don't have permission to manage/delete messages in this channel!", delete_after=5)

    # CASE C: ,purge [amount] (Normal clear)
    else:
        try:
            # Agar pehla argument number hai (e.g. ,purge 100)
            amount = int(target)
        except ValueError:
            return await ctx.send(f"❌ **Error:** Invalid syntax! Use `{current_prefix}purge` to see correct formats.", delete_after=5)

        try:
            deleted_msgs = await ctx.channel.purge(limit=amount)
            deleted = len(deleted_msgs)
        except discord.Forbidden:
            return await ctx.send("❌ **Error:** I don't have permission to manage/delete messages in this channel!", delete_after=5)

    # 2. Success Embed (Sent and auto-deleted after 4 seconds to keep chat clean)
    success_embed = discord.Embed(
        description=f"🧹 **Successfully purged {deleted} messages.**",
        color=0x2B2D31
    )
    await ctx.send(embed=success_embed, delete_after=4)

# ====================================================================
#                  URBAN DICTIONARY COMMAND (Everyone)
# ====================================================================

@bot.command(name="urban", aliases=["ud", "slang"])
async def urban_dictionary(ctx, *, word: str = None):
    # Dynamic prefix fetch safely
    data = await settings_col.find_one({"_id": str(ctx.guild.id)})
    current_prefix = data["prefix"] if data and "prefix" in data else ","

    if not word:
        embed_help = discord.Embed(
            title="📖 Urban Dictionary Search",
            description=(
                "Search the definition of any slang or street lingo.\n\n"
                "**Format:**\n"
                f"`{current_prefix}urban [word]`\n\n"
                "**Example:**\n"
                f"`{current_prefix}urban capping`"
            ),
            color=0x2B2D31
        )
        return await ctx.reply(embed=embed_help)

    # Urban API URL
    url = f"https://api.urbandictionary.com/v0/define?term={word}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                return await ctx.reply("❌ **Error:** API se connect nahi ho pa raha. Baad mein try karein.")
            
            result = await response.json()

    # Agar word ka meaning nahi mila
    if not result.get("list"):
        return await ctx.reply(f"🔍 **No definition found for:** `{word}`. Lagta hai ye lingo abhi naya hai!")

    # Fetching the first/top definition
    top_def = result["list"][0]
    
    # Cleaning brackets [...] from Urban Dictionary format
    definition = top_def["definition"].replace("[", "").replace("]", "")
    example = top_def["example"].replace("[", "").replace("]", "")
    
    # Slicing if definition is too long for Discord Embed limits
    if len(definition) > 1000:
        definition = definition[:1000] + "..."
    if len(example) > 1000:
        example = example[:1000] + "..."

    # Premium Aesthetic Embed
    embed = discord.Embed(
        title=f"📖 {top_def['word']}",
        url=top_def["permalink"],
        color=0x2B2D31
    )
    embed.add_field(name="Definition", value=definition, inline=False)
    
    if example:
        embed.add_field(name="Example", value=f"*{example}*", inline=False)
        
    embed.add_field(
        name="Feedback", 
        value=f"👍 **{top_def['thumbs_up']}** | 👎 **{top_def['thumbs_down']}**", 
        inline=True
    )
    embed.set_footer(text=f"Author: {top_def['author']} | Requested by {ctx.author.name}")

    await ctx.reply(embed=embed)

# ====================================================================
#                  TRANSLATE COMMAND (Everyone)
# ====================================================================

@bot.command(name="translate", aliases=["tr"])
async def translate_text(ctx, language: str = None, *, text: str = None):
    # Dynamic prefix fetch safely
    data = await settings_col.find_one({"_id": str(ctx.guild.id)})
    current_prefix = data["prefix"] if data and "prefix" in data else ","

    if not language or not text:
        embed_help = discord.Embed(
            title="🌐 Instant Translator",
            description=(
                "Translates any language text into your target language.\n\n"
                "**Format:**\n"
                f"`{current_prefix}translate [target_language] [text]`\n\n"
                "**Examples:**\n"
                f"`{current_prefix}translate english arre bhai kaise ho` (Translates to English)\n"
                f"`{current_prefix}translate hindi what are you doing brother` (Translates to Hindi)\n"
                f"`{current_prefix}translate french hello my friend` (Translates to French)"
            ),
            color=0x2B2D31
        )
        return await ctx.reply(embed=embed_help)

    # Clean target language inputs (simple mappings for lazy typers)
    lang_mapping = {
        "english": "en", "eng": "en", "en": "en",
        "hindi": "hi", "hin": "hi", "hi": "hi",
        "french": "fr", "fr": "fr",
        "spanish": "es", "es": "es",
        "german": "de", "de": "de",
        "japanese": "ja", "ja": "ja",
        "russian": "ru", "ru": "ru",
        "chinese": "zh", "zh": "zh",
        "arabic": "ar", "ar": "ar"
    }

    target_lang = lang_mapping.get(language.lower(), language.lower()[:2])

    # Dynamic API Request to Google Translate via Free API Endpoint
    encoded_text = urllib.parse.quote(text)
    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl={target_lang}&dt=t&q={encoded_text}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                return await ctx.reply("❌ **Error:** Translation server offline hai. Baad mein try karein.")
            
            result = await response.json()

    try:
        # Extracting translated sentences safely
        translated_text = "".join([sentence[0] for sentence in result[0] if sentence[0]])
        detected_lang = result[2]  # Automatically detected input language
        
        # Premium Output Embed
        embed = discord.Embed(
            title="🌐 Translation Success",
            color=0x2B2D31
        )
        embed.add_field(name="Original Text", value=f"```{text}```", inline=False)
        embed.add_field(name=f"Translated Text ({language.upper()})", value=f"```{translated_text}```", inline=False)
        embed.set_footer(text=f"Detected Source Language: {detected_lang.upper()} | Request by {ctx.author.name}")
        
        await ctx.reply(embed=embed)

    except Exception:
        await ctx.reply("❌ **Error:** Is language code ko main dhang se decode nahi kar paya. Language name check karo (e.g. `english`, `hindi`, `french` etc).")

# ====================================================================
#                      TICKET SYSTEM (Buttons & MongoDB)
# ====================================================================

# ----------------------------
# 1. BUTTON VIEW FOR CREATING TICKETS
# ----------------------------
class TicketCreateView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Timeout=None zaroori hai taaki buttons forever chalte rahein

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.success, emoji="📩", custom_id="create_ticket_btn")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user

        # Fetch current ticket count or settings for this guild
        guild_data = await tickets_col.find_one({"_id": str(guild.id)})
        if not guild_data:
            guild_data = {"_id": str(guild.id), "ticket_count": 0, "staff_role_id": None}
            await tickets_col.insert_one(guild_data)

        # Check if user already has an active ticket to avoid spam
        existing_ticket = await tickets_col.find_one({
            "guild_id": str(guild.id),
            "owner_id": str(member.id),
            "active": True
        })
        if existing_ticket:
            # Check if channel actually exists on discord
            channel_check = guild.get_channel(int(existing_ticket["channel_id"]))
            if channel_check:
                return await interaction.response.send_message(
                    f"❌ You already have an active ticket: {channel_check.mention}", 
                    ephemeral=True
                )

        # Increment ticket counter
        ticket_num = guild_data.get("ticket_count", 0) + 1
        await tickets_col.update_one({"_id": str(guild.id)}, {"$set": {"ticket_count": ticket_num}})

        # Set up permissions for the new ticket channel
        # 1. Everyone should NOT see the ticket
        # 2. Ticket creator should see and write
        # 3. Bot should see and write
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True)
        }

        # If a staff role is configured, give them access
        staff_role = None
        if guild_data.get("staff_role_id"):
            staff_role = guild.get_role(int(guild_data["staff_role_id"]))
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True)

        # Create the ticket channel
        channel_name = f"ticket-{ticket_num:04d}"
        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            topic=f"Ticket created by {member.name} (ID: {member.id})",
            reason=f"Ticket created via button"
        )

        # Save ticket info in DB
        await tickets_col.insert_one({
            "guild_id": str(guild.id),
            "channel_id": str(ticket_channel.id),
            "owner_id": str(member.id),
            "active": True
        })

        # Send welcome message inside the ticket channel
        embed_welcome = discord.Embed(
            title=f"🎫 Ticket #{ticket_num:04d}",
            description=(
                f"Welcome {member.mention} to your private ticket!\n"
                "Please describe your query/issue here.\n"
                "Support staff will assist you shortly.\n\n"
                "**Staff Commands:**\n"
                "`,ticket close` - Closes and deletes this ticket.\n"
                "`,ticket add @user` - Adds a user to this ticket.\n"
                "`,ticket remove @user` - Removes a user from this ticket."
            ),
            color=0x2B2D31
        )
        embed_welcome.set_footer(text="Use command ',ticket close' when done.")
        await ticket_channel.send(content=f"{member.mention} | Staff Group", embed=embed_welcome)

        # Let the user know their ticket is created (Ephemeral/Private message)
        await interaction.response.send_message(f"✅ Ticket created! Go to {ticket_channel.mention}", ephemeral=True)


# ----------------------------
# 2. PERSISTENT VIEW REGISTRATION (Add inside on_ready)
# ----------------------------
# Note: Apne bot ke `on_ready` event ke andar ye lines daalna mat bhoolna taaki 
# bot restart hone par bhi ticket buttons active rahein!
@bot.event
async def on_ready():
    bot.add_view(TicketCreateView()) # Buttons persistence setup
    print(f"Logged in as {bot.user} - Ticket buttons registered!")


# ----------------------------
# 3. TICKET COMMANDS GROUP
# ----------------------------
@bot.group(name="ticket", invoke_without_command=True)
async def ticket_group(ctx):
    # Dynamic prefix fetch safely
    data = await settings_col.find_one({"_id": str(ctx.guild.id)})
    current_prefix = data["prefix"] if data and "prefix" in data else ","

    embed_help = discord.Embed(
        title="🎫 Ticket Management",
        description=(
            "Set up and manage private support ticket channels.\n\n"
            "**Setup & Settings (Admin Only):**\n"
            f"`{current_prefix}ticket setup` - Sends the ticket creation panel.\n\n"
            "**Inside Ticket Commands (Mods Only):**\n"
            f"`{current_prefix}ticket add [@user]` - Adds a user to the ticket.\n"
            f"`{current_prefix}ticket remove [@user]` - Removes a user from the ticket.\n"
            f"`{current_prefix}ticket close` - Closes and deletes the ticket."
        ),
        color=0x2B2D31
    )
    await ctx.reply(embed=embed_help)


# --- SUB-COMMAND: SETUP PANEL ---
@ticket_group.command(name="setup")
@commands.has_permissions(administrator=True) # Admin only setup
async def ticket_setup(ctx):
    embed_panel = discord.Embed(
        title="✉️ Support Ticket Panel",
        description=(
            "Need assistance or have a question?\n"
            "Click the button below to open a private ticket with our staff team!"
        ),
        color=0x2B2D31
    )
    embed_panel.set_footer(text="Please do not open tickets without a valid reason.")
    
    # Send the panel message with the green button view
    await ctx.send(embed=embed_panel, view=TicketCreateView())
    await ctx.message.delete()


# --- SUB-COMMAND: ADD USER TO TICKET ---
@ticket_group.command(name="add")
@commands.has_permissions(manage_channels=True)
async def ticket_add(ctx, member: discord.Member = None):
    if not member:
        return await ctx.reply("❌ **Error:** Please mention a member to add to this ticket!")

    # Check if this channel is actually an active ticket
    ticket_data = await tickets_col.find_one({"channel_id": str(ctx.channel.id), "active": True})
    if not ticket_data:
        return await ctx.reply("❌ **Error:** This channel is not an active support ticket!")

    try:
        # Give member access to see and write in the channel
        await ctx.channel.set_permissions(member, read_messages=True, send_messages=True, attach_files=True)
        await ctx.reply(f"✅ Added {member.mention} to this ticket channel.")
    except Exception as e:
        await ctx.reply(f"❌ **Error:** Could not update channel permissions. Details: {e}")


# --- SUB-COMMAND: REMOVE USER FROM TICKET ---
@ticket_group.command(name="remove")
@commands.has_permissions(manage_channels=True)
async def ticket_remove(ctx, member: discord.Member = None):
    if not member:
        return await ctx.reply("❌ **Error:** Please mention a member to remove from this ticket!")

    ticket_data = await tickets_col.find_one({"channel_id": str(ctx.channel.id), "active": True})
    if not ticket_data:
        return await ctx.reply("❌ **Error:** This channel is not an active support ticket!")

    try:
        # Deny access to the channel
        await ctx.channel.set_permissions(member, overwrite=None)
        await ctx.reply(f"❌ Removed {member.mention} from this ticket channel.")
    except Exception as e:
        await ctx.reply(f"❌ **Error:** Could not update channel permissions. Details: {e}")


# --- SUB-COMMAND: CLOSE TICKET ---
@ticket_group.command(name="close")
@commands.has_permissions(manage_channels=True)
async def ticket_close(ctx):
    # Check if this channel is an active ticket
    ticket_data = await tickets_col.find_one({"channel_id": str(ctx.channel.id), "active": True})
    if not ticket_data:
        return await ctx.reply("❌ **Error:** You can only close tickets inside active ticket channels!")

    await ctx.reply("⚠️ **Closing ticket...** This channel will be deleted in 5 seconds.")
    await asyncio.sleep(5)

    try:
        # Delete from DB
        await tickets_col.delete_one({"channel_id": str(ctx.channel.id)})
        # Delete channel from server
        await ctx.channel.delete(reason=f"Ticket closed by {ctx.author}")
    except Exception as e:
        await ctx.reply(f"❌ **Error while deleting channel:** {e}")

# ====================================================================
#                  INTERACTIVE EMBED BUILDER (Admins Only)
# ====================================================================

# Embed builder database collection (Line 35 ke paas add kar sakte hain)
embed_col = db["embed_drafts"]

@bot.group(name="embed", invoke_without_command=True)
@commands.has_permissions(administrator=True) # Admin only
async def embed_group(ctx):
    data = await settings_col.find_one({"_id": str(ctx.guild.id)})
    current_prefix = data["prefix"] if data and "prefix" in data else ","

    embed_help = discord.Embed(
        title="🎨 Interactive Embed Builder",
        description=(
            "Create and design beautiful custom embeds directly from Discord.\n\n"
            "**Step-by-Step Commands:**\n"
            f"1. `{current_prefix}embed create` — Starts a new empty draft.\n"
            f"2. `{current_prefix}embed title [text]` — Sets the main heading.\n"
            f"3. `{current_prefix}embed description [text]` — Sets the main body text.\n"
            f"4. `{current_prefix}embed color [hex]` — Sets side border color (e.g., `#ff0000`).\n"
            f"5. `{current_prefix}embed thumbnail [url]` — Sets a small top-right image.\n"
            f"6. `{current_prefix}embed send [#channel]` — Sends the final embed to target channel."
        ),
        color=0x2B2D31
    )
    await ctx.reply(embed=embed_help)


# --- STEP 1: CREATE DRAFT ---
@embed_group.command(name="create")
@commands.has_permissions(administrator=True)
async def embed_create(ctx):
    guild_id = str(ctx.guild.id)
    author_id = str(ctx.author.id)

    # Initialize or reset draft
    await embed_col.update_one(
        {"guild_id": guild_id, "author_id": author_id},
        {"$set": {
            "title": None,
            "description": "This is a default description. Use `,embed description [text]` to change it.",
            "color": "2B2D31", # Default greyish black
            "thumbnail": None
        }},
        upsert=True
    )
    await ctx.reply("🆕 **New embed draft created!** Use other `,embed` sub-commands to design it.")


# --- STEP 2: SET TITLE ---
@embed_group.command(name="title")
@commands.has_permissions(administrator=True)
async def embed_title(ctx, *, text: str = None):
    guild_id = str(ctx.guild.id)
    author_id = str(ctx.author.id)

    if not text:
        return await ctx.reply("❌ **Error:** Please provide a title text!")

    # Check if draft exists
    draft = await embed_col.find_one({"guild_id": guild_id, "author_id": author_id})
    if not draft:
        return await ctx.reply("❌ **Error:** No active draft found! Start with `,embed create` first.")

    await embed_col.update_one(
        {"guild_id": guild_id, "author_id": author_id},
        {"$set": {"title": text}}
    )
    await ctx.reply(f"✅ **Title updated to:** `{text}`")


# --- STEP 3: SET DESCRIPTION ---
@embed_group.command(name="description", aliases=["desc"])
@commands.has_permissions(administrator=True)
async def embed_description(ctx, *, text: str = None):
    guild_id = str(ctx.guild.id)
    author_id = str(ctx.author.id)

    if not text:
        return await ctx.reply("❌ **Error:** Please provide description text!")

    draft = await embed_col.find_one({"guild_id": guild_id, "author_id": author_id})
    if not draft:
        return await ctx.reply("❌ **Error:** No active draft found! Start with `,embed create` first.")

    await embed_col.update_one(
        {"guild_id": guild_id, "author_id": author_id},
        {"$set": {"description": text}}
    )
    await ctx.reply("✅ **Description updated successfully!**")


# --- STEP 4: SET COLOR ---
@embed_group.command(name="color")
@commands.has_permissions(administrator=True)
async def embed_color(ctx, hex_code: str = None):
    guild_id = str(ctx.guild.id)
    author_id = str(ctx.author.id)

    if not hex_code:
        return await ctx.reply("❌ **Error:** Please provide a hex color code (e.g., `#FF0000` or `FF0000`)!")

    draft = await embed_col.find_one({"guild_id": guild_id, "author_id": author_id})
    if not draft:
        return await ctx.reply("❌ **Error:** No active draft found! Start with `,embed create` first.")

    # Format check & stripping # symbol if present
    clean_hex = hex_code.replace("#", "").strip()
    try:
        # Check if it's a valid hex by converting it
        int(clean_hex, 16)
    except ValueError:
        return await ctx.reply("❌ **Error:** Invalid Hex color code! Use formats like `#FF0000` or `FF0000`.")

    await embed_col.update_one(
        {"guild_id": guild_id, "author_id": author_id},
        {"$set": {"color": clean_hex}}
    )
    await ctx.reply(f"✅ **Border color updated to:** `#{clean_hex}`")


# --- STEP 5: SET THUMBNAIL ---
@embed_group.command(name="thumbnail")
@commands.has_permissions(administrator=True)
async def embed_thumbnail(ctx, url: str = None):
    guild_id = str(ctx.guild.id)
    author_id = str(ctx.author.id)

    if not url:
        return await ctx.reply("❌ **Error:** Please provide an image URL for the thumbnail!")

    draft = await embed_col.find_one({"guild_id": guild_id, "author_id": author_id})
    if not draft:
        return await ctx.reply("❌ **Error:** No active draft found! Start with `,embed create` first.")

    # Basic link validation
    if not url.startswith(("http://", "https://")):
        return await ctx.reply("❌ **Error:** Invalid URL! It must start with `http://` or `https://`.")

    await embed_col.update_one(
        {"guild_id": guild_id, "author_id": author_id},
        {"$set": {"thumbnail": url}}
    )
    await ctx.reply("✅ **Thumbnail updated successfully!**")


# --- STEP 6: SEND EMBED ---
@embed_group.command(name="send")
@commands.has_permissions(administrator=True)
async def embed_send(ctx, channel: discord.TextChannel = None):
    guild_id = str(ctx.guild.id)
    author_id = str(ctx.author.id)
    
    channel = channel or ctx.channel

    # Fetch user's current draft
    draft = await embed_col.find_one({"guild_id": guild_id, "author_id": author_id})
    if not draft:
        return await ctx.reply("❌ **Error:** No active draft found to send! Use `,embed create` to start.")

    # Build the final Discord embed
    try:
        # Hex conversion
        color_int = int(draft.get("color", "2B2D31"), 16)
        
        final_embed = discord.Embed(
            title=draft.get("title"),
            description=draft.get("description"),
            color=color_int
        )
        
        # Add thumbnail if available
        if draft.get("thumbnail"):
            final_embed.set_thumbnail(url=draft["thumbnail"])

        # Send to target channel
        await channel.send(embed=final_embed)
        await ctx.reply(f"🚀 **Embed successfully sent to {channel.mention}!**")

        # Draft delete karke database clean up kar dete hain
        await embed_col.delete_one({"guild_id": guild_id, "author_id": author_id})

    except discord.HTTPException as e:
        await ctx.reply(f"❌ **Error sending embed:** Double check your draft values (especially the thumbnail URL). Details: {e}")
    except Exception as e:
        await ctx.reply(f"❌ **An unexpected error occurred:** {e}")

# ====================================================================
#                     SETTINGS DASHBOARD COMMAND (Admins Only)
# ====================================================================

@bot.command(name="settings", aliases=["config", "panel"])
@commands.has_permissions(administrator=True)
async def server_settings(ctx):
    guild_id = str(ctx.guild.id)
    
    # 1. Fetch Dynamic Prefix & AutoMod details
    guild_settings = await settings_col.find_one({"_id": guild_id})
    current_prefix = guild_settings.get("prefix", ",") if guild_settings else ","
    automod_invite = "🟢 Enabled" if guild_settings and guild_settings.get("invite_block") else "🔴 Disabled"
    
    # 2. Fetch Ticket Configs
    ticket_data = await tickets_col.find_one({"_id": guild_id})
    ticket_status = "🟢 Ready" if ticket_data else "⚪ Not Configured"
    
    # 3. Fetch Disabled Commands List
    disabled_cmds_cursor = disabled_commands_col.find({"guild_id": guild_id})
    disabled_list = [doc["command_name"] async for doc in disabled_cmds_cursor]
    disabled_text = ", ".join([f"`{cmd}`" for cmd in disabled_list]) if disabled_list else "*None*"

    # Premium Dashboard Embed Setup
    embed = discord.Embed(
        title=f"🛡️ {ctx.guild.name} Configuration Panel",
        description="Here is the current status of the bot's features and modules in this server:",
        color=0x2B2D31
    )
    
    # Fields
    embed.add_field(name="⚙️ Prefix Settings", value=f"Active Prefix: `{current_prefix}`", inline=True)
    embed.add_field(name="🛡️ AutoMod Status", value=f"Anti-Invite: {automod_invite}", inline=True)
    embed.add_field(name="🎫 Ticket Module", value=f"Status: {ticket_status}", inline=True)
    embed.add_field(name="🚫 Disabled Commands", value=disabled_text, inline=False)
    
    # Thumbnail and footer
    if ctx.guild.icon:
        embed.set_thumbnail(url=ctx.guild.icon.url)
        
    embed.set_footer(text=f"Requested by {ctx.author.name} | Server ID: {guild_id}")
    
    await ctx.reply(embed=embed)

# ====================================================================
#                   NICKNAME COMMAND (Moderators)
# ====================================================================

@bot.command(name="nickname", aliases=["nick", "setnick"])
@commands.has_permissions(manage_nicknames=True)
async def change_nickname(ctx, member: discord.Member = None, *, new_name: str = None):
    # Dynamic prefix fetch safely
    data = await settings_col.find_one({"_id": str(ctx.guild.id)})
    current_prefix = data["prefix"] if data and "prefix" in data else ","

    # 1. Agar member mention nahi kiya (Help)
    if not member:
        embed_help = discord.Embed(
            title="🏷️ Nickname Manager",
            description=(
                "Change or reset a member's nickname in the server.\n\n"
                "**Formats:**\n"
                f"`{current_prefix}nickname [@user] [new_name]` — Changes nickname.\n"
                f"`{current_prefix}nickname [@user]` — Resets nickname back to default.\n\n"
                "**Examples:**\n"
                f"`{current_prefix}nickname @Rohan Pappu`\n"
                f"`{current_prefix}nickname @Rohan` *(Resets to normal)*"
            ),
            color=0x2B2D31
        )
        return await ctx.reply(embed=embed_help)

    # 2. Hierarchy Protection: Mod cannot touch equal or higher roles
    if ctx.author.top_role <= member.top_role and ctx.author.id != ctx.guild.owner_id:
        return await ctx.reply("❌ **Error:** You cannot change the nickname of someone with an equal or higher role than you.")

    # 3. Bot Hierarchy Protection: Bot cannot touch members above its own role
    if ctx.guild.me.top_role <= member.top_role:
        return await ctx.reply("❌ **Error:** I cannot change this user's nickname. Drag my role higher in server settings!")

    # 4. Server Owner Check (Discord API owners ka nickname change karne nahi deta bots ko)
    if member.id == ctx.guild.owner_id:
        return await ctx.reply("❌ **Error:** Discord API does not allow bots to change the nickname of the Server Owner!")

    try:
        if new_name:
            # Clean nickname length check (Discord max 32 chars support karta hai)
            if len(new_name) > 32:
                return await ctx.reply("❌ **Error:** Nickname cannot be longer than 32 characters!")
            
            # Change nickname
            await member.edit(nick=new_name, reason=f"Nickname changed by {ctx.author}")
            
            embed_success = discord.Embed(
                description=f"✅ Changed nickname of {member.mention} to **`{new_name}`**",
                color=0x2B2D31
            )
            await ctx.reply(embed=embed_success)
            
        else:
            # Reset nickname (Passing None resets the nick)
            await member.edit(nick=None, reason=f"Nickname reset by {ctx.author}")
            
            embed_reset = discord.Embed(
                description=f"🧹 Reset nickname of {member.mention} back to default.",
                color=0x2B2D31
            )
            await ctx.reply(embed=embed_reset)

    except discord.Forbidden:
        await ctx.reply("❌ **Error:** I don't have 'Manage Nicknames' permission or my role position is too low!")
    except Exception as e:
        await ctx.reply(f"❌ **An unexpected error occurred:** {e}")

# ====================================================================
#                      SHRUG COMMAND (Everyone)
# ====================================================================

@bot.command(name="shrug")
async def shrug_message(ctx, *, message: str = None):
    # Try deleting original command trigger to make it look clean
    try:
        await ctx.message.delete()
    except Exception:
        pass

    shrug_emoji = r"¯\_(ツ)_/¯"

    if message:
        # User message + Shrug
        await ctx.send(f"{message} {shrug_emoji}")
    else:
        # Only Shrug
        await ctx.send(shrug_emoji)

# ====================================================================
#                        SHIP COMMAND (Fun)
# ====================================================================

import random # File ke top par import check kar lena

@bot.command(name="ship", aliases=["love", "match"])
async def ship_users(ctx, user1: discord.Member = None, user2: discord.Member = None):
    # Dynamic prefix fetch safely
    data = await settings_col.find_one({"_id": str(ctx.guild.id)})
    current_prefix = data["prefix"] if data and "prefix" in data else ","

    # Agar valid mentions nahi hain
    if not user1 or not user2:
        embed_help = discord.Embed(
            title="❤️ Love Matcher (Ship)",
            description=(
                "Find out the love match percentage between two members!\n\n"
                "**Format:**\n"
                f"`{current_prefix}ship [@user1] [@user2]`\n\n"
                "**Example:**\n"
                f"`{current_prefix}ship @Rohan @Sneha`"
            ),
            color=0x2B2D31
        )
        return await ctx.reply(embed=embed_help)

    # Self-shipping safeguard (Agar khud ko hi ship kar raha hai)
    if user1.id == user2.id:
        return await ctx.reply("💔 **Error:** Apne aap se hi prem? Thoda dosto ko bhi chance do!")

    # Mathematical Consistent seed logic:
    # Dono user IDs ko combine karke ek unique number banayenge taaki same couple ka percentage hamesha same aaye
    combined_id = user1.id + user2.id
    random.seed(combined_id)
    love_percentage = random.randint(0, 100)
    random.seed() # Seed reset taaki baaki random functions kharab na hon

    # Custom dynamic status messages
    if love_percentage < 20:
        status = "💔 Ekdum ghatiya match! Dur raho ek dusre se."
    elif love_percentage < 45:
        status = f"🩹 Friendzone hone ke 99% chances hain."
    elif love_percentage < 75:
        status = "👀 Hawa chal rahi hai... Thodi koshish aur, baat ban sakti hai!"
    elif love_percentage < 90:
        status = "💖 Sacha Pyaar! Rab ne bana di jodi."
    else:
        status = "🔥 Match made in Heaven! Direct shaadi ki taiyari karo."

    # Creating a visual progress bar
    filled_bars = round(love_percentage / 10)
    empty_bars = 10 - filled_bars
    progress_bar = "🟥" * filled_bars + "⬛" * empty_bars

    # Sleek Matchmaker Embed
    embed = discord.Embed(
        title="💘 Matchmaker Connection",
        description=f"Checking affinity between **{user1.name}** and **{user2.name}**...\n\n",
        color=0x2B2D31
    )
    embed.add_field(
        name=f"📊 Affinity: {love_percentage}%", 
        value=f"{progress_bar}\n\n**Verdict:** {status}", 
        inline=False
    )
    embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)

    await ctx.reply(embed=embed)

# ====================================================================
#                      HOTNESS METER COMMAND (Fun)
# ====================================================================

@bot.command(name="hot", aliases=["hotness", "cute"])
async def hot_meter(ctx, member: discord.Member = None):
    # Default to sender if no member is mentioned
    member = member or ctx.author

    # Consistent Seed Calculation
    random.seed(member.id)
    hot_percentage = random.randint(0, 100)
    random.seed() # Reset seed

    # Custom status messages based on hotness
    if hot_percentage < 15:
        status = "🧊 Ice cold. Zero hotness, ekdum shareef lag rahe ho."
    elif hot_percentage < 40:
        status = "🥱 Thoda facewash use karo, normal category mein ho."
    elif hot_percentage < 70:
        status = "🕶️ Dekhne layak ho! Thoda style aur upgrade karo."
    elif hot_percentage < 90:
        status = "💅 Damn, hot alert! DMs checks karte rehna apne."
    else:
        status = "🥵 Extreme Hazard! Pure server mein aag lagani hai kya?"

    # Creating a visual progress bar
    filled_bars = round(hot_percentage / 10)
    empty_bars = 10 - filled_bars
    progress_bar = "🔥" * filled_bars + "⬛" * empty_bars

    # Hotness Embed
    embed = discord.Embed(
        title="🌡️ Hotness Calculator",
        description=f"Scanning **{member.name}**'s aesthetic levels...\n\n",
        color=0x2B2D31
    )
    embed.add_field(
        name=f"🔥 Hotness: {hot_percentage}%", 
        value=f"{progress_bar}\n\n**Verdict:** {status}", 
        inline=False
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Checked by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)

    await ctx.reply(embed=embed)

# ====================================================================
#                     VIP PROFILE SYSTEM (Everyone)
# ====================================================================

@bot.group(name="profile",aliases=["loc"], invoke_without_command=True)
async def profile_group(ctx, member: discord.Member = None):
    # Agar kisi aur ka tag nahi kiya toh sender ki profile dikhao
    member = member or ctx.author
    guild_id = str(ctx.guild.id)
    user_id = str(member.id)

    # Database se profile data fetch karo
    user_data = await profiles_col.find_one({"guild_id": guild_id, "user_id": user_id})
    
    # Default values agar DB mein data na ho
    bio = user_data.get("bio", "No bio set. Use `,profile set bio [text]`") if user_data else "No bio set. Use `,profile set bio [text]`"
    location = user_data.get("location", "Not specified") if user_data else "Not specified"

    # Server Join Date formatting
    joined_at = member.joined_at.strftime("%b %d, %Y") if member.joined_at else "Unknown"
    
    # Account Creation Date formatting
    created_at = member.created_at.strftime("%b %d, %Y")

    # Member ke active roles count (excluding @everyone)
    roles_count = len(member.roles) - 1

    # VIP Aesthetic Card Embed
    embed = discord.Embed(
        title=f"💳 Identity Card — {member.name}",
        color=0x2B2D31
    )
    
    embed.add_field(name="📝 About Me (Bio)", value=f"*{bio}*", inline=False)
    embed.add_field(name="📍 Location", value=f"`{location}`", inline=True)
    embed.add_field(name="🛡️ Roles Count", value=f"`{roles_count} Roles`", inline=True)
    
    # Important timestamps
    embed.add_field(name="📅 Joined Server", value=joined_at, inline=True)
    embed.add_field(name="🚀 Created Account", value=created_at, inline=True)

    # User ka avatar profile pic lagane ke liye
    embed.set_thumbnail(url=member.display_avatar.url)
    
    # VIP Card look footer
    embed.set_footer(
        text=f"ID: {user_id} | Card Requested by {ctx.author.name}", 
        icon_url=ctx.author.display_avatar.url
    )

    await ctx.reply(embed=embed)


# --- SUB-COMMAND: SET BIO ---
@profile_group.group(name="set", invoke_without_command=True)
async def profile_set_group(ctx):
    # Helper message agar kisi ne sirf ',profile set' chalaya
    current_prefix = await get_prefix(bot, ctx.message)
    embed = discord.Embed(
        title="🔧 Profile Customizer",
        description=(
            "Customize your Card using these sub-commands:\n\n"
            f"`{current_prefix}profile set bio [your text]` — Set your bio line.\n"
            f"`{current_prefix}profile set location [city]` — Set your location."
        ),
        color=0x2B2D31
    )
    await ctx.reply(embed=embed)


@profile_set_group.command(name="bio")
async def profile_set_bio(ctx, *, text: str = None):
    if not text:
        return await ctx.reply("❌ **Error:** Please write a cool bio to save!")

    # Bio limit set (taaki embed look kharab na ho)
    if len(text) > 150:
        return await ctx.reply("❌ **Error:** Bio bahut lamba hai! Max 150 characters allowed hain.")

    guild_id = str(ctx.guild.id)
    user_id = str(ctx.author.id)

    # MongoDB update/insert
    await profiles_col.update_one(
        {"guild_id": guild_id, "user_id": user_id},
        {"$set": {"bio": text}},
        upsert=True
    )

    success_embed = discord.Embed(
        description=f"✅ **Bio Saved!** Your new bio is now:\n`{text}`",
        color=0x2B2D31
    )
    await ctx.reply(embed=success_embed)


# --- SUB-COMMAND: SET LOCATION ---
@profile_set_group.command(name="location", aliases=["loc"])
async def profile_set_location(ctx, *, city: str = None):
    if not city:
        return await ctx.reply("❌ **Error:** Please provide your city or country name!")

    if len(city) > 40:
        return await ctx.reply("❌ **Error:** Location name is too long! Max 40 characters.")

    guild_id = str(ctx.guild.id)
    user_id = str(ctx.author.id)

    # MongoDB update/insert
    await profiles_col.update_one(
        {"guild_id": guild_id, "user_id": user_id},
        {"$set": {"location": city}},
        upsert=True
    )

    success_embed = discord.Embed(
        description=f"✅ **Location Saved!** Set to `{city}`.",
        color=0x2B2D31
    )
    await ctx.reply(embed=success_embed)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        missing_perms = ", ".join(error.missing_permissions)
        embed = discord.Embed(
            description=f"❌ **Access Denied:** You need `{missing_perms}` permission to run this command.",
            color=0xff0000 # Red color for warning
        )
        return await ctx.reply(embed=embed, delete_after=10)

    elif isinstance(error, commands.CheckFailure):
        embed = discord.Embed(
            description=f"❌ **Authorization Failed:** {str(error)}",
            color=0xff0000
        )
        return await ctx.reply(embed=embed, delete_after=10)

    elif isinstance(error, commands.MissingRequiredArgument):
        embed = discord.Embed(
            description=f"❌ **Missing Arguments:** Please provide all required fields.\nUse `{ctx.prefix}help {ctx.command.name}` for details.",
            color=0xff0000
        )
        return await ctx.reply(embed=embed, delete_after=10)

    # Baaki kisi unexpected crash ke liye console mein log karo
    print(f"[Command Error Logging] {error}")


if __name__ == "__main__":
    Thread(target=run).start()
    bot.run(TOKEN)
