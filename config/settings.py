from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in .env")

WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")
PORT: int = int(os.getenv("PORT", "8080"))

ADMIN_USER_ID: int = int(os.getenv("ADMIN_USER_ID", "0"))

GITHUB_PAT: str     = os.getenv("GITHUB_PAT", "")
MYMEMORY_EMAIL: str = os.getenv("MYMEMORY_EMAIL", "bot@olxfreestuff.local")
GITHUB_REPO: str    = os.getenv("GITHUB_REPO", "alexalkor/olx-free-stuff-bot")
