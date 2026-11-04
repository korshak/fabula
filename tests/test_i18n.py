"""Locales.

Adding a language is the contribution CONTRIBUTING advertises as the easiest
one, so these are the checks that make reviewing such a PR mechanical: a
translation may be incomplete, but it must never be malformed, and it must
never invent a placeholder the caller does not pass.
"""

import json
import re
import unittest
from unittest import mock

from fabula import i18n
from fabula.i18n import LOCALES_DIR, _fill, available, bundle, t, tn

SOURCES = [LOCALES_DIR.parent / "web" / "index.html"]
SOURCES += sorted((LOCALES_DIR.parent).glob("*.py"))
TN_CALL = re.compile(r'\btn\("([A-Za-z0-9_.]+)"')


def plural_keys() -> set:
    """Every key the code actually pluralizes, read off the call sites.

    Derived rather than listed so that a new tn() call is covered the day it
    lands, without anyone remembering to update this file."""
    found = set()
    for path in SOURCES:
        found |= set(TN_CALL.findall(path.read_text(encoding="utf-8")))
    assert found, "no tn() call sites found — has the helper been renamed?"
    return found

PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def flatten(node, prefix=""):
    """{dotted key: value} for every leaf of a locale file."""
    out = {}
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(flatten(value, path))
        else:
            out[path] = value
    return out


def locales() -> dict:
    return {p.stem: json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(LOCALES_DIR.glob("*.json"))}


class LocaleFiles(unittest.TestCase):
    def setUp(self):
        self.locales = locales()
        self.english = flatten(self.locales["en"])

    def test_english_is_present_and_is_the_fallback(self):
        self.assertIn("en", self.locales)
        self.assertEqual(i18n.FALLBACK_LANG, "en")

    def test_every_locale_declares_its_name_and_plural_rule(self):
        for code, data in self.locales.items():
            with self.subTest(locale=code):
                meta = data.get("_meta", {})
                self.assertTrue(meta.get("name"), "_meta.name is what the UI shows")
                self.assertIn(meta.get("plural"), i18n._PLURAL_RULES,
                              "_meta.plural must name a rule the code implements")

    def test_ukrainian_and_english_are_complete_against_each_other(self):
        """The two first-party locales: a key added to one must reach the other,
        or the author's own UI silently falls back to the wrong language."""
        ukrainian = flatten(self.locales["uk"])
        self.assertEqual(sorted(set(self.english) - set(ukrainian)), [])
        self.assertEqual(sorted(set(ukrainian) - set(self.english)), [])

    def test_no_locale_invents_a_key_english_does_not_have(self):
        """A partial translation is fine; a key with no English original is a
        typo that would never be reached."""
        for code, data in self.locales.items():
            if code == "en":
                continue
            with self.subTest(locale=code):
                self.assertEqual(sorted(set(flatten(data)) - set(self.english) - {"_meta.name",
                                                                                 "_meta.plural"}), [])

    def test_translations_only_use_placeholders_the_english_string_uses(self):
        """`{nam}` for `{name}` renders as literal braces in front of a user."""
        for code, data in self.locales.items():
            if code == "en":
                continue
            for key, value in flatten(data).items():
                if key.startswith("_meta") or key not in self.english:
                    continue
                with self.subTest(locale=code, key=key):
                    self.assertLessEqual(self.placeholders(value),
                                         self.placeholders(self.english[key]))

    @staticmethod
    def placeholders(value) -> set:
        forms = value if isinstance(value, list) else [value]
        return {m for form in forms if isinstance(form, str)
                for m in PLACEHOLDER.findall(form)}

    def test_plural_keys_carry_a_form_for_every_case_of_their_rule(self):
        """A missing form is not a fallback — tn() would index past the end,
        or silently show the wrong form for every count."""
        forms_needed = {"en": 2, "uk": 3}
        for code, data in self.locales.items():
            rule = data.get("_meta", {}).get("plural", "en")
            flat = flatten(data)
            for key in plural_keys():
                if key not in flat:
                    continue        # a partial translation falls back to English
                with self.subTest(locale=code, key=key):
                    self.assertIsInstance(flat[key], list)
                    self.assertEqual(len(flat[key]), forms_needed.get(rule, 2))

    def test_every_pluralized_key_exists_in_english(self):
        for key in plural_keys():
            with self.subTest(key=key):
                self.assertIsInstance(self.english.get(key), list)

    def test_no_locale_string_blows_up_when_filled(self):
        """Prose is full of braces a translator has no reason to escape."""
        for code, data in self.locales.items():
            for key, value in flatten(data).items():
                for form in (value if isinstance(value, list) else [value]):
                    if isinstance(form, str):
                        with self.subTest(locale=code, key=key):
                            _fill(form, {"n": 1})


class Resolution(unittest.TestCase):
    def setUp(self):
        i18n.set_lang(None)
        self.addCleanup(i18n.set_lang, None)

    def test_explicit_language_wins(self):
        i18n.set_lang("uk")
        self.assertEqual(i18n.resolve_lang("en"), "en")

    def test_unknown_language_falls_back_to_english(self):
        self.assertEqual(i18n.resolve_lang("tlh"), "en")

    def test_a_locale_code_can_never_carry_path_syntax(self):
        """The code arrives on `?lang=` of every HTTP request and becomes a
        filename, so it must not be able to leave the locales directory."""
        for hostile in ("../../etc/passwd", "/etc/passwd", "..", "en/../../x", "a" * 64):
            with self.subTest(lang=hostile):
                self.assertEqual(i18n.resolve_lang(hostile), "en")
                self.assertEqual(i18n.load(hostile), {})

    def test_system_locale_is_used_when_nothing_else_says(self):
        with mock.patch.dict("os.environ", {"LANG": "uk_UA.UTF-8"}, clear=False):
            i18n.os.environ.pop("FABULA_LANG", None)
            i18n.os.environ.pop("LC_ALL", None)
            i18n.os.environ.pop("LC_MESSAGES", None)
            self.assertEqual(i18n.resolve_lang(), "uk")

    def test_a_system_locale_we_do_not_ship_falls_back_to_english(self):
        with mock.patch.dict("os.environ", {"LANG": "ja_JP.UTF-8"}, clear=False):
            i18n.os.environ.pop("FABULA_LANG", None)
            i18n.os.environ.pop("LC_ALL", None)
            i18n.os.environ.pop("LC_MESSAGES", None)
            self.assertEqual(i18n.resolve_lang(), "en")

    def test_env_var_overrides_the_system_locale(self):
        with mock.patch.dict("os.environ",
                                      {"LANG": "en_US.UTF-8", "FABULA_LANG": "uk"}, clear=False):
            self.assertEqual(i18n.resolve_lang(), "uk")


class Rendering(unittest.TestCase):
    def test_unknown_key_renders_as_itself(self):
        self.assertEqual(t("no.such.key.at.all"), "no.such.key.at.all")

    def test_ukrainian_plurals_pick_the_three_forms(self):
        forms = {tn("ui.builder.scenes_n", n, "uk") for n in (1, 3, 11)}
        self.assertEqual(len(forms), 3, forms)

    def test_english_plurals_pick_the_two_forms(self):
        self.assertNotEqual(tn("ui.builder.scenes_n", 1, "en"),
                            tn("ui.builder.scenes_n", 5, "en"))
        self.assertIn("1", tn("ui.builder.scenes_n", 1, "en"))

    def test_bundle_fills_gaps_from_english(self):
        merged = bundle("uk")
        self.assertEqual(merged["_lang"], "uk")
        self.assertEqual(set(flatten(locales()["en"])) - set(flatten(merged)), set())

    def test_available_lists_every_shipped_locale(self):
        codes = {entry["code"] for entry in available()}
        self.assertEqual(codes, {p.stem for p in LOCALES_DIR.glob("*.json")})
        self.assertTrue(all(entry["name"] for entry in available()))


if __name__ == "__main__":
    unittest.main()
