from aiogram import Router, F
from aiogram.types import CallbackQuery

import asyncio
import logging

from database.db import get_language, get_latest_offers, get_translation, save_translation
from database.github_storage import fetch_offers_data, save_offers_data
from keyboards.language_kb import language_keyboard
from keyboards.menu_kb import menu_keyboard
from utils.i18n import t
from utils.translator import translate, SOURCE_LANG

router = Router()
logger = logging.getLogger(__name__)

BATCH_SIZE = 10
MAX_MSG = 4090

def _parse_offers(text: str) -> tuple[str | None, list[str]]:
    """Split blob into (label_or_None, [offer_items]).
    Splits only on boundaries between numbered offers (N. ...) so that
    internal blank lines within one offer are preserved as single newlines.
    """
    import re
    first_split = text.split("\n\n", 1)
    if len(first_split) == 2 and not re.match(r"^\d+\.\s", first_split[0].strip()):
        label = first_split[0].strip()
        body = first_split[1]
    else:
        label = None
        body = text
    if label is None and "\n" in body:
        first_line_split = body.split("\n", 1)
        if len(first_line_split) == 2 and not re.match(r"^\d+\.\s", first_line_split[0].strip()):
            if len(first_line_split[0].strip()) < 30:
                label = first_line_split[0].strip()
                body = first_line_split[1].lstrip("\n")
    items = re.split(r"\n+(?=\d+\.\s)", body)
    items = [item.replace("\n\n", "\n").strip() for item in items if item.strip()]
    return label, items

def _split_long(text: str, max_len: int) -> list[str]:
    """Split a single oversized item on line boundaries so that no piece
    can exceed Telegram's per-message character limit."""
    pieces: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > max_len:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:max_len])
            line = line[max_len:]
        if current and len(current) + 1 + len(line) > max_len:
            pieces.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        pieces.append(current)
    return pieces

def _chunk_items(items: list[str], max_len: int = MAX_MSG, max_count: int = BATCH_SIZE) -> list[str]:
    """Group items into messages of up to max_count items each, but never
    let a message exceed Telegram's character limit even if that means
    fewer than max_count items in a given message."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for item in items:
        if len(item) > max_len:
            # A single offer longer than one message: flush what we have
            # and split the offer itself rather than sending an
            # over-limit message that Telegram would reject outright.
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0
            pieces = _split_long(item, max_len)
            chunks.extend(pieces[:-1])
            current = [pieces[-1]]
            current_len = len(pieces[-1]) + 2
            continue
        item_len = len(item) + 2  # account for the joiner
        would_overflow_len = current and current_len + item_len > max_len
        would_overflow_count = len(current) >= max_count
        if current and (would_overflow_len or would_overflow_count):
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(item)
        current_len += item_len
    if current:
        chunks.append("\n\n".join(current))
    return chunks

async def _persist_lang_to_github(lang: str, translated: str) -> None:
    """Add a lazily-computed translation to the GitHub JSON backup."""
    try:
        data = await fetch_offers_data()
        if data:
            data["translations"][lang] = translated
            await save_offers_data(data["raw"], data["translations"])
    except Exception as e:
        logging.getLogger(__name__).warning("Failed to persist %s translation to GitHub: %s", lang, e)

# Leave headroom in each chunk so a merged header/footer never pushes a
# message over Telegram's 4096-char hard limit.
HEADER_FOOTER_MARGIN = 250

async def send_latest_offers(callback: CallbackQuery, lang: str) -> None:
    """Send the latest batch of offers and attach the menu keyboard to the
    very last message instead of sending it as a separate follow-up message.

    Telegram always scrolls a chat to reveal each newly-arrived message, so
    previously — header, then item batches, then footer, then a whole extra
    "choose an action" + buttons message — the client would jump straight
    past the header down to that trailing buttons message. Folding the
    header into the first chunk and the footer+buttons into the last chunk
    means far fewer separate messages land after the header, so the view
    stays much closer to the top of the list instead of falling to the
    buttons at the bottom.
    """
    offers = await get_latest_offers()
    if not offers:
        await callback.answer(t(lang, "no_offers"), show_alert=True)
        return

    footer = t(lang, "offers_footer")

    for batch_idx, batch in enumerate(offers):
        translated = await get_translation(batch["id"], lang)

        if translated is None:
            result = await translate(batch["text"], lang)
            if result is not None:
                translated = result
                await save_translation(batch["id"], lang, result)
                asyncio.create_task(_persist_lang_to_github(lang, result))
            else:
                translated = batch["text"]

        label, items = _parse_offers(translated)

        if label:
            header = f"{t(lang, 'offers_header')}\n{label}"
        else:
            header = t(lang, "offers_header")

        if not items:
            logger.warning("No parsed items for offer batch #%s (lang=%s)", batch["id"], lang)

        # Batch by item count (BATCH_SIZE) capped by char length, so we
        # never exceed Telegram's message limit even on long offer text.
        # The margin here leaves room to fold the header/footer text in.
        chunks = _chunk_items(items, MAX_MSG - HEADER_FOOTER_MARGIN, BATCH_SIZE)
        parts = list(chunks) if chunks else [""]

        # Fold the header into the first part instead of sending it alone.
        parts[0] = f"{header}\n\n{parts[0]}" if parts[0] else header

        is_last_batch = batch_idx == len(offers) - 1
        # Fold the footer into the last part. Only the very last part of
        # the very last batch carries the menu keyboard.
        parts[-1] = f"{parts[-1]}\n\n{footer}" if parts[-1] else footer

        keyboard_delivered = False
        for part_idx, part in enumerate(parts):
            is_final_part = is_last_batch and part_idx == len(parts) - 1
            markup = menu_keyboard(lang) if is_final_part else None
            try:
                await callback.message.answer(part, reply_markup=markup)
                if is_final_part:
                    keyboard_delivered = True
            except Exception:
                logger.exception(
                    "Failed to send offers part %s for batch #%s (lang=%s)",
                    part_idx, batch["id"], lang,
                )

        if is_last_batch and not keyboard_delivered:
            # The message carrying the menu keyboard never made it, so the
            # user would be left with no buttons at all. Send them alone.
            try:
                await callback.message.answer(
                    t(lang, "choose_action"), reply_markup=menu_keyboard(lang)
                )
            except Exception:
                logger.exception("Failed to send fallback menu keyboard (lang=%s)", lang)

@router.callback_query(F.data == "menu:offers")
async def cb_offers(callback: CallbackQuery) -> None:
    lang = await get_language(callback.from_user.id) or "en"
    await send_latest_offers(callback, lang)
    await callback.answer()

@router.callback_query(F.data == "menu:change_lang")
async def cb_change_lang(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "🌐 Choose your language:",
        reply_markup=language_keyboard(),
    )
    await callback.answer()

@router.callback_query(F.data == "menu:stop")
async def cb_stop(callback: CallbackQuery) -> None:
    lang = await get_language(callback.from_user.id) or "en"
    await callback.message.edit_text(t(lang, "stopped"))
    await callback.answer()
