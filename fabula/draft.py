"""Draft agent: raw author notes -> a fully filled book graph.

The model reads the notes once and returns a constrained JSON draft; the
normalizer then folds that draft into the compiler's graph shape
deterministically — scene-mechanics defaults, deduplication, auto-declared
characters/locations/threads for every reference, dead-cast cleanup — so the
resulting book.fab compiles on the first try.

Prompt language follows the TARGET book's language (like every other prompt
in Fabula), with the usual English fallback.
"""

from __future__ import annotations

from .compiler import compile_source
from .i18n import t
from .forms import form_contract, review_plan, validate_form_settings

SENSES = ("smell", "sound", "touch", "taste", "sight")
VOICES = {"first_person", "third_person_limited", "third_person_omniscient"}
TENSES = {"past", "present"}
DEFAULT_TENSION = [0.3, 0.6, 0.8, 0.5]
DEFAULT_LENGTH = [800, 1200]


def _s(value) -> str:
    """A model field as clean text; anything non-textual becomes ''."""
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value).strip()
    return ""


def _slist(value) -> list:
    return [_s(v) for v in value if _s(v)] if isinstance(value, list) else []


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dedupe(items: list, key: str = "name") -> list:
    seen, out = set(), []
    for item in items:
        if item[key] and item[key] not in seen:
            seen.add(item[key])
            out.append(item)
    return out


def _norm_scene(raw: dict, used_names: set, lang: str) -> dict:
    name = _s(raw.get("name")) or t("seed.scene", lang)
    base, n = name, 2
    while name in used_names:
        name, n = f"{base} {n}", n + 1
    used_names.add(name)

    tension = []
    for v in (raw.get("tension") if isinstance(raw.get("tension"), list) else []) or []:
        try:
            tension.append(min(1.0, max(0.0, float(v))))
        except (TypeError, ValueError):
            pass
    length = raw.get("length")
    if isinstance(length, dict):
        length = length.get("range")
    rng = None
    if isinstance(length, (list, tuple)) and len(length) == 2:
        lo, hi = _int(length[0]), _int(length[1])
        if lo and hi and 0 < lo <= hi:
            rng = [lo, hi]

    return {
        "name": name,
        "pov": _s(raw.get("pov")),
        "cast": _slist(raw.get("cast")),
        "kill": _slist(raw.get("kill")),
        "threads": _slist(raw.get("threads")),
        "location": _s(raw.get("location")),
        "time": _s(raw.get("time")),
        "tension": tension or list(DEFAULT_TENSION),
        "length": {"range": rng or list(DEFAULT_LENGTH)},
        "variants": min(5, max(1, _int(raw.get("variants")) or 1)),
        "style_hints": _slist(raw.get("style_hints")),
        "show": _slist(raw.get("show")),
        "forbid": _slist(raw.get("forbid")),
    }


def normalize_draft(data: dict, lang: str) -> dict:
    """Model draft (loose JSON) -> compiler-shaped book graph that compiles.

    Every guarantee the compiler checks as an error is enforced here:
    unique character/location/thread names, declared references for
    pov/cast/kill/location/threads, no dead character in later scenes,
    valid length ranges.
    """
    settings = {
        "voice": (_s(data.get("voice")) if _s(data.get("voice")) in VOICES
                  else "third_person_limited"),
        "tense": _s(data.get("tense")) if _s(data.get("tense")) in TENSES else "past",
        "language": lang,
        "style_profile": _s(data.get("style_profile")),
    }
    if _s(data.get("premise")):
        settings["premise"] = _s(data.get("premise"))
    if _int(data.get("target_words")):
        settings["target_words"] = _int(data.get("target_words"))
    if _s(data.get("form")):
        settings["form"] = _s(data["form"])
    validate_form_settings(settings)

    characters = _dedupe([{
        "name": _s(c.get("name")), "age": _int(c.get("age")),
        "traits": _slist(c.get("traits")), "arc": _s(c.get("arc")) or None,
        "appearance": _s(c.get("appearance")) or None, "voice": _s(c.get("voice")) or None,
        "wants": _s(c.get("wants")) or None, "fears": _s(c.get("fears")) or None,
        "secret": _s(c.get("secret")) or None, "never": _slist(c.get("never")),
        "must_resolve": _slist(c.get("must_resolve")), "notes": _s(c.get("notes")) or None,
    } for c in data.get("characters") or [] if isinstance(c, dict)])

    locations = _dedupe([dict(
        {"name": _s(l.get("name")), "description": _s(l.get("description"))},
        **{sense: _s(l.get(sense)) for sense in SENSES},
    ) for l in data.get("locations") or [] if isinstance(l, dict)])

    threads = _dedupe([{"name": _s(th.get("name")), "description": _s(th.get("description"))}
                       for th in data.get("threads") or [] if isinstance(th, dict)])

    world = None
    if isinstance(data.get("world"), dict):
        w = data["world"]
        rules = [{"name": _s(r.get("name")) or t("seed.rule", lang),
                  "description": _s(r.get("description")),
                  "severity": "hard" if _s(r.get("severity")) == "hard" else "soft"}
                 for r in w.get("rules") or [] if isinstance(r, dict)]
        if _s(w.get("name")) or rules:
            world = {"name": _s(w.get("name")) or t("seed.world", lang), "rules": rules}

    used_names: set = set()
    acts = []
    for ai, act in enumerate(a for a in data.get("acts") or [] if isinstance(a, dict)):
        chapters = []
        for ci, ch in enumerate(c for c in act.get("chapters") or [] if isinstance(c, dict)):
            scenes = [_norm_scene(sc, used_names, lang)
                      for sc in ch.get("scenes") or [] if isinstance(sc, dict)]
            if scenes:
                chapters.append({"number": ci + 1,
                                 "title": _s(ch.get("title")) or t("seed.chapter", lang),
                                 "scenes": scenes})
        if chapters:
            acts.append({"number": ai + 1, "title": _s(act.get("title")) or None,
                         "chapters": chapters})
    for i, act in enumerate(acts):  # renumber after empty ones dropped
        act["number"] = i + 1
        for j, ch in enumerate(act["chapters"]):
            ch["number"] = j + 1
    if not acts:
        acts = [{"number": 1, "title": None, "chapters": [{
            "number": 1, "title": t("seed.chapter", lang),
            "scenes": [_norm_scene({"pov": characters[0]["name"] if characters else ""},
                                   used_names, lang)]}]}]

    book = {"title": _s(data.get("title")) or t("seed.book", lang),
            "settings": settings, "world": world, "characters": characters,
            "locations": locations, "threads": threads, "acts": acts}

    _declare_references(book, lang)
    _drop_dead_references(book)
    return book


def _iter_scenes(book: dict):
    for act in book["acts"]:
        for chapter in act["chapters"]:
            for scene in chapter["scenes"]:
                yield scene


def _declare_references(book: dict, lang: str):
    """Every name a scene mentions must exist as a declaration (FAB101/107/109)."""
    chars = {c["name"] for c in book["characters"]}
    locs = {l["name"] for l in book["locations"]}
    threads = {th["name"] for th in book["threads"]}
    for scene in _iter_scenes(book):
        for name in [scene["pov"], *scene["cast"], *scene["kill"]]:
            if name and name not in chars:
                chars.add(name)
                book["characters"].append({"name": name, "age": None, "traits": [],
                                           "arc": None, "appearance": None, "voice": None,
                                           "wants": None, "fears": None, "secret": None,
                                           "never": [], "must_resolve": [], "notes": None})
        if scene["location"] and scene["location"] not in locs:
            locs.add(scene["location"])
            book["locations"].append(dict({"name": scene["location"], "description": ""},
                                          **{sense: "" for sense in SENSES}))
        for name in scene["threads"]:
            if name not in threads:
                threads.add(name)
                book["threads"].append({"name": name, "description": ""})
    if not book["characters"]:
        book["characters"].append({"name": t("seed.character", lang), "age": None,
                                   "traits": [], "arc": None, "appearance": None,
                                   "voice": None, "wants": None, "fears": None,
                                   "secret": None, "never": [], "must_resolve": [],
                                   "notes": None})
        chars = {book["characters"][0]["name"]}
    first = book["characters"][0]["name"]
    for scene in _iter_scenes(book):
        if not scene["pov"]:
            scene["pov"] = first


def _drop_dead_references(book: dict):
    """A character killed in one scene must not reappear later (FAB108)."""
    dead: set = set()
    alive_order = [c["name"] for c in book["characters"]]
    for scene in _iter_scenes(book):
        scene["cast"] = [n for n in scene["cast"] if n not in dead]
        scene["kill"] = [n for n in scene["kill"] if n not in dead]
        if scene["pov"] in dead:
            fallback = scene["cast"] + [n for n in alive_order if n not in dead]
            scene["pov"] = next((n for n in fallback if n not in dead), scene["pov"])
        dead.update(scene["kill"])


def draft_prompt_pair(notes: str, lang: str, settings: dict | None = None):
    """(system, user) for the drafting call, in the book's language."""
    settings = {**(settings or {}), "language": lang}
    validate_form_settings(settings)
    system = t("prompts.draft_system", lang)
    if settings.get("form"):
        system += "\n\n" + form_contract(settings)
    elif settings.get("target_words"):
        system += "\n\n" + t("forms.budget", lang, words=settings["target_words"])
    return system, t("prompts.draft", lang, notes=notes)


def draft_book(notes: str, lang: str, provider, on_progress=None,
               settings: dict | None = None) -> dict:
    """Notes -> normalized book graph, verified against the compiler."""
    progress = on_progress or (lambda msg: print(msg))
    progress(t("agent.draft", provider=provider.name))
    system, prompt = draft_prompt_pair(notes, lang, settings)
    from .agent import _extract_json
    data = _extract_json(provider.generate(system, prompt, effort="high"))
    if (not isinstance(data, dict) or data.get("verdict") == "unparsed"
            or not (data.get("acts") or data.get("characters") or data.get("title"))):
        raise ValueError(t("agent.draft_unparsed"))
    # Explicit author choices win even when the model returns another form.
    for key in ("form", "target_words"):
        if (settings or {}).get(key) is not None:
            data[key] = settings[key]
    book = normalize_draft(data, lang)
    book["settings"].update(settings or {})
    book["settings"]["language"] = lang

    # safety net: the normalizer must have produced a compiling book
    from .serializer import serialize_book
    result = compile_source(serialize_book(book))
    errors = [d for d in result["diagnostics"] if d["severity"] == "error"]
    if not result["ok"] or errors:
        raise ValueError(t("agent.draft_broken",
                           codes=", ".join(sorted({d["code"] for d in errors})) or "?"))

    if book["settings"].get("form"):
        progress(t("forms.checking"))
        book["_plan_review"] = review_plan(book, provider)
        progress(book["_plan_review"]["summary"])

    scenes = sum(1 for _ in _iter_scenes(book))
    progress(t("agent.draft_done", title=book["title"], n=scenes))
    return book
