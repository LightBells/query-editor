"""
Parser for the function-style DSL:  tokens → ``Program`` AST.

Top level is a sequence of ``QUERY name:`` and ``PREDICATE name(args):`` blocks.
A query body is newline-separated statements, each either an assignment
(``u = from(users)``) or a bare call (``where(...)``).  Expressions use a small
Python-like grammar (names, literals, calls, ``.method()`` chains, operators,
``[lists]``) parsed with precedence climbing.
"""
from __future__ import annotations

from typing import Optional

from .lexer import Token, tokenize, LexError
from . import ast_nodes as A


class ParseError(Exception):
    def __init__(self, message: str, line: int, col: int):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col


_LITERAL_WORDS = {"true": True, "false": False, "null": None,
                  "True": True, "False": False, "None": None}
_COMPARE = {"==", "!=", "<>", "<", "<=", ">", ">="}


class Parser:
    def __init__(self, tokens: list[Token]):
        self.toks = tokens
        self.pos = 0

    # ── token helpers ─────────────────────────────────────────

    def peek(self, ahead: int = 0) -> Token:
        i = self.pos + ahead
        return self.toks[i] if i < len(self.toks) else self.toks[-1]

    def next(self) -> Token:
        t = self.toks[self.pos]
        if t.type != "EOF":
            self.pos += 1
        return t

    def at(self, type_: str, value: Optional[str] = None) -> bool:
        t = self.peek()
        return t.type == type_ and (value is None or t.value == value)

    def at_kw(self, *values: str) -> bool:
        t = self.peek()
        return t.type == "KEYWORD" and t.value in values

    def expect(self, type_: str, value: Optional[str] = None) -> Token:
        t = self.peek()
        if t.type != type_ or (value is not None and t.value != value):
            want = f"{type_} {value!r}" if value else type_
            got = f"{t.value!r}" if t.value else t.type
            raise ParseError(f"Expected {want}, got {got}", t.line, t.col)
        return self.next()

    def expect_name(self) -> str:
        t = self.peek()
        if t.type != "NAME":
            raise ParseError(f"Expected a name, got {t.value or t.type!r}", t.line, t.col)
        return self.next().value

    def skip_newlines(self) -> None:
        while self.at("NEWLINE"):
            self.next()

    # ── program / blocks ──────────────────────────────────────

    def parse_program(self) -> A.Program:
        stmts = []
        self.skip_newlines()
        while not self.at("EOF"):
            if self.at_kw("QUERY"):
                stmts.append(self.parse_query_def())
            elif self.at_kw("PREDICATE"):
                stmts.append(self.parse_predicate_def())
            else:
                t = self.peek()
                raise ParseError(
                    f"Expected QUERY or PREDICATE, got {t.value or t.type!r}", t.line, t.col)
            self.skip_newlines()
        return A.Program(stmts)

    def parse_query_def(self) -> A.QueryDef:
        kw = self.expect("KEYWORD", "QUERY")
        name = self.expect_name()
        self.expect("PUNC", ":")
        self.skip_newlines()
        body = []
        while not (self.at("EOF") or self.at_kw("QUERY", "PREDICATE")):
            body.append(self.parse_statement())
            self.skip_newlines()
        if not body:
            t = self.peek()
            raise ParseError(f"QUERY {name} has no statements", t.line, t.col)
        return A.QueryDef(name, body, kw.line, kw.col)

    def parse_predicate_def(self) -> A.PredicateDef:
        kw = self.expect("KEYWORD", "PREDICATE")
        name = self.expect_name()
        self.expect("PUNC", "(")
        params: list[str] = []
        if not self.at("PUNC", ")"):
            params.append(self.expect_name())
            while self.at("PUNC", ","):
                self.next()
                params.append(self.expect_name())
        self.expect("PUNC", ")")
        self.expect("PUNC", ":")
        self.skip_newlines()
        body = self.parse_expr()
        return A.PredicateDef(name, params, body, kw.line, kw.col)

    def parse_statement(self):
        t = self.peek()
        # assignment:  NAME '=' expr   (but not '==')
        if t.type == "NAME" and self.peek(1).type == "OP" and self.peek(1).value == "=":
            target = self.next().value
            self.next()  # '='
            value = self.parse_expr()
            return A.Assign(target, value, t.line, t.col)
        value = self.parse_expr()
        return A.ExprStmt(value, t.line, t.col)

    # ── expressions (precedence climbing) ─────────────────────

    def parse_expr(self, min_bp: int = 0) -> A.ExprNode:
        left = self.parse_unary()
        while True:
            op, bp = self._infix(self.peek())
            if op is None or bp < min_bp:
                break
            t = self.next()
            right = self.parse_expr(bp + 1)
            left = A.BinOp(t.line, t.col, op, left, right)
        return left

    def _infix(self, t: Token) -> tuple[Optional[str], int]:
        if t.type == "OP":
            if t.value == "|":
                return "or", 1
            if t.value == "&":
                return "and", 2
            if t.value in _COMPARE:
                return t.value, 4
            if t.value in ("+", "-"):
                return t.value, 5
            if t.value in ("*", "/", "%"):
                return t.value, 6
        if t.type == "NAME":
            if t.value == "or":
                return "or", 1
            if t.value == "and":
                return "and", 2
        return None, -1

    def parse_unary(self) -> A.ExprNode:
        t = self.peek()
        if (t.type == "NAME" and t.value == "not") or (t.type == "OP" and t.value == "~"):
            self.next()
            return A.UnaryOp(t.line, t.col, "not", self.parse_expr(3))
        if t.type == "OP" and t.value == "-":
            self.next()
            return A.UnaryOp(t.line, t.col, "-", self.parse_expr(7))
        return self.parse_postfix(self.parse_atom())

    def parse_postfix(self, node: A.ExprNode) -> A.ExprNode:
        while True:
            if self.at("PUNC", "."):
                self.next()
                attr = self.expect_name()
                node = A.Attribute(node.line, node.col, node, attr)
            elif self.at("PUNC", "("):
                args, kwargs = self.parse_call_args()
                node = A.Call(node.line, node.col, node, args, kwargs)
            else:
                return node

    def parse_atom(self) -> A.ExprNode:
        t = self.peek()
        if t.type == "NUMBER":
            self.next()
            val = float(t.value) if "." in t.value else int(t.value)
            return A.Literal(t.line, t.col, val)
        if t.type == "STRING":
            self.next()
            return A.Literal(t.line, t.col, t.value)
        if t.type == "BTICK":
            self.next()
            return A.Quoted(t.line, t.col, t.value)
        if t.type == "NAME":
            self.next()
            if t.value in _LITERAL_WORDS:
                return A.Literal(t.line, t.col, _LITERAL_WORDS[t.value])
            return A.Name(t.line, t.col, t.value)
        if t.type == "PUNC" and t.value == "(":
            self.next()
            inner = self.parse_expr()
            self.expect("PUNC", ")")
            return inner
        if t.type == "PUNC" and t.value == "[":
            return self.parse_list()
        raise ParseError(f"Unexpected {t.value or t.type!r} in expression", t.line, t.col)

    def parse_list(self) -> A.ListLit:
        lb = self.expect("PUNC", "[")
        elts: list[A.ExprNode] = []
        if not self.at("PUNC", "]"):
            elts.append(self.parse_expr())
            while self.at("PUNC", ","):
                self.next()
                if self.at("PUNC", "]"):
                    break
                elts.append(self.parse_expr())
        self.expect("PUNC", "]")
        return A.ListLit(lb.line, lb.col, elts)

    def parse_call_args(self) -> tuple[list[A.ExprNode], dict[str, A.ExprNode]]:
        self.expect("PUNC", "(")
        args: list[A.ExprNode] = []
        kwargs: dict[str, A.ExprNode] = {}
        if not self.at("PUNC", ")"):
            while True:
                if (self.at("NAME") and self.peek(1).type == "OP"
                        and self.peek(1).value == "="):
                    key = self.next().value
                    self.next()  # '='
                    kwargs[key] = self.parse_expr()
                else:
                    args.append(self.parse_expr())
                if self.at("PUNC", ","):
                    self.next()
                    if self.at("PUNC", ")"):   # allow a trailing comma
                        break
                    continue
                break
        self.expect("PUNC", ")")
        return args, kwargs


def parse(text: str) -> A.Program:
    try:
        tokens = tokenize(text)
    except LexError as e:
        raise ParseError(e.message, e.line, e.col) from e
    return Parser(tokens).parse_program()
