# Contributing to Fabula

Thanks for considering a contribution! The project is intentionally small and
dependency-free — please keep it that way.

## Dev setup

```bash
git clone https://github.com/korshak/fabula.git && cd fabula
pip install -e .          # or just run `python3 -m fabula …` from the clone
fabula serve            # opens the library in books/
```

Python ≥ 3.10. The core must stay **stdlib-only**; model SDKs are optional
extras (see `[project.optional-dependencies]` in `pyproject.toml`).

## Where things live

- `fabula/lexer.py`, `parser.py`, `compiler.py` — the language front end;
  diagnostics have stable `FABxxx` codes (documented in `docs/LANGUAGE.md`).
- `fabula/serializer.py` — graph → canonical `.fab`; must round-trip losslessly.
- `fabula/prompts.py` — prompt templates come from the `prompts` section of
  the locale files, in the **book's** language.
- `fabula/agent.py` — the generate → critique → revise → summarize loop.
- `fabula/providers.py` — model backends; new backends implement
  `generate(system, prompt, effort)` and `status()`.
- `fabula/server.py` + `fabula/web/index.html` — the local studio
  (stdlib HTTP; the whole frontend is one dependency-free file).
- `fabula/i18n.py` + `fabula/locales/` — all user-facing strings.
- `fabula/library.py` — the book library: every book is one folder under
  `books/` (`book.fab` + optional `models.json` + generated `build/`).
  `books/Тіні пам'яті/` is the sample book; the rest of `books/` is gitignored.

## Ground rules

- **Every user-facing string goes through i18n** (`t()` / `tn()` in Python,
  `t()` / `tn()` in the frontend). Add keys to *both* `uk.json` and `en.json`.
  Adding a whole language: see `fabula/locales/README.md` — no code changes.
- **Diagnostics are API.** Never change the meaning of an existing `FABxxx`
  code; add new codes instead, with a localized message and a `hint` an agent
  could act on.
- **The serializer must stay a lossless inverse of the parser.** If you add a
  scene/character/world attribute, teach the compiler, the serializer, the
  Builder UI, and `docs/LANGUAGE.md` about it in the same change.
- Verify locally before opening a PR: compile + plan the example, the
  serializer round-trip, and a pass through the web studio.

## Translations

The easiest contribution: copy `fabula/locales/en.json` to your language,
translate, open a PR. Partial translations are fine — missing keys fall back
to English.
