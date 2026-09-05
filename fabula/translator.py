"""Translation agent: the approved manuscript -> another language.

The unit of work is a scene (same as writing): each chosen scene text is
translated separately and cached under <build>/translations/<lang>/, keyed by
a fingerprint of the source text — re-approving or editing a scene makes its
translation stale, and the next run retranslates only what changed.

Style is preserved through two mechanisms:
- a stable system prompt (cache-friendly, like the writer's book bible) that
  carries the book's voice/tense/style_profile and hard style-keeping rules;
- a glossary built once per language by a dedicated model call: proper names,
  key terms, chapter titles, and natural heading formats for the target
  language, so every scene call renders names and headings identically.

Prompt language follows the BOOK's language (the usual English fallback);
the TARGET language is spelled out inside the prompt by name.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .compiler import iter_scenes
from .i18n import t
from .state import slug

# Human-readable names make the target unambiguous for the model — a bare
# ISO code stays the fallback for languages not listed here.
LANGUAGE_NAMES = {
    "en": "English", "uk": "Ukrainian", "de": "German", "fr": "French",
    "es": "Spanish", "it": "Italian", "pl": "Polish", "pt": "Portuguese",
    "nl": "Dutch", "cs": "Czech", "sk": "Slovak", "bg": "Bulgarian",
    "hr": "Croatian", "sr": "Serbian", "sl": "Slovenian", "ro": "Romanian",
    "hu": "Hungarian", "el": "Greek", "tr": "Turkish", "sv": "Swedish",
    "no": "Norwegian", "da": "Danish", "fi": "Finnish", "et": "Estonian",
    "lv": "Latvian", "lt": "Lithuanian", "ka": "Georgian", "be": "Belarusian",
    "ja": "Japanese", "ko": "Korean", "zh": "Chinese",
    "ar": "Arabic", "he": "Hebrew", "hi": "Hindi", "id": "Indonesian",
    "vi": "Vietnamese", "th": "Thai",
}

_LANG_CODE = re.compile(r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


def valid_lang(code: str) -> bool:
    """A code becomes a directory name — accept only plausible BCP-47 shapes."""
    return bool(_LANG_CODE.fullmatch(str(code or "")))


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(str(code).lower().split("-")[0], str(code))


def fingerprint(text: str) -> str:
    return hashlib.sha1(str(text).strip().encode("utf-8")).hexdigest()[:16]


def _book_lang(book: dict) -> str:
    return str((book.get("settings") or {}).get("language") or "uk")


def _p(book: dict, key: str, **params) -> str:
    return t("prompts." + key, _book_lang(book), **params)


class TranslationState:
    """One target language of one project: glossary + per-scene cache.

    Lives in <build>/translations/<lang>/ as state.json plus scenes/<slug>.md,
    mirroring how ProjectState stores the original variants.
    """

    def __init__(self, build_dir: Path, lang: str):
        if not valid_lang(lang):
            raise ValueError(t("server.bad_lang", lang=str(lang)))
        self.lang = str(lang)
        self.dir = Path(build_dir) / "translations" / self.lang
        self.path = self.dir / "state.json"
        self.data = {"glossary": None, "scenes": {}}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        self.data.setdefault("glossary", None)
        self.data.setdefault("scenes", {})

    def save(self):
        import os
        self.dir.mkdir(parents=True, exist_ok=True)
        # atomic replace, like ProjectState: the UI polls while a job writes
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    @property
    def glossary(self) -> dict | None:
        return self.data.get("glossary")

    def set_glossary(self, glossary: dict):
        self.data["glossary"] = glossary
        self.save()

    def entry(self, scene_name: str) -> dict:
        return self.data["scenes"].get(scene_name) or {}

    def scene_path(self, scene_name: str) -> Path:
        return self.dir / "scenes" / (slug(scene_name) + ".md")

    def set_scene_text(self, scene_name: str, text: str, source_fp: str):
        path = self.scene_path(scene_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.data["scenes"][scene_name] = {
            "file": path.name, "fingerprint": source_fp,
            "words": len(text.split()),
        }
        self.save()

    def text(self, scene_name: str) -> str | None:
        path = self.scene_path(scene_name)
        return path.read_text(encoding="utf-8") if path.exists() else None


def _glossary_names(book: dict) -> list[str]:
    names = [c["name"] for c in book.get("characters") or []]
    names += [l["name"] for l in book.get("locations") or []]
    names += [th["name"] for th in book.get("threads") or []]
    return [n for n in dict.fromkeys(names) if n]


def _chapter_titles(book: dict) -> list[str]:
    titles = []
    for act in book.get("acts") or []:
        for chapter in act.get("chapters") or []:
            if chapter.get("title"):
                titles.append(chapter["title"])
    return list(dict.fromkeys(titles))


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def chapter_heading(book: dict, tstate: TranslationState, chapter: dict) -> str:
    """Chapter heading in the target language: glossary format + glossary title."""
    g = tstate.glossary or {}
    title = (g.get("chapter_titles") or {}).get(chapter["title"]) or chapter["title"]
    fmt = g.get("chapter_heading")
    if isinstance(fmt, str) and "{n}" in fmt:
        return fmt.format_map(_SafeDict(n=chapter["number"], title=title))
    return t("prompts.chapter_heading", tstate.lang, n=chapter["number"], title=title)


def act_heading(book: dict, tstate: TranslationState, n: int) -> str:
    fmt = (tstate.glossary or {}).get("act_heading")
    if isinstance(fmt, str) and "{n}" in fmt:
        return fmt.format_map(_SafeDict(n=n))
    return t("prompts.act_heading", tstate.lang, n=n)


def translated_title(book: dict, tstate: TranslationState) -> str:
    return (tstate.glossary or {}).get("title") or book.get("title") or "Fabula"


def translation_status(book: dict, state, tstate: TranslationState) -> dict:
    """done / stale / missing counts the UI and CLI report before translating."""
    done = stale = missing = total = 0
    for _, _, scene in iter_scenes(book):
        source = state.chosen_text(scene["name"])
        if not source:
            continue  # nothing to translate yet — not counted against the language
        total += 1
        if tstate.text(scene["name"]) is None:
            missing += 1
        elif tstate.entry(scene["name"]).get("fingerprint") != fingerprint(source):
            stale += 1
        else:
            done += 1
    return {"lang": tstate.lang, "language": language_name(tstate.lang),
            "title": translated_title(book, tstate),
            "done": done, "stale": stale, "missing": missing, "total": total}


def translated_manuscript(book: dict, state, tstate: TranslationState) -> dict:
    """Assemble the translated scenes the way Project.manuscript assembles
    the original — same chapter grouping, headings from the glossary."""
    parts, missing, stale, chapters = [], [], [], []
    current_chapter = None
    # place names come from the glossary so they read as they do in the prose;
    # the time of day is free text the glossary does not carry, so it stays as
    # the contract wrote it
    names = (tstate.glossary or {}).get("names") or {}
    for act, chapter, scene in iter_scenes(book):
        source = state.chosen_text(scene["name"])
        text = tstate.text(scene["name"])
        if text:
            key = (act["number"], chapter["number"])
            if key != current_chapter:
                heading = chapter_heading(book, tstate, chapter)
                parts.append(heading + "\n")
                chapters.append({"act": act["number"],
                                 "act_heading": act_heading(book, tstate, act["number"]),
                                 "heading": heading, "scenes": []})
                current_chapter = key
            parts.append(text.strip() + "\n")
            location = scene.get("location") or ""
            chapters[-1]["scenes"].append({
                "name": scene["name"],
                "location": names.get(location, location),
                "time": scene.get("time") or "",
                "text": text.strip(),
            })
            if source and tstate.entry(scene["name"]).get("fingerprint") != fingerprint(source):
                stale.append(scene["name"])
        elif source:
            missing.append(scene["name"])
    return {"title": translated_title(book, tstate), "lang": tstate.lang,
            "text": "\n".join(parts), "missing": missing, "stale": stale,
            "chapters": chapters}


def _strip_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text)
    return text.strip()


class Translator:
    def __init__(self, book: dict, provider, target: str, on_progress=None):
        if not valid_lang(target):
            raise ValueError(t("server.bad_lang", lang=str(target)))
        self.book = book
        self.provider = provider
        self.target = target
        self.on_progress = on_progress or (lambda msg: print(msg))
        self.source_name = language_name(_book_lang(book))
        self.target_name = language_name(target)

    # --- glossary ---

    def _glossary_prompt(self, names: list[str], chapters: list[str]) -> str:
        return _p(self.book, "translate.glossary",
                  dst=self.target_name,
                  title=self.book.get("title") or "?",
                  names="\n".join(f"- {n}" for n in names) or "-",
                  chapters="\n".join(f"- {c}" for c in chapters) or "-")

    def ensure_glossary(self, tstate: TranslationState, force: bool = False) -> dict:
        """Build (or top up) the name glossary; existing entries always win so
        scenes translated earlier keep their spelling."""
        stored = tstate.glossary if isinstance(tstate.glossary, dict) else None
        names = _glossary_names(self.book)
        chapters = _chapter_titles(self.book)
        if stored and not force:
            known = stored.get("names") or {}
            known_ch = stored.get("chapter_titles") or {}
            missing = [n for n in names if n not in known]
            missing_ch = [c for c in chapters if c not in known_ch]
            if not missing and not missing_ch:
                return stored
            names, chapters = missing, missing_ch  # only the new entries go to the model
        self.on_progress(t("agent.translate_glossary", lang=self.target_name,
                           provider=self.provider.name))
        from .agent import _extract_json
        data = _extract_json(self.provider.generate(
            _p(self.book, "translate.system_glossary"),
            self._glossary_prompt(names, chapters), effort="medium"))
        if not isinstance(data, dict) or data.get("verdict") == "unparsed":
            raise ValueError(t("agent.glossary_unparsed"))

        def str_map(value) -> dict:
            if not isinstance(value, dict):
                return {}
            return {str(k).strip(): str(v).strip() for k, v in value.items()
                    if str(k).strip() and str(v).strip()}

        def heading_fmt(value):
            return value if isinstance(value, str) and "{n}" in value else None

        fresh = {
            "title": str(data.get("title") or "").strip() or None,
            "names": str_map(data.get("names")),
            "chapter_titles": str_map(data.get("chapter_titles")),
            "chapter_heading": heading_fmt(data.get("chapter_heading")),
            "act_heading": heading_fmt(data.get("act_heading")),
        }
        if stored and not force:  # merge: earlier decisions are immutable
            merged = dict(stored)
            merged["names"] = {**fresh["names"], **(stored.get("names") or {})}
            merged["chapter_titles"] = {**fresh["chapter_titles"],
                                        **(stored.get("chapter_titles") or {})}
            for key in ("title", "chapter_heading", "act_heading"):
                merged.setdefault(key, None)
                merged[key] = merged[key] or fresh[key]
            fresh = merged
        tstate.set_glossary(fresh)
        return fresh

    # --- scenes ---

    def _system(self, glossary: dict) -> str:
        settings = self.book.get("settings") or {}
        lines = [_p(self.book, "translate.system",
                    title=self.book.get("title") or "?",
                    src=self.source_name, dst=self.target_name,
                    voice=settings.get("voice") or "-",
                    tense=settings.get("tense") or "-")]
        if settings.get("style_profile"):
            lines += ["", _p(self.book, "translate.style_profile",
                             x=settings["style_profile"])]
        entries = {**(glossary.get("names") or {}), **(glossary.get("chapter_titles") or {})}
        if glossary.get("title"):
            entries[self.book.get("title") or ""] = glossary["title"]
        if entries:
            lines += ["", _p(self.book, "translate.glossary_hdr")]
            lines += [f"- {src} → {dst}" for src, dst in entries.items() if src]
        return "\n".join(lines)

    def translate_scene_text(self, system: str, scene_name: str, text: str) -> str:
        prompt = _p(self.book, "translate.scene", scene=scene_name,
                    dst=self.target_name, text=text)
        return _strip_fence(self.provider.generate(system, prompt, effort="high"))

    def translate_book(self, state, tstate: TranslationState,
                       force: bool = False) -> dict:
        """Translate every chosen scene text that is new or stale."""
        glossary = self.ensure_glossary(tstate, force=force)
        system = self._system(glossary)
        report = {"lang": self.target, "translated": [], "skipped": [], "missing": []}
        for _, _, scene in iter_scenes(self.book):
            name = scene["name"]
            source = state.chosen_text(name)
            if not source:
                report["missing"].append(name)
                continue
            fp = fingerprint(source)
            if (not force and tstate.entry(name).get("fingerprint") == fp
                    and tstate.text(name) is not None):
                report["skipped"].append(name)
                continue
            self.on_progress(t("agent.translate_scene", scene=name,
                               lang=self.target_name, provider=self.provider.name))
            tstate.set_scene_text(name, self.translate_scene_text(system, name, source), fp)
            report["translated"].append(name)
        self.on_progress(t("agent.translate_done", lang=self.target_name,
                           n=len(report["translated"]), s=len(report["skipped"]),
                           m=len(report["missing"])))
        return report


def list_translations(build_dir: Path) -> list[str]:
    """Language codes that already have a translation directory."""
    root = Path(build_dir) / "translations"
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir() and valid_lang(d.name))
