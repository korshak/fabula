"""Project state: what has been generated, approved, and summarized.

Lives next to the generated scenes in the build directory, as plain JSON, so
both the CLI and the web UI (and the agent itself) share one source of truth.

Shape:
{
  "scenes": {
    "<scene name>": {
      "variants": {"v1.md": {"critique": {...}}, ...},
      "approved": "v1.md" | null,
      "summary": "..." | null
    }
  }
}
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import threading
from pathlib import Path

# One lock per state file: server request handlers and background jobs share
# the process, and each of them builds its own ProjectState over the same file.
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = str(Path(path).resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = threading.RLock()
        return lock


def slug(name: str) -> str:
    cleaned = re.sub(r"[^\w]+", "-", name, flags=re.UNICODE).strip("-").lower()
    return cleaned or "scene"


class ProjectState:
    def __init__(self, build_dir: Path):
        self.build_dir = Path(build_dir)
        self.path = self.build_dir / "state.json"
        self._lock = _lock_for(self.path)
        self.data = {"scenes": {}}
        self.reload()

    def reload(self):
        """Re-read the file; the object becomes a snapshot of it as of now."""
        data = {"scenes": {}}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        data.setdefault("scenes", {})
        self.data = data

    @contextlib.contextmanager
    def _mutate(self):
        """Load, modify, save — as one atomic step against the file.

        A ProjectState is a snapshot, and it may be an old one: a generation
        job holds its state across a whole LLM call, and the web UI serves
        concurrent requests from separate threads. So the file, not this
        object, is the truth at write time: re-read it inside the lock, apply
        the change to what is really there, then write. Without this, two
        approvals racing each other each save their own stale copy of the
        whole document and the later save silently drops the earlier one.
        """
        with self._lock:
            self.reload()
            yield
            self.save()

    def save(self):
        self.build_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.data, ensure_ascii=False, indent=2)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with self._lock:  # atomic replace: readers never see a half-written file
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.path)

    def scene(self, name: str) -> dict:
        entry = self.data["scenes"].setdefault(
            name, {"variants": {}, "approved": None, "summary": None}
        )
        # fields added later; tolerate state files written by older versions
        entry.setdefault("summary_of", None)      # variant file the summary was made from
        entry.setdefault("summary_stale", False)  # approved variant differs from summary_of
        entry.setdefault("state_changes", [])     # [{"character", "change"}] from the summarizer
        entry.setdefault("viewpoints", [])        # [{"character", "view"}] — each character's own picture
        return entry

    def scene_dir(self, name: str) -> Path:
        return self.build_dir / "scenes" / slug(name)

    def record_variant(self, name: str, filename: str, critique: dict,
                       words: int | None = None):
        import datetime
        if words is None:
            path = self.scene_dir(name) / filename
            try:
                words = len(path.read_text(encoding="utf-8").split())
            except OSError:
                words = None
        with self._mutate():
            self.scene(name)["variants"][filename] = {"critique": critique, "words": words}
            # writing history feeds the statistics tab: words produced per day
            self.data.setdefault("history", []).append({
                "date": datetime.date.today().isoformat(),
                "scene": name, "file": filename, "words": words or 0,
            })

    def mark_variant_edited(self, name: str, filename: str):
        with self._mutate():
            entry = self.scene(name)["variants"].setdefault(filename, {"critique": {}})
            entry["edited"] = True

    def add_comment(self, name: str, filename: str, quote: str, note: str) -> str:
        """An inline editor comment anchored to a quoted passage of a variant."""
        import uuid
        comment = {"id": uuid.uuid4().hex[:8], "quote": quote, "note": note}
        with self._mutate():
            entry = self.scene(name)["variants"].setdefault(filename, {"critique": {}})
            entry.setdefault("comments", []).append(comment)
        return comment["id"]

    def delete_comment(self, name: str, filename: str, comment_id: str):
        with self._mutate():
            entry = self.scene(name)["variants"].get(filename) or {}
            entry["comments"] = [c for c in entry.get("comments") or []
                                 if c.get("id") != comment_id]

    def comments(self, name: str, filename: str) -> list:
        entry = self.scene(name)["variants"].get(filename) or {}
        return entry.get("comments") or []

    def rename_scene(self, old: str, new: str):
        """Keep variants/summary/approval when a scene is renamed in the builder."""
        with self._lock:  # a no-op rename must not rewrite the file
            self.reload()
            if old == new or old not in self.data["scenes"]:
                return
            if new not in self.data["scenes"]:
                self.data["scenes"][new] = self.data["scenes"].pop(old)
            old_dir, new_dir = self.scene_dir(old), self.scene_dir(new)
            if old_dir.exists() and not new_dir.exists():
                new_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_dir), str(new_dir))
            self.save()

    def set_summary(self, name: str, summary: str, of: str | None = None,
                    changes: list | None = None, viewpoints: list | None = None):
        with self._mutate():
            entry = self.scene(name)
            entry["summary"] = summary
            entry["summary_of"] = of
            entry["summary_stale"] = False
            entry["state_changes"] = changes or []
            entry["viewpoints"] = viewpoints or []

    def mark_summary_stale(self, name: str):
        with self._mutate():
            self.scene(name)["summary_stale"] = True

    def approve(self, name: str, filename: str):
        with self._mutate():
            entry = self.scene(name)
            if filename not in entry["variants"]:
                raise KeyError(f"scene {name!r} has no variant {filename!r}")
            entry["approved"] = filename

    def scenes_meta(self) -> dict:
        """Per-scene memory the prompt builder consumes: summary + state changes."""
        return {name: {"summary": s.get("summary"),
                       "state_changes": s.get("state_changes") or [],
                       "viewpoints": s.get("viewpoints") or []}
                for name, s in self.data["scenes"].items()}

    def variant_text(self, name: str, filename: str) -> str:
        return (self.scene_dir(name) / filename).read_text(encoding="utf-8")

    def chosen_text(self, name: str) -> str | None:
        """Approved variant, or the only variant, else None."""
        entry = self.data["scenes"].get(name)
        if not entry:
            return None
        filename = entry.get("approved")
        if not filename and len(entry["variants"]) == 1:
            filename = next(iter(entry["variants"]))
        if not filename:
            return None
        path = self.scene_dir(name) / filename
        return path.read_text(encoding="utf-8") if path.exists() else None
