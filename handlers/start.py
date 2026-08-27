from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from database.db import get_language
from keyboards.language_kb import language_keyboard
from keyboards.menu_kb import menu_keyboard
from utils.i18n import t

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    lang = await get_language(message.from_user.id)
    if lang is None:
        # New user — ask to pick a language
        await message.answer(
            "👋 Hi! Welcome to the OLX Free Stuff Bot! Please choose your language:",
            reply_markup=language_keyboard(),
        )
    else:
        # Returning user — show the menu directly
        await message.answer(t(lang, "choose_action"), reply_markup=menu_keyboard(lang))
