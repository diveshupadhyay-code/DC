"""
utils/db.py — All MongoDB collections in one place.
Import from here everywhere else so the client is created once.
"""

import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    raise ValueError("MONGO_URL not found in environment variables.")

_cluster = AsyncIOMotorClient(MONGO_URL)
db = _cluster["HappyBotDB"]

# ── Collections ───────────────────────────────────────────────────────────────
settings_col        = db["server_settings"]
warns_col           = db["warnings"]
afk_col             = db["afk_users"]
sticky_col          = db["sticky_messages"]
reaction_roles_col  = db["reaction_roles"]
tickets_col         = db["tickets"]
profiles_col        = db["user_profiles"]
embed_col           = db["embed_drafts"]
premium_col         = db["premium"]
levels_col          = db["levels"]
birthdays_col       = db["birthdays"]
server_status_col   = db["server_status"]
jail_col            = db["jail"]
counters_col        = db["counters"]
logs_col            = db["logging_config"]
voicemaster_col     = db["voicemaster"]
bump_col            = db["bump_reminder"]
personal_prefix_col = db["personal_prefix"]
booster_roles_col   = db["booster_roles"]
button_roles_col    = db["button_roles"]
disabled_cmds_col   = db["disabled_commands"]
notes_col           = db["mod_notes"]
cases_col           = db["mod_cases"]
antispam_col        = db["antispam_config"]
aesthetic_col       = db["aesthetic_config"]
color_roles_col     = db["color_roles"]
milestone_col       = db["milestones"]
counting_col        = db["counting"]
wordguess_col       = db["word_guess"]
economy_col         = db["economy"]
trades_col          = db["trades"]
activity_col        = db["server_activity"]
market_col          = db["market_stocks"]
portfolio_col       = db["portfolios"]
global_status_col   = db["global_status"]   # owner global status overrides
giveaways_col       = db["giveaways"]        # persistent giveaway store, restart-safe

# ── Tracker collections (invite tracker + message counter) ────────────────────
invites_col         = db["invite_tracker"]      # {guild_id, inviter_id, code, uses, ...}
invite_log_col      = db["invite_log_config"]   # {guild_id, channel_id}
msg_count_col       = db["message_counts"]      # {guild_id, user_id, count}