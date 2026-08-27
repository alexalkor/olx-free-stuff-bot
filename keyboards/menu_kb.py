from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from utils.i18n import t


def menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t(lang, "btn_offers"),      callback_data="menu:offers")
    builder.button(text=t(lang, "btn_change_lang"), callback_data="menu:change_lang")
    builder.button(text=t(lang, "btn_stop"),        callback_data="menu:stop")
    builder.adjust(1, 2)  # offers alone on row 1, lang+stop on row 2
    return builder.as_markup()
