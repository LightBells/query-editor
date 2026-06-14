"""
monasql — Monadic SQL DSL
=========================

Write SQL queries as Python generators with do-notation style.
TVF application becomes a simple ``yield apply_(…)`` — no more
nested CROSS APPLY boilerplate.

Quick start::

    from monasql import *

    users  = Table("users",  ["id", "name", "email"])
    orders = Table("orders", ["id", "user_id", "total"])

    get_stats = TVF("get_user_stats",
                     params=["user_id", "since"],
                     columns=["total_purchases", "top_category"])

    @query
    def report():
        u = yield from_(users)
        s = yield apply_(get_stats, u.id, lit('2024-01-01'))
        yield where_(s.total_purchases > 100)
        yield order_by(s.total_purchases.desc())
        yield select(u.name, s.total_purchases, s.top_category)

    print(report.sql())
"""

# schema
from .schema import Table, TVF, TableRef

# query builder (monadic core)
from .query import (
    query,
    Query,
    from_,
    join_,
    apply_,
    lateral_,
    where_,
    select,
    group_by,
    having_,
    order_by,
    limit_,
    offset_,
    distinct_,
    union,
    intersect,
    except_,
    with_cte,
)

# expressions
from .expr import (
    Expr,
    Col,
    Lit,
    Star,
    lit,
    col,
    star,
    raw,
    # aggregates
    count,
    sum_,
    avg,
    min_,
    max_,
    string_agg,
    # window
    row_number,
    rank,
    dense_rank,
    ntile,
    lag,
    lead,
    first_value,
    last_value,
    # conditionals
    case,
    when,
    exists,
    # scalar
    coalesce,
    nullif,
    greatest,
    least,
    concat,
    func,
)

# rendering
from .render import render

# composable helpers
from .helpers import (
    # predicates (return Expr)
    is_active,
    in_date_range,
    no_outliers,
    value_in_range,
    has_value,
    # multi-instruction (return list)
    top_n,
    paginate,
    # fanout-safe lateral
    agg_lateral,
    agg_lateral_grouped,
)

__all__ = [
    # schema
    "Table", "TVF", "TableRef",
    # query
    "query", "Query",
    "from_", "join_", "apply_", "lateral_",
    "where_", "select", "group_by", "having_", "order_by",
    "limit_", "offset_", "distinct_",
    "union", "intersect", "except_", "with_cte",
    # expr
    "Expr", "Col", "Lit", "Star",
    "lit", "col", "star", "raw",
    "count", "sum_", "avg", "min_", "max_", "string_agg",
    "row_number", "rank", "dense_rank", "ntile",
    "lag", "lead", "first_value", "last_value",
    "case", "when", "exists",
    "coalesce", "nullif", "greatest", "least", "concat", "func",
    # render
    "render",
    # helpers
    # helpers
    "is_active", "in_date_range", "no_outliers", "value_in_range", "has_value",
    "top_n", "paginate",
    "agg_lateral", "agg_lateral_grouped",
]
