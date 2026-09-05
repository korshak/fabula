"""Serializer: book graph (the compiler's JSON shape) -> .fab source.

The inverse of the compiler. The web constructor edits the graph through
forms and this module renders it back to canonical Fabula text, so a human
never has to hand-type the syntax. All strings are quoted uniformly — bare
idents like `past` and `"past"` are the same value to the compiler.

Fallback names for nameless blocks come from the locale (seed.*), so they
follow the active interface language instead of being hardwired.
"""

from __future__ import annotations

from .i18n import t


def _q(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _val(value) -> str:
    if isinstance(value, bool):
        return _q(str(value))
    if isinstance(value, (int, float)):
        return f"{value:g}" if isinstance(value, float) else str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_val(v) for v in value) + "]"
    if isinstance(value, dict) and "range" in value:
        lo, hi = value["range"]
        return f"{_val(lo)}..{_val(hi)}"
    return _q(value)


def _attr(lines: list, indent: str, key: str, value, repeat: bool = False):
    """Emit `key: value;` — or one line per item when repeat=True."""
    if value is None or value == "" or value == []:
        return
    if repeat and isinstance(value, list):
        for item in value:
            lines.append(f"{indent}{key}: {_val(item)};")
    else:
        lines.append(f"{indent}{key}: {_val(value)};")


def _emit_comments(b: list, indent: str, node, key: str = "_comments"):
    for line in (node.get(key) or []) if isinstance(node, dict) else []:
        b.append(f"{indent}// {line}")


def serialize_book(book: dict) -> str:
    b = []
    _emit_comments(b, "", book)
    b.append(f"book {_q(book.get('title') or t('seed.book'))} {{")
    ind = "  "
    _emit_comments(b, ind, book, "_inner_comments")

    for key, value in (book.get("settings") or {}).items():
        _attr(b, ind, key, value)

    world = book.get("world")
    if world:
        b.append("")
        _emit_comments(b, ind, world)
        b.append(f"{ind}world {_q(world.get('name') or t('seed.world'))} {{")
        _emit_comments(b, ind + "  ", world, "_inner_comments")
        for rule in world.get("rules") or []:
            _emit_comments(b, ind + "  ", rule)
            b.append(f"{ind}  rule {_q(rule.get('name') or t('seed.rule'))} {{")
            _attr(b, ind + "    ", "description", rule.get("description"))
            _attr(b, ind + "    ", "severity", rule.get("severity") or "soft")
            b.append(f"{ind}  }}")
        b.append(f"{ind}}}")

    for c in book.get("characters") or []:
        b.append("")
        _emit_comments(b, ind, c)
        b.append(f"{ind}character {_q(c.get('name') or t('seed.character'))} {{")
        _attr(b, ind + "  ", "age", c.get("age"))
        _attr(b, ind + "  ", "traits", [t for t in (c.get("traits") or []) if t])
        _attr(b, ind + "  ", "arc", c.get("arc"))
        _attr(b, ind + "  ", "appearance", c.get("appearance"))
        _attr(b, ind + "  ", "voice", c.get("voice"))
        _attr(b, ind + "  ", "wants", c.get("wants"))
        _attr(b, ind + "  ", "fears", c.get("fears"))
        _attr(b, ind + "  ", "secret", c.get("secret"))
        _attr(b, ind + "  ", "never", [x for x in (c.get("never") or []) if x], repeat=True)
        _attr(b, ind + "  ", "must_resolve", [x for x in (c.get("must_resolve") or []) if x], repeat=True)
        _attr(b, ind + "  ", "notes", c.get("notes"))
        b.append(f"{ind}}}")

    for loc in book.get("locations") or []:
        b.append("")
        _emit_comments(b, ind, loc)
        b.append(f"{ind}location {_q(loc.get('name') or t('seed.location'))} {{")
        _attr(b, ind + "  ", "description", loc.get("description"))
        for sense in ("smell", "sound", "touch", "taste", "sight"):
            _attr(b, ind + "  ", sense, loc.get(sense))
        b.append(f"{ind}}}")

    for th in book.get("threads") or []:
        b.append("")
        _emit_comments(b, ind, th)
        b.append(f"{ind}thread {_q(th.get('name') or t('seed.thread'))} {{")
        _attr(b, ind + "  ", "description", th.get("description"))
        b.append(f"{ind}}}")

    for ai, act in enumerate(book.get("acts") or [], 1):
        b.append("")
        _emit_comments(b, ind, act)
        number = act.get("number") or ai
        title = f" {_q(act['title'])}" if act.get("title") else ""
        b.append(f"{ind}act {number}{title} {{")
        for ci, chapter in enumerate(act.get("chapters") or [], 1):
            cnum = chapter.get("number") or ci
            _emit_comments(b, ind + "  ", chapter)
            b.append(f"{ind}  chapter {cnum} {_q(chapter.get('title') or t('seed.chapter'))} {{")
            for scene in chapter.get("scenes") or []:
                _serialize_scene(b, ind + "    ", scene)
            b.append(f"{ind}  }}")
        b.append(f"{ind}}}")

    b.append("}")
    return "\n".join(b) + "\n"


def _serialize_scene(b: list, ind: str, scene: dict):
    _emit_comments(b, ind, scene)
    b.append(f"{ind}scene {_q(scene.get('name') or t('seed.scene'))} {{")
    i = ind + "  "
    _attr(b, i, "pov", scene.get("pov"))
    _attr(b, i, "cast", [x for x in (scene.get("cast") or []) if x])
    _attr(b, i, "kill", [x for x in (scene.get("kill") or []) if x], repeat=True)
    _attr(b, i, "threads", [x for x in (scene.get("threads") or []) if x])
    _attr(b, i, "location", scene.get("location"))
    _attr(b, i, "time", scene.get("time"))
    _attr(b, i, "tension", [t for t in (scene.get("tension") or []) if isinstance(t, (int, float))])
    length = scene.get("length")
    rng = length.get("range") if isinstance(length, dict) else None
    if rng and len(rng) == 2 and all(isinstance(x, (int, float)) for x in rng):
        _attr(b, i, "length", {"range": rng})
    variants = scene.get("variants")
    if variants and int(variants) > 1:
        _attr(b, i, "variants", int(variants))
    _attr(b, i, "style_hints", [x for x in (scene.get("style_hints") or []) if x])
    _attr(b, i, "show", [x for x in (scene.get("show") or []) if x], repeat=True)
    _attr(b, i, "forbid", [x for x in (scene.get("forbid") or []) if x], repeat=True)
    b.append(f"{ind}}}")
