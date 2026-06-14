"""
Tokenizer for the monasql Web-IDE DSL.

The DSL is **function-style** — it reads like the Python monasql API with the
``yield`` / ``lambda`` boilerplate removed::

    QUERY report:
      u = from(users)
      o = join(orders, on = o.user_id == u.id)
      where(is_active(u) and o.total > 100)
      select(u.name, count(o.id).alias('n'), sum(o.total).alias('rev'))
      order_by(rev.desc())
      limit(100)

Only ``QUERY`` and ``PREDICATE`` are reserved keywords; everything else
(``from``, ``where``, ``count``, ``and`` …) is an ordinary name/operator the
parser interprets by position.

NEWLINEs terminate statements, but only at bracket-depth 0 — so a ``select(…)``
call may span several lines.  Every token carries 1-based ``line``/``col``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Only these two are hard keywords (block headers).
KEYWORDS = {"QUERY", "PREDICATE"}


@dataclass
class Token:
    type: str        # KEYWORD | NAME | NUMBER | STRING | OP | PUNC | NEWLINE | EOF
    value: str
    line: int
    col: int

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"{self.type}({self.value!r})@{self.line}:{self.col}"


class LexError(Exception):
    def __init__(self, message: str, line: int, col: int):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col


_TOKEN_SPEC = [
    ("COMMENT",  r"--[^\n]*"),
    ("NEWLINE",  r"\n"),
    ("SKIP",     r"[ \t\r]+"),
    ("NUMBER",   r"\d+\.\d+|\d+"),
    ("STRING",   r"'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.)*\""),
    ("BTICK",    r"`[^`]*`"),
    ("NAME",     r"[A-Za-z_][A-Za-z0-9_]*"),
    # longer operators first
    ("OP",       r"==|!=|<>|<=|>=|&&|\|\||[=<>+\-*/%&|~]"),
    ("PUNC",     r"[(),.:\[\]]"),
]
_MASTER_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in _TOKEN_SPEC))

_OPEN = {"(", "["}
_CLOSE = {")", "]"}


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    line = 1
    line_start = 0
    pos = 0
    depth = 0           # bracket nesting; NEWLINEs inside brackets are ignored
    n = len(text)

    def emit(tok: Token) -> None:
        tokens.append(tok)

    while pos < n:
        m = _MASTER_RE.match(text, pos)
        if not m:
            raise LexError(f"Unexpected character {text[pos]!r}", line, pos - line_start + 1)
        kind = m.lastgroup
        value = m.group()
        col = pos - line_start + 1

        if kind == "NEWLINE":
            if depth == 0 and tokens and tokens[-1].type != "NEWLINE":
                emit(Token("NEWLINE", "\\n", line, col))
            line += 1
            line_start = m.end()
        elif kind in ("SKIP", "COMMENT"):
            pass
        elif kind == "NUMBER":
            emit(Token("NUMBER", value, line, col))
        elif kind == "STRING":
            emit(Token("STRING", _decode_string(value), line, col))
        elif kind == "BTICK":
            emit(Token("BTICK", value[1:-1], line, col))  # back-ticked identifier path
        elif kind == "NAME":
            emit(Token("KEYWORD" if value in KEYWORDS else "NAME", value, line, col))
        elif kind == "OP":
            v = {"&&": "and", "||": "or"}.get(value, value)
            emit(Token("OP", v, line, col))
        elif kind == "PUNC":
            if value in _OPEN:
                depth += 1
            elif value in _CLOSE:
                depth = max(0, depth - 1)
            emit(Token("PUNC", value, line, col))

        pos = m.end()

    # drop a trailing NEWLINE so the parser sees a clean EOF
    while tokens and tokens[-1].type == "NEWLINE":
        tokens.pop()
    tokens.append(Token("EOF", "", line, pos - line_start + 1))
    return tokens


def _decode_string(raw: str) -> str:
    inner = raw[1:-1]
    return inner.replace("''", "'").replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")
