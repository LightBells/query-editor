"""
monasql.helpers - Composable predicates & fanout-safe aggregation.

Design principle:
  - **Predicates** return ``Expr`` -> compose with ``&`` / ``|``, wrap in ``yield where_(...)``
  - **Multi-instruction helpers** return ``list[Instr]`` -> use plain ``yield``
  - **Lateral helpers** return ``LateralJoinInstr`` -> use plain ``yield``

No ``yield from`` needed -- every helper is used with a single ``yield``.

Example::

    @query
    def report():
        u = yield from_(users)
        o = yield join_(orders, on=lambda o: o.user_id == u.id)

        # Predicates compose with & -- intent is explicit
        yield where_(
            is_active(u)
            & in_date_range(o.created_at, start='2024-01-01')
            & no_outliers(o.total, stddevs=2)
        )

        yield top_n(sum_(o.total), 50)          # returns [OrderByInstr, LimitInstr]
        yield select(u.name, sum_(o.total))
"""
from __future__ import annotations

from typing import Any, Callable

from .expr import (
    Expr, Col, Lit, BinOp,
    avg, count, func, lit, max_, min_, star, sum_,
)
from .query import (
    LateralJoinInstr, LimitInstr, OffsetInstr, OrderByInstr,
    from_, group_by, lateral_, limit_, offset_, order_by, select, where_,
)
from .schema import Table


# =====================================================================
# Predicate helpers  (return Expr -- use inside yield where_())
# =====================================================================

def is_active(ref: Any, column: str = "deleted_at") -> Expr:
    """Soft-delete filter: ``deleted_at IS NULL``.

    ::

        yield where_(is_active(u))
        yield where_(is_active(u) & u.email.is_not_null())
    """
    return Col(column, ref._alias).is_null()


def in_date_range(
    column: Expr,
    *,
    start: str | None = None,
    end: str | None = None,
) -> Expr:
    """Date/timestamp range predicate.

    ::

        yield where_(in_date_range(o.created_at, start='2024-01-01', end='2024-12-31'))
    """
    parts: list[Expr] = []
    if start is not None:
        parts.append(column >= lit(start))
    if end is not None:
        parts.append(column <= lit(end))
    if not parts:
        return lit(True)
    result = parts[0]
    for p in parts[1:]:
        result = result & p
    return result


def no_outliers(column: Expr, *, stddevs: float = 3) -> Expr:
    """Exclude values beyond +/-N standard deviations (window-based).

    ::

        yield where_(no_outliers(o.total, stddevs=2))
    """
    mu  = func("AVG",    column).over()
    sig = func("STDDEV", column).over()
    return column.between(mu - lit(stddevs) * sig, mu + lit(stddevs) * sig)


def value_in_range(column: Expr, *, lo: Any = None, hi: Any = None) -> Expr:
    """Simple numeric range predicate.

    ::

        yield where_(value_in_range(o.total, lo=100, hi=10000))
    """
    parts: list[Expr] = []
    if lo is not None:
        parts.append(column >= lit(lo))
    if hi is not None:
        parts.append(column <= lit(hi))
    if not parts:
        return lit(True)
    result = parts[0]
    for p in parts[1:]:
        result = result & p
    return result


def has_value(column: Expr) -> Expr:
    """``column IS NOT NULL`` predicate.

    ::

        yield where_(has_value(u.email) & has_value(u.phone))
    """
    return column.is_not_null()


# =====================================================================
# Multi-instruction helpers  (return list -- use with plain yield)
# =====================================================================

def top_n(
    column: Expr,
    n: int,
    *,
    ascending: bool = False,
) -> list:
    """ORDER BY + LIMIT in one yield.

    ::

        yield top_n(o.total, 10)                      # top 10 DESC
        yield top_n(o.created_at, 5, ascending=True)   # oldest 5
    """
    expr = column.asc() if ascending else column.desc()
    return [OrderByInstr((expr,)), LimitInstr(n)]


def paginate(*, page: int = 1, size: int = 50) -> list:
    """LIMIT + OFFSET pagination in one yield.

    ::

        yield paginate(page=3, size=25)   # rows 51-75
    """
    instrs: list = [LimitInstr(size)]
    if page > 1:
        instrs.append(OffsetInstr((page - 1) * size))
    return instrs


# =====================================================================
# Fanout-safe lateral aggregation  (return LateralJoinInstr)
# =====================================================================

def agg_lateral(
    table: Table,
    *,
    join_on: Callable,
    aggs: Callable | list,
    where: Callable | None = None,
    outer: bool = False,
    alias: str | None = None,
) -> LateralJoinInstr:
    """Pre-aggregate *table* in an independent lateral subquery,
    avoiding the fanout problem.

    ::

        @query
        def report():
            u = yield from_(users)

            os = yield agg_lateral(orders,
                     join_on = lambda o: o.user_id == u.id,
                     aggs    = lambda o: [count(o.id).alias('cnt'),
                                          sum_(o.total).alias('rev')],
                     outer=True)

            cs = yield agg_lateral(comments,
                     join_on = lambda c: c.user_id == u.id,
                     aggs    = lambda c: [count(c.id).alias('cnt')],
                     outer=True)

            yield select(u.name, os.cnt, os.rev, cs.cnt)
    """
    def _sub():
        t = yield from_(table)
        yield where_(join_on(t))
        if where is not None:
            yield where_(where(t))
        agg_list = aggs(t) if callable(aggs) else aggs
        yield select(*agg_list)

    return lateral_(_sub, outer=outer, alias=alias)


def agg_lateral_grouped(
    table: Table,
    *,
    join_on: Callable,
    group_cols: Callable,
    aggs: Callable | list,
    where: Callable | None = None,
    outer: bool = False,
    alias: str | None = None,
) -> LateralJoinInstr:
    """Like ``agg_lateral`` but with GROUP BY inside the lateral subquery.

    ::

        os = yield agg_lateral_grouped(order_items,
                 join_on    = lambda oi: oi.order_id == o.id,
                 group_cols = lambda oi: [oi.category],
                 aggs       = lambda oi: [oi.category,
                                          sum_(oi.amount).alias('total')])
    """
    def _sub():
        t = yield from_(table)
        yield where_(join_on(t))
        if where is not None:
            yield where_(where(t))
        g = group_cols(t) if callable(group_cols) else group_cols
        yield group_by(*g)
        agg_list = aggs(t) if callable(aggs) else aggs
        yield select(*agg_list)

    return lateral_(_sub, outer=outer, alias=alias)
