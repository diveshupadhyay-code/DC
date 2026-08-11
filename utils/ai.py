import os
import asyncio
from groq import Groq

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
_MODEL = "llama-3.3-70b-versatile"


def _call(system: str, prompt: str, max_tokens: int) -> str:
    resp = _client.chat.completions.create(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        model=_MODEL,
        max_tokens=max_tokens,
        temperature=0.8,
    )
    return resp.choices[0].message.content.strip()


async def generate_text(system: str, prompt: str, max_tokens: int = 50, fallback: str = "") -> str:
    if not _client:
        return fallback
    try:
        text = await asyncio.wait_for(asyncio.to_thread(_call, system, prompt, max_tokens), timeout=6)
        return text if text else fallback
    except Exception:
        return fallback
