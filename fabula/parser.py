"""Recursive-descent parser for the Fabula DSL.

Grammar (EBNF):

    file    := block* EOF
    block   := IDENT label* '{' item* '}'
    label   := STRING | NUMBER
    item    := IDENT ':' value ';'      -- attribute
             | block                    -- nested block
    value   := STRING | NUMBER
             | NUMBER '..' NUMBER       -- range
             | IDENT                    -- bare word (hard, soft, past, ...)
             | '[' value (',' value)* ']'

Repeated attribute keys accumulate into a list, so `forbid: "x"; forbid: "y";`
is valid and yields ["x", "y"].
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .i18n import t
from .lexer import FabulaSyntaxError, Token, tokenize


@dataclass
class Block:
    kind: str            # book / world / rule / character / act / chapter / scene / ...
    labels: list         # e.g. ["Ринок"] or [1, "Початок"]
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    line: int = 0
    col: int = 0
    lead_comments: list = field(default_factory=list)   # // lines right before the block
    inner_comments: list = field(default_factory=list)  # // lines inside, before attributes
    _repeated: set = field(default_factory=set, repr=False)

    @property
    def name(self):
        for lbl in self.labels:
            if isinstance(lbl, str):
                return lbl
        return None

    @property
    def number(self):
        for lbl in self.labels:
            if isinstance(lbl, (int, float)):
                return lbl
        return None


class Parser:
    def __init__(self, tokens: list[Token]):
        # split off comments: each attaches to the position of the next real
        # token, so blocks can claim the comments written just above them
        self.tokens = []
        self._comments: dict[int, list] = {}
        pending = []
        for tok in tokens:
            if tok.kind == "COMMENT":
                pending.append(tok.value)
            else:
                if pending:
                    self._comments[len(self.tokens)] = pending
                    pending = []
                self.tokens.append(tok)
        self.pos = 0

    def comments_at(self, pos: int) -> list:
        return self._comments.pop(pos, [])

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def next(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect(self, kind: str, hint: str = "") -> Token:
        tok = self.peek()
        if tok.kind != kind:
            raise FabulaSyntaxError(
                "FAB010", t("diag.FAB010.m", kind=kind, got=tok.kind, value=repr(tok.value)),
                tok.line, tok.col, hint,
            )
        return self.next()

    def parse_file(self) -> list[Block]:
        blocks = []
        while self.peek().kind != "EOF":
            blocks.append(self.parse_block())
        return blocks

    def parse_block(self) -> Block:
        lead = self.comments_at(self.pos)
        head = self.expect("IDENT", t("diag.hint.block_start"))
        labels = []
        while self.peek().kind in ("STRING", "NUMBER"):
            labels.append(self.next().value)
        self.expect("LBRACE", t("diag.hint.block_open", kind=head.value))
        block = Block(kind=head.value, labels=labels, line=head.line, col=head.col,
                      lead_comments=lead)

        while self.peek().kind != "RBRACE":
            tok = self.peek()
            if tok.kind == "EOF":
                raise FabulaSyntaxError("FAB011", t("diag.FAB011.m", kind=head.value),
                                        head.line, head.col, t("diag.FAB011.h"))
            if tok.kind != "IDENT":
                raise FabulaSyntaxError("FAB012", t("diag.FAB012.m", kind=tok.kind),
                                        tok.line, tok.col)
            # lookahead: IDENT ':' -> attribute, otherwise nested block
            if self.tokens[self.pos + 1].kind == "COLON":
                block.inner_comments += self.comments_at(self.pos)
                key_tok = self.next()
                self.next()  # colon
                value = self.parse_value()
                self.expect("SEMI", t("diag.hint.attr_semi", key=key_tok.value))
                key = key_tok.value
                if key in block.attrs:
                    if key not in block._repeated:
                        block.attrs[key] = [block.attrs[key]]
                        block._repeated.add(key)
                    block.attrs[key].append(value)
                else:
                    block.attrs[key] = value
            else:
                block.children.append(self.parse_block())

        block.inner_comments += self.comments_at(self.pos)  # before the '}'
        self.next()  # closing brace
        return block

    def parse_value(self):
        tok = self.peek()
        if tok.kind == "STRING":
            return self.next().value
        if tok.kind == "IDENT":
            return self.next().value
        if tok.kind == "NUMBER":
            num = self.next().value
            if self.peek().kind == "DOTDOT":
                self.next()
                hi = self.expect("NUMBER", t("diag.hint.range_ends")).value
                return {"range": [num, hi]}
            return num
        if tok.kind == "LBRACKET":
            self.next()
            items = []
            if self.peek().kind != "RBRACKET":
                items.append(self.parse_value())
                while self.peek().kind == "COMMA":
                    self.next()
                    items.append(self.parse_value())
            self.expect("RBRACKET", t("diag.hint.list_close"))
            return items
        raise FabulaSyntaxError("FAB013", t("diag.FAB013.m", kind=tok.kind),
                                tok.line, tok.col)


def parse(source: str) -> list[Block]:
    return Parser(tokenize(source)).parse_file()
