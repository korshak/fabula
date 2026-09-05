"""Project state on disk, and how a book name resolves to a folder.

The concurrency test here is the regression guard for the lost-approval bug:
two threads each holding their own (stale) ProjectState used to save their own
copy of the whole document, and whoever wrote last silently dropped the other's
work.
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from fabula import library
from fabula.state import ProjectState, slug


class Slugs(unittest.TestCase):
    def test_a_scene_name_becomes_a_single_safe_path_segment(self):
        for name in ("../../etc/passwd", "a/b\\c", "..", "  ", "///"):
            with self.subTest(name=name):
                self.assertNotIn("/", slug(name))
                self.assertNotIn("\\", slug(name))
                self.assertNotIn("..", slug(name).strip("-"))
                self.assertTrue(slug(name))

    def test_non_latin_names_keep_their_letters(self):
        self.assertEqual(slug("Тіні пам'яті"), "тіні-пам-яті")

    def test_an_empty_name_still_yields_a_directory(self):
        self.assertEqual(slug("!!!"), "scene")

    def test_a_scene_directory_stays_under_the_build_directory(self):
        build = Path(tempfile.mkdtemp(prefix="fabula-test-"))
        self.addCleanup(shutil.rmtree, build, True)
        state = ProjectState(build)
        for name in ("../../../etc", "..", "a/../../b"):
            with self.subTest(name=name):
                resolved = state.scene_dir(name).resolve()
                self.assertTrue(str(resolved).startswith(str(build.resolve())), resolved)


class StateFile(unittest.TestCase):
    def setUp(self):
        self.build = Path(tempfile.mkdtemp(prefix="fabula-test-"))
        self.addCleanup(shutil.rmtree, self.build, True)
        self.state = ProjectState(self.build)

    def variant(self, scene, filename, text="some prose here"):
        directory = self.state.scene_dir(scene)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / filename).write_text(text, encoding="utf-8")
        self.state.record_variant(scene, filename, {"verdict": "ok"})

    def test_a_missing_state_file_reads_as_an_empty_project(self):
        self.assertEqual(ProjectState(self.build / "nope").data, {"scenes": {}})

    def test_a_corrupt_state_file_does_not_block_the_studio(self):
        self.build.mkdir(parents=True, exist_ok=True)
        (self.build / "state.json").write_text("{ not json", encoding="utf-8")
        self.assertEqual(ProjectState(self.build).data, {"scenes": {}})

    def test_approving_a_variant_that_does_not_exist_is_refused(self):
        with self.assertRaises(KeyError):
            self.state.approve("Opening", "v1.md")

    def test_the_chosen_text_is_the_approved_one(self):
        self.variant("Opening", "v1.md", "first")
        self.variant("Opening", "v2.md", "second")
        self.assertIsNone(self.state.chosen_text("Opening"))   # two, none approved
        self.state.approve("Opening", "v2.md")
        self.assertEqual(self.state.chosen_text("Opening"), "second")

    def test_a_lone_variant_needs_no_approval(self):
        self.variant("Opening", "v1.md", "only one")
        self.assertEqual(self.state.chosen_text("Opening"), "only one")

    def test_comments_round_trip_and_delete_by_id(self):
        self.variant("Opening", "v1.md")
        cid = self.state.add_comment("Opening", "v1.md", "some", "tighten this")
        self.assertEqual([c["id"] for c in self.state.comments("Opening", "v1.md")], [cid])
        self.state.delete_comment("Opening", "v1.md", cid)
        self.assertEqual(self.state.comments("Opening", "v1.md"), [])

    def test_a_write_is_never_seen_half_finished(self):
        self.variant("Opening", "v1.md")
        raw = (self.build / "state.json").read_text(encoding="utf-8")
        json.loads(raw)                                   # parses at rest
        self.assertFalse(list(self.build.glob("*.tmp")),  # and leaves no scratch file
                         "the temporary file outlived the replace")

    def test_concurrent_writers_do_not_drop_each_others_work(self):
        """The regression guard: every writer holds its own stale snapshot."""
        scenes = [f"Scene {i}" for i in range(24)]
        for name in scenes:
            self.variant(name, "v1.md")

        def approve(name):
            ProjectState(self.build).approve(name, "v1.md")   # a fresh, soon-stale snapshot

        threads = [threading.Thread(target=approve, args=(name,)) for name in scenes]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        final = ProjectState(self.build)
        approved = {name for name in scenes if final.scene(name)["approved"] == "v1.md"}
        self.assertEqual(approved, set(scenes), f"lost {len(scenes) - len(approved)} approvals")

    def test_renaming_a_scene_carries_its_variants_along(self):
        self.variant("Old Name", "v1.md", "prose")
        self.state.rename_scene("Old Name", "New Name")
        moved = ProjectState(self.build)
        self.assertIn("New Name", moved.data["scenes"])
        self.assertNotIn("Old Name", moved.data["scenes"])
        self.assertEqual(moved.variant_text("New Name", "v1.md"), "prose")


class Library(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fabula-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.previous = os.environ.get(library.ENV)
        os.environ[library.ENV] = str(self.tmp)
        self.addCleanup(self.restore)

    def restore(self):
        if self.previous is None:
            os.environ.pop(library.ENV, None)
        else:
            os.environ[library.ENV] = self.previous

    def make_book(self, name="Some Book") -> Path:
        directory = self.tmp / name
        directory.mkdir(parents=True)
        (directory / "book.fab").write_text('book "T" { language: "en"; }', encoding="utf-8")
        return directory

    def test_the_env_var_moves_the_library(self):
        self.assertEqual(library.root(), self.tmp)

    def test_a_bare_name_resolves_inside_the_library(self):
        self.assertEqual(library.book_dir("Some Book"), self.tmp / "Some Book")
        self.assertEqual(library.book_file("Some Book"), self.tmp / "Some Book" / "book.fab")

    def test_naming_the_fab_file_resolves_to_the_same_book(self):
        self.make_book()
        directory = self.tmp / "Some Book"
        self.assertEqual(library.book_file(directory), directory / "book.fab")
        self.assertEqual(library.book_dir(directory / "book.fab"), directory)

    def test_an_explicit_path_is_honoured_as_given(self):
        """The CLI must keep working on a book kept outside the library."""
        outside = self.tmp.parent / "elsewhere.fab"
        self.assertEqual(library.book_file(str(outside)), outside.resolve())

    def test_discover_lists_books_most_recently_edited_first(self):
        first = self.make_book("First")
        second = self.make_book("Second")
        os.utime(second / "book.fab", (1, 1))          # older
        os.utime(first / "book.fab", (10**9, 10**9))   # newer
        self.assertEqual(library.discover()[0], first / "book.fab")
        self.assertEqual(library.default_book(), first / "book.fab")

    def test_an_empty_library_has_no_default_book(self):
        self.assertEqual(library.discover(), [])
        self.assertIsNone(library.default_book())

    def test_the_title_can_be_read_without_a_full_compile(self):
        directory = self.make_book("Titled")
        (directory / "book.fab").write_text('book "Читається" {\n}', encoding="utf-8")
        self.assertEqual(library.peek_title(directory / "book.fab"), "Читається")

    def test_an_unparsable_book_simply_has_no_title(self):
        directory = self.make_book("Broken")
        (directory / "book.fab").write_text("not a book at all", encoding="utf-8")
        self.assertIsNone(library.peek_title(directory / "book.fab"))
        self.assertIsNone(library.peek_title(self.tmp / "nothing here.fab"))

    def test_build_state_lives_next_to_the_source(self):
        directory = self.make_book()
        self.assertEqual(library.build_dir(directory / "book.fab"), directory / "build")


if __name__ == "__main__":
    unittest.main()
