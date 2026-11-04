"""Literary form contracts and a cached, whole-plan editorial review.

Profiles are editorial guidance, not genre laws. A reported conflict never
rewrites the author's plan. A saved form_override records a deliberate exception.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .i18n import t

FORMS = ("short_story", "novella", "novel")
REVIEW_VERSION = 1


def validate_form_settings(settings: dict):
    form = settings.get("form")
    if form and form not in FORMS:
        raise ValueError(t("forms.invalid", form=form))
    words = settings.get("target_words")
    if words is not None and (type(words) is not int or words <= 0):
        raise ValueError(t("forms.invalid_words"))


def form_contract(settings: dict) -> str:
    form = settings.get("form")
    if form not in FORMS:
        return ""
    lang = settings.get("language") or "uk"
    parts = [t("forms.contract", lang, form=t(f"forms.{form}.name", lang)),
             t(f"forms.{form}.profile", lang), t("forms.shared", lang)]
    if settings.get("target_words"):
        parts.append(t("forms.budget", lang, words=settings["target_words"]))
    if settings.get("form_override"):
        parts.append(t("forms.override_context", lang, note=settings["form_override"]))
    return "\n".join(parts)


def _plan(book):
    # Source positions/comments and an editorial waiver do not change the plot.
    if isinstance(book, dict):
        return {k: _plan(v) for k, v in book.items()
                if not k.startswith("_") and k != "form_override"}
    if isinstance(book, list):
        return [_plan(v) for v in book]
    return book


def budget_conflict(book: dict) -> dict | None:
    from .compiler import iter_scenes
    words = (book.get("settings") or {}).get("target_words")
    if type(words) is not int or words <= 0:
        return None
    ranges = []
    for _, _, scene in iter_scenes(book):
        length = scene.get("length")
        rng = length.get("range") if isinstance(length, dict) else None
        if (not isinstance(rng, list) or len(rng) != 2 or
                not all(type(v) in (int, float) for v in rng) or not 0 < rng[0] <= rng[1]):
            return None  # Incomplete/invalid lengths are not evidence of a mismatch.
        ranges.append(rng)
    lo, hi = sum(r[0] for r in ranges), sum(r[1] for r in ranges)
    if ranges and not lo <= words <= hi:
        return {"words": words, "lo": lo, "hi": hi}
    return None


def review_fingerprint(book: dict, provider) -> str:
    from .compiler import compile_source
    from .serializer import serialize_book
    # Normalize omitted empty/default fields just as saving and reopening does.
    canonical = compile_source(serialize_book(book))["book"]
    data = {"version": REVIEW_VERSION, "book": _plan(canonical),
            "profile": form_contract(_plan(book.get("settings") or {})),
            "review_prompt": t("forms.review", (book.get("settings") or {}).get("language") or "uk",
                               plan=""),
            "provider": provider.name, "config": provider.cfg}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def valid_review(report) -> bool:
    if not isinstance(report, dict) or report.get("verdict") not in ("ok", "issues"):
        return False
    if not isinstance(report.get("summary"), str) or not report["summary"].strip():
        return False
    issues = report.get("issues")
    if not isinstance(issues, list) or bool(issues) != (report["verdict"] == "issues"):
        return False
    for issue in issues:
        if not isinstance(issue, dict) or not all(isinstance(issue.get(k), str) and issue[k].strip()
                                                for k in ("problem", "evidence")):
            return False
        options = issue.get("options")
        if not isinstance(options, list) or not options:
            return False
        for option in options:
            if (not isinstance(option, dict) or option.get("action") not in
                    ("focus", "expand", "split", "keep") or
                    not isinstance(option.get("proposal"), str) or not option["proposal"].strip()):
                return False
    return True


def review_plan(book: dict, provider) -> dict:
    from .agent import _extract_json
    settings = book.get("settings") or {}
    validate_form_settings(settings)
    if not settings.get("form"):
        return {"verdict": "skipped", "summary": t("forms.choose"), "issues": []}
    lang = settings.get("language") or "uk"
    raw = provider.generate(
        t("forms.reviewer", lang) + "\n\n" + form_contract(_plan(settings)),
        t("forms.review", lang, plan=json.dumps(_plan(book), ensure_ascii=False)), effort="medium")
    report = _extract_json(raw)
    if not valid_review(report):
        raise ValueError(t("forms.bad_review"))
    conflict = budget_conflict(book)
    if conflict:
        report["verdict"] = "issues"
        report["issues"].append({"problem": t("forms.budget_conflict", lang, **conflict),
                                 "evidence": t("forms.budget_evidence", lang),
                                 "options": [{"action": "focus", "proposal": t("forms.budget_hint", lang)}]})
        report["summary"] += " " + t("forms.budget_conflict", lang, **conflict)
    return {**report, "fingerprint": review_fingerprint(book, provider), "provider": provider.name}


def save_review(build_dir: Path, report: dict):
    build_dir = Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    # Concurrent scene jobs must never read a partially written report.
    fd, name = tempfile.mkstemp(prefix="plan-review-", suffix=".tmp", dir=build_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        os.replace(name, build_dir / "plan-review.json")
    finally:
        Path(name).unlink(missing_ok=True)


def cached_review(book: dict, provider, build_dir: Path) -> dict | None:
    try:
        report = json.loads((Path(build_dir) / "plan-review.json").read_text(encoding="utf-8"))
        if valid_review(report) and report.get("fingerprint") == review_fingerprint(book, provider):
            return report
    except (OSError, ValueError):
        pass
    return None


def ensure_plan_review(book: dict, provider, build_dir: Path, progress):
    settings = book.get("settings") or {}
    if not settings.get("form"):
        return  # Existing projects retain their original workflow.
    report = cached_review(book, provider, build_dir)
    if report is None:
        progress(t("forms.checking"))
        report = review_plan(book, provider)
        save_review(build_dir, report)
    if report["verdict"] == "issues" and not str(settings.get("form_override") or "").strip():
        raise ValueError(t("forms.blocked", summary=report["summary"]))
