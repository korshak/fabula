# Security policy

## Reporting a vulnerability

Please report privately, through
[GitHub's private vulnerability reporting](https://github.com/korshak/fabula/security/advisories/new),
not in a public issue. If that form is unavailable to you, email
denis.korshak@brontelu.com with `fabula security` in the subject.

Expect an acknowledgement within a week. Fabula is a one-maintainer project, so
please do not expect a same-day turnaround — but you will get an answer, and
credit in the advisory unless you would rather not be named.

## What Fabula is, in security terms

Fabula runs entirely on your own machine. There is no Fabula service, no
account and no telemetry; the only traffic it generates is the call to whichever
model provider you configured yourself.

Two properties are worth stating plainly, because they shape what counts as a
vulnerability here.

**The studio is a local, unauthenticated web server.** `fabula serve` binds to
`127.0.0.1` and has no login. It is guarded against the browser, not against the
machine: the `Host` header is checked (so a hostname rebound to `127.0.0.1`
cannot reach it), the `Origin` header is checked (so a page on another site
cannot drive it), and non-JSON request bodies are rejected (so a cross-site
`<form>` cannot either). Any process or user on the same machine, however, can
drive the studio in full. Do not run it on a shared or multi-user host, and do
not expose the port.

**Prose comes back from a language model, and is not trusted.** Model output is
rendered as markdown in the studio and in the EPUB. It is escaped, and link
targets are restricted to `http`, `https` and `mailto` — a `javascript:` URL in
generated text is inert. Anything that gets model-supplied text to execute in
the studio's origin, or to write outside the book's own `build/` directory, is a
vulnerability. Please report it.

Your **API keys** are read from environment variables only. `models.json` stores
the *name* of the variable, never its value, so the file is safe to commit; a key
found written into a config file is a bug in whatever wrote it.

### In scope

- Anything that lets a web page you merely visit read from, or write through,
  the local studio.
- Path traversal: a scene name, variant filename, book name or locale code that
  reaches a file outside the book's own directory.
- Model output that escapes escaping — script execution in the studio, or markup
  injection into an exported EPUB.
- A code path that writes an API key to disk or sends it anywhere but the
  provider it belongs to.

### Not in scope

- Anything that requires an attacker who is already a local user on your
  machine. The studio has no authentication by design.
- Exposing the studio's port to a network yourself, or putting it behind a
  reverse proxy.
- What a model chooses to write. Fabula does not filter prose for content.
- Prompt injection through your own book source or notes — you are the author of
  both. Content that arrives from *outside* your project and steers the agent is
  a different matter; report that.

## Supported versions

Fabula is pre-1.0 (`0.x`). Fixes land on `main`; there are no backports to
earlier tags.
