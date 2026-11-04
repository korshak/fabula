# The Fabula language

Fabula is a small declarative DSL for describing a book as a set of
machine-checkable contracts. It is deliberately **regular**: one statement
shape, one block shape, no operator precedence, no context-sensitive parsing —
so that both humans and LLM agents can read, write, and repair it reliably.

## Grammar (EBNF)

```ebnf
file     = { block } ;
block    = IDENT { label } "{" { item } "}" ;
label    = STRING | NUMBER ;
item     = attribute | block ;
attribute= IDENT ":" value ";" ;
value    = STRING | NUMBER | range | IDENT | list ;
range    = NUMBER ".." NUMBER ;
list     = "[" [ value { "," value } ] "]" ;
```

Lexical rules:

- **Strings** are double-quoted; escapes: `\"`, `\\`, `\n`, `\t`.
- **Numbers** are integers or decimal fractions (`0.75`). A range uses `..`
  between two numbers: `900..1200`.
- **Bare identifiers** in value position (`past`, `hard`) are equivalent to
  the same value quoted — the canonical serializer always quotes.
- **Comments** are `// to end of line`. Comments written directly above a
  block are attached to it and survive canonical re-serialization (e.g. by
  the web Builder); comments between list items are dropped.
- **Repeated attribute keys accumulate**: two `show: "…";` lines produce a
  list of two beats.

## Block structure

```
book "Title" {
  <settings attributes>
  world "Name" { rule "Name" { description: "…"; severity: hard; } … }
  character "Name" { … }
  location "Name" { … }
  thread "Name" { … }
  act 1 [ "Title" ] {
    chapter 1 "Title" {
      scene "Name" { … }
    }
  }
}
```

### `book` settings (attributes directly on `book`)

| key | meaning | default |
|---|---|---|
| `voice` | narration: `third_person_limited` \| `third_person_omniscient` \| `first_person` | — |
| `tense` | `past` \| `present` | — |
| `language` | the language the book is written in; also selects the prompt-template language (locale code; falls back to English) | `uk` |
| `style_profile` | free-form tone/rhythm/manner description for the agent | — |
| `memory_window` | how many most-recent scenes keep full summaries in the prompt; older ones are compressed to one sentence | `3` |
| `revisions` | self-revision rounds per variant when the validator finds violations | `1` |
| `thread_gap` | how many consecutive scenes a declared thread may stay silent before `FAB204` warns | `7` |
| `target_words` | positive integer: total planning budget and manuscript word target | — |
| `form` | literary form: `short_story`, `novella`, or `novel`; absent preserves the legacy generation workflow | — |
| `form_override` | nonempty author explanation accepting the current plan despite editorial issues | — |
| `premise` | the one-sentence premise (the wizard writes it); reaches the agent among the settings | — |

Unknown settings keys are preserved and passed to the agent verbatim.

### `world` / `rule`

Each `rule` has a `description` (what the agent actually relies on) and a
`severity`: `hard` rules go into the scene contract and the validator checks
them; `soft` rules are advisory.

### `character`

`traits` (list), `arc`, `age`, `notes`, and constraints: repeated `never:`
lines (things the character never does) and `must_resolve:` lines (threads
that must resolve within the book).

Questionnaire fields — cheap structure that keeps the agent's prose specific
(all optional strings, all fed into the book bible): `appearance`, `voice`
(speech habits, pet phrases, rhythm), `wants`, `fears`, `secret` (must not be
revealed by accident).

### `location`

A declared place with a `description` and five optional sensory fields the
agent must use whenever a scene is set there: `smell`, `sound`, `touch`,
`taste`, `sight`. A scene's free-text `location` attribute still works on its
own; declared locations simply enrich the book bible. Duplicate names are
`FAB110`.

### `thread`

A named plot thread with a `description` (what the line is about and where it
must arrive). Scenes advance threads via their `threads: [ … ]` attribute.
The compiler warns when a declared thread is never advanced (`FAB203`) or
goes silent for more than `thread_gap` consecutive scenes (`FAB204`); an
undeclared thread name in a scene is an error (`FAB109`). The Board tab
renders a thread × scene map from the same data.

### `scene`

| key | meaning |
|---|---|
| `pov` | the viewpoint character — must be declared (`FAB101`) |
| `cast` | characters on the page besides the POV (`FAB107`) |
| `kill` | characters who die in this scene; they may not appear later (`FAB108`) |
| `location`, `time` | where and when |
| `tension` | normalized curve `[0..1, …]` across the scene (`FAB105`) |
| `length` | word range, e.g. `700..1000` (`FAB103`) |
| `variants` | how many variants the agent writes (default 1) |
| `style_hints` | list of local style nudges |
| `threads` | plot threads this scene visibly advances (`FAB109` if undeclared) |
| `show` | repeated — beats that MUST happen on the page (`FAB102` warns if none) |
| `forbid` | repeated — scene-local hard bans |

## Diagnostics

The compiler returns JSON: `{book, diagnostics, ok}`. Every diagnostic has a
stable `code`, `severity`, `message`, `line:col`, and a machine-actionable
`hint`. Messages are localized to the active interface language.

| code | severity | meaning |
|---|---|---|
| FAB001–FAB013 | error | lexer/parser errors (unterminated string, missing `;`, unclosed block, …) |
| FAB100 | error | no `book` block |
| FAB101 | error | scene POV is not a declared character |
| FAB102 | warning | scene has no `show` beat |
| FAB103 | error | invalid length range |
| FAB104 | error | duplicate character name |
| FAB105 | warning | tension value outside [0, 1] |
| FAB106 | warning | unknown block kind (ignored) |
| FAB107 | error | `cast`/`kill` references an unknown character |
| FAB108 | error | a dead character appears in a later scene |
| FAB109 | error | scene references an undeclared thread |
| FAB110 | error | duplicate location/thread name |
| FAB111 | error | unknown literary form |
| FAB112 | error | invalid target word budget |
| FAB201 | warning | book has no world block |
| FAB202 | warning | hard rule without a description |
| FAB203 | warning | a declared thread is never advanced by any scene |
| FAB204 | warning | a thread is silent for more than `thread_gap` consecutive scenes |
| FAB205 | warning | target word budget lies outside the sum of all declared scene length ranges |

## Literary form review

`form` is independent of a plot template. The `short_story` template sets it;
other templates do not guess a form. Profiles live under `forms` in the locale
files and reach the planner, writer and critic. They describe scope, emotional
development and endings without imposing fixed cast or scene counts.

`fabula check-plan BOOK -m codex-cli` reviews the entire saved plan and writes
`build/plan-review.json`. Its JSON contains `verdict`, `summary`, and `issues`,
each with `problem`, `evidence`, and concrete `options` (`focus`, `expand`,
`split`, `keep`). Exit status is 0 for `ok`, 1 for conflicts or no selected form.
The review never edits the source. The Builder displays the same report.

The writer reviews a form-aware plan before the first prose call. An `issues`
report stops generation until the author edits the plan or saves a nonempty
`form_override` explaining the deliberate exception. The original report stays
visible. Clear that explanation when it no longer applies; it persists across
edits. Malformed reviews stop generation even with an exception.

Reviews are reused for the same canonical plan, provider configuration and
review/profile prompts. Changing scenes, form or budget invalidates the cache.
Saving/reopening an unchanged plan does not. Source comments and the exception
explanation do not invalidate it. The compiler's budget check is deterministic;
other editorial findings are model judgments and can vary between runs.

## Canonical form

`fabula/serializer.py` renders the compiled graph back to `.fab`: two-space
indentation, all strings quoted, block-attached comments preserved. The
round-trip `parse → serialize → parse` yields an identical graph, and
`serialize` is stable (serializing twice yields identical text). The web
Builder edits the graph and saves through this serializer.
