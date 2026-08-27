from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database.db import get_language
from utils.i18n import t

router = Router()


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    lang = await get_language(message.from_user.id) or "en"
    await message.answer(t(lang, "help_text"), parse_mode="HTML")
