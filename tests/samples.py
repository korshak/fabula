"""The sample books that ship with the repository, as test fixtures.

They are the only books in `books/` that are not gitignored, and they are what
the README screenshots show — so keeping them compiling is part of keeping the
documentation honest.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOKS = ROOT / "books"


def book_files() -> list[Path]:
    found = sorted(p for p in BOOKS.glob("*/book.fab"))
    assert found, f"no sample books under {BOOKS}"
    return found


def read(name: str) -> str:
    return (BOOKS / name / "book.fab").read_text(encoding="utf-8")
