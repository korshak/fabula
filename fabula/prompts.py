"""Prompt builder: project graph -> prompts for the writer agent.

Split deliberately into a STABLE part (book bible: world, rules, characters,
style) and a VOLATILE part (the one scene being written). The stable part goes
into the system prompt with cache_control so every scene generation after the
first reads it from the prompt cache.

Prompt language follows the BOOK's `language` setting (not the UI locale):
templates live in the `prompts` section of fabula/locales/<code>.json and fall
back to English when the book's language has no locale file.
"""

from __future__ import annotations

import json

from .compiler import iter_scenes
from .i18n import t as _t


def _book_lang(book: dict) -> str:
    return str((book.get("settings") or {}).get("language") or "uk")


def _p(book: dict, key: str, **params) -> str:
    """Prompt-template lookup in the book's language (English fallback)."""
    return _t("prompts." + key, _book_lang(book), **params)


def book_bible(book: dict) -> str:
    """Stable system-prompt text: everything the agent must never contradict."""
    settings = book["settings"]
    lines = [
        _p(book, "bible.role", title=book["title"]),
        _p(book, "bible.role2"),
        "",
        _p(book, "bible.settings_hdr"),
    ]
    for key, value in settings.items():
        lines.append(f"- {key}: {value}")

    hard = _p(book, "bible.rule_hard")
    if book["world"]:
        lines += ["", _p(book, "bible.world_hdr", name=book["world"]["name"]),
                  _p(book, "bible.world_rules")]
        for rule in book["world"]["rules"]:
            marker = hard if rule["severity"] == "hard" else _p(book, "bible.rule_soft")
            lines.append(f"- [{marker}] {rule['name']}: {rule['description']}")

    if book["characters"]:
        lines += ["", _p(book, "bible.chars_hdr")]
        for c in book["characters"]:
            lines.append(f"### {c['name']}")
            if c.get("age"):
                lines.append(_p(book, "bible.age", age=c["age"]))
            if c["traits"]:
                lines.append(_p(book, "bible.traits", list=", ".join(map(str, c["traits"]))))
            if c.get("arc"):
                lines.append(_p(book, "bible.arc", arc=c["arc"]))
            for key in ("appearance", "voice", "wants", "fears", "secret"):
                if c.get(key):
                    lines.append(_p(book, "bible." + key, x=c[key]))
            for never in c["never"]:
                lines.append(_p(book, "bible.never", x=never))
            for must in c["must_resolve"]:
                lines.append(_p(book, "bible.must", x=must))

    if book.get("locations"):
        lines += ["", _p(book, "bible.locations_hdr")]
        for loc in book["locations"]:
            lines.append(f"### {loc['name']}")
            if loc.get("description"):
                lines.append(f"- {loc['description']}")
            for sense in ("smell", "sound", "touch", "taste", "sight"):
                if loc.get(sense):
                    lines.append(_p(book, "bible.sense_" + sense, x=loc[sense]))

    if book.get("threads"):
        lines += ["", _p(book, "bible.threads_hdr"), _p(book, "bible.threads_note")]
        for th in book["threads"]:
            desc = f": {th['description']}" if th.get("description") else ""
            lines.append(f"- {th['name']}{desc}")

    lines += ["", _p(book, "bible.reqs_hdr"),
              _p(book, "bible.reqs1", hard=hard),
              _p(book, "bible.reqs2"),
              _p(book, "bible.reqs3"),
              _p(book, "bible.reqs4")]
    return "\n".join(lines)


def find_scene(book: dict, scene_name: str):
    for act, chapter, scene in iter_scenes(book):
        if scene["name"] == scene_name:
            return act, chapter, scene
    return None


def scene_prompt(book: dict, scene_name: str, previous_summary: str = "",
                 ledger: str = "", instruction: str = "") -> str:
    """Volatile user-prompt text: the spec of one scene."""
    found = find_scene(book, scene_name)
    if not found:
        raise KeyError(f"scene {scene_name!r} not found")
    act, chapter, scene = found

    lines = [
        _p(book, "scene.write", scene=scene["name"], act=act["number"],
           ch=chapter["number"], title=chapter["title"]),
        "",
        _p(book, "scene.spec_hdr"),
    ]
    if scene["pov"]:
        lines.append(_p(book, "scene.pov", pov=scene["pov"]))
    if scene["location"]:
        lines.append(_p(book, "scene.location", location=scene["location"]))
    if scene["time"]:
        lines.append(_p(book, "scene.time", time=scene["time"]))
    if scene["tension"]:
        lines.append(_p(book, "scene.tension", tension=scene["tension"]))
    if scene["length"]:
        lo, hi = scene["length"]["range"]
        lines.append(_p(book, "scene.length", a=lo, b=hi))
    if scene["style_hints"]:
        lines.append(_p(book, "scene.hints", list=", ".join(map(str, scene["style_hints"]))))
    if scene["cast"]:
        lines.append(_p(book, "scene.cast", list=", ".join(scene["cast"])))
    for victim in scene["kill"]:
        lines.append(_p(book, "scene.kill", name=victim))
    if scene.get("threads"):
        by_name = {th["name"]: th for th in book.get("threads") or []}
        for name in scene["threads"]:
            desc = (by_name.get(name) or {}).get("description") or ""
            lines.append(_p(book, "scene.thread", name=name, desc=desc))

    if scene["show"]:
        lines += ["", _p(book, "scene.show_hdr")]
        lines += [f"- {beat}" for beat in scene["show"]]
    if scene["forbid"]:
        lines += ["", _p(book, "scene.forbid_hdr")]
        lines += [f"- {item}" for item in scene["forbid"]]
    if previous_summary:
        lines += ["", _p(book, "scene.before_hdr"), previous_summary]
    if ledger:
        lines += ["", _p(book, "scene.ledger_hdr"), ledger]
    if instruction:
        lines += ["", _p(book, "scene.instruction_hdr"), instruction]

    return "\n".join(lines)


def _memory_window(book: dict) -> int:
    """How many most-recent scenes keep their full summary in the prompt.

    Configurable per book: `memory_window: 5;` among the book's settings.
    """
    try:
        return max(1, int(book.get("settings", {}).get("memory_window") or 3))
    except (TypeError, ValueError):
        return 3


def _first_sentence(text: str) -> str:
    text = " ".join(str(text).split())
    for stop in (". ", "! ", "? "):
        if stop in text:
            return text.split(stop)[0] + stop.strip()
    return text


def previous_context(book: dict, scene_name: str, scenes_meta: dict | None = None) -> str:
    """Build the 'what happened before' block for a scene, hierarchically.

    The last `memory_window` scenes keep their full summary; older scenes are
    compressed to one sentence each (so the block stays small on long books).
    For scenes with no generated text yet, the scene's own 'show' beats serve
    as the fallback. Deaths are always kept, regardless of age.
    """
    scenes_meta = scenes_meta or {}
    entries = []
    for act, chapter, scene in iter_scenes(book):
        if scene["name"] == scene_name:
            break
        entries.append((act, chapter, scene))
    window = _memory_window(book)
    lines = []
    for idx, (act, chapter, scene) in enumerate(entries):
        summary = (scenes_meta.get(scene["name"]) or {}).get("summary")
        planned = "; ".join(map(str, scene["show"])) if scene["show"] else ""
        if idx >= len(entries) - window:  # recent: full detail
            if summary:
                lines.append(_p(book, "ctx.full", scene=scene["name"], s=summary))
            elif planned:
                lines.append(_p(book, "ctx.planned", scene=scene["name"], beats=planned))
        else:  # distant: one sentence
            base = summary or planned
            if base:
                lines.append(_p(book, "ctx.brief", scene=scene["name"],
                                act=act["number"], ch=chapter["number"],
                                s=_first_sentence(base)))
        for victim in scene["kill"]:
            lines.append(_p(book, "ctx.died", name=victim))
    return "\n".join(lines)


def character_ledger(book: dict, scene_name: str, scenes_meta: dict | None = None) -> str:
    """State of the characters relevant to a scene, right before it starts.

    Deterministic facts come from the graph (alive/dead, last known location);
    semantic notes come from the summarizer's state_changes stored per scene.
    """
    scenes_meta = scenes_meta or {}
    found = find_scene(book, scene_name)
    if not found:
        return ""
    _, _, target = found
    relevant = list(dict.fromkeys(
        n for n in [target.get("pov"), *(target.get("cast") or []), *(target.get("kill") or [])] if n
    ))
    if not relevant:
        return ""
    info = {name: {"dead_in": None, "last_loc": None, "last_scene": None,
                   "changes": [], "views": []}
            for name in relevant}
    prior = False
    for _, _, scene in iter_scenes(book):
        if scene["name"] == scene_name:
            break
        prior = True
        present = {n for n in [scene.get("pov"), *(scene.get("cast") or [])] if n}
        for name in relevant:
            if name in present and scene.get("location"):
                info[name]["last_loc"] = scene["location"]
                info[name]["last_scene"] = scene["name"]
            if name in (scene.get("kill") or []):
                info[name]["dead_in"] = scene["name"]
            meta = scenes_meta.get(scene["name"]) or {}
            for change in meta.get("state_changes") or []:
                if isinstance(change, dict) and change.get("character") == name and change.get("change"):
                    info[name]["changes"].append(str(change["change"]))
            for view in meta.get("viewpoints") or []:
                if isinstance(view, dict) and view.get("character") == name and view.get("view"):
                    info[name]["views"].append(str(view["view"]))
    if not prior:
        return ""
    lines = []
    for name in relevant:
        d = info[name]
        parts = [_p(book, "ledger.dead", scene=d["dead_in"]) if d["dead_in"]
                 else _p(book, "ledger.alive")]
        if d["last_loc"]:
            parts.append(_p(book, "ledger.last_seen", loc=d["last_loc"], scene=d["last_scene"]))
        parts.extend(d["changes"][-3:])  # the freshest semantic notes
        if d["views"]:
            # the character's own picture of events — what they know and believe,
            # so the agent never gives them knowledge they could not have
            parts.append(_p(book, "ledger.view", x=d["views"][-1]))
        lines.append(f"- {name}: " + "; ".join(parts))
    return "\n".join(lines)


def variant_note(book: dict, i: int, v: int) -> str:
    return _p(book, "variant_note", i=i, v=v)


def targeted_revise_prompt(book: dict, text: str, comments: list) -> str:
    """Minimal-edit rewrite: only the commented passages may change."""
    return _p(book, "targeted_revise", text=text,
              comments=comments_instruction(book, comments))


def comments_instruction(book: dict, comments: list) -> str:
    """Editor comments anchored to quoted passages -> instruction lines."""
    lines = []
    for c in comments or []:
        if isinstance(c, dict) and c.get("note"):
            lines.append(_p(book, "comment_item",
                            quote=str(c.get("quote") or "")[:300], note=c["note"]))
    return "\n".join(lines)


def revise_prompt(book: dict, text: str, violations: list) -> str:
    return _p(book, "revise", text=text,
              violations=json.dumps(violations, ensure_ascii=False, indent=2))


def summarize_prompt(book: dict, scene_name: str, text: str) -> str:
    return _p(book, "summarize", scene=scene_name, text=text)


def fix_prompt(book: dict, source: str, diagnostics: list[dict]) -> str:
    """Ask the model to repair the .fab source from compiler diagnostics."""
    return _p(book, "fix", source=source,
              diagnostics=json.dumps(diagnostics, ensure_ascii=False, indent=2))


def critique_prompt(book: dict, scene_name: str, text: str) -> str:
    """Ask the model to audit a generated scene against its contracts.

    The answer must be pure JSON — this is the machine-readable half of the loop.
    """
    found = find_scene(book, scene_name)
    _, _, scene = found
    hard_rules = [r for r in (book["world"]["rules"] if book["world"] else []) if r["severity"] == "hard"]

    contract = {
        "hard_world_rules": [{"name": r["name"], "description": r["description"]} for r in hard_rules],
        "required_beats": scene["show"] + [_p(book, "critique_death", name=v) for v in scene["kill"]],
        "forbidden": scene["forbid"],
        "pov": scene["pov"],
        "length_words": scene["length"]["range"] if scene["length"] else None,
    }
    return _p(book, "critique", text=text,
              contract=json.dumps(contract, ensure_ascii=False, indent=2))
