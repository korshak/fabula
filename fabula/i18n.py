"""i18n: user-facing strings live in fabula/locales/<code>.json.

To add a language, contributors drop a new JSON file into fabula/locales/
(copy en.json, translate the values, set _meta.name and _meta.plural) —
no code changes needed. Keys missing from a locale fall back to English,
so partial translations still work.

Language resolution order: explicit argument > set_lang() (per request /
per CLI run) > FABULA_LANG env var > "uk".

Note: prompts sent to the writing model (fabula/prompts.py) are NOT part
of the UI locale — their language follows the book being written, not the
interface.
"""

from __future__ import annotations

import json
import os
import re
from contextvars import ContextVar
from pathlib import Path

LOCALES_DIR = Path(__file__).parent / "locales"
DEFAULT_LANG = "uk"
FALLBACK_LANG = "en"

_current: ContextVar = ContextVar("fabula_lang", default=None)
_cache: dict = {}


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def available() -> list[dict]:
    """[{code, name}] for every locale file present."""
    out = []
    for path in sorted(LOCALES_DIR.glob("*.json")):
        meta = load(path.stem).get("_meta", {})
        out.append({"code": path.stem, "name": meta.get("name", path.stem)})
    return out


# A locale code becomes a filename, and it arrives from `?lang=` on every HTTP
# request, so it must never be able to carry path syntax out of LOCALES_DIR.
_LANG_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")


def load(lang: str) -> dict:
    """Load a locale, re-reading the file when it changes on disk.

    The mtime check keeps a long-running server picking up locale edits
    (and plugin updates) without a restart.
    """
    if not _LANG_RE.fullmatch(str(lang or "")):
        return {}
    path = LOCALES_DIR / f"{lang}.json"
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    cached = _cache.get(lang)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    data = json.loads(path.read_text(encoding="utf-8")) if mtime is not None else {}
    _cache[lang] = (mtime, data)
    return data


def resolve_lang(lang: str | None = None) -> str:
    lang = lang or _current.get() or os.environ.get("FABULA_LANG") or DEFAULT_LANG
    if not _LANG_RE.fullmatch(str(lang or "")):
        return FALLBACK_LANG
    if not (LOCALES_DIR / f"{lang}.json").exists():
        return FALLBACK_LANG
    return lang


def set_lang(lang: str | None):
    """Set the language for the current context (thread/request/CLI run)."""
    _current.set(lang)


def _lookup(data: dict, key: str):
    node = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _find(key: str, lang: str | None):
    for lg in (resolve_lang(lang), FALLBACK_LANG):
        value = _lookup(load(lg), key)
        if value is not None:
            return value, lg
    return None, None


def t(_key: str, _lang: str | None = None, **params) -> str:
    """Translate a dotted key; unknown keys render as the key itself.

    The first two parameters are underscore-prefixed so that locale
    placeholders may freely be named `key` or `lang`.
    """
    value, _ = _find(_key, _lang)
    if not isinstance(value, str):
        return _key
    return value.format_map(_SafeDict(params))


_PLURAL_RULES = {
    "uk": lambda n: 0 if n % 10 == 1 and n % 100 != 11 else
                    (1 if 2 <= n % 10 <= 4 and not 10 <= n % 100 < 20 else 2),
    "en": lambda n: 0 if n == 1 else 1,
}


def tn(_key: str, n: int, _lang: str | None = None, **params) -> str:
    """Pluralized translation: the key must map to a list of forms."""
    forms, lg = _find(_key, _lang)
    if not isinstance(forms, list) or not forms:
        return t(_key, _lang, n=n, **params)
    rule_id = load(lg).get("_meta", {}).get("plural", "en")
    rule = _PLURAL_RULES.get(rule_id, _PLURAL_RULES["en"])
    idx = min(rule(int(n)), len(forms) - 1)
    return str(forms[idx]).format_map(_SafeDict({"n": n, **params}))


def bundle(lang: str | None = None) -> dict:
    """Full locale dict for the web UI, with English filling the gaps."""
    lg = resolve_lang(lang)

    def merge(base, over):
        out = dict(base)
        for k, v in over.items():
            out[k] = merge(base.get(k, {}), v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
        return out

    merged = merge(load(FALLBACK_LANG), load(lg)) if lg != FALLBACK_LANG else dict(load(FALLBACK_LANG))
    merged["_lang"] = lg
    return merged
