"""Local web UI for a Fabula project.

Zero dependencies beyond the stdlib (the anthropic SDK is imported lazily and
only for generation). JSON API + a single-page UI served from fabula/web/.

Long API calls (scene generation, source repair) run as background jobs the
UI polls via /api/jobs.
"""

from __future__ import annotations

import datetime
import json
import os
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import i18n, library
from .compiler import compile_source, iter_scenes
from .epub import build_epub
from .i18n import t
from .prompts import (book_bible, character_ledger, comments_instruction,
                      previous_context, scene_prompt)
from .providers import ProviderError, load_registry
from .serializer import serialize_book
from .state import ProjectState
from .translator import (TranslationState, Translator, list_translations,
                         translated_manuscript, translation_status, valid_lang)

WEB_DIR = Path(__file__).parent / "web"
# Optional, never written by Fabula: notes an author keeps next to their book.
# The advisor reads the first one it finds to size the book against them.
NOTES_FILES = ("Нотатки.md", "notes.md", "NOTES.md")


# Settings the draft dialog may carry over from the advisor. Anything else a
# client sends is dropped rather than written into a fresh book.
ADVISED_SETTINGS = {"thread_gap": int, "target_words": int, "revisions": int,
                    "language": str}


def _advised_settings(raw) -> dict:
    out = {}
    for key, cast in ADVISED_SETTINGS.items():
        if not isinstance(raw, dict) or raw.get(key) is None:
            continue
        try:
            value = cast(raw[key])
        except (TypeError, ValueError):
            continue
        if cast is int and value <= 0:
            continue
        out[key] = value
    return out


class Project:
    def __init__(self, file: Path, build_dir: Path):
        self.file = file
        self.build_dir = build_dir
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}

    # --- source / compile ---

    def read_source(self) -> str:
        return self.file.read_text(encoding="utf-8")

    def write_source(self, source: str):
        self.file.write_text(source, encoding="utf-8")

    def compiled(self, source: str | None = None) -> dict:
        return compile_source(source if source is not None else self.read_source())

    def notes(self) -> str:
        """The author's raw notes kept beside the book, if they kept any.

        Purely optional and never written to: the advisor reads it to size a
        book against the material it came from.
        """
        for name in NOTES_FILES:
            path = self.file.parent / name
            if path.is_file():
                return path.read_text(encoding="utf-8")
        return ""

    def state(self) -> ProjectState:
        return ProjectState(self.build_dir)

    # --- providers ---

    def registry(self):
        # reloaded on demand so edits to models.json apply without a restart
        return load_registry(self.file)

    # --- jobs ---

    def start_job(self, kind: str, target, scene: str | None = None) -> dict:
        lang = i18n.resolve_lang()  # captured from the request; threads don't inherit it
        with self.lock:
            for job in self.jobs.values():
                if job["status"] == "running" and (kind, scene) == (job["kind"], job["scene"]):
                    return job
            job = {"id": uuid.uuid4().hex[:8], "kind": kind, "scene": scene,
                   "status": "running", "progress": [], "error": None, "result": None}
            self.jobs[job["id"]] = job

        def progress(msg: str):
            job["progress"].append(msg)

        def run():
            i18n.set_lang(lang)
            try:
                job["result"] = target(progress)
                job["status"] = "done"
            except Exception as e:  # surfaced to the UI verbatim
                traceback.print_exc()
                job["error"] = f"{type(e).__name__}: {e}"
                job["status"] = "error"

        threading.Thread(target=run, daemon=True).start()
        return job

    def make_agent(self, book: dict, progress, provider_name: str | None):
        from .agent import WriterAgent
        provider = self.registry().get(provider_name)
        st = provider.status()
        if not st["available"]:
            raise ValueError(t("server.unavailable", name=provider.name, reason=st["reason"]))
        return WriterAgent(book, provider, on_progress=progress)

    def generate_scene(self, scene_name: str, provider_name: str | None = None,
                       instruction: str = "", comments_from: str | None = None) -> dict:
        result = self.compiled()
        if not result["ok"]:
            raise ValueError(t("server.not_compiling"))
        # fail fast on an unusable provider, before the background thread starts
        self.registry().get(provider_name)
        if comments_from:
            anchored = comments_instruction(result["book"],
                                            self.state().comments(scene_name, comments_from))
            instruction = "\n".join(x for x in (instruction, anchored) if x)

        def target(progress):
            agent = self.make_agent(result["book"], progress, provider_name)
            return agent.write_scene(scene_name, self.state(), instruction=instruction)

        return self.start_job("generate", target, scene=scene_name)

    def revise_variant_job(self, scene_name: str, filename: str,
                           provider_name: str | None) -> dict:
        """Targeted revision of one variant from its anchored comments."""
        result = self.compiled()
        if not result["ok"]:
            raise ValueError(t("server.not_compiling"))
        self.registry().get(provider_name)  # fail fast

        def target(progress):
            agent = self.make_agent(result["book"], progress, provider_name)
            return agent.revise_variant(scene_name, filename, self.state())

        return self.start_job("revise", target, scene=scene_name)

    def save_variant_text(self, scene_name: str, filename: str, text: str,
                          provider_name: str | None) -> dict | None:
        """A human edited a variant by hand: persist it and refresh memory if
        the edited variant is the approved one."""
        state = self.state()
        if filename not in state.scene(scene_name)["variants"]:
            raise KeyError(filename)
        path = state.scene_dir(scene_name) / filename
        path.write_text(text, encoding="utf-8")
        state.mark_variant_edited(scene_name, filename)
        entry = state.scene(scene_name)
        if entry.get("approved") == filename or entry.get("summary_of") == filename:
            state.mark_summary_stale(scene_name)
            return self.resummarize_scene(scene_name, filename, provider_name)
        return None

    def draft_project_job(self, directory: str, notes: str, lang: str | None,
                          provider_name: str | None = None,
                          settings: dict | None = None) -> dict:
        """Draft a whole NEW project from the author's raw notes: the model
        fills the entire book graph, then the scaffolder writes it to disk.
        The UI opens the finished project when the job completes.

        `settings` are the advisor's suggestions the author accepted in the
        draft dialog; they are folded in after the model's own choices, so a
        deliberate pick always wins over the model's default."""
        from .scaffold import write_project
        target_dir = library.book_dir(directory)  # a bare name lands in books/
        book_path = target_dir / "book.fab"
        if book_path.exists():
            raise ValueError(t("server.project_exists", path=str(book_path)))
        provider = self.registry().get(provider_name)  # fail fast
        st = provider.status()
        if not st["available"]:
            raise ValueError(t("server.unavailable", name=provider.name, reason=st["reason"]))
        book_lang = str(lang or i18n.resolve_lang() or "uk").strip() or "uk"

        def target(progress):
            from .draft import draft_book
            book = draft_book(notes, book_lang, provider, on_progress=progress)
            book["settings"].update(_advised_settings(settings))
            if book_path.exists():  # re-check: the model call took a while
                raise ValueError(t("server.project_exists", path=str(book_path)))
            write_project(target_dir, book)
            return {"file": str(book_path), "title": book["title"]}

        return self.start_job("draft", target)

    def fix_source_job(self, provider_name: str | None = None) -> dict:
        source = self.read_source()
        result = self.compiled(source)
        if not result["diagnostics"]:
            raise ValueError(t("server.nothing_to_fix"))

        def target(progress):
            book = result["book"] or {"title": "?", "settings": {}, "world": None,
                                      "characters": [], "acts": []}
            agent = self.make_agent(book, progress, provider_name)
            fixed = agent.fix_source(source, result["diagnostics"])
            recheck = compile_source(fixed)
            return {"source": fixed, "ok": recheck["ok"],
                    "diagnostics": recheck["diagnostics"]}

        return self.start_job("fix", target)

    def resummarize_scene(self, scene_name: str, filename: str,
                          provider_name: str | None) -> dict | None:
        """Refresh the scene's memory from the newly approved variant.

        Best-effort: returns the job, or None when generation is unavailable
        (the summary is then left marked stale)."""
        result = self.compiled()
        if not result["ok"]:
            return None
        try:
            provider = self.registry().get(provider_name)
        except ProviderError:
            return None
        if not provider.status()["available"]:
            return None
        text = self.state().variant_text(scene_name, filename)

        def target(progress):
            from .agent import WriterAgent
            agent = WriterAgent(result["book"], provider, on_progress=progress)
            return {"summary": agent.summarize(scene_name, text, self.state(),
                                               source_file=filename)}

        return self.start_job("summarize", target, scene=scene_name)

    # --- translations ---

    def translation(self, lang: str) -> TranslationState:
        if not valid_lang(lang):
            raise ValueError(t("server.bad_lang", lang=str(lang)))
        return TranslationState(self.build_dir, lang)

    def translations_overview(self) -> dict:
        """Status of every started translation, for the manuscript tab."""
        result = self.compiled()
        state = self.state()
        book = result["book"] or {}
        out = []
        if book:
            for lang in list_translations(self.build_dir):
                out.append(translation_status(book, state, self.translation(lang)))
        return {"translations": out,
                "book_lang": str((book.get("settings") or {}).get("language") or "uk")}

    def translate_job(self, lang: str, provider_name: str | None = None,
                      force: bool = False) -> dict:
        result = self.compiled()
        if not result["ok"]:
            raise ValueError(t("server.not_compiling"))
        book_lang = str((result["book"].get("settings") or {}).get("language") or "uk")
        if not valid_lang(lang):
            raise ValueError(t("server.bad_lang", lang=str(lang)))
        if lang == book_lang:
            raise ValueError(t("server.same_lang", lang=lang))
        provider = self.registry().get(provider_name)  # fail fast
        st = provider.status()
        if not st["available"]:
            raise ValueError(t("server.unavailable", name=provider.name, reason=st["reason"]))

        def target(progress):
            translator = Translator(result["book"], provider, lang, on_progress=progress)
            return translator.translate_book(self.state(), self.translation(lang),
                                             force=force)

        # scene=lang: dedups concurrent runs per language, and labels the job log
        return self.start_job("translate", target, scene=lang)

    # --- read models for the UI ---

    def snapshot(self) -> dict:
        source = self.read_source()
        result = self.compiled(source)
        state = self.state()
        scenes = {}
        if result["book"]:
            for act, chapter, scene in iter_scenes(result["book"]):
                entry = state.data["scenes"].get(scene["name"], {})
                scenes[scene["name"]] = {
                    "act": act["number"], "chapter": chapter["number"],
                    "chapter_title": chapter["title"],
                    "variants": entry.get("variants", {}),
                    "approved": entry.get("approved"),
                    "summary": entry.get("summary"),
                    "summary_stale": entry.get("summary_stale", False),
                    "viewpoints": entry.get("viewpoints") or [],
                    "state_changes": entry.get("state_changes") or [],
                }
        registry = self.registry()
        providers = registry.describe()
        return {
            "file": str(self.file),
            "source": source,
            "result": result,
            "scenes": scenes,
            "providers": providers,
            "generation": next(
                ({"available": p["available"], "reason": p["reason"]}
                 for p in providers if p["default"]),
                {"available": False, "reason": t("server.no_profiles")},
            ),
            "jobs": list(self.jobs.values()),
        }

    def stats(self) -> dict:
        """Word counts the statistics tab renders: per scene, per day, totals."""
        result = self.compiled()
        state = self.state()
        scenes = []
        total = 0
        approved = 0
        if result["book"]:
            for act, chapter, scene in iter_scenes(result["book"]):
                entry = state.data["scenes"].get(scene["name"]) or {}
                filename = entry.get("approved")
                variants = entry.get("variants") or {}
                if not filename and len(variants) == 1:
                    filename = next(iter(variants))
                words = (variants.get(filename) or {}).get("words") if filename else None
                if words is None and filename:
                    text = state.chosen_text(scene["name"])
                    words = len(text.split()) if text else 0
                scenes.append({"name": scene["name"], "act": act["number"],
                               "chapter": chapter["number"],
                               "words": words or 0, "approved": bool(entry.get("approved"))})
                total += words or 0
                approved += 1 if entry.get("approved") else 0
        per_day: dict[str, int] = {}
        for rec in state.data.get("history") or []:
            if isinstance(rec, dict) and rec.get("date"):
                per_day[rec["date"]] = per_day.get(rec["date"], 0) + int(rec.get("words") or 0)
        target = None
        if result["book"]:
            try:
                target = int(result["book"]["settings"].get("target_words") or 0) or None
            except (TypeError, ValueError):
                target = None
        return {"total_words": total, "target_words": target, "approved": approved,
                "scenes": scenes,
                "history": [{"date": d, "words": per_day[d]} for d in sorted(per_day)]}

    def manuscript(self, translation: str | None = None) -> dict:
        result = self.compiled()
        if not result["book"]:
            return {"text": "", "missing": [], "title": "?", "chapters": []}
        state = self.state()
        if translation:
            return translated_manuscript(result["book"], state,
                                         self.translation(translation))
        book = result["book"]
        from .prompts import _p  # chapter headings follow the BOOK's language
        parts, missing, chapters = [], [], []
        current_chapter = None
        for act, chapter, scene in iter_scenes(book):
            text = state.chosen_text(scene["name"])
            if text:
                key = (act["number"], chapter["number"])
                if key != current_chapter:
                    heading = _p(book, "chapter_heading",
                                 n=chapter["number"], title=chapter["title"])
                    parts.append(heading + "\n")
                    # the studio reads the manuscript by chapter and scene; `text`
                    # stays the flat prose that copy, .md and EPUB already use
                    chapters.append({"act": act["number"],
                                     "act_heading": _p(book, "act_heading", n=act["number"]),
                                     "heading": heading, "scenes": []})
                    current_chapter = key
                parts.append(text.strip() + "\n")
                chapters[-1]["scenes"].append({
                    "name": scene["name"],
                    "location": scene.get("location") or "",
                    "time": scene.get("time") or "",
                    "text": text.strip(),
                })
            else:
                missing.append(scene["name"])
        return {"title": book["title"], "text": "\n".join(parts),
                "missing": missing, "chapters": chapters}


class ProjectManager:
    """Several book projects behind one server: the library of books (folders
    under books/, see fabula.library), a registry of recently opened files
    (~/.fabula/projects.json) and live Project instances, switchable at runtime.

    Project instances are cached per file, so switching back to a book keeps
    its running jobs and their logs.
    """

    REGISTRY = Path(os.environ.get("FABULA_HOME") or (Path.home() / ".fabula")) / "projects.json"
    KEEP = 20  # most-recent projects remembered

    def __init__(self, file: Path, build_dir: Path):
        self._instances: dict[str, Project] = {}
        self.lock = threading.Lock()
        self.current = self._instance(Path(file).resolve(), Path(build_dir))
        self.remember(self.current)

    def _instance(self, file: Path, build_dir: Path | None = None) -> Project:
        key = str(file)
        with self.lock:
            if key not in self._instances:
                self._instances[key] = Project(file, build_dir or file.parent / "build")
            return self._instances[key]

    # --- registry (plain JSON, best-effort: a broken file never blocks the UI) ---

    def registry(self) -> list[dict]:
        try:
            data = json.loads(self.REGISTRY.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def remember(self, project: Project):
        entries = [e for e in self.registry() if e.get("file") != str(project.file)]
        result = project.compiled()
        title = result["book"]["title"] if result.get("book") else project.file.stem
        entries.insert(0, {"file": str(project.file), "build": str(project.build_dir),
                           "title": title,
                           "last_opened": datetime.datetime.now().isoformat(timespec="seconds")})
        try:
            self.REGISTRY.parent.mkdir(parents=True, exist_ok=True)
            self.REGISTRY.write_text(json.dumps(entries[:self.KEEP], ensure_ascii=False, indent=2),
                                     encoding="utf-8")
        except OSError:
            pass

    def describe(self) -> dict:
        """Recently opened books first, then anything else found in books/."""
        projects, seen = [], set()

        def add(file: str, title: str, last_opened: str | None):
            if file in seen:
                return
            seen.add(file)
            projects.append({"file": file, "title": title,
                             "last_opened": last_opened,
                             "exists": Path(file).exists(),
                             "current": file == str(self.current.file)})

        for e in self.registry():
            file = e.get("file") or ""
            add(file, e.get("title") or Path(file).stem, e.get("last_opened"))
        for file in library.discover():  # never opened: read the title off disk
            add(str(file), library.peek_title(file) or file.parent.name, None)
        return {"current": str(self.current.file), "library": str(library.root()),
                "projects": projects}

    def open(self, file: str) -> Project:
        path = library.book_file(file)  # a bare name is a book in books/
        if path.suffix != ".fab":
            raise ValueError(t("server.not_fab", path=str(path)))
        if not path.exists():
            raise FileNotFoundError(t("server.no_such_file", path=str(path)))
        build = next((Path(e["build"]) for e in self.registry()
                      if e.get("file") == str(path) and e.get("build")), None)
        self.current = self._instance(path, build)
        self.remember(self.current)
        return self.current

    def create(self, directory: str, lang: str | None = None,
               template: str | None = None, title: str | None = None) -> Project:
        from .scaffold import starter_book, write_project
        target = library.book_dir(directory)  # a bare name lands in books/
        book_path = target / "book.fab"
        if book_path.exists():
            raise ValueError(t("server.project_exists", path=str(book_path)))
        write_project(target, starter_book(lang, template, title))
        return self.open(str(book_path))


class Handler(BaseHTTPRequestHandler):
    manager: ProjectManager = None  # set by serve()

    @property
    def project(self) -> Project:
        return self.manager.current

    def log_message(self, fmt, *args):
        pass  # keep the console quiet; jobs report their own progress

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status: int = 200):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400):
        self._json({"error": message}, status)

    def _loopback(self, netloc: str) -> bool:
        """True for this very server's address — `127.0.0.1:8787` and friends."""
        value = (netloc or "").strip().lower()
        if value.startswith("["):                      # [::1]:8787
            name, _, port = value.rpartition("]")
            name, port = name[1:], port.lstrip(":")
        else:
            name, _, port = value.partition(":")
        expected = str(self.server.server_address[1])
        return name in ("127.0.0.1", "localhost", "::1") and (port or expected) == expected

    def _same_origin(self) -> bool:
        """Only the studio's own page may drive the API.

        Binding to loopback keeps the network out but not the browser: any site
        the author visits can POST to 127.0.0.1 without a preflight, and a
        hostname rebound to 127.0.0.1 would read the answers as same-origin.
        Checking Host stops the rebinding, checking Origin stops the cross-site
        request — neither costs the real UI anything, since it is served from
        this address itself.
        """
        if not self._loopback(self.headers.get("Host") or ""):
            return False
        origin = self.headers.get("Origin")
        return not origin or self._loopback(urlparse(origin).netloc)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        url = urlparse(self.path)
        if not self._same_origin():
            return self._error(t("server.forbidden_origin"), 403)
        p = self.project
        q = parse_qs(url.query)
        i18n.set_lang(q.get("lang", [None])[0])
        try:
            if url.path in ("/", "/index.html"):
                html = (WEB_DIR / "index.html").read_bytes()
                self._send(200, html, "text/html; charset=utf-8")
            elif url.path == "/api/i18n":
                self._json({"available": i18n.available(), "strings": i18n.bundle()})
            elif url.path == "/api/project":
                self._json(p.snapshot())
            elif url.path == "/api/jobs":
                self._json({"jobs": list(p.jobs.values())})
            elif url.path == "/api/manuscript":
                self._json(p.manuscript(q.get("translation", [None])[0]))
            elif url.path == "/api/translations":
                self._json(p.translations_overview())
            elif url.path == "/api/stats":
                self._json(p.stats())
            elif url.path == "/api/templates":
                from .templates import list_templates
                self._json({"templates": list_templates(q.get("lang", [None])[0])})
            elif url.path == "/api/projects":
                self._json(self.manager.describe())
            elif url.path == "/api/epub":
                result = p.compiled()
                tcode = q.get("translation", [None])[0]
                tstate = p.translation(tcode) if tcode else None
                data = (build_epub(result["book"], p.state(), translation=tstate)
                        if result["book"] else None)
                if not data:
                    return self._error(t("server.no_translation", lang=tcode) if tcode
                                       else t("server.empty_manuscript"))
                from urllib.parse import quote
                from .translator import translated_title
                title = (translated_title(result["book"], tstate) if tstate
                         else result["book"]["title"])
                filename = (title or "book").replace(" ", "_") + ".epub"
                self.send_response(200)
                self.send_header("Content-Type", "application/epub+zip")
                self.send_header("Content-Disposition",
                                 "attachment; filename=\"book.epub\"; "
                                 f"filename*=UTF-8''{quote(filename)}")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif url.path == "/api/variant":
                q = parse_qs(url.query)
                scene, filename = q["scene"][0], q["file"][0]
                if "/" in filename or "\\" in filename or "/" in scene or "\\" in scene:
                    return self._error("invalid name")
                self._json({"text": p.state().variant_text(scene, filename)})
            elif url.path == "/api/prompt":
                q = parse_qs(url.query)
                scene = q["scene"][0]
                result = p.compiled()
                if not result["ok"]:
                    return self._error(t("server.not_compiling"))
                meta = p.state().scenes_meta()
                memory = previous_context(result["book"], scene, meta)
                ledger = character_ledger(result["book"], scene, meta)
                self._json({"system": book_bible(result["book"]),
                            "user": scene_prompt(result["book"], scene, memory, ledger)})
            else:
                self._error("not found", 404)
        except FileNotFoundError:
            self._error("file not found", 404)
        except ValueError as e:
            self._error(str(e))
        except Exception as e:
            traceback.print_exc()
            self._error(f"{type(e).__name__}: {e}", 500)

    def do_POST(self):
        url = urlparse(self.path)
        if not self._same_origin():
            return self._error(t("server.forbidden_origin"), 403)
        # `application/json` is not a CORS-safelisted content type, so demanding
        # it means a cross-site POST needs a preflight this server never answers
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype and ctype != "application/json":
            return self._error(t("server.bad_content_type"), 415)
        p = self.project
        i18n.set_lang(parse_qs(url.query).get("lang", [None])[0])
        try:
            body = self._body()
            if url.path == "/api/compile":
                self._json(p.compiled(body.get("source", "")))
            elif url.path == "/api/advise":
                # settings the notes (and, when there is one, the book) imply;
                # `book` lets the constructor advise on unsaved edits
                from .advisor import recommend
                book = body.get("book")
                if book is None and not body.get("notes_only"):
                    book = (p.compiled().get("book") if p.file.exists() else None)
                notes = str(body.get("notes") or "")
                if not notes and not body.get("notes_only"):
                    notes = p.notes()  # the project's own notes file, if it kept one
                self._json(recommend(notes, book))
            elif url.path == "/api/construct":
                # book graph -> canonical .fab source (+ compile), optionally saved
                source = serialize_book(body["book"])
                result = p.compiled(source)
                if body.get("save"):
                    state = p.state()
                    for rename in body.get("renames") or []:
                        if isinstance(rename, dict) and rename.get("from") and rename.get("to"):
                            state.rename_scene(str(rename["from"]), str(rename["to"]))
                    p.write_source(source)
                self._json({"source": source, "result": result, "saved": bool(body.get("save"))})
            elif url.path == "/api/source":
                p.write_source(body["source"])
                self._json(p.compiled(body["source"]))
            elif url.path == "/api/generate":
                job = p.generate_scene(body["scene"], body.get("provider"),
                                       str(body.get("instruction") or "").strip(),
                                       body.get("comments_from"))
                self._json({"job": job})
            elif url.path == "/api/revise":
                scene, filename = body["scene"], body["file"]
                if "/" in filename or "\\" in filename or "/" in scene or "\\" in scene:
                    return self._error("invalid name")
                job = p.revise_variant_job(scene, filename, body.get("provider"))
                self._json({"job": job})
            elif url.path == "/api/comment/add":
                note = str(body.get("note") or "").strip()
                if not note:
                    return self._error("empty note")
                cid = p.state().add_comment(body["scene"], body["file"],
                                            str(body.get("quote") or "")[:500], note)
                self._json({"ok": True, "id": cid})
            elif url.path == "/api/comment/delete":
                p.state().delete_comment(body["scene"], body["file"], body["id"])
                self._json({"ok": True})
            elif url.path == "/api/variant/save":
                scene, filename = body["scene"], body["file"]
                if "/" in filename or "\\" in filename or "/" in scene or "\\" in scene:
                    return self._error("invalid name")
                job = p.save_variant_text(scene, filename, str(body.get("text") or ""),
                                          body.get("provider"))
                self._json({"ok": True, "job": job})
            elif url.path == "/api/project/open":
                project = self.manager.open(str(body.get("file") or ""))
                result = project.compiled()
                self._json({"ok": True, "file": str(project.file),
                            "title": result["book"]["title"] if result.get("book") else project.file.stem})
            elif url.path == "/api/project/create":
                directory = str(body.get("directory") or "").strip()
                if not directory:
                    return self._error(t("server.no_directory"))
                project = self.manager.create(directory, body.get("lang"),
                                              body.get("template"), body.get("title"))
                self._json({"ok": True, "file": str(project.file)})
            elif url.path == "/api/project/draft":
                directory = str(body.get("directory") or "").strip()
                notes = str(body.get("notes") or "").strip()
                if not directory:
                    return self._error(t("server.no_directory"))
                if not notes:
                    return self._error(t("server.no_notes"))
                job = p.draft_project_job(directory, notes, body.get("lang"),
                                          body.get("provider"), body.get("settings"))
                self._json({"job": job})
            elif url.path == "/api/fix":
                job = p.fix_source_job(body.get("provider"))
                self._json({"job": job})
            elif url.path == "/api/translate":
                job = p.translate_job(str(body.get("lang") or "").strip().lower(),
                                      body.get("provider"), bool(body.get("force")))
                self._json({"job": job})
            elif url.path == "/api/approve":
                scene, variant = body["scene"], body["variant"]
                state = p.state()
                state.approve(scene, variant)
                job = None
                if state.scene(scene).get("summary_of") != variant:
                    # the memory describes another text now — refresh it if we can
                    state.mark_summary_stale(scene)
                    job = p.resummarize_scene(scene, variant, body.get("provider"))
                self._json({"ok": True, "job": job})
            else:
                self._error("not found", 404)
        except (KeyError, ValueError, ProviderError, FileNotFoundError) as e:
            self._error(str(e))
        except Exception as e:
            traceback.print_exc()
            self._error(f"{type(e).__name__}: {e}", 500)


def serve(file: Path, build_dir: Path, port: int = 8787) -> int:
    Handler.manager = ProjectManager(file, build_dir)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(t("cli.serve", port=port, file=file))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
