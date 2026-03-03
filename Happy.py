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
            await channel.send(f'Welcome to the server, {member.mention}! 🎉')

    async def on_message(self, message):
        if message.author == self.user:
            return

        if self.user.mentioned_in(message):
            # 1. Cleaning the prompt
            clean_prompt = message.content.replace(f'<@!{self.user.id}>', '').replace(f'<@{self.user.id}>', '')
            
            try:
                # 2. AI Call
                # Updated AI Setup with Happy's Persona
                instruction = """ Purpose and Goals:
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
* make your replies short like chatting messages (such as 1 line reply). 
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
                if "429" in str(e) or "limit" in str(e).lower():
                    await message.reply("Bhai thoda saans lene de! Google waale keh rahe hain aaj ka quota khatam ho gaya. Thodi der baad ya kal try kariyo! 😎")
                else:
            # Agar koi aur error ho toh logs mein print karega
                    print(f"Error hua hai bhai: {e}")
                    await message.reply("Arre yaar, dimaag mein thoda short circuit ho gaya hai. Phir se bolna?")
                # Sirf tabhi ye bolega jab ASLI mein error aaye
               # print(f"Asli Error ye hai bhai: {e}")
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

