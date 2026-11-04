"""Plot templates: classic story structures that scaffold a book's acts and scenes.

Each template lives in the locale files (fabula/locales/<code>.json) under the
"templates" key, so stage names and starter beats arrive in the book's language.
A template is data, not code:

    "universal": {
      "name": "...", "desc": "...",
      "acts": [{"title": "...", "scenes": [["Stage name", "starter show beat"], ...]}, ...]
    }

`apply_template` turns that structure into the compiler's book-graph shape, so
the result serializes straight back to .fab through the existing serializer.
"""

from __future__ import annotations

from . import i18n

# order defines how templates are listed in the UI and CLI
TEMPLATE_IDS = ["universal", "heros_journey", "mystery", "romance", "short_story"]


def _bundle(lang: str | None) -> dict:
    return i18n.bundle(lang).get("templates") or {}


def list_templates(lang: str | None = None) -> list[dict]:
    """[{id, name, desc, acts}] in a stable order, localized with en fallback."""
    data = _bundle(lang)
    out = []
    for tid in TEMPLATE_IDS:
        tpl = data.get(tid)
        if isinstance(tpl, dict) and tpl.get("acts"):
            out.append({"id": tid, **tpl})
    return out


def get_template(tid: str, lang: str | None = None) -> dict | None:
    tpl = _bundle(lang).get(tid)
    return {"id": tid, **tpl} if isinstance(tpl, dict) and tpl.get("acts") else None


def _scene(name: str, beat: str, pov: str | None) -> dict:
    return {
        "name": name, "pov": pov, "cast": [], "kill": [], "threads": [],
        "location": "", "time": "", "tension": [0.3, 0.6, 0.8, 0.5],
        "length": {"range": [800, 1200]}, "variants": 1,
        "style_hints": [], "show": [beat] if beat else [], "forbid": [],
    }


def apply_template(tpl: dict, book: dict, lang: str | None = None) -> dict:
    """Fill book['acts'] from a template; POV defaults to the first character."""
    chars = book.get("characters") or []
    pov = chars[0]["name"] if chars else None
    acts = []
    for ai, act in enumerate(tpl.get("acts") or [], 1):
        title = act.get("title")
        scenes = [_scene(name, beat, pov) for name, beat in (act.get("scenes") or [])]
        acts.append({
            "number": ai, "title": title,
            "chapters": [{"number": 1,
                          "title": title or i18n.t("templates._chapter", lang, n=ai),
                          "scenes": scenes}],
        })
    book["acts"] = acts
    if tpl.get("id") == "short_story":
        book.setdefault("settings", {})["form"] = "short_story"
    return book
