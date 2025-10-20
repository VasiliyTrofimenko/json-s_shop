import os

# Path to SQLite database file
DB_PATH = "backend/candy_store.db"

# Bot token is read from environment for safety. Set BOT_TOKEN before running the bot.
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Public base URL where the WebApp (frontend) is hosted (HTTPS required for Telegram)
WEBAPP_BASE_URL = os.getenv("WEBAPP_BASE_URL", "https://yourdomain.com")

# List of admin Telegram user IDs allowed to access admin endpoints
# You can also set ADMIN_IDS env as comma-separated integers, e.g. "111,222"
_env_admins = os.getenv("ADMIN_IDS")
if _env_admins:
    ADMIN_IDS = [int(x.strip()) for x in _env_admins.split(",") if x.strip().isdigit()]
else:
    ADMIN_IDS = [123456789, 987654321]
