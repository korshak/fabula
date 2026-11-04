"""Markdown -> XHTML for the EPUB export.

The studio reads and writes scene variants as Markdown, so the export has to
understand the same grammar the editor does: ATX and setext headings, thematic
breaks, fenced code, blockquotes, bullet/ordered/task lists with nesting, GFM
tables, paragraphs; inline code spans, backslash escapes, inline/reference/auto
links, images, strong, emphasis and strikethrough.

Two deliberate departures from CommonMark, both mirrored in the web UI's
markdown module (fabula/web/index.html) and kept in step with it: a single
newline inside a paragraph is a hard break, because the writing agent lays
scenes out that way; and raw HTML in a file is emitted as text rather than
markup, so nothing a model wrote into a book can inject markup into a reader.

Output is XHTML (void elements closed), as EPUB 3 requires.
"""

from __future__ import annotations

import html
import re

_HOLE = "\ue000"

_BREAK_RE = re.compile(r"^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
_ATX_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_FENCE_RE = re.compile(r"^( {0,3})(`{3,}|~{3,})[ \t]*(.*)$")
_QUOTE_RE = re.compile(r"^ {0,3}>[ \t]?")
_ITEM_RE = re.compile(r"^( *)([-+*]|\d{1,9}[.)])([ \t]+)(.*)$")
_SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_DELIM_RE = re.compile(r"^ {0,3}\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")
_REF_RE = re.compile(r"""^ {0,3}\[([^\]]+)\]:[ \t]*(\S+)(?:[ \t]+["'(](.*)["')])?[ \t]*$""")
_TASK_RE = re.compile(r"^\[([ xX])\][ \t]+(.*)$")


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def _esc_text(text: str) -> str:
    """Only &<> — a quote has to stay itself so a link title can be read."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _blank(line: str) -> bool:
    return not line.strip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _opens_block(line: str) -> bool:
    return bool(_FENCE_RE.match(line) or _ATX_RE.match(line) or _BREAK_RE.match(line)
                or _QUOTE_RE.match(line) or _ITEM_RE.match(line))


def to_xhtml(text: str) -> str:
    """Render one prose document."""
    src = str(text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    lines, refs = _take_refs(src.split("\n"))
    return _blocks(lines, refs)


def _take_refs(lines: list[str]) -> tuple[list[str], dict]:
    refs: dict[str, tuple[str, str]] = {}
    kept: list[str] = []
    fence = None
    for line in lines:
        f = _FENCE_RE.match(line)
        if fence:
            if f and f.group(2)[0] == fence[0] and len(f.group(2)) >= len(fence):
                fence = None
            kept.append(line)
            continue
        if f:
            fence = f.group(2)
            kept.append(line)
            continue
        m = _REF_RE.match(line)
        if m:
            refs[m.group(1).strip().lower()] = (m.group(2).strip("<>"), m.group(3) or "")
            continue
        kept.append(line)
    return kept, refs


def _blocks(lines: list[str], refs: dict) -> str:
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _blank(line):
            i += 1
            continue

        fence = _FENCE_RE.match(line)
        if fence:
            i = _fence(lines, i, fence, out)
            continue

        if _BREAK_RE.match(line):
            out.append('<hr class="scene-break"/>')
            i += 1
            continue

        atx = _ATX_RE.match(line)
        if atx:
            n = len(atx.group(1))
            body = re.sub(r"[ \t]+#+[ \t]*$", "", atx.group(2) or "")
            out.append(f"<h{n}>{_inline(body, refs)}</h{n}>")
            i += 1
            continue

        if _QUOTE_RE.match(line):
            i = _quote(lines, i, refs, out)
            continue

        if _ITEM_RE.match(line):
            i = _list(lines, i, refs, out)
            continue

        if ("|" in line and i + 1 < len(lines)
                and _DELIM_RE.match(lines[i + 1]) and "-" in lines[i + 1]):
            i = _table(lines, i, refs, out)
            continue

        i = _paragraph(lines, i, refs, out)
    return "".join(out)


def _fence(lines: list[str], i: int, fence: re.Match, out: list[str]) -> int:
    pad, marks, info = fence.group(1), fence.group(2), fence.group(3)
    close = re.compile(r"^ {0,3}" + re.escape(marks[0]) + "{" + str(len(marks)) + r",}[ \t]*$")
    buf: list[str] = []
    i += 1
    while i < len(lines) and not close.match(lines[i]):
        line = lines[i]
        buf.append(line[len(pad):] if line.startswith(pad) else line)
        i += 1
    if i < len(lines):
        i += 1                                       # the closing fence
    lang = info.strip().split(" ")[0] if info.strip() else ""
    attr = f' class="language-{_esc(lang)}"' if lang else ""
    body = _esc("\n".join(buf))
    out.append(f"<pre><code{attr}>{body}</code></pre>")
    return i


def _quote(lines: list[str], i: int, refs: dict, out: list[str]) -> int:
    buf: list[str] = []
    while i < len(lines):
        line = lines[i]
        if _QUOTE_RE.match(line):
            buf.append(_QUOTE_RE.sub("", line, count=1))
            i += 1
            continue
        # a plain line right after a quoted one keeps the quote going
        if not _blank(line) and buf and not _opens_block(line):
            buf.append(line)
            i += 1
            continue
        break
    out.append(f"<blockquote>{_blocks(buf, refs)}</blockquote>")
    return i


def _list(lines: list[str], i: int, refs: dict, out: list[str]) -> int:
    first = _ITEM_RE.match(lines[i])
    ordered = first.group(2)[0].isdigit()
    start = int(re.sub(r"\D", "", first.group(2))) if ordered else 1
    items: list[list[str]] = []
    loose = False

    while i < len(lines):
        m = _ITEM_RE.match(lines[i])
        if not m or m.group(2)[0].isdigit() != ordered:
            break
        if len(m.group(1)) > len(first.group(1)) + 3:
            break
        cont = len(m.group(1)) + len(m.group(2)) + len(m.group(3))
        buf = [m.group(4)]
        i += 1
        while i < len(lines):
            line = lines[i]
            if _blank(line):
                nxt = lines[i + 1] if i + 1 < len(lines) else None
                if nxt is None or _blank(nxt) or not (_indent(nxt) >= cont or _ITEM_RE.match(nxt)):
                    i += 1
                    break
                if _ITEM_RE.match(nxt) and _indent(nxt) < cont:
                    i += 1
                    loose = True
                    break
                buf.append("")
                loose = True
                i += 1
                continue
            if _indent(line) >= cont:
                buf.append(line[cont:])
                i += 1
                continue
            if _ITEM_RE.match(line) or _opens_block(line):
                break
            buf.append(line)                          # lazy continuation
            i += 1
        items.append(buf)

    rows = []
    for buf in items:
        task = _TASK_RE.match(buf[0]) if buf else None
        if task:
            buf = [task.group(2)] + buf[1:]
        box = ('<input type="checkbox"'
               + (' checked="checked"' if task.group(1).lower() == "x" else "")
               + ' disabled="disabled"/> ') if task else ""
        simple = not loose and not any(_blank(l) or _opens_block(l) for l in buf[1:])
        inner = _inline("\n".join(buf), refs) if simple else _blocks(buf, refs)
        cls = ' class="task"' if task else ""
        rows.append(f"<li{cls}>{box}{inner}</li>")

    body = "".join(rows)
    if ordered:
        out.append(f'<ol start="{start}">{body}</ol>' if start != 1 else f"<ol>{body}</ol>")
    else:
        out.append(f"<ul>{body}</ul>")
    return i


def _cells(row: str) -> list[str]:
    cells, cur, escaped = [], "", False
    s = row.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    for ch in s:
        if escaped:
            cur += ch
            escaped = False
        elif ch == "\\":
            cur += ch
            escaped = True
        elif ch == "|":
            cells.append(cur)
            cur = ""
        else:
            cur += ch
    cells.append(cur)
    return [c.strip() for c in cells]


def _table(lines: list[str], i: int, refs: dict, out: list[str]) -> int:
    head = _cells(lines[i])
    align = []
    for c in _cells(lines[i + 1]):
        if re.match(r"^:-+:$", c):
            align.append("center")
        elif c.endswith(":"):
            align.append("right")
        elif c.startswith(":"):
            align.append("left")
        else:
            align.append("")
    i += 2
    rows = []
    while i < len(lines) and not _blank(lines[i]) and "|" in lines[i]:
        rows.append(_cells(lines[i]))
        i += 1

    def cell(tag: str, text: str, k: int) -> str:
        style = f' style="text-align:{align[k]}"' if k < len(align) and align[k] else ""
        return f"<{tag}{style}>{_inline(text, refs)}</{tag}>"

    head_html = "".join(cell("th", c, k) for k, c in enumerate(head))
    body_html = "".join(
        "<tr>" + "".join(cell("td", r[k] if k < len(r) else "", k)
                         for k in range(len(head))) + "</tr>"
        for r in rows)
    out.append(f"<table><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>")
    return i


def _paragraph(lines: list[str], i: int, refs: dict, out: list[str]) -> int:
    buf: list[str] = []
    while i < len(lines) and not _blank(lines[i]):
        setext = _SETEXT_RE.match(lines[i]) if buf else None
        if setext:
            n = 1 if setext.group(1)[0] == "=" else 2
            out.append(f"<h{n}>{_inline(chr(10).join(buf), refs)}</h{n}>")
            return i + 1
        if buf and _opens_block(lines[i]):
            break
        buf.append(lines[i])
        i += 1
    if buf:
        out.append(f"<p>{_inline(chr(10).join(buf), refs)}</p>")
    return i


_CODE_SPAN_RE = re.compile(r"(`+)([\s\S]*?[^`])\1(?!`)")
_ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")
_LINK_RE = re.compile(r"""(!?)\[([^\]]*)\]\([ \t]*([^()\s]*(?:\([^()]*\)[^()\s]*)*)"""
                      r"""(?:[ \t]+["'(]([^"')]*)["')])?[ \t]*\)""")
_REFLINK_RE = re.compile(r"(!?)\[([^\]]*)\]\[([^\]]*)\]")
_SHORTCUT_RE = re.compile(r"(!?)\[([^\]]+)\](?!\()")
_AUTOLINK_RE = re.compile(r"&lt;((?:https?|mailto):[^\s&]+)&gt;")
_BARE_URL_RE = re.compile(r"(^|[\s(])(https?://[^\s<>()]+[^\s<>().,;:!?])")
_WORD = r"[^\W_]"


# A link href out of markdown we did not write: the prose comes back from a
# model, and this XHTML is read by an EPUB reader. Only the schemes a book
# could legitimately link to survive; anything else renders inert. Kept in
# step with mdHref() in fabula/web/index.html.
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)
_SAFE_SCHEMES = ("http:", "https:", "mailto:")


def _href(href: str) -> str:
    bare = re.sub(r"[\x00-\x20\x7f]", "", str(href or ""))
    scheme = _SCHEME_RE.match(bare)
    if not scheme:
        return href                        # relative, anchor, protocol-relative
    return href if scheme.group(0).lower() in _SAFE_SCHEMES else ""


def _inline(src: str, refs: dict) -> str:
    slots: list[str] = []

    def hold(markup: str) -> str:
        slots.append(markup)
        return f"{_HOLE}{len(slots) - 1}{_HOLE}"

    # code spans first: nothing inside them is markup
    def code_span(m: re.Match) -> str:
        body = re.sub(r"^ ([\s\S]*) $", r"\1", m.group(2))
        return hold(f"<code>{_esc(body)}</code>")

    s = _CODE_SPAN_RE.sub(code_span, str(src or ""))
    s = _ESCAPE_RE.sub(lambda m: hold(_esc(m.group(1))), s)
    s = _esc_text(s)

    def link(text: str, href: str, title: str, image: bool) -> str:
        url = _esc(_href(href.strip("<>")))
        t = f' title="{_esc(title)}"' if title else ""
        if image:
            return hold(f'<img src="{url}" alt="{_esc(text)}"{t}/>')
        return hold(f'<a href="{url}"{t}>') + text + hold("</a>")

    s = _LINK_RE.sub(lambda m: link(m.group(2), m.group(3), m.group(4) or "", bool(m.group(1))), s)

    def ref_link(m: re.Match, key_group: int) -> str:
        key = (m.group(key_group) or m.group(2)).strip().lower()
        if key not in refs:
            return m.group(0)
        href, title = refs[key]
        return link(m.group(2), href, title, bool(m.group(1)))

    s = _REFLINK_RE.sub(lambda m: ref_link(m, 3), s)
    s = _SHORTCUT_RE.sub(lambda m: ref_link(m, 2), s)
    s = _AUTOLINK_RE.sub(lambda m: hold(f'<a href="{_esc(m.group(1))}">') + _esc(m.group(1)) + hold("</a>"), s)
    s = _BARE_URL_RE.sub(
        lambda m: m.group(1) + hold(f'<a href="{_esc(m.group(2))}">') + _esc(m.group(2)) + hold("</a>"), s)

    s = re.sub(r"~~(?=\S)(.*?\S)~~", r"<del>\1</del>", s, flags=re.S)
    s = re.sub(r"\*\*\*(?=\S)(.*?\S)\*\*\*", r"<strong><em>\1</em></strong>", s, flags=re.S)
    s = re.sub(r"___(?=\S)(.*?\S)___", r"<strong><em>\1</em></strong>", s, flags=re.S)
    s = re.sub(r"\*\*(?=\S)(.*?\S)\*\*", r"<strong>\1</strong>", s, flags=re.S)
    s = re.sub(r"__(?=\S)(.*?\S)__", r"<strong>\1</strong>", s, flags=re.S)
    s = re.sub(r"\*(?=\S)([^*\n]*?\S)\*", r"<em>\1</em>", s)
    # a lone underscore inside a word (snake_case) is not emphasis
    s = re.sub(rf"(^|[^{_WORD[1:-1]}_]|^)_(?=\S)([^_\n]*?\S)_(?!{_WORD}|_)",
               r"\1<em>\2</em>", s)

    s = re.sub(r"[ \t]*\n", "<br/>", s)              # a single newline is a hard break here
    return re.sub(_HOLE + r"(\d+)" + _HOLE, lambda m: slots[int(m.group(1))], s)
