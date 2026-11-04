"""Compiler: Fabula AST -> project graph (JSON) + machine-readable diagnostics.

AI-first design decision: the primary output is a structured JSON graph and a
list of typed diagnostics with stable codes, so a writing agent can parse the
result and repair the source without reading human prose.

Diagnostic codes:
    FAB0xx  lexer/parser errors (raised as FabulaSyntaxError)
    FAB101  scene POV references an unknown character        (error)
    FAB102  scene has no 'show' beat                          (warning)
    FAB103  invalid length range                              (error)
    FAB104  duplicate character name                          (error)
    FAB105  tension value outside [0, 1]                      (warning)
    FAB106  unknown block kind                                 (warning)
    FAB107  cast/kill references an unknown character          (error)
    FAB108  dead character appears in a later scene            (error)
    FAB109  scene references an undeclared thread              (error)
    FAB110  duplicate location/thread name                     (error)
    FAB111  unknown literary form                              (error)
    FAB112  invalid target word budget                         (error)
    FAB201  book has no world block                            (warning)
    FAB202  hard rule without description                      (warning)
    FAB203  declared thread is never advanced by any scene     (warning)
    FAB204  thread is silent for too many consecutive scenes   (warning)
    FAB205  target outside total scene length range             (warning)
"""

from __future__ import annotations

from .i18n import t
from .lexer import FabulaSyntaxError
from .parser import Block, parse


def _listify(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


class Compiler:
    def __init__(self):
        self.diagnostics: list[dict] = []

    def diag(self, code: str, severity: str, message: str, block: Block, hint: str = ""):
        self.diagnostics.append({
            "code": code,
            "severity": severity,
            "message": message,
            "line": block.line,
            "col": block.col,
            "hint": hint,
        })

    def compile(self, source: str) -> dict:
        try:
            blocks = parse(source)
        except FabulaSyntaxError as e:
            return {"book": None, "diagnostics": [e.to_diagnostic()], "ok": False}

        book = None
        for block in blocks:
            if block.kind == "book":
                book = self.compile_book(block)
            else:
                self.diag("FAB106", "warning", t("diag.FAB106_top.m", kind=block.kind), block)

        if book is None:
            self.diagnostics.append({
                "code": "FAB100", "severity": "error",
                "message": t("diag.FAB100.m"), "line": 1, "col": 1,
                "hint": t("diag.FAB100.h"),
            })

        ok = not any(d["severity"] == "error" for d in self.diagnostics)
        return {"book": book, "diagnostics": self.diagnostics, "ok": ok}

    def compile_book(self, block: Block) -> dict:
        book = {
            "title": block.name or "Untitled",
            "_comments": block.lead_comments,
            "_inner_comments": block.inner_comments,
            "settings": {k: v for k, v in block.attrs.items()},
            "world": None,
            "characters": [],
            "locations": [],
            "threads": [],
            "acts": [],
        }

        for child in block.children:
            if child.kind == "world":
                book["world"] = self.compile_world(child)
            elif child.kind == "character":
                book["characters"].append(self.compile_character(child))
            elif child.kind == "location":
                book["locations"].append(self.compile_location(child))
            elif child.kind == "thread":
                book["threads"].append(self.compile_thread(child))
            elif child.kind == "act":
                book["acts"].append(self.compile_act(child))
            else:
                self.diag("FAB106", "warning", t("diag.FAB106.m", kind=child.kind, where="book"), child)

        if book["world"] is None:
            self.diag("FAB201", "warning", t("diag.FAB201.m"), block, t("diag.FAB201.h"))

        seen = set()
        for character in book["characters"]:
            if character["name"] in seen:
                self.diagnostics.append({
                    "code": "FAB104", "severity": "error",
                    "message": t("diag.FAB104.m", name=character["name"]),
                    "line": character["_line"], "col": 1, "hint": t("diag.FAB104.h"),
                })
            seen.add(character["name"])

        for kind in ("locations", "threads"):
            names = set()
            for item in book[kind]:
                if item["name"] in names:
                    self.diagnostics.append({
                        "code": "FAB110", "severity": "error",
                        "message": t("diag.FAB110.m", kind=kind[:-1], name=item["name"]),
                        "line": item["_line"], "col": 1, "hint": t("diag.FAB110.h"),
                    })
                names.add(item["name"])

        self.validate_scenes(book)
        self.validate_threads(book)
        from .forms import FORMS, budget_conflict
        form = book["settings"].get("form")
        if form and form not in FORMS:
            self.diag("FAB111", "error", t("forms.invalid", form=form), block)
        words = book["settings"].get("target_words")
        if words is not None and (type(words) is not int or words <= 0):
            self.diag("FAB112", "error", t("forms.invalid_words"), block)
        conflict = budget_conflict(book)
        if conflict:
            self.diag("FAB205", "warning", t("forms.budget_conflict", **conflict), block,
                      t("forms.budget_hint"))
        return book

    def compile_world(self, block: Block) -> dict:
        rules = []
        for child in block.children:
            if child.kind != "rule":
                self.diag("FAB106", "warning", t("diag.FAB106.m", kind=child.kind, where="world"), child)
                continue
            rule = {
                "name": child.name,
                "description": child.attrs.get("description", ""),
                "severity": child.attrs.get("severity", "soft"),
                "_comments": child.lead_comments,
            }
            if rule["severity"] == "hard" and not rule["description"]:
                self.diag("FAB202", "warning", t("diag.FAB202.m", rule=rule["name"]), child)
            rules.append(rule)
        return {"name": block.name, "rules": rules,
                "_comments": block.lead_comments, "_inner_comments": block.inner_comments,
                **{k: v for k, v in block.attrs.items()}}

    def compile_character(self, block: Block) -> dict:
        return {
            "name": block.name,
            "traits": _listify(block.attrs.get("traits")),
            "arc": block.attrs.get("arc"),
            "never": _listify(block.attrs.get("never")),
            "must_resolve": _listify(block.attrs.get("must_resolve")),
            "notes": block.attrs.get("notes"),
            "age": block.attrs.get("age"),
            # questionnaire fields: cheap structure that keeps prose specific
            "appearance": block.attrs.get("appearance"),
            "voice": block.attrs.get("voice"),        # speech habits, pet phrases
            "wants": block.attrs.get("wants"),
            "fears": block.attrs.get("fears"),
            "secret": block.attrs.get("secret"),
            "_comments": block.lead_comments,
            "_line": block.line,
        }

    _SENSES = ("smell", "sound", "touch", "taste", "sight")

    def compile_location(self, block: Block) -> dict:
        loc = {
            "name": block.name,
            "description": block.attrs.get("description"),
            "_comments": block.lead_comments,
            "_line": block.line,
        }
        for sense in self._SENSES:
            loc[sense] = block.attrs.get(sense)
        return loc

    def compile_thread(self, block: Block) -> dict:
        return {
            "name": block.name,
            "description": block.attrs.get("description"),
            "_comments": block.lead_comments,
            "_line": block.line,
        }

    def compile_act(self, block: Block) -> dict:
        act = {"number": block.number, "title": block.name,
               "_comments": block.lead_comments, "chapters": []}
        for child in block.children:
            if child.kind == "chapter":
                act["chapters"].append(self.compile_chapter(child))
            else:
                self.diag("FAB106", "warning", t("diag.FAB106.m", kind=child.kind, where="act"), child)
        return act

    def compile_chapter(self, block: Block) -> dict:
        chapter = {"number": block.number, "title": block.name,
                   "_comments": block.lead_comments, "scenes": []}
        for child in block.children:
            if child.kind == "scene":
                chapter["scenes"].append(self.compile_scene(child))
            else:
                self.diag("FAB106", "warning", t("diag.FAB106.m", kind=child.kind, where="chapter"), child)
        return chapter

    def compile_scene(self, block: Block) -> dict:
        attrs = block.attrs
        scene = {
            "name": block.name,
            "pov": attrs.get("pov"),
            "location": attrs.get("location"),
            "time": attrs.get("time"),
            "tension": _listify(attrs.get("tension")),
            "length": attrs.get("length"),          # {"range": [min, max]} in words
            "variants": attrs.get("variants", 1),
            "show": _listify(attrs.get("show")),     # beats that MUST happen on page
            "forbid": _listify(attrs.get("forbid")), # scene-local hard bans
            "style_hints": _listify(attrs.get("style_hints")),
            "cast": _listify(attrs.get("cast")),     # characters on page besides POV
            "kill": _listify(attrs.get("kill")),     # characters who die in this scene
            "threads": _listify(attrs.get("threads")),  # plot threads this scene advances
            "_comments": block.lead_comments,
            "_line": block.line,
        }
        return scene

    def validate_scenes(self, book: dict):
        names = {c["name"] for c in book["characters"]}
        dead: dict[str, str] = {}  # name -> scene where they died
        for act in book["acts"]:
            for chapter in act["chapters"]:
                for scene in chapter["scenes"]:
                    loc = {"line": scene["_line"], "col": 1}
                    for name in scene["cast"] + scene["kill"]:
                        if name not in names:
                            self.diagnostics.append({
                                "code": "FAB107", "severity": "error",
                                "message": t("diag.FAB107.m", scene=scene["name"], name=name),
                                **loc, "hint": t("diag.FAB107.h", names=sorted(names)),
                            })
                    present = [n for n in [scene["pov"], *scene["cast"]] if n]
                    for name in present:
                        if name in dead:
                            self.diagnostics.append({
                                "code": "FAB108", "severity": "error",
                                "message": t("diag.FAB108.m", scene=scene["name"], name=name, dead_in=dead[name]),
                                **loc, "hint": t("diag.FAB108.h"),
                            })
                    for name in scene["kill"]:
                        dead.setdefault(name, scene["name"])
                    if scene["pov"] and scene["pov"] not in names:
                        self.diagnostics.append({
                            "code": "FAB101", "severity": "error",
                            "message": t("diag.FAB101.m", scene=scene["name"], pov=scene["pov"]),
                            **loc, "hint": t("diag.FAB101.h", names=sorted(names)),
                        })
                    if not scene["show"]:
                        self.diagnostics.append({
                            "code": "FAB102", "severity": "warning",
                            "message": t("diag.FAB102.m", scene=scene["name"]),
                            **loc, "hint": t("diag.FAB102.h"),
                        })
                    length = scene["length"]
                    if length is not None:
                        rng = length.get("range") if isinstance(length, dict) else None
                        if not rng or rng[0] <= 0 or rng[1] < rng[0]:
                            self.diagnostics.append({
                                "code": "FAB103", "severity": "error",
                                "message": t("diag.FAB103.m", scene=scene["name"], length=repr(length)),
                                **loc, "hint": t("diag.FAB103.h"),
                            })
                    for value in scene["tension"]:
                        if not isinstance(value, (int, float)) or not (0 <= value <= 1):
                            self.diagnostics.append({
                                "code": "FAB105", "severity": "warning",
                                "message": t("diag.FAB105.m", scene=scene["name"], value=repr(value)),
                                **loc, "hint": t("diag.FAB105.h"),
                            })

    def validate_threads(self, book: dict):
        declared = {th["name"]: th for th in book["threads"]}
        scenes = [scene for _, _, scene in iter_scenes(book)]
        mentions: dict[str, list[int]] = {name: [] for name in declared}
        for idx, scene in enumerate(scenes):
            for name in scene["threads"]:
                if name not in declared:
                    self.diagnostics.append({
                        "code": "FAB109", "severity": "error",
                        "message": t("diag.FAB109.m", scene=scene["name"], name=name),
                        "line": scene["_line"], "col": 1,
                        "hint": t("diag.FAB109.h", names=sorted(declared)),
                    })
                else:
                    mentions[name].append(idx)
        try:
            gap_limit = max(2, int(book["settings"].get("thread_gap") or 7))
        except (TypeError, ValueError):
            gap_limit = 7
        for name, th in declared.items():
            hits = mentions[name]
            if not hits:
                self.diagnostics.append({
                    "code": "FAB203", "severity": "warning",
                    "message": t("diag.FAB203.m", name=name),
                    "line": th["_line"], "col": 1, "hint": t("diag.FAB203.h"),
                })
                continue
            # silence measured between the thread's first and last on-page beats
            for a, b in zip(hits, hits[1:]):
                if b - a - 1 > gap_limit:
                    self.diagnostics.append({
                        "code": "FAB204", "severity": "warning",
                        "message": t("diag.FAB204.m", name=name, n=b - a - 1,
                                     a=scenes[a]["name"], b=scenes[b]["name"]),
                        "line": th["_line"], "col": 1,
                        "hint": t("diag.FAB204.h", limit=gap_limit),
                    })


def compile_source(source: str) -> dict:
    return Compiler().compile(source)


def iter_scenes(book: dict):
    """Yield (act, chapter, scene) triples in narrative order."""
    for act in book["acts"]:
        for chapter in act["chapters"]:
            for scene in chapter["scenes"]:
                yield act, chapter, scene
