"""Tokenizer for the Fabula DSL.

The grammar is deliberately regular: identifiers, strings, numbers,
ranges (a..b), lists, and nested blocks. No operator precedence, no
statement ambiguity — an agent should never have to guess how to parse it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .i18n import t


class FabulaSyntaxError(Exception):
    def __init__(self, code: str, message: str, line: int, col: int, hint: str = ""):
        super().__init__(f"{code} at {line}:{col}: {message}")
        self.code = code
        self.message = message
        self.line = line
        self.col = col
        self.hint = hint

    def to_diagnostic(self) -> dict:
        return {
            "code": self.code,
            "severity": "error",
            "message": self.message,
            "line": self.line,
            "col": self.col,
            "hint": self.hint,
        }


@dataclass
class Token:
    kind: str  # IDENT STRING NUMBER LBRACE RBRACE LBRACKET RBRACKET COLON SEMI COMMA DOTDOT EOF
    value: object
    line: int
    col: int


_SYMBOLS = {
    "{": "LBRACE",
    "}": "RBRACE",
    "[": "LBRACKET",
    "]": "RBRACKET",
    ":": "COLON",
    ";": "SEMI",
    ",": "COMMA",
}


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    line, col = 1, 1
    i, n = 0, len(source)

    def err(code: str, msg: str, hint: str = "") -> FabulaSyntaxError:
        return FabulaSyntaxError(code, msg, line, col, hint)

    while i < n:
        ch = source[i]

        if ch == "\n":
            line += 1
            col = 1
            i += 1
            continue
        if ch in " \t\r":
            i += 1
            col += 1
            continue

        # comments: // to end of line — emitted as tokens so the parser can
        # attach them to blocks and the serializer can write them back
        if ch == "/" and i + 1 < n and source[i + 1] == "/":
            start_col = col
            j = i + 2
            while j < n and source[j] != "\n":
                j += 1
            tokens.append(Token("COMMENT", source[i + 2:j].strip(), line, start_col))
            col += j - i
            i = j
            continue

        if ch in _SYMBOLS:
            tokens.append(Token(_SYMBOLS[ch], ch, line, col))
            i += 1
            col += 1
            continue

        if ch == ".":
            if i + 1 < n and source[i + 1] == ".":
                tokens.append(Token("DOTDOT", "..", line, col))
                i += 2
                col += 2
                continue
            raise err("FAB001", t("diag.FAB001.m"), t("diag.FAB001.h"))

        if ch == '"':
            start_line, start_col = line, col
            i += 1
            col += 1
            buf = []
            while i < n and source[i] != '"':
                c = source[i]
                if c == "\n":
                    raise FabulaSyntaxError(
                        "FAB002", t("diag.FAB002_nl.m"),
                        start_line, start_col, t("diag.FAB002_nl.h"),
                    )
                if c == "\\" and i + 1 < n:
                    nxt = source[i + 1]
                    buf.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, nxt))
                    i += 2
                    col += 2
                    continue
                buf.append(c)
                i += 1
                col += 1
            if i >= n:
                raise FabulaSyntaxError("FAB002", t("diag.FAB002.m"), start_line, start_col,
                                        t("diag.FAB002.h"))
            i += 1
            col += 1
            tokens.append(Token("STRING", "".join(buf), start_line, start_col))
            continue

        if ch.isdigit() or (ch == "-" and i + 1 < n and source[i + 1].isdigit()):
            start_col = col
            j = i + 1 if ch == "-" else i
            while j < n and source[j].isdigit():
                j += 1
            # a fraction — but not the '..' range operator
            if j < n and source[j] == "." and j + 1 < n and source[j + 1].isdigit():
                j += 1
                while j < n and source[j].isdigit():
                    j += 1
                value: object = float(source[i:j])
            else:
                value = int(source[i:j])
            col += j - i
            tokens.append(Token("NUMBER", value, line, start_col))
            i = j
            continue

        if ch.isalpha() or ch == "_":
            start_col = col
            j = i
            while j < n and (source[j].isalnum() or source[j] == "_"):
                j += 1
            word = source[i:j]
            col += j - i
            tokens.append(Token("IDENT", word, line, start_col))
            i = j
            continue

        raise err("FAB003", t("diag.FAB003.m", ch=repr(ch)), t("diag.FAB003.h"))

    tokens.append(Token("EOF", None, line, col))
    return tokens
