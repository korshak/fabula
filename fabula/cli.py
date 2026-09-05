"""CLI: compile / plan / prompt / write / fix / translate / manuscript / serve.

Books live in the library — one folder per book under books/ — so every
command takes a book NAME; an explicit path to a .fab still works.

    python3 -m fabula compile my-book            # JSON graph + diagnostics
    python3 -m fabula plan my-book               # human-readable outline
    python3 -m fabula prompt my-book -s "Артефакт"  # exact agent prompts
    python3 -m fabula write my-book -s "Артефакт"   # generate via Claude API
    python3 -m fabula fix my-book                # agent repairs the source
    python3 -m fabula translate my-book --to en  # style-preserving translation
    python3 -m fabula manuscript my-book         # assemble approved scenes
    python3 -m fabula serve                      # web UI on the whole library
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import library
from .compiler import compile_source, iter_scenes
from .i18n import set_lang, t
from .prompts import book_bible, character_ledger, previous_context, scene_prompt
from .providers import load_registry
from .state import ProjectState


def _load(path: str) -> dict:
    """Compile a book from disk, or bow out with a sentence.

    A mistyped book name is the most ordinary mistake there is, and it used to
    surface as a bare Python traceback — the first thing a newcomer saw.
    """
    try:
        source = Path(path).read_text(encoding="utf-8")
    except OSError:
        raise SystemExit(t("server.no_such_file", path=str(path)))
    return compile_source(source)


def _print_diagnostics(result: dict):
    for d in result["diagnostics"]:
        print(f"[{d['severity']}] {d['code']} {d['line']}:{d['col']} — {d['message']}"
              + (f"  (hint: {d['hint']})" if d.get("hint") else ""), file=sys.stderr)


def _build_dir(args) -> Path:
    """The book's own build/ unless --out says otherwise."""
    return Path(args.out) if args.out else library.build_dir(args.file)


def cmd_compile(args):
    result = _load(args.file)
    if args.out_file:
        Path(args.out_file).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        print(t("cli.wrote", path=args.out_file), file=sys.stderr)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    _print_diagnostics(result)
    return 0 if result["ok"] else 1


def cmd_plan(args):
    result = _load(args.file)
    _print_diagnostics(result)
    if not result["book"]:
        return 1
    book = result["book"]
    from .i18n import tn
    print(t("cli.book", title=book["title"]))
    if book["world"]:
        hard = sum(1 for r in book["world"]["rules"] if r["severity"] == "hard")
        print(t("cli.world", name=book["world"]["name"],
                rules=tn("ui.builder.rules_n", len(book["world"]["rules"])), hard=hard))
    print(t("cli.characters", list=", ".join(c["name"] for c in book["characters"])))
    for act, chapter, scene in iter_scenes(book):
        rng = scene["length"]["range"] if scene["length"] else None
        size = t("cli.size_words", a=rng[0], b=rng[1]) if rng else t("cli.size_free")
        print(t("cli.scene_line", act=act["number"], ch=chapter["number"], title=chapter["title"],
                scene=scene["name"], pov=scene["pov"], size=size,
                v=scene["variants"], beats=len(scene["show"])))
    return 0 if result["ok"] else 1


def cmd_prompt(args):
    result = _load(args.file)
    _print_diagnostics(result)
    if not result["ok"]:
        return 1
    state = ProjectState(_build_dir(args))
    meta = state.scenes_meta()
    memory = previous_context(result["book"], args.scene, meta)
    ledger = character_ledger(result["book"], args.scene, meta)
    print("=== SYSTEM ===\n")
    print(book_bible(result["book"]))
    print("\n=== USER ===\n")
    print(scene_prompt(result["book"], args.scene, memory, ledger))
    return 0


def _make_agent(args, book):
    from .agent import WriterAgent
    from .providers import ProviderError
    registry = load_registry(Path(args.file))
    try:
        provider = registry.get(getattr(args, "model", None))
    except ProviderError as e:
        raise SystemExit(str(e))
    st = provider.status()
    if not st["available"]:
        raise SystemExit(t("server.unavailable", name=provider.name, reason=st["reason"]))
    if st["reason"]:
        print(t("cli.provider_note", name=provider.name, reason=st["reason"]), file=sys.stderr)
    return WriterAgent(book, provider)


def cmd_write(args):
    result = _load(args.file)
    _print_diagnostics(result)
    if not result["ok"]:
        print(t("cli.refusing_write"), file=sys.stderr)
        return 1
    agent = _make_agent(args, result["book"])
    state = ProjectState(_build_dir(args))
    scenes = [args.scene] if args.scene else [s["name"] for _, _, s in iter_scenes(result["book"])]
    for name in scenes:
        agent.write_scene(name, state)
    print(t("cli.done", dir=f"{state.build_dir}/scenes/"))
    return 0


def cmd_fix(args):
    path = Path(args.file)
    source = path.read_text(encoding="utf-8")
    result = compile_source(source)
    if not result["diagnostics"]:
        print(t("cli.fix_clean"))
        return 0
    _print_diagnostics(result)
    agent = _make_agent(args, result["book"] or {"title": "?", "settings": {}, "world": None,
                                                 "characters": [], "acts": []})
    fixed = agent.fix_source(source, result["diagnostics"])
    recheck = compile_source(fixed)
    if not recheck["ok"]:
        print(t("cli.fix_still_broken"), file=sys.stderr)
        _print_diagnostics(recheck)
        return 1
    if args.dry_run:
        print(fixed)
    else:
        path.write_text(fixed, encoding="utf-8")
        print(t("cli.fix_done", path=path, n=len(recheck["diagnostics"])))
    return 0


def cmd_translate(args):
    """Translate the approved scenes into another language, style preserved."""
    result = _load(args.file)
    _print_diagnostics(result)
    if not result["ok"]:
        print(t("cli.refusing_write"), file=sys.stderr)
        return 1
    from .translator import TranslationState, Translator, language_name, valid_lang
    book = result["book"]
    lang = str(args.to or "").strip().lower()
    if not valid_lang(lang):
        raise SystemExit(t("server.bad_lang", lang=lang))
    if lang == str((book.get("settings") or {}).get("language") or "uk"):
        raise SystemExit(t("server.same_lang", lang=lang))
    agent = _make_agent(args, book)  # validates the provider the same way
    translator = Translator(book, agent.provider, lang)
    tstate = TranslationState(_build_dir(args), lang)
    try:
        report = translator.translate_book(ProjectState(_build_dir(args)), tstate,
                                           force=args.force)
    except ValueError as e:
        raise SystemExit(str(e))
    print(t("cli.translate_report", lang=language_name(lang),
            n=len(report["translated"]), s=len(report["skipped"]),
            m=len(report["missing"]), dir=str(tstate.dir)))
    return 0


def _translation_state(args, book):
    """--translation LANG on manuscript/epub: the state, or an exit error."""
    lang = str(getattr(args, "translation", None) or "").strip().lower()
    if not lang:
        return None
    from .translator import TranslationState, valid_lang
    if not valid_lang(lang):
        raise SystemExit(t("server.bad_lang", lang=lang))
    tstate = TranslationState(_build_dir(args), lang)
    if not tstate.path.exists():
        raise SystemExit(t("server.no_translation", lang=lang))
    return tstate


def cmd_manuscript(args):
    result = _load(args.file)
    if not result["book"]:
        _print_diagnostics(result)
        return 1
    from .prompts import _p  # chapter headings follow the BOOK's language
    state = ProjectState(_build_dir(args))
    book = result["book"]
    tstate = _translation_state(args, book)
    if tstate:
        from .translator import translated_manuscript
        ms = translated_manuscript(book, state, tstate)
        output = f"# {ms['title']}\n\n{ms['text']}"
        missing = ms["missing"]
    else:
        parts = [f"# {book['title']}\n"]
        missing = []
        current_chapter = None
        for act, chapter, scene in iter_scenes(book):
            text = state.chosen_text(scene["name"])
            key = (act["number"], chapter["number"])
            if text and key != current_chapter:
                parts.append("\n## " + _p(book, "chapter_heading",
                                          n=chapter["number"], title=chapter["title"]) + "\n")
                current_chapter = key
            if text:
                parts.append(text.strip() + "\n")
            else:
                missing.append(scene["name"])
        output = "\n".join(parts)
    if args.out_file:
        Path(args.out_file).write_text(output, encoding="utf-8")
        print(t("cli.wrote", path=args.out_file), file=sys.stderr)
    else:
        print(output)
    if missing:
        print(t("cli.missing_scenes", list=", ".join(missing)), file=sys.stderr)
    return 0


def cmd_models(args):
    registry = load_registry(Path(args.file))
    src = registry.source or t("cli.models_builtin")
    print(t("cli.models_config", src=src))
    for info in registry.describe():
        mark = "→" if info["default"] else " "
        ok = t("cli.status_ok") if info["available"] else t("cli.status_unavailable")
        note = f" — {info['reason']}" if info["reason"] else ""
        print(f" {mark} {info['name']:<12} {info['type']:<10} {info['model'] or '-':<22} {ok}{note}")
    return 0


def cmd_init(args):
    """Scaffold a new book project: starter book.fab + models.json.

    With --from-notes the agent drafts the WHOLE book graph from the author's
    raw notes instead of the starter seed.
    """
    from .scaffold import starter_book, write_project

    target = library.book_dir(args.directory)  # a bare name lands in books/
    book_path = target / "book.fab"
    # books in the library are addressed by name, anything else by path
    handle = target.name if target.parent == library.root() else book_path
    if getattr(args, "from_notes", None):
        if book_path.exists():
            raise SystemExit(t("server.project_exists", path=str(book_path)))
        notes_path = Path(args.from_notes)
        if not notes_path.exists():
            raise SystemExit(t("cli.no_notes_file", path=str(notes_path)))
        notes = notes_path.read_text(encoding="utf-8").strip()
        if not notes:
            raise SystemExit(t("server.no_notes"))
        from .draft import draft_book
        from .i18n import resolve_lang
        from .providers import ProviderError
        registry = load_registry(book_path)
        try:
            provider = registry.get(getattr(args, "model", None))
        except ProviderError as e:
            raise SystemExit(str(e))
        st = provider.status()
        if not st["available"]:
            raise SystemExit(t("server.unavailable", name=provider.name, reason=st["reason"]))
        try:
            book = draft_book(notes, resolve_lang(args.lang), provider,
                              on_progress=lambda m: print(m, file=sys.stderr))
        except ValueError as e:
            raise SystemExit(str(e))
        for path in write_project(target, book):
            print(t("cli.wrote", path=path))
        print(t("cli.init_next", book=handle))
        return 0
    try:
        starter = starter_book(args.lang, getattr(args, "template", None))
    except ValueError as e:
        raise SystemExit(str(e))
    if book_path.exists():
        print(t("cli.init_exists", path=book_path), file=sys.stderr)
    for path in write_project(target, starter):
        print(t("cli.wrote", path=path))
    print(t("cli.init_next", book=handle))
    return 0


def cmd_epub(args):
    from .epub import build_epub
    result = _load(args.file)
    if not result["book"]:
        _print_diagnostics(result)
        return 1
    tstate = _translation_state(args, result["book"])
    data = build_epub(result["book"], ProjectState(_build_dir(args)), translation=tstate)
    if not data:
        print(t("server.empty_manuscript"), file=sys.stderr)
        return 1
    if tstate:
        from .translator import translated_title
        title = translated_title(result["book"], tstate)
    else:
        title = result["book"]["title"]
    out = args.out_file or (title or "book").replace(" ", "_") + ".epub"
    Path(out).write_bytes(data)
    print(t("cli.wrote", path=out))
    return 0


def cmd_templates(args):
    from .templates import list_templates
    for tpl in list_templates(args.lang):
        scenes = sum(len(a.get("scenes") or []) for a in tpl["acts"])
        print(f"  {tpl['id']:<15} {tpl['name']} — {tpl['desc']} "
              f"({t('cli.template_size', n=scenes, a=len(tpl['acts']))})")
    return 0


def cmd_serve(args):
    from .server import serve
    return serve(Path(args.file), build_dir=_build_dir(args), port=args.port)


def _resolve_book(args, parser):
    """Turn a book name into its .fab path; `serve` may omit it entirely."""
    if not args.file:
        default = library.default_book()
        if not default:
            parser.error(t("cli.empty_library", dir=str(library.root())))
        args.file = str(default)
    else:
        args.file = str(library.book_file(args.file))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="fabula", description="AI-first DSL for book-writing agents")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, scene=False, file_optional=False):
        p.add_argument("file", nargs="?" if file_optional else None,
                       help="book name in books/, or a path to a .fab file")
        p.add_argument("--out", help="build directory (default: build/ inside the book folder)")
        p.add_argument("--lang", help="interface language (a locale from fabula/locales/; "
                                      "default: FABULA_LANG env or uk)")
        if scene:
            p.add_argument("-s", "--scene")

    p = sub.add_parser("init", help="scaffold a new book project (book.fab + models.json)")
    p.add_argument("directory", help="book name (created in books/) or a path")
    p.add_argument("--lang", help="interface/starter language (default: FABULA_LANG env, else the system locale)")
    p.add_argument("--template", help="plot template id (see `fabula templates`)")
    p.add_argument("--from-notes", metavar="FILE",
                   help="draft the whole book from a file with raw author notes "
                        "(the agent fills every field; needs a working model provider)")
    p.add_argument("-m", "--model", help="provider profile for --from-notes")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("templates", help="list available plot templates")
    p.add_argument("--lang", help="language of names and stage beats")
    p.set_defaults(func=cmd_templates)

    p = sub.add_parser("compile", help="compile .fab to JSON graph + diagnostics")
    common(p)
    p.add_argument("-o", "--out-file", help="write JSON to a file instead of stdout")
    p.set_defaults(func=cmd_compile)

    p = sub.add_parser("plan", help="human-readable outline")
    common(p)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("prompt", help="show the exact prompts the agent would get")
    common(p, scene=True)
    p.set_defaults(func=cmd_prompt)
    p.set_defaults(scene_required=True)

    p = sub.add_parser("write", help="generate scene variants via a model provider")
    common(p, scene=True)
    p.add_argument("-m", "--model", help="provider profile from models.json (default: its 'default')")
    p.set_defaults(func=cmd_write)

    p = sub.add_parser("fix", help="let the agent repair the source from diagnostics")
    common(p)
    p.add_argument("-m", "--model", help="provider profile from models.json")
    p.add_argument("--dry-run", action="store_true", help="print the fix instead of writing it")
    p.set_defaults(func=cmd_fix)

    p = sub.add_parser("models", help="list configured model providers and their status")
    common(p)
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("translate", help="translate approved scenes into another language, "
                                         "keeping the author's style")
    common(p)
    p.add_argument("--to", required=True, metavar="LANG",
                   help="target language code, e.g. en, de, pl")
    p.add_argument("-m", "--model", help="provider profile from models.json")
    p.add_argument("--force", action="store_true",
                   help="retranslate every scene, ignoring the cache and glossary")
    p.set_defaults(func=cmd_translate)

    p = sub.add_parser("manuscript", help="assemble approved scenes into one markdown file")
    common(p)
    p.add_argument("-o", "--out-file")
    p.add_argument("--translation", metavar="LANG",
                   help="assemble a translation made by `fabula translate` instead of the original")
    p.set_defaults(func=cmd_manuscript)

    p = sub.add_parser("epub", help="export approved scenes as an EPUB book")
    common(p)
    p.add_argument("-o", "--out-file")
    p.add_argument("--translation", metavar="LANG",
                   help="export a translation made by `fabula translate` instead of the original")
    p.set_defaults(func=cmd_epub)

    p = sub.add_parser("serve", help="run the web UI")
    common(p, file_optional=True)
    p.add_argument("--port", type=int, default=8787)
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    set_lang(getattr(args, "lang", None))
    if hasattr(args, "file"):
        _resolve_book(args, parser)
    if getattr(args, "scene_required", False) and not args.scene:
        parser.error("-s/--scene is required for this command")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
