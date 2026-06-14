"""
monasql.expr - Expression AST with operator overloading.

Columns, literals, binary/unary ops, aggregates, window functions,
CASE, CAST, subqueries, and more — all composable via Python operators.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence


# ── helpers ──────────────────────────────────────────────────

def _coerce(val: Any) -> Expr:
    """Wrap a raw Python value in Lit if it isn't already an Expr."""
    if isinstance(val, Expr):
        return val
    return Lit(val)


# ── base class ───────────────────────────────────────────────

class Expr:
    """Base for every SQL expression node.  Overloads Python operators so
    that ``col > 100`` builds a *BinOp* AST instead of a bool."""

    # — comparison —
    def __eq__(self, other: Any) -> BinOp:       # type: ignore[override]
        return BinOp("=", self, _coerce(other))

    def __ne__(self, other: Any) -> BinOp:       # type: ignore[override]
        return BinOp("<>", self, _coerce(other))

    def __gt__(self, other: Any) -> BinOp:
        return BinOp(">", self, _coerce(other))

    def __ge__(self, other: Any) -> BinOp:
        return BinOp(">=", self, _coerce(other))

    def __lt__(self, other: Any) -> BinOp:
        return BinOp("<", self, _coerce(other))

    def __le__(self, other: Any) -> BinOp:
        return BinOp("<=", self, _coerce(other))

    # — arithmetic —
    def __add__(self, other: Any) -> BinOp:
        return BinOp("+", self, _coerce(other))

    def __radd__(self, other: Any) -> BinOp:
        return BinOp("+", _coerce(other), self)

    def __sub__(self, other: Any) -> BinOp:
        return BinOp("-", self, _coerce(other))

    def __rsub__(self, other: Any) -> BinOp:
        return BinOp("-", _coerce(other), self)

    def __mul__(self, other: Any) -> BinOp:
        return BinOp("*", self, _coerce(other))

    def __rmul__(self, other: Any) -> BinOp:
        return BinOp("*", _coerce(other), self)

    def __truediv__(self, other: Any) -> BinOp:
        return BinOp("/", self, _coerce(other))

    def __mod__(self, other: Any) -> BinOp:
        return BinOp("%", self, _coerce(other))

    # — logical  (use ``&``, ``|``, ``~`` because Python's
    #   ``and`` / ``or`` / ``not`` cannot be overloaded) —
    def __and__(self, other: Any) -> BinOp:
        return BinOp("AND", self, _coerce(other))

    def __rand__(self, other: Any) -> BinOp:
        return BinOp("AND", _coerce(other), self)

    def __or__(self, other: Any) -> BinOp:
        return BinOp("OR", self, _coerce(other))

    def __ror__(self, other: Any) -> BinOp:
        return BinOp("OR", _coerce(other), self)

    def __invert__(self) -> UnaryOp:
        return UnaryOp("NOT", self)

    # — SQL helpers —
    def like(self, pattern: str) -> BinOp:
        return BinOp("LIKE", self, Lit(pattern))

    def ilike(self, pattern: str) -> BinOp:
        return BinOp("ILIKE", self, Lit(pattern))

    def in_(self, *values: Any) -> InExpr:
        return InExpr(self, [_coerce(v) for v in values])

    def not_in(self, *values: Any) -> InExpr:
        return InExpr(self, [_coerce(v) for v in values], negated=True)

    def between(self, low: Any, high: Any) -> BetweenExpr:
        return BetweenExpr(self, _coerce(low), _coerce(high))

    def is_null(self) -> UnaryOp:
        return UnaryOp("IS NULL", self, postfix=True)

    def is_not_null(self) -> UnaryOp:
        return UnaryOp("IS NOT NULL", self, postfix=True)

    def asc(self) -> OrderExpr:
        return OrderExpr(self, "ASC")

    def desc(self) -> OrderExpr:
        return OrderExpr(self, "DESC")

    def alias(self, name: str) -> AliasExpr:
        return AliasExpr(self, name)

    def cast(self, type_name: str) -> CastExpr:
        return CastExpr(self, type_name)

    def over(
        self,
        partition_by: Sequence[Expr] | None = None,
        order_by: Sequence[Expr | OrderExpr] | None = None,
        frame: str | None = None,
    ) -> WindowExpr:
        return WindowExpr(
            self,
            list(partition_by) if partition_by else None,
            list(order_by) if order_by else None,
            frame,
        )

    # — safety —
    def __bool__(self) -> bool:
        raise TypeError(
            "Expr cannot be used in boolean context. "
            "Use & for AND, | for OR, ~ for NOT."
        )

    def __hash__(self) -> int:
        return id(self)


# ── concrete nodes ───────────────────────────────────────────

@dataclass(eq=False)
class Col(Expr):
    """Column reference, optionally qualified: ``t1.name``."""
    name: str
    table_alias: Optional[str] = None


@dataclass(eq=False)
class Lit(Expr):
    """Literal value (string, number, bool, None)."""
    value: Any


@dataclass(eq=False)
class Star(Expr):
    """``*`` or ``t1.*``."""
    table_alias: Optional[str] = None


@dataclass(eq=False)
class BinOp(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass(eq=False)
class UnaryOp(Expr):
    op: str
    operand: Expr
    postfix: bool = False


@dataclass(eq=False)
class FuncCall(Expr):
    """``NAME([DISTINCT] arg, …)``."""
    name: str
    args: list[Expr] = field(default_factory=list)
    distinct: bool = False


@dataclass(eq=False)
class InExpr(Expr):
    expr: Expr
    values: list[Expr] = field(default_factory=list)
    negated: bool = False


@dataclass(eq=False)
class BetweenExpr(Expr):
    expr: Expr
    low: Expr = field(default_factory=lambda: Lit(None))
    high: Expr = field(default_factory=lambda: Lit(None))


@dataclass
class OrderExpr:
    """Wraps an Expr with ASC/DESC for ORDER BY."""
    expr: Expr
    direction: str = "ASC"


@dataclass(eq=False)
class AliasExpr(Expr):
    expr: Expr
    name: str = ""


@dataclass(eq=False)
class CastExpr(Expr):
    expr: Expr
    type_name: str = ""


@dataclass(eq=False)
class CaseExpr(Expr):
    whens: list[tuple[Expr, Expr]] = field(default_factory=list)
    else_: Optional[Expr] = None


@dataclass(eq=False)
class WindowExpr(Expr):
    expr: Expr
    partition_by: Optional[list[Expr]] = None
    order_by: Optional[list[Expr | OrderExpr]] = None
    frame: Optional[str] = None


@dataclass(eq=False)
class SubqueryExpr(Expr):
    """Scalar subquery used as an expression: ``(SELECT …)``."""
    query: Any = None          # QueryState — forward-ref to avoid circular import


@dataclass(eq=False)
class ExistsExpr(Expr):
    """``EXISTS (SELECT …)``."""
    query: Any = None


@dataclass(eq=False)
class RawExpr(Expr):
    """Escape hatch — inject a raw SQL fragment."""
    sql: str = ""


# ── convenience constructors ─────────────────────────────────

def lit(value: Any) -> Lit:
    return Lit(value)

def col(name: str, table: str | None = None) -> Col:
    return Col(name, table)

def star(table: str | None = None) -> Star:
    return Star(table)

def raw(sql: str) -> RawExpr:
    return RawExpr(sql)

# aggregates
def count(expr: Expr | None = None, distinct: bool = False) -> FuncCall:
    return FuncCall("COUNT", [expr] if expr is not None else [Star()], distinct=distinct)

def sum_(expr: Expr) -> FuncCall:
    return FuncCall("SUM", [expr])

def avg(expr: Expr) -> FuncCall:
    return FuncCall("AVG", [expr])

def min_(expr: Expr) -> FuncCall:
    return FuncCall("MIN", [expr])

def max_(expr: Expr) -> FuncCall:
    return FuncCall("MAX", [expr])

def string_agg(expr: Expr, sep: str = ",") -> FuncCall:
    return FuncCall("STRING_AGG", [expr, Lit(sep)])

# window helpers
def row_number() -> FuncCall:
    return FuncCall("ROW_NUMBER", [])

def rank() -> FuncCall:
    return FuncCall("RANK", [])

def dense_rank() -> FuncCall:
    return FuncCall("DENSE_RANK", [])

def ntile(n: int) -> FuncCall:
    return FuncCall("NTILE", [Lit(n)])

def lag(expr: Expr, offset: int = 1, default: Any = None) -> FuncCall:
    args: list[Expr] = [expr, Lit(offset)]
    if default is not None:
        args.append(Lit(default))
    return FuncCall("LAG", args)

def lead(expr: Expr, offset: int = 1, default: Any = None) -> FuncCall:
    args: list[Expr] = [expr, Lit(offset)]
    if default is not None:
        args.append(Lit(default))
    return FuncCall("LEAD", args)

def first_value(expr: Expr) -> FuncCall:
    return FuncCall("FIRST_VALUE", [expr])

def last_value(expr: Expr) -> FuncCall:
    return FuncCall("LAST_VALUE", [expr])

# CASE
def case(*whens: tuple[Expr, Expr], else_: Any = None) -> CaseExpr:
    return CaseExpr(
        list(whens),
        _coerce(else_) if else_ is not None else None,
    )

def when(cond: Expr, then: Any) -> tuple[Expr, Expr]:
    return (cond, _coerce(then))

# subquery predicates
def exists(subquery: Any) -> ExistsExpr:
    return ExistsExpr(subquery)

# scalar functions
def coalesce(*args: Any) -> FuncCall:
    return FuncCall("COALESCE", [_coerce(a) for a in args])

def nullif(a: Any, b: Any) -> FuncCall:
    return FuncCall("NULLIF", [_coerce(a), _coerce(b)])

def greatest(*args: Any) -> FuncCall:
    return FuncCall("GREATEST", [_coerce(a) for a in args])

def least(*args: Any) -> FuncCall:
    return FuncCall("LEAST", [_coerce(a) for a in args])

def concat(*args: Any) -> FuncCall:
    return FuncCall("CONCAT", [_coerce(a) for a in args])

def func(name: str, *args: Any) -> FuncCall:
    """Generic function call: ``func('MY_FUNC', col, lit(1))``."""
    return FuncCall(name.upper(), [_coerce(a) for a in args])
