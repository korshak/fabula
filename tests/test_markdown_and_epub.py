"""The markdown renderer and the EPUB it feeds.

The prose these render comes back from a language model, so the renderer is
the one place in Fabula that turns untrusted text into markup. The JS copy in
fabula/web/index.html renders the same text same-origin in the studio, where a
`javascript:` URL would run against the studio's own API — the two must keep
the same idea of a safe link, so the scheme list is checked on both sides.
"""

import re
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from fabula.epub import build_epub
from fabula.markdown import to_xhtml

WEB_UI = Path(__file__).resolve().parent.parent / "fabula" / "web" / "index.html"


class Links(unittest.TestCase):
    def test_a_javascript_url_never_becomes_a_link_target(self):
        hostile = [
            "javascript:alert(1)",
            "JavaScript:alert(1)",
            "java\tscript:alert(1)",
            "  javascript:alert(1)",
            "vbscript:msgbox(1)",
            "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            "file:///etc/passwd",
        ]
        for href in hostile:
            with self.subTest(href=href):
                out = to_xhtml(f"[click]({href})")
                self.assertNotIn("javascript", out.lower())
                self.assertNotIn("vbscript", out.lower())
                self.assertNotIn("data:", out.lower())
                self.assertNotIn("file:", out.lower())

    def test_a_reference_link_is_filtered_too(self):
        out = to_xhtml("[click][ref]\n\n[ref]: javascript:alert(1)")
        self.assertNotIn("javascript", out.lower())

    def test_an_image_source_is_filtered_too(self):
        self.assertNotIn("javascript", to_xhtml("![alt](javascript:alert(1))").lower())

    def test_ordinary_links_survive(self):
        for href in ("https://example.org/a?b=c", "http://example.org",
                     "mailto:someone@example.org", "../chapter-2.xhtml", "#anchor"):
            with self.subTest(href=href):
                self.assertIn(href, to_xhtml(f"[text]({href})"))

    def test_markup_in_the_prose_is_escaped_not_executed(self):
        out = to_xhtml('<script>alert(1)</script> & <img src=x onerror=alert(1)>')
        # the characters survive as text; none of them opens a tag
        self.assertNotIn("<script", out)
        self.assertNotIn("<img", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", out)
        self.assertIn("&amp;", out)

    def test_an_attribute_cannot_be_broken_out_of_via_a_title(self):
        out = to_xhtml('[t](https://example.org "a\\" onmouseover=\\"alert(1)")')
        self.assertNotIn("onmouseover=\"alert", out)

    def test_the_studio_and_the_epub_agree_on_which_schemes_are_safe(self):
        """Two renderers, one rule — if they drift, one of them is a hole."""
        from fabula.markdown import _SAFE_SCHEMES
        found = re.search(r"MD_SAFE_SCHEMES = new Set\(\[([^\]]*)\]\)",
                          WEB_UI.read_text(encoding="utf-8"))
        self.assertIsNotNone(found, "the studio's scheme list moved or was removed")
        in_js = tuple(re.findall(r'"([^"]+)"', found.group(1)))
        self.assertEqual(in_js, _SAFE_SCHEMES)


class Structure(unittest.TestCase):
    def test_headings_lists_and_emphasis_render(self):
        out = to_xhtml("# Title\n\nSome *emphasis* and **strength**.\n\n- one\n- two\n")
        for fragment in ("<h1>Title</h1>", "<em>emphasis</em>",
                         "<strong>strength</strong>", "<li>", "one"):
            self.assertIn(fragment, out)

    def test_a_code_span_is_not_markup(self):
        self.assertIn("<code>&lt;b&gt;</code>", to_xhtml("`<b>`"))

    def test_output_is_well_formed_xml(self):
        """EPUB readers reject XHTML that does not parse."""
        from xml.etree import ElementTree
        body = to_xhtml("# T\n\nA paragraph with a [link](https://example.org) & an "
                        "ampersand.\n\n- item\n- item\n\n> quoted\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
        ElementTree.fromstring(f"<root>{body}</root>")


class Epub(unittest.TestCase):
    """A minimal book with one approved scene, straight to a readable file."""

    class FakeState:
        """Just the two methods build_epub asks of a ProjectState."""

        def __init__(self, texts):
            self.texts = texts

        def chosen_text(self, name):
            return self.texts.get(name)

        def scenes_meta(self):
            return {}

    BOOK = {
        "title": "A Title",
        "settings": {"language": "en"},
        "acts": [{"number": 1, "chapters": [{"number": 1, "title": "One", "scenes": [
            {"name": "Opening", "pov": "Ann", "beats": [], "forbid": []}]}]}],
    }

    def test_an_empty_manuscript_produces_no_file(self):
        self.assertIsNone(build_epub(self.BOOK, self.FakeState({})))

    def test_a_written_scene_produces_a_readable_epub(self):
        data = build_epub(self.BOOK, self.FakeState({"Opening": "The prose itself."}))
        self.assertIsInstance(data, bytes)
        with zipfile.ZipFile(BytesIO(data)) as archive:
            self.assertIsNone(archive.testzip())
            names = archive.namelist()
            self.assertEqual(names[0], "mimetype", "mimetype must come first, uncompressed")
            self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
            self.assertIn("META-INF/container.xml", names)
            joined = b"".join(archive.read(n) for n in names if n.endswith(".xhtml"))
            self.assertIn("The prose itself.", joined.decode("utf-8"))

    def test_prose_that_looks_like_markup_does_not_break_the_package(self):
        hostile = "A <script>alert(1)</script> and a [link](javascript:alert(1))."
        data = build_epub(self.BOOK, self.FakeState({"Opening": hostile}))
        with zipfile.ZipFile(BytesIO(data)) as archive:
            pages = b"".join(archive.read(n) for n in archive.namelist()
                             if n.endswith(".xhtml")).decode("utf-8")
        self.assertNotIn("<script", pages)
        self.assertNotIn("javascript:", pages)


if __name__ == "__main__":
    unittest.main()
