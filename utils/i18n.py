import json
import os
from functools import lru_cache

LOCALES_DIR = os.path.join(os.path.dirname(__file__), "..", "locales")


@lru_cache(maxsize=None)
def _load(lang: str) -> dict:
    path = os.path.join(LOCALES_DIR, f"{lang}.json")
    fallback = os.path.join(LOCALES_DIR, "en.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        with open(fallback, encoding="utf-8") as f:
            return json.load(f)


def t(lang: str, key: str) -> str:
    return _load(lang).get(key, _load("en").get(key, key))
