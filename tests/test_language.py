"""The language front end: compile, diagnose, serialize, round-trip.

These are the invariants CONTRIBUTING promises — diagnostics are API, and the
serializer is a lossless inverse of the parser — so they are the ones a
contributor must not be able to break silently.
"""

import unittest

from fabula.compiler import compile_source, iter_scenes
from fabula.lexer import FabulaSyntaxError, tokenize
from fabula.parser import parse
from fabula.serializer import serialize_book

from . import samples

MINIMAL = '''
book "T" {
  language: "en";
  character "Ann" { age: 30; }
  act 1 {
    chapter 1 "One" {
      scene "Opening" {
        pov: "Ann";
        length: 100..200;
        show: "something happens";
      }
    }
  }
}
'''


class CompileSamples(unittest.TestCase):
    def test_every_sample_book_compiles_clean(self):
        for path in samples.book_files():
            with self.subTest(book=path.parent.name):
                result = compile_source(path.read_text(encoding="utf-8"))
                self.assertTrue(result["ok"], result["diagnostics"])
                errors = [d for d in result["diagnostics"] if d["severity"] == "error"]
                self.assertEqual(errors, [])
                self.assertTrue(result["book"]["title"])
                self.assertTrue(list(iter_scenes(result["book"])))

    def test_scene_walk_is_ordered_by_act_then_chapter(self):
        book = compile_source(samples.read("Shadows of Memory"))["book"]
        keys = [(a["number"], c["number"]) for a, c, _ in iter_scenes(book)]
        self.assertEqual(keys, sorted(keys))


class Diagnostics(unittest.TestCase):
    """Codes are API: an agent repairs its own source from them."""

    def codes(self, source: str) -> set:
        return {d["code"] for d in compile_source(source)["diagnostics"]}

    def test_unknown_pov_is_fab101(self):
        self.assertIn("FAB101", self.codes(MINIMAL.replace('pov: "Ann"', 'pov: "Nobody"')))

    def test_scene_without_show_beat_is_fab102(self):
        self.assertIn("FAB102", self.codes(MINIMAL.replace(
            'show: "something happens";', 'forbid: "anything at all";')))

    def test_inverted_length_range_is_fab103(self):
        self.assertIn("FAB103", self.codes(MINIMAL.replace("length: 100..200", "length: 200..100")))

    def test_duplicate_character_is_fab104(self):
        self.assertIn("FAB104", self.codes(MINIMAL.replace(
            'character "Ann" { age: 30; }',
            'character "Ann" { age: 30; }\n  character "Ann" { age: 31; }')))

    def test_missing_world_is_fab201(self):
        self.assertIn("FAB201", self.codes(MINIMAL))

    def test_every_diagnostic_carries_a_position_and_a_hint_field(self):
        for d in compile_source(MINIMAL.replace('pov: "Ann"', 'pov: "Nobody"'))["diagnostics"]:
            self.assertIsInstance(d["line"], int)
            self.assertIsInstance(d["col"], int)
            self.assertIn("hint", d)
            self.assertIn(d["severity"], ("error", "warning"))

    def test_syntax_error_compiles_to_a_diagnostic_not_an_exception(self):
        result = compile_source('book "T" { character "Ann" { ')
        self.assertFalse(result["ok"])
        self.assertIsNone(result["book"])
        self.assertTrue(result["diagnostics"])

    def test_lexer_reports_position(self):
        with self.assertRaises(FabulaSyntaxError) as caught:
            tokenize('book "unterminated')
        self.assertTrue(caught.exception.to_diagnostic()["code"].startswith("FAB"))


class SerializerRoundTrip(unittest.TestCase):
    """graph -> .fab -> graph must be the identity, or the Builder silently
    eats attributes it does not know about."""

    @staticmethod
    def without_positions(node):
        """`_line` records where a node sat in *this* source, so re-emitting
        legitimately moves it. Everything else must survive untouched."""
        if isinstance(node, dict):
            return {k: SerializerRoundTrip.without_positions(v)
                    for k, v in node.items() if k != "_line"}
        if isinstance(node, list):
            return [SerializerRoundTrip.without_positions(v) for v in node]
        return node

    def assert_round_trips(self, source: str):
        first = compile_source(source)
        self.assertTrue(first["ok"], first["diagnostics"])
        emitted = serialize_book(first["book"])
        second = compile_source(emitted)
        self.assertTrue(second["ok"], second["diagnostics"])
        self.assertEqual(self.without_positions(first["book"]),
                         self.without_positions(second["book"]))
        # and a second pass must be byte-identical: the output is canonical
        self.assertEqual(emitted, serialize_book(second["book"]))

    def test_samples_round_trip(self):
        for path in samples.book_files():
            with self.subTest(book=path.parent.name):
                self.assert_round_trips(path.read_text(encoding="utf-8"))

    def test_minimal_book_round_trips(self):
        self.assert_round_trips(MINIMAL)

    def test_comments_survive_the_round_trip(self):
        book = compile_source(samples.read("Shadows of Memory"))["book"]
        self.assertTrue(book["_comments"], "the sample lost its header comments")
        self.assertIn(book["_comments"][0], serialize_book(book))

    def test_quotes_in_a_title_survive(self):
        source = MINIMAL.replace('book "T"', 'book "A \\"quoted\\" title"')
        self.assert_round_trips(source)
        self.assertEqual(compile_source(source)["book"]["title"], 'A "quoted" title')


class Parser(unittest.TestCase):
    def test_blocks_know_where_they_came_from(self):
        blocks = parse(MINIMAL)
        self.assertEqual(blocks[0].kind, "book")
        self.assertGreater(blocks[0].line, 0)


if __name__ == "__main__":
    unittest.main()
