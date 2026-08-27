import aiosqlite
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "bot.db")


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id  INTEGER PRIMARY KEY,
                language TEXT NOT NULL DEFAULT 'en'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS offers (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                text       TEXT    NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS offer_translations (
                offer_id INTEGER NOT NULL,
                language TEXT    NOT NULL,
                text     TEXT    NOT NULL,
                PRIMARY KEY (offer_id, language)
            )
        """)
        await db.commit()


# ── Users ───────────────────────────────────────────────────────────────────

async def get_language(user_id: int) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT language FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def set_language(user_id: int, language: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, language) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET language = excluded.language
        """, (user_id, language))
        await db.commit()


# ── Offers ──────────────────────────────────────────────────────────────────
# Unlike the baseline "weekly events" bot, OLX listings are a continuously
# refreshed feed rather than something grouped by ISO week — so there is
# always just one "current" batch of offers, which the scheduled scraper
# either replaces wholesale or appends new finds onto.

async def replace_current_offers(text: str) -> int:
    """Wipe the current batch of offers + cached translations, insert fresh
    text. Returns the new offer batch id."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM offers") as cur:
            old_ids = [r[0] for r in await cur.fetchall()]
        for oid in old_ids:
            await db.execute("DELETE FROM offer_translations WHERE offer_id=?", (oid,))
        await db.execute("DELETE FROM offers")
        cursor = await db.execute(
            "INSERT INTO offers (text) VALUES (?)", (text,)
        )
        await db.commit()
        return cursor.lastrowid


async def append_to_offers(new_text: str) -> int:
    """Append new_text onto the current batch (merges into one row)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, text FROM offers ORDER BY id LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
        if row:
            existing_id, existing_text = row
            # Invalidate cached translations so they get re-translated
            await db.execute("DELETE FROM offer_translations WHERE offer_id=?", (existing_id,))
            combined = existing_text.rstrip() + "\n\n" + new_text.strip()
            await db.execute("UPDATE offers SET text=? WHERE id=?", (combined, existing_id))
            await db.commit()
            return existing_id
        else:
            cursor = await db.execute(
                "INSERT INTO offers (text) VALUES (?)", (new_text,)
            )
            await db.commit()
            return cursor.lastrowid


async def save_offer(text: str) -> int:
    """Insert a brand-new offers row without touching any existing one
    (used by the admin fallback handler)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO offers (text) VALUES (?)", (text,)
        )
        await db.commit()
        return cursor.lastrowid


async def get_latest_offers() -> list[dict]:
    """Return the current batch of offers (there's normally just one row)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, text FROM offers ORDER BY id"
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"id": r[0], "text": r[1]} for r in rows]


# ── Translations ─────────────────────────────────────────────────────────────

async def clear_all_translations() -> int:
    """Delete all cached translations. Returns number of rows deleted."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM offer_translations") as cur:
            count = (await cur.fetchone())[0]
        await db.execute("DELETE FROM offer_translations")
        await db.commit()
        return count


async def get_translation(offer_id: int, language: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT text FROM offer_translations WHERE offer_id=? AND language=?",
            (offer_id, language),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def save_translation(offer_id: int, language: str, text: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO offer_translations (offer_id, language, text) VALUES (?, ?, ?)
            ON CONFLICT(offer_id, language) DO UPDATE SET text = excluded.text
        """, (offer_id, language, text))
        await db.commit()
