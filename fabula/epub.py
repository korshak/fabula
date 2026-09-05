"""EPUB 3 export: approved scenes -> a standards-shaped .epub, stdlib only.

An EPUB is a ZIP archive: an uncompressed `mimetype` entry first, then
META-INF/container.xml pointing at the OPF package, XHTML chapters, an EPUB 3
nav document (plus a legacy NCX for older readers), and a stylesheet.
Chapter/act headings are rendered in the BOOK's language (same mechanism as
the writing prompts).
"""

from __future__ import annotations

import hashlib
import html
import io
import zipfile

from .compiler import iter_scenes
from .markdown import to_xhtml as _paras
from .i18n import t as _t


def _bp(book: dict, key: str, **params) -> str:
    lang = str((book.get("settings") or {}).get("language") or "uk")
    return _t("prompts." + key, lang, **params)


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def _xhtml(lang: str, title: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{_esc(lang)}" lang="{_esc(lang)}">\n'
        f"<head><title>{_esc(title)}</title>"
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        f"<body>\n{body}\n</body>\n</html>\n"
    )


_CSS = """body { font-family: Georgia, 'Times New Roman', serif; line-height: 1.6; margin: 5%; }
h1 { text-align: center; font-size: 1.6em; margin: 1.4em 0 0.3em; }
h2 { text-align: center; font-size: 1.25em; font-weight: normal; font-style: italic; margin: 0.2em 0 1.6em; }
p { text-indent: 1.4em; margin: 0 0 0.2em; text-align: justify; }
p:first-of-type { text-indent: 0; }
.title-page { text-align: center; margin-top: 30%; }
.title-page h1 { font-size: 2em; font-style: italic; }
hr.scene-break { border: none; text-align: center; margin: 1.2em 0; }
hr.scene-break:after { content: "\\2766"; }
h3, h4, h5, h6 { margin: 1.4em 0 0.4em; }
em { font-style: italic; }
strong { font-weight: bold; }
del { text-decoration: line-through; }
ul, ol { margin: 0.6em 0 0.8em 1.4em; padding: 0; }
li { margin: 0.2em 0; text-align: left; }
li p { text-indent: 0; text-align: left; }
li.task { list-style: none; margin-left: -1em; }
blockquote { margin: 0.8em 1.4em; font-style: italic; }
blockquote p { text-indent: 0; }
code { font-family: 'Courier New', monospace; font-size: 0.9em; }
pre { font-family: 'Courier New', monospace; font-size: 0.85em; margin: 0.8em 0;
      padding: 0.6em 0.8em; background: #f2f0eb; white-space: pre-wrap; }
pre code { font-size: 1em; }
img { max-width: 100%; }
table { border-collapse: collapse; margin: 0.8em 0; font-size: 0.92em; }
th, td { border: 1px solid #b9b3a6; padding: 0.28em 0.6em; text-align: left; }
th { background: #f2f0eb; }
"""


def build_epub(book: dict, state, translation=None) -> bytes | None:
    """Assemble the chosen scene texts into an .epub; None when nothing is written.

    With `translation` (a TranslationState), the translated scene texts are
    used instead of the originals, and the title/headings/language metadata
    come from that translation's glossary.
    """
    if translation is not None:
        from .translator import chapter_heading as _tr_chapter, translated_title
        lang = translation.lang
        title = translated_title(book, translation)
    else:
        lang = str((book.get("settings") or {}).get("language") or "uk")
        title = book.get("title") or "Fabula"

    # group chosen texts per chapter, remembering act boundaries
    chapters = []  # {"id", "act", "act_first", "heading", "texts": [...]}
    last_act = None
    for act, chapter, scene in iter_scenes(book):
        text = (translation.text(scene["name"]) if translation is not None
                else state.chosen_text(scene["name"]))
        if not text:
            continue
        heading = (_tr_chapter(book, translation, chapter) if translation is not None
                   else _bp(book, "chapter_heading", n=chapter["number"], title=chapter["title"]))
        if not chapters or chapters[-1]["heading"] != heading:
            chapters.append({"id": f"ch-{len(chapters) + 1}", "heading": heading,
                             "act": act["number"], "act_first": act["number"] != last_act,
                             "texts": []})
            last_act = act["number"]
        chapters[-1]["texts"].append(text.strip())
    if not chapters:
        return None

    many_acts = len({c["act"] for c in chapters}) > 1
    files: list[tuple[str, str]] = [("style.css", _CSS)]
    files.append(("title.xhtml", _xhtml(lang, title,
                  f'<div class="title-page"><h1>{_esc(title)}</h1></div>')))
    for ch in chapters:
        parts = []
        if many_acts and ch["act_first"]:
            if translation is not None:
                from .translator import act_heading as _tr_act
                act_hdr = _tr_act(book, translation, ch["act"])
            else:
                act_hdr = _bp(book, "act_heading", n=ch["act"])
            parts.append(f"<h1>{_esc(act_hdr)}</h1>")
        parts.append(f"<h2>{_esc(ch['heading'])}</h2>")
        parts.append('\n<hr class="scene-break"/>\n'.join(_paras(x) for x in ch["texts"]))
        files.append((ch["id"] + ".xhtml", _xhtml(lang, ch["heading"], "\n".join(parts))))

    uid = "urn:fabula:" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]
    spine_items = [("title", "title.xhtml")] + [(c["id"], c["id"] + ".xhtml") for c in chapters]

    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="style.css" media-type="text/css"/>']
    manifest += [f'<item id="{i}" href="{h}" media-type="application/xhtml+xml"/>'
                 for i, h in spine_items]
    spine = "".join(f'<itemref idref="{i}"/>' for i, _ in spine_items)
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'<dc:identifier id="uid">{uid}</dc:identifier>\n'
        f"<dc:title>{_esc(title)}</dc:title>\n"
        f"<dc:language>{_esc(lang)}</dc:language>\n"
        '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>\n'
        "</metadata>\n"
        f"<manifest>{''.join(manifest)}</manifest>\n"
        f"<spine toc=\"ncx\">{spine}</spine>\n"
        "</package>\n"
    )

    nav_lis = "".join(f'<li><a href="{c["id"]}.xhtml">{_esc(c["heading"])}</a></li>'
                      for c in chapters)
    nav = _xhtml(lang, title,
                 '<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc">'
                 f"<h1>{_esc(title)}</h1><ol>{nav_lis}</ol></nav>"
                 ).replace('<html xmlns="http://www.w3.org/1999/xhtml"',
                           '<html xmlns="http://www.w3.org/1999/xhtml" '
                           'xmlns:epub="http://www.idpf.org/2007/ops"')

    nav_points = "".join(
        f'<navPoint id="{c["id"]}" playOrder="{i + 1}">'
        f"<navLabel><text>{_esc(c['heading'])}</text></navLabel>"
        f'<content src="{c["id"]}.xhtml"/></navPoint>'
        for i, c in enumerate(chapters))
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        f'<head><meta name="dtb:uid" content="{uid}"/></head>\n'
        f"<docTitle><text>{_esc(title)}</text></docTitle>\n"
        f"<navMap>{nav_points}</navMap>\n</ncx>\n"
    )

    container = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles>\n</container>\n'
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # the mimetype entry must be first and uncompressed — per the EPUB spec
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for name, content in [("META-INF/container.xml", container),
                              ("OEBPS/content.opf", opf),
                              ("OEBPS/nav.xhtml", nav),
                              ("OEBPS/toc.ncx", ncx)] + \
                             [("OEBPS/" + n, c) for n, c in files]:
            zf.writestr(name, content, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()
