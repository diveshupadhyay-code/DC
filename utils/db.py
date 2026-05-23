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
settings_col       = db["server_settings"]
warns_col          = db["warnings"]
afk_col            = db["afk_users"]
sticky_col         = db["sticky_messages"]
reaction_roles_col = db["reaction_roles"]
tickets_col        = db["tickets"]
profiles_col       = db["user_profiles"]
embed_col          = db["embed_drafts"]
premium_col        = db["premium"]
levels_col         = db["levels"]
birthdays_col      = db["birthdays"]
server_status_col  = db["server_status"]
jail_col           = db["jail"]
counters_col       = db["counters"]
logs_col           = db["logging_config"]
voicemaster_col    = db["voicemaster"]
bump_col           = db["bump_reminder"]
personal_prefix_col = db["personal_prefix"]
booster_roles_col  = db["booster_roles"]
button_roles_col   = db["button_roles"]
disabled_cmds_col  = db["disabled_commands"]
notes_col          = db["mod_notes"]
cases_col          = db["mod_cases"]
antispam_col       = db["antispam_config"]
aesthetic_col      = db["aesthetic_config"]
color_roles_col    = db["color_roles"]
milestone_col      = db["milestones"]
counting_col       = db["counting"]
wordguess_col      = db["word_guess"]