"""The book library: every project is one folder under `books/`.

A book folder holds everything about that book — `book.fab`, its optional
`models.json` and the generated `build/` state — so a book travels, is backed
up or is deleted as a single directory.  The library root is the `books/`
directory of the checkout, overridable with `$FABULA_BOOKS`.

Anything that names a book (CLI arguments, the studio's project manager)
goes through `book_file()`/`book_dir()`: a bare name is a folder in the
library, an explicit path is still honoured as given.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ENV = "FABULA_BOOKS"
DIRNAME = "books"
BOOK_FILE = "book.fab"


def root() -> Path:
    """The library directory: `$FABULA_BOOKS`, else `books/` of the checkout."""
    env = (os.environ.get(ENV) or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    checkout = Path(__file__).resolve().parent.parent
    base = checkout if (checkout / "pyproject.toml").exists() else Path.cwd()
    return (base / DIRNAME).resolve()


def _is_path(spec: str) -> bool:
    """True for anything the user clearly meant as a path, not a book name."""
    return (spec.startswith("~") or spec.startswith(".")
            or os.sep in spec or "/" in spec or Path(spec).is_absolute())


def book_dir(spec: str | Path) -> Path:
    """The folder of a book: a bare name resolves inside the library.

    A name that already exists in the current directory wins over the library,
    so `fabula plan some-dir` keeps working from anywhere.
    """
    text = str(spec).strip()
    path = Path(text).expanduser()
    if not _is_path(text) and not path.exists():
        path = root() / text
    path = path.resolve()
    return path.parent if path.suffix == ".fab" else path


def book_file(spec: str | Path) -> Path:
    """The `.fab` file of a book (a folder resolves to its `book.fab`)."""
    text = str(spec).strip()
    path = Path(text).expanduser()
    if not _is_path(text) and not path.exists():
        path = root() / text
    path = path.resolve()
    return path / BOOK_FILE if path.suffix != ".fab" else path


def build_dir(file: str | Path) -> Path:
    """Where a book keeps its generated state: `build/` next to the source."""
    return Path(file).expanduser().resolve().parent / "build"


def discover() -> list[Path]:
    """Every `book.fab` in the library, most recently edited first."""
    base = root()
    if not base.is_dir():
        return []
    found = [entry / BOOK_FILE for entry in base.iterdir()
             if (entry / BOOK_FILE).is_file()]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def default_book() -> Path | None:
    """The book to open when none was named: the last one worked on."""
    found = discover()
    return found[0] if found else None


TITLE_RE = re.compile(r'^\s*book\s+"([^"]*)"', re.MULTILINE)


def peek_title(file: str | Path) -> str | None:
    """The book's title straight from the source, without a full compile.

    Used to label books in the library that have never been opened; a book
    that does not parse simply has no title to show.
    """
    try:
        head = Path(file).read_text(encoding="utf-8")[:4096]
    except OSError:
        return None
    match = TITLE_RE.search(head)
    return match.group(1).strip() or None if match else None
