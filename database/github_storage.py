"""GitHub-backed persistence for offers + translations (survives redeploys)."""
import base64
import json
import logging
import os

import aiohttp

logger = logging.getLogger(__name__)

REPO      = os.getenv("GITHUB_REPO", "alexalkor/olx-free-stuff-bot")
JSON_PATH = "data/offers.json"   # raw text + all translations
API_BASE  = "https://api.github.com"


def _headers() -> dict:
    pat = os.getenv("GITHUB_PAT", "")
    return {"Authorization": f"token {pat}", "Accept": "application/vnd.github+json"}


async def _get_sha(session: aiohttp.ClientSession, path: str) -> str | None:
    url = f"{API_BASE}/repos/{REPO}/contents/{path}"
    async with session.get(url, headers=_headers()) as r:
        if r.status == 200:
            data = await r.json()
            return data.get("sha")
    return None


async def fetch_offers_data() -> dict | None:
    """Load {raw, translations} dict from GitHub. Returns None on error/empty."""
    url = f"https://raw.githubusercontent.com/{REPO}/main/{JSON_PATH}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
                if not data.get("raw"):
                    return None
                return data
    except Exception as e:
        logger.warning("fetch_offers_data failed: %s", e)
        return None


async def save_offers_data(raw: str, translations: dict) -> tuple[int, str]:
    """Save {raw, translations} to GitHub. Returns (http_status, message)."""
    payload = {"raw": raw, "translations": translations}
    encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode()
    try:
        async with aiohttp.ClientSession() as session:
            sha = await _get_sha(session, JSON_PATH)
            body: dict = {
                "message": "chore: update offers [skip railway]",
                "content": encoded,
            }
            if sha:
                body["sha"] = sha
            url = f"{API_BASE}/repos/{REPO}/contents/{JSON_PATH}"
            async with session.put(url, headers=_headers(), json=body,
                                   timeout=aiohttp.ClientTimeout(total=15)) as r:
                status = r.status
                msg = "ok" if status in (200, 201) else await r.text()
                return status, msg
    except Exception as e:
        logger.warning("save_offers_data failed: %s", e)
        return 0, str(e)
