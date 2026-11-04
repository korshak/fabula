"""Writer agent: generates scene variants through a pluggable model provider,
self-checks each variant against the scene's contracts, keeps story memory
between scenes, and can repair the .fab source from compiler diagnostics.

Output per scene (under <build>/scenes/<slug>/):
    v1.md, v2.md, ...   — the variants (the human editor picks one)
    report.json          — machine-readable critique per variant
"""

from __future__ import annotations

import json
import re

from .prompts import (
    book_bible,
    character_ledger,
    critique_prompt,
    find_scene,
    fix_prompt,
    previous_context,
    revise_prompt,
    scene_prompt,
    summarize_prompt,
    targeted_revise_prompt,
    variant_note,
)
from .i18n import t
from .providers import Provider
from .state import ProjectState


def _extract_json(raw: str) -> dict:
    """The critique must be pure JSON, but strip a stray code fence if present."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # last resort: grab the first {...} block from a chatty answer
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return {"verdict": "unparsed", "violations": [], "raw": raw}


def _strip_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text)
    return text


class WriterAgent:
    def __init__(self, book: dict, provider: Provider, on_progress=None):
        self.book = book
        self.provider = provider
        self.on_progress = on_progress or (lambda msg: print(msg))
        # Stable prefix; the anthropic provider caches it between calls.
        self.system = book_bible(book)

    def _generate_text(self, prompt: str, effort: str = "high") -> str:
        return self.provider.generate(self.system, prompt, effort=effort)

    def _critique(self, scene_name: str, text: str) -> dict:
        critique = _extract_json(self._generate_text(
            critique_prompt(self.book, scene_name, text), effort="medium"
        ))
        critique["provider"] = self.provider.name
        return critique

    def _revision_limit(self) -> int:
        """Self-revision rounds per variant; `revisions: 2;` in book settings."""
        try:
            return max(0, int(self.book.get("settings", {}).get("revisions", 1)))
        except (TypeError, ValueError):
            return 1

    def write_scene(self, scene_name: str, state: ProjectState,
                    instruction: str = "") -> dict:
        found = find_scene(self.book, scene_name)
        if not found:
            raise KeyError(f"scene {scene_name!r} not found")
        _, _, scene = found

        from .forms import ensure_plan_review
        ensure_plan_review(self.book, self.provider, state.build_dir, self.on_progress)

        scene_dir = state.scene_dir(scene_name)
        scene_dir.mkdir(parents=True, exist_ok=True)

        meta = state.scenes_meta()
        memory = previous_context(self.book, scene_name, meta)
        ledger = character_ledger(self.book, scene_name, meta)
        prompt = scene_prompt(self.book, scene_name, memory, ledger, instruction)
        variants = int(scene.get("variants") or 1)
        report = {"scene": scene_name, "provider": self.provider.name, "variants": []}
        best_text, best_file = None, None

        for i in range(1, variants + 1):
            self.on_progress(t("agent.gen_variant", scene=scene_name, i=i, v=variants,
                               provider=self.provider.name))
            variant_prompt = prompt if variants == 1 else (
                prompt + "\n\n" + variant_note(self.book, i, variants)
            )
            text = self._generate_text(variant_prompt)
            self.on_progress(t("agent.check_variant", scene=scene_name, i=i))
            critique = self._critique(scene_name, text)

            # self-revision loop: feed the violations back until clean or out of rounds
            rounds = 0
            while (critique.get("verdict") == "issues" and critique.get("violations")
                   and rounds < self._revision_limit()):
                rounds += 1
                self.on_progress(t("agent.revise", scene=scene_name, i=i, r=rounds))
                revised = self._generate_text(
                    variant_prompt + "\n\n" + revise_prompt(self.book, text,
                                                            critique["violations"]))
                revised_critique = self._critique(scene_name, revised)
                better = (revised_critique.get("verdict") == "ok"
                          or len(revised_critique.get("violations") or [])
                          < len(critique.get("violations") or []))
                if not better:
                    break
                text, critique = revised, revised_critique
            critique["revisions"] = rounds

            filename = f"v{i}.md"
            (scene_dir / filename).write_text(text, encoding="utf-8")
            state.record_variant(scene_name, filename, critique)
            report["variants"].append({"file": filename, "critique": critique})
            if best_text is None or critique.get("verdict") == "ok":
                best_text, best_file = text, filename
            self.on_progress(t("agent.verdict", scene=scene_name, file=filename,
                               verdict=critique.get("verdict", "?"),
                               n=len(critique.get("violations", []))))

        if best_text:
            # always refresh: regeneration must not leave a stale summary behind
            self.summarize(scene_name, best_text, state, source_file=best_file)

        (scene_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report

    def revise_variant(self, scene_name: str, filename: str, state: ProjectState) -> dict:
        """Apply the editor's anchored comments to ONE variant with minimal
        changes — the plot and everything not commented stays as written."""
        comments = state.comments(scene_name, filename)
        if not comments:
            raise ValueError("no comments")
        text = state.variant_text(scene_name, filename)
        meta = state.scenes_meta()
        spec = scene_prompt(self.book, scene_name,
                            previous_context(self.book, scene_name, meta),
                            character_ledger(self.book, scene_name, meta))
        self.on_progress(t("agent.targeted", scene=scene_name, file=filename, n=len(comments)))
        new_text = self._generate_text(
            spec + "\n\n" + targeted_revise_prompt(self.book, text, comments))
        self.on_progress(t("agent.check_variant", scene=scene_name, i=filename))
        critique = self._critique(scene_name, new_text)

        (state.scene_dir(scene_name) / filename).write_text(new_text, encoding="utf-8")
        # record_variant replaces the variant entry, clearing the now-applied comments
        state.record_variant(scene_name, filename, critique)
        entry = state.scene(scene_name)
        if entry.get("approved") == filename or entry.get("summary_of") == filename:
            self.summarize(scene_name, new_text, state, source_file=filename)
        return {"file": filename, "critique": critique}

    def summarize(self, scene_name: str, text: str, state: ProjectState,
                  source_file: str | None = None) -> str:
        self.on_progress(t("agent.summary", scene=scene_name))
        raw = self._generate_text(summarize_prompt(self.book, scene_name, text),
                                  effort="low").strip()
        data = _extract_json(raw)
        if isinstance(data.get("summary"), str) and data["summary"].strip():
            summary = data["summary"].strip()
            changes = [c for c in (data.get("state_changes") or [])
                       if isinstance(c, dict) and c.get("character") and c.get("change")]
            views = [v for v in (data.get("viewpoints") or [])
                     if isinstance(v, dict) and v.get("character") and v.get("view")]
        else:
            summary, changes, views = raw, [], []  # the model answered in prose — keep it
        state.set_summary(scene_name, summary, of=source_file, changes=changes,
                          viewpoints=views)
        return summary

    def fix_source(self, source: str, diagnostics: list[dict]) -> str:
        self.on_progress(t("agent.fix", provider=self.provider.name))
        return _strip_fence(self._generate_text(
            fix_prompt(self.book, source, diagnostics), effort="medium"))
