import discord
from discord.ext import commands
from google import genai
from flask import Flask
from threading import Thread
import os, json
from dotenv import load_dotenv
import datetime

afk_users = {}
# --- Database Setup (Channel IDs yaad rakhne ke liye) ---
DATA_FILE = "settings.json"

def load_settings():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f: return json.load(f)
    return {"welcome_channel": None, "bye_channel": None}

def save_settings(data):
    with open(DATA_FILE, "w") as f: json.dump(data, f)

# --- Flask & AI Setup (Tera Original) ---
app = Flask('')
@app.route('/')
def home(): return "Happy is Online!"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
API_KEY = os.getenv('GEMINI_API_KEY')
client_ai = genai.Client(api_key=API_KEY)
MODEL_ID = "gemini-2.5-flash"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True 
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Youuuu 🫣"))
    await bot.tree.sync()
    print(f'Lo bhai, {bot.user} online hai!')

# --- ADMIN COMMANDS: Channel Choose Karne Ke Liye ---
@bot.tree.command(name="setwelcome", description="Set welcome channel")
@commands.has_permissions(administrator=True)
async def setwelcome(interaction: discord.Interaction, channel: discord.TextChannel):
    settings = load_settings()
    settings["welcome_channel"] = channel.id
    save_settings(settings)
    await interaction.response.send_message(f"✅ Welcome messages ab {channel.mention} mein aayenge!")

@bot.tree.command(name="setbye", description="Set bye channel")
@commands.has_permissions(administrator=True)
async def setbye(interaction: discord.Interaction, channel: discord.TextChannel):
    settings = load_settings()
    settings["bye_channel"] = channel.id
    save_settings(settings)
    await interaction.response.send_message(f"✅ Bye messages ab {channel.mention} mein aayenge!")

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
@commands.has_permissions(administrator=True)
async def role(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if role in member.roles:
        await member.remove_roles(role)
        await interaction.response.send_message(f"❌ {role.name} role {member.name} se le liya gaya.")
    else:
        await member.add_roles(role)
        await interaction.response.send_message(f"✅ {role.name} role {member.name} ko de diya gaya.")

# 5. WARNING (Simple message warning)
@bot.tree.command(name="warn", description="Member ko warning do")
@commands.has_permissions(administrator=True)
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    embed = discord.Embed(
        title="⚠️ Warning Alert!",
        description=f"Oye {member.mention}, sudhar ja! \n**Reason:** {reason}",
        color=discord.Color.red()
    )
    await interaction.response.send_message(content=f"{member.mention}", embed=embed)

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

# 4. HELP: Bot kya-kya kar sakta hai
@bot.tree.command(name="help", description="Happy ki shaktiyon ki list")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 Happy Bot Help Menu",
        description="Bhai, main yahan sabka swagat karne aur thodi masti karne ke liye hoon!",
        color=0x2B2D31
    )
    embed.add_field(name="🌍 Global Commands", value="`/afk`, `/userinfo`, `/avatar`, `/stats`, `/ping`", inline=False)
    embed.add_field(name="🧠 AI Chat", value="Bas mujhe mention karo (`@Happy`) aur kuch bhi pucho, jawab milega!", inline=False)
    embed.add_field(name="🛡️ Admin Only", value="`/kick`, `/ban`, `/mute`, `/setwelcome`, `/setbye`, `/role`, `/warn`", inline=False)
    embed.set_footer(text="Developed by the 💜")
    await interaction.response.send_message(embed=embed)

# --- WELCOME EVENT (Glassy Embed) ---
@bot.event
async def on_member_join(member):
    settings = load_settings()
    channel_id = settings.get("welcome_channel")
    channel = bot.get_channel(channel_id) if channel_id else member.guild.system_channel
    
    if channel:
        embed = discord.Embed(
            description=f"Welcome to the server, {member.mention}! 🎉",
            color=0x2B2D31 # Glassy Look (Match Discord Dark Theme)
        )
        embed.set_author(name=member.name, icon_url=member.display_avatar.url)
        embed.set_image(url=member.display_avatar.url) # Badi image user ki
        await channel.send(embed=embed)

# --- BYE EVENT (Specific Channel) ---
@bot.event
async def on_member_remove(member):
    settings = load_settings()
    channel_id = settings.get("bye_channel")
    channel = bot.get_channel(channel_id)
    
    if channel:
        embed = discord.Embed(
            description=f"**{member.name}** chala gaya... Sad scene ho gaya! 😢",
            color=0x2B2D31
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await channel.send(embed=embed)


# AFK SLASH COMMAND
@bot.tree.command(name="afk", description="AFK set karo taaki log pareshan na karein")
async def afk(interaction: discord.Interaction, reason: str = "Break le raha hoon!"):
    afk_users[interaction.user.id] = reason
    # User ka nickname change karke [AFK] add karna (Optional)
    try:
        await interaction.user.edit(nick=f"[AFK] {interaction.user.display_name}")
    except:
        pass # Admin permissions na ho toh skip karega
        
    await interaction.response.send_message(f"✅ {interaction.user.mention}, ab aap AFK ho. Reason: {reason}")

# --- AI CHAT LOGIC (Tera Pura Original Instruction) ---
@bot.event
async def on_message(message):
    if message.author == bot.user: return

    # --- AFK REMOVAL: Agar AFK banda khud message kare toh AFK hat jaye ---
    if message.author.id in afk_users:
        del afk_users[message.author.id]
        try:
            # [AFK] tag hatana nickname se
            new_nick = message.author.display_name.replace("[AFK] ", "")
            await message.author.edit(nick=new_nick)
        except:
            pass
        await message.channel.send(f"Welcome back {message.author.mention}! Aapka AFK hata diya gaya hai.", delete_after=5)

    # --- AFK CHECK: Agar koi AFK bande ko mention kare ---
    for mention in message.mentions:
        if mention.id in afk_users:
            reason = afk_users[mention.id]
            await message.reply(f"🚨 Bhai, **{mention.name}** abhi AFK hai. \n**Reason:** {reason}", delete_after=10)
    
    # Heart Reaction logic
    greetings = ["good morning", "gm", "good night", "gn", "happy birthday", "hbd", "hello", "hi"]
    if any(word in message.content.lower() for word in greetings):
        await message.add_reaction("💜")

    if bot.user.mentioned_in(message):
        clean_prompt = message.content.replace(f'<@!{bot.user.id}>', '').replace(f'<@{bot.user.id}>', '')
        instruction = """Purpose and Goals:
* Embody the persona of 'Happy', a person from India who speaks in 'Hinglish' (a mix of Hindi and English) with a distinct street accent.
* Engage users in casual, energetic, and relatable conversations that reflect the vibrant street culture of urban India.
* Use local slang, idioms, and expressions common in Indian street lingo (e.g., 'Bhai', 'Mast', 'Kya scene hai?').

Behaviors and Rules:

1) Language and Dialect:
 a) Primarily use Hinglish, blending English vocabulary with Hindi grammar and colloquialisms.
 b) Adopt a 'street accent' which is informal, rhythmic, and high-energy.
 c) Avoid overly formal or academic language. Keep it raw and authentic.

2) Interaction Style:
 a) Greet users with local informal greetings like 'Arre, kya haal hai?' or 'Yo, what's up, mere bhai?'.
 b) Be expressive and use common fillers like 'yaar', 'bas', or 'woh'.
 c) If a user asks a complex question, explain it using simple, everyday analogies relevant to Indian life.

3) Cultural Context:
 a) Reference popular Indian street food, movies, cricket, and daily life experiences to add flavor to the conversation.
 b) Maintain a friendly, slightly cheeky, and very approachable vibe.

Overall Tone:

* Informal, street-smart, and friendly.
* High energy and conversational.
* Authentic to the 'tapori' or urban street vibe of India..
* make your replies short like chatting messages (such as 1 line reply).""" 
        
        try:
            response = client_ai.models.generate_content(model=MODEL_ID, contents=f"Instruction: {instruction}\n\nUser: {clean_prompt}")
            if response and response.text: await message.reply(response.text)
        except Exception as e:
            print(f"Error: {e}")
            await message.reply("Dimaag garam hai bhai!")
    
    await bot.process_commands(message)

if __name__ == "__main__":
    Thread(target=run).start()
    bot.run(TOKEN)
