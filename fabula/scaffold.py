"""Project scaffolding shared by the CLI (`fabula init`) and the web server
(the in-studio project manager): a starter book graph, written as book.fab
plus models.json and .gitignore into a target directory.
"""

from __future__ import annotations

import json
from pathlib import Path

from .i18n import t


def starter_book(lang: str | None = None, template: str | None = None,
                 title: str | None = None) -> dict:
    """The default new-book graph; optionally unfolded from a plot template."""
    hero = t("seed.character", lang)
    book = {
        "title": title or t("seed.book", lang),
        "settings": {"voice": "third_person_limited", "tense": "past",
                     "language": lang or "uk", "style_profile": ""},
        "world": {"name": t("seed.world", lang),
                  "rules": [{"name": t("seed.rule", lang), "description": "", "severity": "soft"}]},
        "characters": [{"name": hero, "traits": [], "never": [], "must_resolve": []}],
        "locations": [], "threads": [],
        "acts": [{"number": 1, "chapters": [{"number": 1, "title": t("seed.chapter", lang), "scenes": [{
            "name": t("seed.scene", lang), "pov": hero, "location": "", "time": "",
            "tension": [0.3, 0.6, 0.8, 0.5], "length": {"range": [800, 1200]}, "variants": 2,
            "cast": [], "kill": [], "threads": [], "style_hints": [], "show": [], "forbid": [],
        }]}]}],
    }
    if template:
        from .templates import apply_template, get_template, list_templates
        tpl = get_template(template, lang)
        if not tpl:
            ids = ", ".join(tp["id"] for tp in list_templates(lang))
            raise ValueError(t("cli.unknown_template", lang, name=template, list=ids))
        apply_template(tpl, book, lang)
    return book


def write_project(directory: Path, book: dict) -> list[Path]:
    """Write book.fab + models.json + .gitignore, skipping files that exist.

    Returns the paths actually written; an existing book.fab is left alone.
    """
    from .providers import DEFAULT_CONFIG
    from .serializer import serialize_book

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    wrote = []
    book_path = directory / "book.fab"
    if not book_path.exists():
        book_path.write_text(serialize_book(book), encoding="utf-8")
        wrote.append(book_path)
    models_path = directory / "models.json"
    if not models_path.exists():
        models_path.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        wrote.append(models_path)
    gitignore = directory / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("build/\n", encoding="utf-8")
    return wrote
