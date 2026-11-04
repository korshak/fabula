"""The command line, end to end.

Exactly the walk-through the README gives a newcomer — init, compile, plan,
templates, models — run against a throwaway library so a contributor's own
books are never touched. Anything that reaches a model provider is out of
scope here: these must pass with no API key and no network.
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from fabula import library
from fabula.cli import main


class CliCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fabula-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = mock.patch.dict(os.environ, {
            library.ENV: str(self.tmp),      # a library of our own
            "FABULA_HOME": str(self.tmp / "home"),
            "FABULA_LANG": "en",             # assert against English output
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_cli(self, *argv) -> tuple[int, str, str]:
        """(exit code, stdout, stderr). They are kept apart on purpose: the
        machine-readable half of every command goes to stdout, so `fabula
        compile | jq` must not be polluted by human-facing notes."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(list(argv))
            except SystemExit as exit_code:      # argparse and refusals
                code = exit_code.code if isinstance(exit_code.code, int) else 1
                if isinstance(exit_code.code, str):
                    err.write(exit_code.code)
        return code, out.getvalue(), err.getvalue()


class Walkthrough(CliCase):
    def test_init_compile_plan_manuscript(self):
        code, _, _ = self.run_cli("init", "my-book")
        self.assertEqual(code, 0)
        book = self.tmp / "my-book" / "book.fab"
        self.assertTrue(book.is_file(), "init did not scaffold into the library")
        self.assertTrue((self.tmp / "my-book" / "models.json").is_file())

        code, out, _ = self.run_cli("compile", "my-book")
        self.assertEqual(code, 0)
        payload = json.loads(out)      # stdout is the JSON graph, and only that
        self.assertTrue(payload["ok"], payload["diagnostics"])
        self.assertTrue(payload["book"]["title"])

        code, out, _ = self.run_cli("plan", "my-book")
        self.assertEqual(code, 0)
        self.assertIn("BOOK:", out)

        # nothing is written yet, so the manuscript is empty but must not crash
        code, _, _ = self.run_cli("manuscript", "my-book", "-o", str(self.tmp / "out.md"))
        self.assertEqual(code, 0)

    def test_init_never_overwrites_an_existing_book(self):
        """Running init twice must be harmless — the author's own text is the
        one thing the scaffolder must never touch."""
        self.assertEqual(self.run_cli("init", "my-book")[0], 0)
        book = self.tmp / "my-book" / "book.fab"
        book.write_text('book "Mine" { language: "en"; }', encoding="utf-8")

        code, _, err = self.run_cli("init", "my-book")
        self.assertEqual(code, 0)
        self.assertIn("book.fab", err, "the author was not told the file was kept")
        self.assertEqual(book.read_text(encoding="utf-8"),
                         'book "Mine" { language: "en"; }')

    def test_a_scaffolded_book_round_trips_through_the_compiler(self):
        self.run_cli("init", "round-trip")
        from fabula.compiler import compile_source
        from fabula.serializer import serialize_book
        source = (self.tmp / "round-trip" / "book.fab").read_text(encoding="utf-8")
        first = compile_source(source)
        self.assertTrue(first["ok"], first["diagnostics"])
        self.assertTrue(compile_source(serialize_book(first["book"]))["ok"])

    def test_every_template_scaffolds_a_book_that_compiles(self):
        code, out, _ = self.run_cli("templates")
        self.assertEqual(code, 0)
        from fabula.templates import list_templates
        for template in list_templates("en"):
            with self.subTest(template=template["id"]):
                name = f"tpl-{template['id']}"
                self.assertEqual(self.run_cli("init", name, "--template", template["id"])[0], 0)
                payload = json.loads(self.run_cli("compile", name)[1])
                self.assertTrue(payload["ok"], payload["diagnostics"])
                self.assertIn(template["id"], out)

    def test_compiling_a_book_that_is_not_there_fails_loudly(self):
        code, out, err = self.run_cli("compile", "no-such-book")
        self.assertNotEqual(code, 0)
        self.assertTrue((out + err).strip())

    def test_models_reports_status_without_a_key_or_a_network(self):
        """`fabula models` is the first command a newcomer runs when nothing
        generates — it must explain itself, not raise."""
        self.run_cli("init", "my-book")
        code, out, err = self.run_cli("models", "my-book")
        self.assertEqual(code, 0)
        self.assertTrue((out + err).strip())

    def test_the_interface_language_follows_the_environment(self):
        self.run_cli("init", "my-book")
        with mock.patch.dict(os.environ, {"FABULA_LANG": "uk"}):
            self.assertIn("КНИГА:", self.run_cli("plan", "my-book")[1])


if __name__ == "__main__":
    unittest.main()
