"""
Intermediate AST for the function-style DSL.

Expression nodes mirror a small Python-expression subset (names, literals,
attribute access, calls, operators, lists).  Statements are either an
assignment (``u = from(users)``) or a bare call (``where(...)``).  The compiler
(``compiler.py``) interprets these against the monasql builder.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# ── expression nodes ──────────────────────────────────────────

@dataclass
class ExprNode:
    line: int = 0
    col: int = 0


@dataclass
class Name(ExprNode):
    id: str = ""


@dataclass
class Literal(ExprNode):
    value: Any = None          # Python int/float/str/bool/None


@dataclass
class Quoted(ExprNode):
    # a back-ticked identifier path, e.g. `my-proj.dataset.table` (BigQuery style)
    value: str = ""


@dataclass
class Attribute(ExprNode):
    value: ExprNode = None     # type: ignore[assignment]
    attr: str = ""


@dataclass
class Call(ExprNode):
    func: ExprNode = None      # type: ignore[assignment]   Name or Attribute
    args: list[ExprNode] = field(default_factory=list)
    keywords: dict[str, ExprNode] = field(default_factory=dict)


@dataclass
class BinOp(ExprNode):
    op: str = ""
    left: ExprNode = None      # type: ignore[assignment]
    right: ExprNode = None     # type: ignore[assignment]


@dataclass
class UnaryOp(ExprNode):
    op: str = ""
    operand: ExprNode = None   # type: ignore[assignment]


@dataclass
class ListLit(ExprNode):
    elts: list[ExprNode] = field(default_factory=list)


# ── statements ────────────────────────────────────────────────

@dataclass
class Assign:
    target: str
    value: ExprNode
    line: int = 0
    col: int = 0


@dataclass
class ExprStmt:
    value: ExprNode
    line: int = 0
    col: int = 0


# ── top-level ─────────────────────────────────────────────────

@dataclass
class QueryDef:
    name: str
    body: list[Any]            # list[Assign | ExprStmt]
    line: int = 0
    col: int = 0


@dataclass
class PredicateDef:
    name: str
    params: list[str]
    body: ExprNode
    line: int = 0
    col: int = 0


@dataclass
class Program:
    statements: list[Any] = field(default_factory=list)
