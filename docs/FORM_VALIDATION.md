# Literary forms: local validation

Verified on 2026-11-04 with Python and the installed Codex CLI
`0.165.0-alpha.1.2`, authenticated using ChatGPT. The `codex-cli` profile omitted
`model`, allowing the CLI to choose its built-in default. No API key or local
machine path is committed.

## Automated checks

```bash
python3 -m unittest discover -s tests -t .
git diff --check
```

114 tests passed. New coverage includes form/budget serialization and diagnostics,
form propagation into planning/writing/critique, explicit author settings winning
over model defaults, malformed review rejection, blocking prose before a plan
conflict is resolved, deliberate exceptions, cache invalidation, unchanged-plan
round trips, server jobs and endpoints, and Codex process/error handling.
The inline studio JavaScript also passed `node --check`.

## Live model calls

The same Ukrainian premise about Mara, a ship AI, memory loss and a cup by its
camera was planned in two forms. The live writer/critic/summarizer cycle was run
on the first short-story scene. A separate `check-plan` call reviewed an
intentionally overloaded variation.

| Input | Target words | Scenes | Characters | Threads | Review |
|---|---:|---:|---:|---:|---|
| Short story | 1,800 | 5 | 2 | 2 | ok |
| Novella | 12,000 | 12 | 4 | 3 | ok |
| Overloaded short-story control | 1,800 | 9 | 2 | 2 | issues |

The short-story plan assigns 1,680–1,800 words across its scenes. The novella
assigns 10,800–12,000. The novella includes separate failed attempts to restore
the old relationship, a conversation about what care means, and a final
invitation without a prescribed answer. These are additional stages of the same
central change, rather than merely longer versions of the five short-story scenes.

The negative control has a numerically feasible total of 1,620–1,980 words, but
adds bodily embodiment, a love story, an Earth journey, another loss and a choice
of mortality after the original ending. The reviewer flagged independent stories
after the climax, conflicting explicit story boundaries, and insufficient scene
time for the pivotal encounters. The CLI returned status 1 as expected.

The generated first scene received `ok` from the scene critic (zero reported
violations), and its memory summary was saved. This smoke run does not generate
an entire story/novella or prove literary quality; the editorial verdicts are
model judgments.

## Studio checks

Tested the local studio at `http://127.0.0.1:8791` using throwaway projects:

- Selected and saved literary form and total word budget.
- Observed FAB205 for a target outside the sum of scene ranges.
- Ran the Review plan action through Codex CLI and read its concrete options.
- Reopened the overloaded control and verified the cached report appeared.
- Verified the creation dialog includes Codex CLI, form and budget.
- Verified advice uses the current project's budget and does not enlarge it to
  accommodate an overloaded plan.

Generated `.fab` files, reports, the scene, and screenshots are under the ignored
`build/form-smoke/` directory. They are local artifacts, not shipped manuscripts.

## Reproduce

```bash
codex --version
codex login status
python3 scripts/smoke_forms.py --provider codex-cli --output build/form-smoke-new
```

The script makes live model calls, refuses an existing output directory, saves
the artifacts, and includes both the focused case and the overloaded control.
The default timeout is 900 seconds per call. Editorial outputs can vary; inspect
the saved reports if the live assertions fail.

Provider configuration is included in `books/models.json`, newly scaffolded
projects, and the standalone `examples/models.codex.json` configuration.
For an existing book whose own config shadows the library, add the profile there.
