import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    raise ValueError("MONGO_URL is missing. Please add it to your .env file.")

_cluster = AsyncIOMotorClient(
    MONGO_URL,
    appname="HappyBot",
    serverSelectionTimeoutMS=10000,
    connectTimeoutMS=10000,
    retryWrites=True,
    maxPoolSize=50,
)
db = _cluster["HappyBotDB"]

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
badge_optout_col    = db["badge_optout"]
counting_col        = db["counting"]
wordguess_col       = db["word_guess"]
economy_col         = db["economy"]
trades_col          = db["trades"]
activity_col        = db["server_activity"]

global_status_col   = db["global_status"]
giveaways_col       = db["giveaways"]
invites_col         = db["invite_tracker"]
invite_log_col      = db["invite_log_config"]
msg_count_col       = db["message_counts"]
ep_config_col       = db["extraperm_config"]
cmd_aliases_col     = db["command_aliases"]
antinuke_col        = db["antinuke_config"]
antiraid_col        = db["antiraid_config"]
automod_col         = db["automod_config"]
fake_perms_col      = db["fake_permissions"]
blacklist_col       = db["blacklist"]
calls_col           = db["Calls"]
autoresponder_col   = db["Autoresponders"]


async def init_indexes():
    plan = [
        (premium_col, [("type", 1), ("id", 1)], {"unique": True}),
        (blacklist_col, [("id", 1)], {"unique": True}),
        (aesthetic_col, [("guild_id", 1)], {"unique": True}),
        (color_roles_col, [("guild_id", 1)], {"unique": True}),
        (milestone_col, [("guild_id", 1)], {"unique": True}),
        (voicemaster_col, [("guild_id", 1)], {"unique": True}),
        (bump_col, [("guild_id", 1)], {"unique": True}),
        (server_status_col, [("guild_id", 1)], {"unique": True}),
        (badge_optout_col, [("user_id", 1)], {"unique": True}),
        (calls_col, [("server_id", 1)], {"unique": True}),
        (
            calls_col,
            [("queued_at", 1)],
            {"expireAfterSeconds": 1800, "partialFilterExpression": {"waiting": True}},
        ),
    ]
    for col, keys, options in plan:
        try:
            await col.create_index(keys, **options)
        except Exception as error:
            print(f"  [INDEX] {col.name}: skipped ({error})")