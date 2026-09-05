"""Entry point for the Fabula web UI (used by .claude/launch.json).

Opens the library — one folder per book under books/ — starting with the
book worked on most recently, or the bundled sample book.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from fabula import library  # noqa: E402
from fabula.server import serve  # noqa: E402

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    book = library.default_book() or library.book_file("Тіні пам'яті")
    sys.exit(serve(book, build_dir=library.build_dir(book), port=port))
