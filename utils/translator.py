import logging
import aiohttp

logger = logging.getLogger(__name__)

SOURCE_LANG = "pl"
CHUNK_SIZE = 1500  # MyMemory truncates large outputs; chunks split on offer boundaries only
MYMEMORY_EMAIL = "bot@olxfreestuff.local"

_MYMEMORY_CODES = {
    "pl": "pl-PL",
    "en": "en-GB",
    "ru": "ru-RU",
    "de": "de-DE",
    "be": "be-BY",
    "uk": "uk-UA",
}

def _chunk(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """Split text into chunks for translation, never splitting in the middle
    of a numbered offer. Each chunk contains one or more whole offers, packed
    up to ~size chars. Falls back to a single oversized chunk if one offer
    alone exceeds size (rare), rather than corrupting it mid-line."""
    import re

    # Peel off a leading preamble (a short first paragraph that doesn't
    # itself start with "N. "), same convention used when parsing.
    first_split = text.split("\n\n", 1)
    if len(first_split) == 2 and not re.match(r"^\d+\.\s", first_split[0].strip()):
        preamble = first_split[0].strip()
        body = first_split[1]
    else:
        preamble = None
        body = text

    items = re.split(r"\n+(?=\d+\.\s)", body)
    items = [item.strip() for item in items if item.strip()]

    if not items:
        return [text] if text else []

    chunks: list[str] = []
    current: list[str] = []
    current_len = len(preamble) + 2 if preamble else 0
    for item in items:
        item_len = len(item) + 2
        if current and current_len + item_len > size:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(item)
        current_len += item_len
    if current:
        chunks.append("\n\n".join(current))

    if preamble and chunks:
        chunks[0] = preamble + "\n\n" + chunks[0]
    elif preamble:
        chunks = [preamble]

    return chunks

async def _translate_chunk(session: aiohttp.ClientSession, chunk: str, langpair: str) -> str:
    """Translate a single chunk via MyMemory API. Returns empty string on failure."""
    async with session.post(
        "https://api.mymemory.translated.net/get",
        data={"q": chunk, "langpair": langpair, "de": MYMEMORY_EMAIL},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        data = await resp.json(content_type=None)
        status = data.get("responseStatus", 0)
        result = data.get("responseData", {}).get("translatedText", "")
        if status == 200 and result:
            return result
        logger.warning("MyMemory chunk failed: status=%s result=%r", status, result[:80] if result else "")
        return ""

async def translate(text: str, target_lang: str) -> str | None:
    """Translate text from Polish to target_lang.
    Returns translated string on success, None on failure (caller must not cache None)."""
    if target_lang == SOURCE_LANG or not text.strip():
        return text

    src = _MYMEMORY_CODES.get(SOURCE_LANG, SOURCE_LANG)
    tgt = _MYMEMORY_CODES.get(target_lang, target_lang)
    langpair = f"{src}|{tgt}"
    chunks = _chunk(text)

    try:
        import asyncio as _asyncio
        async with aiohttp.ClientSession() as session:
            results = []
            for i, chunk in enumerate(chunks):
                if i > 0:
                    await _asyncio.sleep(1)
                translated = await _translate_chunk(session, chunk, langpair)
                if not translated:
                    logger.warning("MyMemory: empty chunk result, aborting %s->%s", SOURCE_LANG, target_lang)
                    return None
                results.append(translated)
            result = "\n\n".join(results)
            logger.info("MyMemory OK: %s->%s (%d->%d chars)", SOURCE_LANG, target_lang, len(text), len(result))
            return result
    except Exception as e:
        logger.warning("MyMemory failed (%s->%s): %s", SOURCE_LANG, target_lang, e)
        return None
