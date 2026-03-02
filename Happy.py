import discord
from google import genai
from flask import Flask
from threading import Thread
import os
from dotenv import load_dotenv

app = Flask('')

@app.route('/')
def home():
    return "Happy is Online!"

def run():
    # Render khud hi PORT environment variable deta hai, hum usse uthayenge
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

load_dotenv() # Ye .env file se values khinch lega

TOKEN = os.getenv('DISCORD_TOKEN')
API_KEY = os.getenv('GEMINI_API_KEY')


# 1. AI Setup (New SDK)
# Yahan apni API Key daal dena
client_ai = genai.Client(api_key=API_KEY)
MODEL_ID = "gemini-2.5-flash"

class MyClient(discord.Client):
    async def on_ready(self):
        print(f'Lo bhai, {self.user} ek dum raapchik mode mein online hai!')

    # Welcome Message
    async def on_member_join(self, member):
        channel = member.guild.system_channel
        if channel:
            await channel.send(f'Arre welcome mere bhai {member.mention}! 🎉')

    async def on_message(self, message):
        if message.author == self.user:
            return

        if self.user.mentioned_in(message):
            # 1. Cleaning the prompt
            clean_prompt = message.content.replace(f'<@!{self.user.id}>', '').replace(f'<@{self.user.id}>', '')
            
            try:
                # 2. AI Call
                # Updated AI Setup with Happy's Persona
                instruction = """ Your name is Happy. You are from India and speak in Hinglish with a distinct street accent. Keep it casual, energetic, and relatable. Use slang like 'Bhai', 'Mast', 'Kya scene hai?'.
Always be informal, use street lingo, and explain complex things using Indian analogies .
Never be formal or academic. Keep it raw and authentic and Short. 
"""

# Agar naya SDK use kar rahe ho:
                response = client_ai.models.generate_content(
                    model=MODEL_ID,
                contents=f"Instruction: {instruction}\n\nUser Question: {clean_prompt}"
                )
                
                # 3. Check if response is valid before replying
                if response and response.text:
                    await message.reply(response.text)
                else:
                    await message.reply("Bhai, kuch samajh nahi aaya, phir se bol?")

            except Exception as e:
                # Sirf tabhi ye bolega jab ASLI mein error aaye
                print(f"Asli Error ye hai bhai: {e}")
                # await message.reply("Bhai, dimaag garam ho gaya hai, thodi der baad puch!") 
                # ^ Isko abhi ke liye comment kar do taaki confusion na ho

# 2. Intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True 

    
client = MyClient(intents=intents)
if __name__ == "__main__":
    keep_alive() # Isse port bind ho jayega aur Render khush!
# Ab client.run(TOKEN) use karo
    client.run(TOKEN)

