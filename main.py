import asyncio
import logging
import os

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config.settings import BOT_TOKEN, WEBHOOK_SECRET, PORT
from database.db import init_db, replace_current_offers, append_to_offers, get_latest_offers, clear_all_translations, save_translation
from database.github_storage import fetch_offers_data, save_offers_data
from handlers import start, help, language, menu, admin

logger = logging.getLogger(__name__)

VERSION = "v1-olx-free-stuff"


def _assign_emojis(text: str) -> str:
    """Replace the generic 🆓 placeholder with a category-appropriate emoji
    per offer, based on Polish keywords in the offer's title line. The
    scraper is expected to prefix each numbered offer's title with 🆓 as a
    placeholder — this only swaps that placeholder, leaving any
    scraper-assigned emoji untouched."""
    import re
    # Ordered: first match wins. Keywords are lowercase substrings of the
    # offer title line (in Polish, since that's OLX's listing language).
    RULES = [
        (["mebl", "kanapa", "sofa", "szafa", "krzesł", "stół", "stol", "łóżko", "regał", "komod"], "🛋"),
        (["lodówk", "pralk", "zmywark", "kuchenk", "piekarnik", "agd", "mikrofal"], "🔌"),
        (["telewizor", " tv", "monitor", "laptop", "komputer", "telefon", "elektronik", "konsol"], "💻"),
        (["ubrania", "ubranka", "buty", "kurtka", "sukienka", "odzież", "spodni"], "👕"),
        (["zabawk", "lalka", "klock"], "🧸"),
        (["książk", "ksiazk", "podręcznik", "podrecznik"], "📚"),
        (["rower", "hulajnog"], "🚲"),
        (["roślin", "roslin", "kwiat", "doniczk"], "🪴"),
        (["akwarium", "klatka dla"], "🐾"),
        (["materiał", "material", "budowl", "narzędz", "narzedz", "deski", "cegł", "płytk"], "🧰"),
        (["wózek", "wozek", "fotelik", "dziecięc", "dziecinn", "łóżeczk"], "🍼"),
        (["rower", "sprzęt sportowy", "narty", "deskorolk"], "🏓"),
        (["naczyni", "garnk", "talerz", "kuchenn"], "🍽"),
    ]

    def _pick_emoji(title_lower: str) -> str:
        for keywords, emoji in RULES:
            if any(kw in title_lower for kw in keywords):
                return emoji
        return "🆓"

    def _fix(m: "re.Match") -> str:
        num = m.group(1)
        title = m.group(2)
        return f"{num}. {_pick_emoji(title.lower())} {title}"

    return re.sub(r"^(\d+)\.\s+🆓\s+(.+)$", _fix, text, flags=re.MULTILINE)


async def handle_post_offers(request: web.Request) -> web.Response:
    """Webhook the OLX Free Stuff Scrapper's scheduled task calls once it has
    scraped + filtered listings. Body: {"text": "...", "mode": "replace"|"append"}.
    Auth: header X-Secret must match WEBHOOK_SECRET."""
    secret = request.headers.get("X-Secret", "")
    if not WEBHOOK_SECRET or secret != WEBHOOK_SECRET:
        return web.Response(status=401, text="Unauthorized")
    try:
        data = await request.json()
        text = data.get("text", "").strip()
        if not text:
            return web.json_response({"ok": False, "error": "empty text"}, status=400)
        mode = data.get("mode", "replace")
        if mode == "append":
            offer_id = await append_to_offers(text)
            latest = await get_latest_offers()
            full_text = latest[0]["text"] if latest else text
        else:
            offer_id = await replace_current_offers(text)
            full_text = text

        # Apply category-appropriate emojis (replaces generic 🆓 placeholder)
        full_text = _assign_emojis(full_text)

        # Preserve translations when the same offers are re-posted; clear
        # only when the content actually changed.
        _existing = await fetch_offers_data()
        if _existing and _existing.get("raw", "")[:100] == full_text[:100]:
            _keep_trans = _existing.get("translations", {})
        else:
            _keep_trans = {}
        gh_status, gh_msg = await save_offers_data(full_text, _keep_trans)
        logger.info("Offer batch #%d raw saved; GitHub: %d %s (kept %d translations)",
                    offer_id, gh_status, gh_msg[:80], len(_keep_trans))

        # Translate all languages in the background (takes 30-60s, don't block response)
        import asyncio as _asyncio
        _asyncio.create_task(_bg_translate(offer_id, full_text))

        return web.json_response({
            "ok": True, "offer_id": offer_id, "mode": mode,
            "github_status": gh_status,
            "note": "translation started in background",
        })
    except Exception as e:
        logger.exception("Error in /offers endpoint")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def handle_test_translate(request: web.Request) -> web.Response:
    """Translate the actual DB offers to verify MyMemory works on real content."""
    from utils.translator import translate
    offers = await get_latest_offers()
    if not offers:
        return web.json_response({"error": "no offers in db"})
    text = offers[0]["text"]
    results = {}
    for lang in ["en", "ru"]:
        res = await translate(text, lang)
        results[lang] = {
            "ok": res is not None and res != text,
            "preview": (res or "")[:200],
        }
    return web.json_response({
        "text_len": len(text),
        "text_preview": text[:300],
        "results": results,
    })


async def _bg_translate(offer_id: int, text: str) -> None:
    """Background task: translate to all langs, cache in DB + GitHub."""
    import asyncio as _asyncio
    from utils.translator import translate
    langs = ["en", "ru", "be", "uk", "de"]  # pl is source, no translation needed
    translations: dict = {}

    # Fast path: seed DB from any existing GitHub translations first
    try:
        existing = await fetch_offers_data()
        if existing:
            for lang, txt in existing.get("translations", {}).items():
                if txt and lang in langs:
                    await save_translation(offer_id, lang, txt)
                    translations[lang] = txt
                    logger.info("BG seeded %s from GitHub cache (%d chars)", lang, len(txt))
    except Exception as e:
        logger.warning("BG seed from GitHub failed: %s", e)

    # Slow path: translate any langs still missing
    newly_translated = False
    for lang in langs:
        if lang in translations:
            continue  # already seeded from GitHub
        try:
            result = await translate(text, lang)
            if result and result != text:
                await save_translation(offer_id, lang, result)
                # Only update GitHub if new translation is better than existing
                existing_len = len(translations.get(lang, ""))
                if len(result) > existing_len:
                    translations[lang] = result
                    newly_translated = True
                    logger.info("BG translated %s (%d chars)", lang, len(result))
                else:
                    logger.info("BG skipped overwrite of %s (existing better: %d > %d)", lang, existing_len, len(result))
            else:
                logger.warning("BG translation failed for %s", lang)
        except Exception as e:
            logger.warning("BG translation error for %s: %s", lang, e)
        await _asyncio.sleep(2)
    if newly_translated:
        await save_offers_data(text, translations)
    logger.info("BG: done — %d/%d translations in DB", len(translations), len(langs))


async def handle_clear_cache(request: web.Request) -> web.Response:
    """Wipe all cached translations so they get re-fetched on next request."""
    secret = request.headers.get("X-Secret", "")
    if not WEBHOOK_SECRET or secret != WEBHOOK_SECRET:
        return web.Response(status=401, text="Unauthorized")
    count = await clear_all_translations()
    logger.info("Cleared %d cached translations via HTTP", count)
    return web.json_response({"ok": True, "cleared": count})


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text=f"ok ({VERSION})")


async def handle_debug(request: web.Request) -> web.Response:
    pat = os.getenv("GITHUB_PAT", "")
    offers = await get_latest_offers()
    from database.db import get_translation
    trans_count = 0
    trans_langs = []
    if offers:
        for lang in ["en", "ru", "be", "uk", "de"]:
            tr = await get_translation(offers[0]["id"], lang)
            if tr:
                trans_count += 1
                trans_langs.append(lang)
    return web.json_response({
        "version": VERSION,
        "GITHUB_PAT_set": bool(pat),
        "GITHUB_PAT_prefix": pat[:8] + "..." if pat else "(empty)",
        "offers_in_db": len(offers),
        "offers_text_len": len(offers[0]["text"]) if offers else 0,
        "cached_translations": trans_count,
        "cached_langs": trans_langs,
    })


async def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    await init_db()

    existing = await get_latest_offers()
    if not existing:
        logger.info("DB empty — seeding from GitHub...")
        data = await fetch_offers_data()
        if data:
            oid = await replace_current_offers(data["raw"])
            for lang, txt in data.get("translations", {}).items():
                await save_translation(oid, lang, txt)
            logger.info("Seeded from GitHub — offer batch #%d with %d translations",
                        oid, len(data.get("translations", {})))

    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_get("/debug",  handle_debug)
    app.router.add_post("/offers", handle_post_offers)
    app.router.add_post("/admin/clear-cache", handle_clear_cache)
    app.router.add_get("/test-translate", handle_test_translate)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("HTTP server on port %d (%s)", PORT, VERSION)

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(start.router)
    dp.include_router(language.router)
    dp.include_router(menu.router)
    dp.include_router(help.router)
    dp.include_router(admin.router)

    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
