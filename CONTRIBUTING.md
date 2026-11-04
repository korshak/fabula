# Contributing to Fabula

Thanks for considering a contribution! The project is intentionally small and
dependency-free — please keep it that way.

Everyone taking part is expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md). Found a security problem? Please report
it privately — see [SECURITY.md](SECURITY.md).

## Dev setup

```bash
git clone https://github.com/korshak/fabula.git && cd fabula
pip install -e .          # or just run `python3 -m fabula …` from the clone
python -m unittest discover -s tests -t .   # 90 tests, ~2s, no network
fabula serve              # opens the library in books/
```

Python ≥ 3.10. The core must stay **stdlib-only**; model SDKs are optional
extras (see `[project.optional-dependencies]` in `pyproject.toml`).

## Tests

```bash
python -m unittest discover -s tests -t .        # all of them, ~2s
python -m unittest tests.test_language -v        # one file
```

Stdlib `unittest`, no test dependency to install, no network and no API key —
anything that would reach a model provider is out of scope for the suite. The
same command runs in CI on Linux, macOS and Windows across Python 3.10–3.13,
plus the README walkthrough and a build of the distributions.

What lives where:

- `tests/test_language.py` — compile, diagnostic codes, serializer round-trip.
- `tests/test_i18n.py` — locale files: key parity, placeholders, plural forms.
- `tests/test_server_guards.py` — the studio's `Host`/`Origin`/content-type
  guards and its path checks, against a real server on an ephemeral port.
- `tests/test_state_and_library.py` — `state.json` (including the concurrent
  approvals that used to be lost) and how a book name resolves to a folder.
- `tests/test_markdown_and_epub.py` — rendering model output safely, both
  renderers, and a readable EPUB out the other end.
- `tests/test_cli.py` — the walkthrough from the README, end to end.

A bug fix wants a test that fails without it. If you are touching the language,
add the case to `tests/test_language.py`; if you are adding a diagnostic, assert
its code.

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
  `books/Тіні пам'яті/` and `books/Shadows of Memory/` are the sample books
  (the same story in Ukrainian and English, used for the README screenshots);
  the rest of `books/` is gitignored.

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
- **Model output is untrusted input.** It is rendered as markdown in the studio
  (same-origin, next to the API) and in the EPUB. Keep both renderers escaping,
  and keep their link-scheme whitelists in step — there is a test that fails if
  they drift.
- Verify locally before opening a PR: the test suite, plus a pass through the
  web studio for anything the eye has to check.

## Translations

The easiest contribution: copy `fabula/locales/en.json` to your language,
translate, open a PR. Partial translations are fine — missing keys fall back
to English. `tests/test_i18n.py` checks the mechanical part for you: that the
file parses, declares `_meta.name` and a plural rule the code implements, has a
form for every plural case, and never invents a placeholder the caller does not
pass.

The interface language follows `FABULA_LANG`, and with that unset, the system
locale — so once your file lands, speakers of your language get it without
configuring anything.

## Licensing of contributions

Fabula is under the [PolyForm Noncommercial License 1.0.0](LICENSE) — not an
open-source license: noncommercial use is free, commercial use needs a separate
license from the copyright holder (Denys Korshak). Please read it before
investing time in a change.

The pull request template asks you to confirm two things explicitly: that your
contribution is licensed to the project under those same terms, and that you
grant the copyright holder the right to license it commercially as part of
Fabula. That second one is what keeps a commercial license possible at all:
without it, every future commercial arrangement would need every past
contributor to sign off.

If you are not comfortable with that, a bug report or a design discussion in an
issue is a contribution too, and carries none of this.
