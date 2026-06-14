"""
monasql.query - Monadic query builder.

The key idea: a ``@query``-decorated generator ``yield``s *instructions*
(``from_``, ``apply_``, ``where_``, …) and receives back *table refs*.
The builder consumes the generator, accumulates a ``QueryState`` AST,
and can then render it to SQL.

Generator-based do-notation example::

    @query
    def analysis():
        u = yield from_(users)
        s = yield apply_(get_user_stats, u.id, lit('2024-01-01'))
        yield where_(s.total > 100)
        yield select(u.name, s.total)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from .expr import Expr, Col, Lit, Star, OrderExpr, _coerce
from .expr import (
    ExistsExpr, SubqueryExpr, BinOp, UnaryOp, FuncCall, InExpr,
    BetweenExpr, AliasExpr, CastExpr, CaseExpr, WindowExpr,
)
from .schema import Table, TableRef, TVF


# ── alias counter (per build) ───────────────────────────────

class _AliasGen:
    """Simple per-build alias generator: t1, t2, t3, …"""

    def __init__(self) -> None:
        self._n = 0

    def next(self, prefix: str = "t") -> str:
        self._n += 1
        return f"{prefix}{self._n}"


# ── instructions ─────────────────────────────────────────────

class JoinKind(Enum):
    INNER = "INNER JOIN"
    LEFT  = "LEFT JOIN"
    RIGHT = "RIGHT JOIN"
    FULL  = "FULL JOIN"
    CROSS = "CROSS JOIN"


@dataclass
class FromInstr:
    # A plain Table, or a sub-query (a @query ``Query`` or a bare generator
    # function) that gets inlined as ``FROM (SELECT …) alias``.
    source: Table | Query | Callable[..., Any]
    alias: Optional[str] = None


@dataclass
class JoinInstr:
    # Same as FromInstr.source — Table or sub-query.
    source: Table | Query | Callable[..., Any]
    on: Optional[Expr | Callable[..., Expr]] = None
    kind: JoinKind = JoinKind.INNER
    alias: Optional[str] = None


@dataclass
class LateralJoinInstr:
    """Lateral join a subquery (not a TVF)."""
    subquery: Any           # QueryState or Query
    on: Optional[Expr] = None
    outer: bool = False
    alias: Optional[str] = None


@dataclass
class ApplyInstr:
    """TVF application — the monadic bind."""
    tvf: TVF
    args: tuple[Any, ...]
    outer: bool = False     # CROSS APPLY vs OUTER APPLY
    alias: Optional[str] = None


@dataclass
class WhereInstr:
    condition: Expr

@dataclass
class SelectInstr:
    exprs: tuple[Expr | Star, ...]

@dataclass
class GroupByInstr:
    exprs: tuple[Expr, ...]

@dataclass
class HavingInstr:
    condition: Expr

@dataclass
class OrderByInstr:
    exprs: tuple[Expr | OrderExpr, ...]

@dataclass
class LimitInstr:
    count: int

@dataclass
class OffsetInstr:
    count: int

@dataclass
class DistinctInstr:
    pass

@dataclass
class UnionInstr:
    other: Any
    all_: bool = False

@dataclass
class IntersectInstr:
    other: Any
    all_: bool = False

@dataclass
class ExceptInstr:
    other: Any
    all_: bool = False

@dataclass
class CTEInstr:
    name: str
    query: Any              # callable (generator func) or QueryState
    columns: tuple[str, ...] | None = None


# ── AST nodes (accumulated state) ───────────────────────────

@dataclass
class FromClause:
    # ``str`` table name, or a ``QueryState`` for an inlined sub-query.
    source: str | QueryState
    alias: str


@dataclass
class JoinClause:
    kind: JoinKind
    # ``str`` table/CTE name, or a ``QueryState`` for an inlined sub-query.
    table_name: str | QueryState
    alias: str
    on: Optional[Expr] = None


@dataclass
class ApplyClause:
    tvf_name: str
    args: list[Expr]
    alias: str
    outer: bool = False


@dataclass
class LateralClause:
    subquery: Any
    alias: str
    on: Optional[Expr] = None
    outer: bool = False


@dataclass
class CTEDef:
    name: str
    query: Any              # QueryState
    columns: tuple[str, ...] | None = None


@dataclass
class SetOp:
    op: str                 # "UNION" | "INTERSECT" | "EXCEPT"
    query: Any              # QueryState
    all_: bool = False


@dataclass
class QueryState:
    """Complete AST for one SELECT statement."""
    ctes:              list[CTEDef]             = field(default_factory=list)
    select_exprs:      list[Expr | Star]        = field(default_factory=list)
    distinct:          bool                     = False
    from_clause:       Optional[FromClause]     = None
    joins:             list[JoinClause | ApplyClause | LateralClause] = field(default_factory=list)
    where_conditions:  list[Expr]               = field(default_factory=list)
    group_by_exprs:    list[Expr]               = field(default_factory=list)
    having_conditions: list[Expr]               = field(default_factory=list)
    order_by_exprs:    list[Expr | OrderExpr]   = field(default_factory=list)
    limit:             Optional[int]            = None
    offset:            Optional[int]            = None
    set_ops:           list[SetOp]              = field(default_factory=list)


# ── instruction constructors (public API) ────────────────────

def from_(source: Table | Query | Callable[..., Any], alias: str | None = None) -> FromInstr:
    """Introduce a source — a ``Table`` or a sub-query (``Query`` / generator
    function).  Returns a ``TableRef``."""
    return FromInstr(source, alias)


def join_(
    source: Table | Query | Callable[..., Any],
    on: Expr | Callable[..., Expr] | None = None,
    *,
    how: str = "inner",
    alias: str | None = None,
) -> JoinInstr:
    _MAP = {
        "inner": JoinKind.INNER,
        "left":  JoinKind.LEFT,
        "right": JoinKind.RIGHT,
        "full":  JoinKind.FULL,
        "cross": JoinKind.CROSS,
    }
    return JoinInstr(source, on, _MAP[how.lower()], alias)


def apply_(
    tvf: TVF,
    *args: Any,
    outer: bool = False,
    alias: str | None = None,
) -> ApplyInstr:
    """Apply a TVF — the monadic bind."""
    return ApplyInstr(tvf, args, outer, alias)


def lateral_(
    subquery: Any,
    *,
    on: Expr | None = None,
    outer: bool = False,
    alias: str | None = None,
) -> LateralJoinInstr:
    """Lateral-join an inline subquery."""
    return LateralJoinInstr(subquery, on, outer, alias)


def where_(*conditions: Expr) -> WhereInstr | list[WhereInstr]:
    if len(conditions) == 1:
        return WhereInstr(conditions[0])
    # multiple conditions → multiple instructions (all ANDed)
    return WhereInstr(conditions[0]) if len(conditions) == 1 else [WhereInstr(c) for c in conditions]


def select(*exprs: Expr | Star) -> SelectInstr:
    return SelectInstr(exprs)


def group_by(*exprs: Expr) -> GroupByInstr:
    return GroupByInstr(exprs)


def having_(condition: Expr) -> HavingInstr:
    return HavingInstr(condition)


def order_by(*exprs: Expr | OrderExpr) -> OrderByInstr:
    return OrderByInstr(exprs)


def limit_(n: int) -> LimitInstr:
    return LimitInstr(n)


def offset_(n: int) -> OffsetInstr:
    return OffsetInstr(n)


def distinct_() -> DistinctInstr:
    return DistinctInstr()


def union(other: Any, *, all_: bool = False) -> UnionInstr:
    return UnionInstr(other, all_)


def intersect(other: Any, *, all_: bool = False) -> IntersectInstr:
    return IntersectInstr(other, all_)


def except_(other: Any, *, all_: bool = False) -> ExceptInstr:
    return ExceptInstr(other, all_)


def with_cte(
    name: str,
    subquery: Any,
    columns: tuple[str, ...] | None = None,
) -> CTEInstr:
    return CTEInstr(name, subquery, columns)


# ── builder (monad runner) ───────────────────────────────────

def _resolve_query(obj: Any, aliases: _AliasGen) -> QueryState:
    """If *obj* is a callable (generator func), build it; otherwise assume
    it's already a QueryState."""
    if isinstance(obj, Query):
        return obj._ensure_built_with(aliases)
    if callable(obj):
        return _build(obj, aliases)
    return obj


def _output_columns(st: QueryState) -> list[str]:
    """Best-effort list of the column names a sub-query exposes, so a
    ``TableRef`` wrapping it can advertise them (for ``ref.col`` & autocomplete)."""
    names: list[str] = []
    for e in st.select_exprs:
        if isinstance(e, AliasExpr):
            names.append(e.name)
        elif isinstance(e, Col):
            names.append(e.name)
    return names


def _resolve_source(
    source: Any, alias: str, aliases: _AliasGen,
) -> tuple[str | QueryState, TableRef]:
    """Resolve a FROM/JOIN source into ``(clause_source, table_ref)``.

    * ``Table``             → table name + TableRef advertising its columns
    * ``Query`` / generator → built ``QueryState`` (sub-query) + TableRef
      advertising the sub-query's output columns
    """
    if isinstance(source, Table):
        return source.full_name, TableRef(source.full_name, alias, source.columns)
    sub_state = _resolve_query(source, aliases)
    return sub_state, TableRef("subquery", alias, _output_columns(sub_state))


def _deep_resolve(expr: Any, aliases: _AliasGen) -> Any:
    """Walk an expression tree and build any nested subqueries with the
    shared alias generator so inner/outer aliases never collide."""

    if isinstance(expr, ExistsExpr):
        q = expr.query
        if callable(q) or isinstance(q, Query):
            return ExistsExpr(_resolve_query(q, aliases))
        return expr

    if isinstance(expr, SubqueryExpr):
        q = expr.query
        if callable(q) or isinstance(q, Query):
            return SubqueryExpr(_resolve_query(q, aliases))
        return expr

    if isinstance(expr, BinOp):
        l = _deep_resolve(expr.left, aliases)
        r = _deep_resolve(expr.right, aliases)
        if l is not expr.left or r is not expr.right:
            return BinOp(expr.op, l, r)
        return expr

    if isinstance(expr, UnaryOp):
        inner = _deep_resolve(expr.operand, aliases)
        if inner is not expr.operand:
            return UnaryOp(expr.op, inner, expr.postfix)
        return expr

    if isinstance(expr, FuncCall):
        new_args = [_deep_resolve(a, aliases) for a in expr.args]
        if any(n is not o for n, o in zip(new_args, expr.args)):
            return FuncCall(expr.name, new_args, expr.distinct)
        return expr

    if isinstance(expr, AliasExpr):
        inner = _deep_resolve(expr.expr, aliases)
        if inner is not expr.expr:
            return AliasExpr(inner, expr.name)
        return expr

    if isinstance(expr, CaseExpr):
        new_whens = [
            (_deep_resolve(c, aliases), _deep_resolve(t, aliases))
            for c, t in expr.whens
        ]
        new_else = _deep_resolve(expr.else_, aliases) if expr.else_ is not None else None
        return CaseExpr(new_whens, new_else)

    if isinstance(expr, WindowExpr):
        inner = _deep_resolve(expr.expr, aliases)
        pb = [_deep_resolve(e, aliases) for e in expr.partition_by] if expr.partition_by else None
        ob = [_deep_resolve(e, aliases) for e in expr.order_by] if expr.order_by else None
        return WindowExpr(inner, pb, ob, expr.frame)

    if isinstance(expr, InExpr):
        new_vals = [_deep_resolve(v, aliases) for v in expr.values]
        return InExpr(_deep_resolve(expr.expr, aliases), new_vals, expr.negated)

    return expr


def _build(gen_func: Callable[..., Any], aliases: _AliasGen | None = None) -> QueryState:
    """Run a generator-based query definition → QueryState."""
    if aliases is None:
        aliases = _AliasGen()
    state = QueryState()
    gen = gen_func()

    def _process(instr: Any) -> Any:
        if isinstance(instr, FromInstr):
            a = instr.alias or aliases.next()
            clause_src, ref = _resolve_source(instr.source, a, aliases)
            state.from_clause = FromClause(clause_src, a)
            return ref

        if isinstance(instr, JoinInstr):
            a = instr.alias or aliases.next()
            clause_src, ref = _resolve_source(instr.source, a, aliases)
            on_expr = instr.on(ref) if callable(instr.on) else instr.on
            state.joins.append(JoinClause(instr.kind, clause_src, a, on_expr))
            return ref

        if isinstance(instr, ApplyInstr):
            a = instr.alias or aliases.next()
            coerced = [x if isinstance(x, Expr) else Lit(x) for x in instr.args]
            state.joins.append(ApplyClause(instr.tvf.full_name, coerced, a, instr.outer))
            return TableRef(instr.tvf.full_name, a, instr.tvf.columns)

        if isinstance(instr, LateralJoinInstr):
            a = instr.alias or aliases.next()
            sub_state = _resolve_query(instr.subquery, aliases)
            state.joins.append(LateralClause(sub_state, a, instr.on, instr.outer))
            return TableRef(f"lateral_{a}", a)

        if isinstance(instr, WhereInstr):
            state.where_conditions.append(_deep_resolve(instr.condition, aliases))
            return None

        if isinstance(instr, SelectInstr):
            state.select_exprs.extend(_deep_resolve(e, aliases) for e in instr.exprs)
            return None

        if isinstance(instr, GroupByInstr):
            state.group_by_exprs.extend(instr.exprs)
            return None

        if isinstance(instr, HavingInstr):
            state.having_conditions.append(_deep_resolve(instr.condition, aliases))
            return None

        if isinstance(instr, OrderByInstr):
            state.order_by_exprs.extend(instr.exprs)
            return None

        if isinstance(instr, LimitInstr):
            state.limit = instr.count
            return None

        if isinstance(instr, OffsetInstr):
            state.offset = instr.count
            return None

        if isinstance(instr, DistinctInstr):
            state.distinct = True
            return None

        if isinstance(instr, (UnionInstr, IntersectInstr, ExceptInstr)):
            op_name = type(instr).__name__.replace("Instr", "").upper()
            if op_name == "EXCEPT_":
                op_name = "EXCEPT"
            sub = _resolve_query(instr.other, aliases)
            state.set_ops.append(SetOp(op_name, sub, instr.all_))
            return None

        if isinstance(instr, CTEInstr):
            sub = _resolve_query(instr.query, aliases)
            state.ctes.append(CTEDef(instr.name, sub, instr.columns))
            # Return a Table-like ref so the CTE can be used in FROM/JOIN
            return Table(instr.name)

        raise TypeError(f"Unknown instruction: {type(instr).__name__}")

    try:
        instr = next(gen)
        while True:
            # Handle list of instructions (e.g. multiple where_ conditions)
            if isinstance(instr, list):
                result = None
                for i in instr:
                    result = _process(i)
                instr = gen.send(result)
            else:
                result = _process(instr)
                instr = gen.send(result)
    except StopIteration:
        pass

    return state


# ── Query (public wrapper) ───────────────────────────────────

class Query:
    """A compiled query object.  Created via the ``@query`` decorator."""

    def __init__(self, gen_func: Callable[..., Any]) -> None:
        self._gen_func = gen_func
        self._state: QueryState | None = None

    def _ensure_built(self) -> QueryState:
        if self._state is None:
            self._state = _build(self._gen_func)
        return self._state

    def _ensure_built_with(self, aliases: _AliasGen) -> QueryState:
        """Build using a shared alias generator (for nested subqueries)."""
        return _build(self._gen_func, aliases)

    def sql(self, *, dialect: str = "bigquery", pretty: bool = True) -> str:
        from .render import render
        return render(self._ensure_built(), dialect=dialect, pretty=pretty)

    def __str__(self) -> str:
        return self.sql()

    def __repr__(self) -> str:
        return f"Query({self.sql(pretty=False)!r})"


def query(gen_func: Callable[..., Any]) -> Query:
    """Decorator: turn a generator function into a Query."""
    return Query(gen_func)
