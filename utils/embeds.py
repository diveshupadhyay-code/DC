import json
import re

import discord

NEUTRAL = 0x2B2D31
SUCCESS = 0x3BA55D
FAILURE = 0xED4245
ACCENT = 0x5865F2

NO_PINGS = discord.AllowedMentions.none()

NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
HEX_PATTERN = re.compile(r"^[0-9A-Fa-f]{6}$")
LINK_PATTERN = re.compile(r"^https://[^\s]{4,490}$")
LINK_PLACEHOLDERS = ("{user_avatar}", "{server_icon}")

TEXT_LIMITS = {"title": 256, "description": 4000, "footer": 2048}
EMBED_TOTAL_LIMIT = 6000

VARIABLES = (
    ("{user}", "Mentions the member"),
    ("{username}", "The member's name"),
    ("{usertag}", "The member's full username"),
    ("{user_id}", "The member's ID"),
    ("{user_avatar}", "The member's picture link"),
    ("{server}", "The server name"),
    ("{server_id}", "The server ID"),
    ("{server_icon}", "The server picture link"),
    ("{count}", "Total members in the server"),
    ("{members}", "Total members in the server"),
)

VARIABLE_PATTERN = re.compile("|".join(re.escape(key) for key, _ in VARIABLES))


def variables_text():
    return "\n".join(f"`{key}`  {meaning}" for key, meaning in VARIABLES)


def trim(text, limit):
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def code(text, limit=200):
    return "`" + trim(str(text).replace("`", "'"), limit) + "`"


def clean_name(text):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(text)))


def render(text, member, guild):
    if not text:
        return ""
    values = {
        "{user}": member.mention,
        "{username}": clean_name(member.display_name),
        "{usertag}": clean_name(str(member)),
        "{user_id}": str(member.id),
        "{user_avatar}": member.display_avatar.url,
        "{server}": clean_name(guild.name),
        "{server_id}": str(guild.id),
        "{server_icon}": guild.icon.url if guild.icon else "",
        "{count}": str(guild.member_count),
        "{members}": str(guild.member_count),
    }
    return VARIABLE_PATTERN.sub(lambda found: values[found.group(0)], text)


def is_link(value):
    if not isinstance(value, str):
        return False
    value = value.strip()
    return value in LINK_PLACEHOLDERS or bool(LINK_PATTERN.match(value))


def parse_color(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if 0 <= value <= 0xFFFFFF:
            return f"{value:06X}"
        return None
    if isinstance(value, str):
        value = value.strip().lstrip("#")
        if HEX_PATTERN.match(value):
            return value.upper()
    return None


def clean_draft(raw):
    if not isinstance(raw, dict):
        return None, "That is not a valid embed."

    data = {}
    for field, limit in TEXT_LIMITS.items():
        value = raw.get(field)
        if value is None or value == "":
            data[field] = None
            continue
        if not isinstance(value, str):
            return None, f"The {field} must be text."
        value = value.strip()
        if len(value) > limit:
            return None, f"The {field} is too long. The limit is {limit} characters."
        data[field] = value or None

    color_value = raw.get("color")
    if color_value is None or color_value == "":
        data["color"] = "2B2D31"
    else:
        color = parse_color(color_value)
        if not color:
            return None, "The color must look like `#5865F2`."
        data["color"] = color

    for field in ("thumbnail", "image"):
        value = raw.get(field)
        if value is None or value == "":
            data[field] = None
            continue
        if not is_link(value):
            return None, f"The {field} must be a link that starts with `https://`."
        data[field] = value.strip()

    if sum(len(data[field] or "") for field in TEXT_LIMITS) > EMBED_TOTAL_LIMIT:
        return None, "The embed has too much text. Please make it shorter."

    return data, None


def has_content(draft):
    return any(draft.get(field) for field in ("title", "description", "thumbnail", "image", "footer"))


def build_embed(draft, member, guild, placeholder=False):
    try:
        color = min(int(str(draft.get("color") or "2B2D31"), 16), 0xFFFFFF)
    except ValueError:
        color = NEUTRAL

    title = trim(render(draft.get("title"), member, guild), 256) or None
    description = trim(render(draft.get("description"), member, guild), 4000) or None
    embed = discord.Embed(title=title, description=description, color=color)

    for setter, field in ((embed.set_thumbnail, "thumbnail"), (embed.set_image, "image")):
        link = render(draft.get(field), member, guild).strip()
        if is_link(link):
            setter(url=link)

    footer = trim(render(draft.get("footer"), member, guild), 2048)
    if footer:
        embed.set_footer(text=footer)

    overflow = len(embed) - EMBED_TOTAL_LIMIT
    if overflow > 0 and embed.description:
        embed.description = embed.description[: max(0, len(embed.description) - overflow)] or None

    if placeholder and not any(key in embed.to_dict() for key in ("title", "description", "image", "thumbnail", "footer")):
        embed.description = "Nothing here yet. Press **Edit text** to add your message."

    return embed


def parse_import(text):
    text = text.strip()
    if len(text) > 8000:
        return None, "That JSON is too long."
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text[:4].lower() == "json":
            text = text[4:]
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return None, "That is not valid JSON."

    if isinstance(data, dict) and isinstance(data.get("embeds"), list) and data["embeds"]:
        data = data["embeds"][0]
    if not isinstance(data, dict):
        return None, "That is not a valid embed."

    flat = dict(data)
    for field in ("thumbnail", "image"):
        if isinstance(flat.get(field), dict):
            flat[field] = flat[field].get("url")
    if isinstance(flat.get("footer"), dict):
        flat["footer"] = flat["footer"].get("text")

    return clean_draft(flat)


def card(title=None, description=None, color=NEUTRAL, footer=None):
    embed = discord.Embed(title=title, description=description, color=color)
    if footer:
        embed.set_footer(text=footer)
    return embed


def success(description, title="Done"):
    return card(f"✓  {title}", description, SUCCESS)


def failure(description, title="Could not do that"):
    return card(f"✕  {title}", description, FAILURE)


def notice(description, title=None):
    return card(title, description, ACCENT)


def usage(syntax, detail=None):
    text = f"Use it like this:\n`{syntax}`"
    if detail:
        text += f"\n\n{detail}"
    return card("Missing information", text, FAILURE)


async def respond(ctx, private=True, **kwargs):
    if ctx.interaction is None:
        kwargs.setdefault("mention_author", False)
        return await ctx.reply(**kwargs)
    return await ctx.send(ephemeral=private, **kwargs)
