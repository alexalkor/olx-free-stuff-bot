import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from config.settings import ADMIN_USER_ID
from database.db import replace_current_offers

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    """Return the sender's Telegram user_id — useful for setting ADMIN_USER_ID."""
    await message.answer(f"Your Telegram user ID: <code>{message.from_user.id}</code>")


@router.message(F.text, F.from_user.id == ADMIN_USER_ID)
async def handle_admin_offers(message: Message) -> None:
    """Any text message from the admin replaces the current batch of offers —
    a manual fallback for testing without waiting on the scheduled scraper."""
    if not message.text.strip():
        return

    offer_id = await replace_current_offers(message.text)
    logger.info(f"Admin saved offer batch #{offer_id}")
    await message.reply(
        f"✅ Saved as offer batch <b>#{offer_id}</b>\n"
        f"Users will now see this in their chosen language."
    )
