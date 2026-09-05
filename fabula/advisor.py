"""Settings advisor: author's notes (and the compiled book) -> suggested settings.

Fabula's defaults are tuned for a book of about a dozen scenes. Notes that
carry a whole plot produce forty-odd scenes, and then `thread_gap` fires a
storm of FAB204 warnings, `target_words` is missing, and the per-scene
`variants` quietly multiply the generation bill. None of that is visible
until the book is already drafted.

This module measures what is measurable and says what it implies. It is
deliberately deterministic — no model call, no network, no cost — so the UI
can re-run it on every keystroke in the notes box and so the rules can be
tested. It never edits anything: it returns suggestions with a reason, and
the caller (the draft flow or the constructor) decides what to apply.

Two inputs, either or both:
  notes — raw author notes; shape is inferred from paragraphs, not content
  book  — a compiled book graph; exact counts win over estimates from notes

Reasons come from the locale (`advisor.*`), so they follow the interface
language like every other user-facing string.
"""

from __future__ import annotations

import re

from .compiler import iter_scenes
from .i18n import t

# A prose paragraph this long is, empirically, about one scene's worth of
# event. Shorter ones are asides, headers or list leftovers.
BEAT_CHARS = 200
# Sanity rails for the scene estimate, in case the notes are all one blob
# or all one-liners: a scene needs material, but not a whole chapter.
WORDS_PER_SCENE_MIN = 60
WORDS_PER_SCENE_MAX = 400
SCENES_MIN, SCENES_MAX = 6, 60
# Scenes a thread may sit out before the reader loses it; scaled by book size.
SCENES_PER_GAP = 6
GAP_MIN, GAP_MAX = 5, 15
# Mid-point of the default 800..1200 scene length.
WORDS_PER_SCENE = 1000
# Above this many scenes, a second variant per scene stops being worth its cost.
VARIANTS_CUTOFF = 25
# Constraint words per 1000 words of notes above which self-revision pays off.
RULE_DENSITY = 4.0

_CONSTRAINT = re.compile(
    r"заборон|не можна|ніколи не|мусить|мають бути|обов'язков"
    r"|forbidden|never |must not|must be|required",
    re.IGNORECASE,
)
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_LATIN = re.compile(r"[A-Za-z]")
_HEADING = re.compile(r"^#{1,6}\s")
_BULLET = re.compile(r"^\s*(?:[-*+>]\s|\d+[.)]\s)")


def _paragraphs(notes: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", notes) if p.strip()]


def analyze_notes(notes: str) -> dict:
    """The measurable shape of the notes — no interpretation, just counts."""
    text = notes or ""
    paras = _paragraphs(text)
    prose = [p for p in paras
             if not _HEADING.match(p) and not _BULLET.match(p)]
    beats = [p for p in prose if len(p) >= BEAT_CHARS]
    words = len(text.split())
    return {
        "words": words,
        "chars": len(text),
        "paragraphs": len(paras),
        "prose": len(prose),
        "beats": len(beats),
        "reference": len(paras) - len(prose),
        "constraints": len(_CONSTRAINT.findall(text)),
        "cyrillic": len(_CYRILLIC.findall(text)),
        "latin": len(_LATIN.findall(text)),
    }


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def estimate_scenes(shape: dict) -> int:
    """How many scenes the notes carry: one substantial prose paragraph each.

    Railed by word count so a single unbroken blob and a wall of one-liners
    both land somewhere sane.
    """
    words = shape["words"]
    if not words:
        return SCENES_MIN
    estimate = shape["beats"] or max(1, words // WORDS_PER_SCENE_MAX)
    floor = max(1, words // WORDS_PER_SCENE_MAX)
    ceiling = max(floor, words // WORDS_PER_SCENE_MIN)
    return _clamp(_clamp(estimate, floor, ceiling), SCENES_MIN, SCENES_MAX)


def suggested_gap(scenes: int) -> int:
    """Silence a thread may keep, scaled to the book: ~1/6 of its scenes."""
    return _clamp(round(scenes / SCENES_PER_GAP), GAP_MIN, GAP_MAX)


def _book_shape(book: dict) -> dict:
    """Exact counts from a compiled graph — these beat any estimate."""
    scenes = [scene for _, _, scene in iter_scenes(book)]
    words = 0
    for scene in scenes:
        rng = (scene.get("length") or {}).get("range") or [800, 1200]
        try:
            words += (int(rng[0]) + int(rng[1])) // 2
        except (TypeError, ValueError, IndexError):
            words += WORDS_PER_SCENE
    return {
        "scenes": len(scenes),
        "acts": len(book.get("acts") or []),
        "threads": len(book.get("threads") or []),
        "words": words,
        "variants": max((int(s.get("variants") or 1) for s in scenes), default=1),
    }


def _thread_gaps(book: dict) -> dict:
    """thread name -> every run of scenes it sits out, measured like FAB204.

    One entry per gap, not per thread: FAB204 fires once per gap, so counting
    threads would understate how many warnings a limit clears.
    """
    scenes = [scene for _, _, scene in iter_scenes(book)]
    out = {}
    for thread in book.get("threads") or []:
        name = thread.get("name")
        hits = [i for i, s in enumerate(scenes) if name in (s.get("threads") or [])]
        out[name] = [b - a - 1 for a, b in zip(hits, hits[1:])]
    return out


def _item(key, current, suggested, reason, level="info", scope="settings"):
    return {"key": key, "scope": scope, "current": current, "suggested": suggested,
            "reason": reason, "level": level,
            "changes": current != suggested and suggested is not None}


def recommend(notes: str = "", book: dict | None = None, lang: str | None = None) -> dict:
    """Suggested settings for this book, with a reason for each one.

    Returns {"shape": …, "settings": {ready-to-apply}, "items": [...]}.
    Nothing here mutates `book`; `settings` is what "apply" would write.
    """
    shape = analyze_notes(notes) if notes else None
    graph = _book_shape(book) if book else None
    current = dict((book or {}).get("settings") or {})

    # Exact counts from a real graph win; notes only estimate.
    scenes = graph["scenes"] if graph else (estimate_scenes(shape) if shape else 0)
    if not scenes:
        return {"shape": {"notes": shape, "book": graph}, "settings": {}, "items": []}

    items = []

    # --- thread_gap ------------------------------------------------------
    gap_now = _int(current.get("thread_gap"), 7)
    gap_new = suggested_gap(scenes)
    if graph:
        gaps = _thread_gaps(book)
        longest = {n: max(g, default=0) for n, g in gaps.items()}
        flat = [g for runs in gaps.values() for g in runs]
        cleared = sum(1 for g in flat if g > gap_now) - sum(1 for g in flat if g > gap_new)
        structural = sorted((n for n, g in longest.items() if g > gap_new * 2),
                            key=lambda n: -longest[n])
        reason = t("advisor.thread_gap.book", lang, scenes=scenes, gap=gap_new,
                   cleared=max(0, cleared))
    else:
        longest = {}
        structural = []
        reason = t("advisor.thread_gap.notes", lang, scenes=scenes, gap=gap_new)
    items.append(_item("thread_gap", gap_now, gap_new, reason,
                       "warn" if gap_new > gap_now else "info"))

    # --- target_words ----------------------------------------------------
    words_now = _int(current.get("target_words"), None)
    words_new = _round_to(graph["words"] if graph else scenes * WORDS_PER_SCENE, 1000)
    items.append(_item(
        "target_words", words_now, words_new,
        t("advisor.target_words.book" if graph else "advisor.target_words.notes",
          lang, scenes=scenes, words=words_new),
        "warn" if words_now is None else "info"))

    # --- variants --------------------------------------------------------
    variants_now = graph["variants"] if graph else 1
    variants_new = 1 if scenes >= VARIANTS_CUTOFF else 2
    items.append(_item(
        "variants", variants_now, variants_new,
        t("advisor.variants.big" if variants_new == 1 else "advisor.variants.small",
          lang, scenes=scenes, calls=scenes * max(variants_now, 1)),
        "warn" if variants_now > variants_new else "info", scope="scenes"))

    # --- revisions -------------------------------------------------------
    revisions_now = _int(current.get("revisions"), 1)
    density = (shape["constraints"] / shape["words"] * 1000) if shape and shape["words"] else 0
    revisions_new = 2 if density >= RULE_DENSITY else 1
    items.append(_item(
        "revisions", revisions_now, revisions_new,
        t("advisor.revisions.dense" if revisions_new == 2 else "advisor.revisions.plain",
          lang, density=round(density, 1))))

    # --- language --------------------------------------------------------
    if shape and (shape["cyrillic"] or shape["latin"]):
        lang_new = "uk" if shape["cyrillic"] > shape["latin"] else "en"
        lang_now = str(current.get("language") or "") or None
        if lang_now != lang_new:
            items.append(_item("language", lang_now, lang_new,
                               t("advisor.language.detected", lang, code=lang_new)))

    # --- structural silences: tuning cannot fix these, so say so ----------
    for name in structural[:3]:
        items.append({"key": "thread", "scope": "note", "current": None,
                      "suggested": None, "changes": False, "level": "warn",
                      "reason": t("advisor.thread.structural", lang,
                                  name=name, n=longest[name])})

    settings = {i["key"]: i["suggested"] for i in items
                if i["scope"] == "settings" and i["suggested"] is not None}
    return {"shape": {"notes": shape, "book": graph},
            "settings": settings, "items": items,
            "scenes": scenes,
            "variants": variants_new}


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _round_to(value: int, step: int) -> int:
    return int(round(value / step) * step) or step
