## What this changes

<!-- One or two sentences. If it fixes an open issue, write "Fixes #123". -->

## Why

<!-- What was wrong, or what became possible. -->

## Checklist

- [ ] `python -m unittest discover -s tests -t .` passes locally
- [ ] User-facing strings go through `t()` / `tn()`, and the keys were added to
      **both** `fabula/locales/uk.json` and `fabula/locales/en.json`
- [ ] No new runtime dependency in `[project.dependencies]` — the core stays stdlib-only
- [ ] If a diagnostic changed: a **new** `FABxxx` code was added rather than an
      existing one being redefined (codes are API — agents parse them)
- [ ] If a scene/character/world attribute changed: the compiler, the serializer,
      the Builder UI and `docs/LANGUAGE.md` all learned about it in this change

## Licensing

Fabula is under the [PolyForm Noncommercial License 1.0.0](../LICENSE) — free for
noncommercial use, **not** an OSI-approved open-source license. Commercial use
requires a separate license from the copyright holder.

- [ ] I have read the license, and I license my contribution to the project under
      those same terms.
- [ ] I grant the copyright holder the right to license my contribution
      commercially as part of Fabula.

<sub>That second box is what keeps a commercial license possible at all: without
it, every future commercial arrangement would need every past contributor to sign
off. If you would rather not, a bug report or a design discussion in an issue is a
contribution too, and carries none of this.</sub>
