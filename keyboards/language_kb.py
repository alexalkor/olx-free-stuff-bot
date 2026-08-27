from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

LANGUAGES = [
    ("🇬🇧 English",    "en"),
    ("🇵🇱 Polski",     "pl"),
    ("🇧🇾 Беларуская", "be"),
    ("🇺🇦 Українська", "uk"),
    ("🇩🇪 Deutsch",    "de"),
    ("🇷🇺 Русский",    "ru"),
]


def language_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for label, code in LANGUAGES:
        builder.button(text=label, callback_data=f"lang:{code}")
    builder.adjust(2, 2, 2)  # 3 rows of 2
    return builder.as_markup()
