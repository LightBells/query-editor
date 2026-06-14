"""
Test suite for the monasql core + the three feature additions.

Run from the project root:  ``pytest -q``
"""
from __future__ import annotations

import re

import pytest

from monasql import (
    Table, TVF, query,
    from_, join_, apply_, where_, select, group_by, having_,
    order_by, limit_, offset_, distinct_, union, with_cte,
    count, sum_, avg, col, lit, exists, coalesce, case, when, row_number,
    is_active, in_date_range, agg_lateral, agg_lateral_grouped, top_n,
)


# ── fixtures ──────────────────────────────────────────────────

users       = Table("users",       ["id", "name", "email", "dept_id", "deleted_at", "created_at"])
orders      = Table("orders",      ["id", "user_id", "total", "created_at"])
comments    = Table("comments",    ["id", "user_id", "body"])
departments = Table("departments", ["id", "name"])

get_stats = TVF("get_user_stats", params=["user_id", "since"],
                columns=["total_purchases", "top_category"])


def norm(sql: str) -> str:
    """Collapse whitespace so assertions are layout-insensitive."""
    return re.sub(r"\s+", " ", sql).strip()


# ── core behaviour ────────────────────────────────────────────

def test_basic_select_from():
    @query
    def q():
        u = yield from_(users)
        yield select(u.id, u.name)
    assert norm(q.sql()) == "SELECT t1.id, t1.name FROM users t1"


def test_join_with_lambda_on():
    @query
    def q():
        u = yield from_(users)
        o = yield join_(orders, on=lambda o: o.user_id == u.id)
        yield select(u.name, o.total)
    s = norm(q.sql())
    assert "INNER JOIN orders t2 ON t2.user_id = t1.id" in s


def test_left_join_how():
    @query
    def q():
        u = yield from_(users)
        o = yield join_(orders, on=lambda o: o.user_id == u.id, how="left")
        yield select(u.name)
    assert "LEFT JOIN orders t2" in norm(q.sql())


def test_where_predicates_compose():
    @query
    def q():
        u = yield from_(users)
        o = yield join_(orders, on=lambda o: o.user_id == u.id)
        yield where_(is_active(u) & in_date_range(o.created_at, start="2024-01-01"))
        yield select(u.name)
    s = norm(q.sql())
    assert "t1.deleted_at IS NULL" in s
    assert "t2.created_at >= '2024-01-01'" in s


def test_group_by_and_aggregates():
    @query
    def q():
        u = yield from_(users)
        o = yield join_(orders, on=lambda o: o.user_id == u.id)
        yield group_by(u.id, u.name)
        yield select(u.name, count(o.id).alias("n"), sum_(o.total).alias("rev"))
        yield having_(count(o.id) > 5)
    s = norm(q.sql())
    assert "GROUP BY t1.id, t1.name" in s
    assert "COUNT(t2.id) AS n" in s
    assert "HAVING COUNT(t2.id) > 5" in s


def test_order_limit_offset():
    @query
    def q():
        u = yield from_(users)
        yield select(u.name)
        yield order_by(u.created_at.desc())
        yield limit_(10)
        yield offset_(20)
    s = norm(q.sql())
    assert "ORDER BY t1.created_at DESC" in s
    assert "LIMIT 10" in s
    assert "OFFSET 20" in s


def test_distinct():
    @query
    def q():
        u = yield from_(users)
        yield distinct_()
        yield select(u.dept_id)
    assert norm(q.sql()).startswith("SELECT DISTINCT t1.dept_id")


def test_top_n_helper():
    @query
    def q():
        u = yield from_(users)
        o = yield join_(orders, on=lambda o: o.user_id == u.id)
        yield top_n(sum_(o.total), 5)
        yield select(u.name)
    s = norm(q.sql())
    assert "ORDER BY SUM(t2.total) DESC" in s
    assert "LIMIT 5" in s


def test_case_expression():
    @query
    def q():
        u = yield from_(users)
        yield select(
            case(when(u.dept_id == 1, lit("eng")), else_=lit("other")).alias("kind")
        )
    s = norm(q.sql())
    assert "CASE WHEN t1.dept_id = 1 THEN 'eng' ELSE 'other' END AS kind" in s


def test_window_function():
    @query
    def q():
        u = yield from_(users)
        o = yield join_(orders, on=lambda o: o.user_id == u.id)
        yield select(
            u.name,
            row_number().over(partition_by=[u.dept_id], order_by=[o.total.desc()]).alias("rn"),
        )
    s = norm(q.sql())
    assert "ROW_NUMBER() OVER (PARTITION BY t1.dept_id ORDER BY t2.total DESC) AS rn" in s


def test_union():
    @query
    def a():
        u = yield from_(users)
        yield select(u.id)

    @query
    def q():
        u = yield from_(users)
        yield select(u.id)
        yield union(a)
    assert "UNION" in norm(q.sql())


def test_exists_shares_alias_space():
    @query
    def q():
        u = yield from_(users)

        @query
        def has_order():
            o = yield from_(orders)
            yield where_(o.user_id == u.id)
            yield select(o.id)

        yield where_(exists(has_order))
        yield select(u.name)
    s = norm(q.sql())
    assert "EXISTS (" in s
    # inner + outer aliases must not collide
    assert "FROM orders t2" in s
    assert "FROM users t1" in s


def test_tvf_apply_bigquery():
    @query
    def q():
        u = yield from_(users)
        st = yield apply_(get_stats, u.id, lit("2024-01-01"))
        yield select(u.name, st.total_purchases)
    s = norm(q.sql())
    assert "get_user_stats(t1.id, '2024-01-01')" in s


# ── fanout-safe agg_lateral → CTE rewrite ─────────────────────

def test_agg_lateral_rewrites_to_cte():
    @query
    def q():
        u = yield from_(users)
        os = yield agg_lateral(
            orders,
            join_on=lambda o: o.user_id == u.id,
            aggs=lambda o: [count(o.id).alias("cnt"), sum_(o.total).alias("rev")],
            outer=True,
        )
        yield select(u.name, coalesce(os.cnt, 0).alias("orders"))
    s = norm(q.sql())
    assert "WITH _agg_t2 AS" in s
    assert "GROUP BY t3.user_id" in s
    assert "LEFT JOIN _agg_t2 t2 ON t1.id = t2.user_id" in s


def test_two_agg_laterals_no_fanout():
    @query
    def q():
        u = yield from_(users)
        os = yield agg_lateral(orders, join_on=lambda o: o.user_id == u.id,
                               aggs=lambda o: [count(o.id).alias("cnt")], outer=True)
        cs = yield agg_lateral(comments, join_on=lambda c: c.user_id == u.id,
                               aggs=lambda c: [count(c.id).alias("cnt")], outer=True)
        yield select(u.name, os.cnt, cs.cnt)
    s = norm(q.sql())
    assert s.count("LEFT JOIN _agg_") == 2


# ── NEW: sub-query composition ────────────────────────────────

@query
def active_users():
    u = yield from_(users)
    yield where_(is_active(u))
    yield select(u.id, u.name, u.email, u.dept_id)


def test_from_subquery():
    @query
    def dept_summary():
        au = yield from_(active_users)
        d = yield join_(departments, on=lambda d: au.dept_id == d.id)
        yield group_by(d.name)
        yield select(d.name, count(au.id).alias("user_count"))
    s = norm(dept_summary.sql())
    assert "FROM (" in s
    assert "SELECT t2.id, t2.name, t2.email, t2.dept_id FROM users t2 WHERE t2.deleted_at IS NULL" in s
    assert "INNER JOIN departments t3 ON t1.dept_id = t3.id" in s
    assert "GROUP BY t3.name" in s


def test_join_subquery():
    @query
    def q():
        u = yield from_(users)
        au = yield join_(active_users, on=lambda au: u.id == au.id)
        yield select(u.name, au.email)
    s = norm(q.sql())
    assert "INNER JOIN ( SELECT" in s
    assert ") t2 ON t1.id = t2.id" in s


def test_subquery_is_idempotent():
    """A @query may be reused in several FROM/JOIN positions; each use rebuilds."""
    @query
    def q1():
        au = yield from_(active_users)
        yield select(au.name)

    @query
    def q2():
        au = yield from_(active_users)
        yield select(au.email)
    # both build cleanly and independently
    assert "FROM (" in q1.sql()
    assert "FROM (" in q2.sql()


def test_nested_subquery_recursive_rewrite():
    """A composed sub-query that itself uses agg_lateral must have its
    LATERAL lifted into a CTE *inside* the sub-query."""
    @query
    def user_rev():
        u = yield from_(users)
        os = yield agg_lateral(orders, join_on=lambda o: o.user_id == u.id,
                               aggs=lambda o: [sum_(o.total).alias("revenue")], outer=True)
        yield select(u.id, u.name, coalesce(os.revenue, 0).alias("revenue"))

    @query
    def top_spenders():
        ur = yield from_(user_rev)
        yield where_(ur.revenue > 1000)
        yield order_by(ur.revenue.desc())
        yield select(ur.name, ur.revenue)
        yield limit_(10)
    s = norm(top_spenders.sql())
    # the CTE must live inside the parenthesised sub-query, before its SELECT
    assert "FROM ( WITH _agg_" in s
    assert "WHERE t1.revenue > 1000" in s


def test_from_subquery_column_propagation():
    """A TableRef over a sub-query advertises the sub-query's output columns."""
    from monasql.query import _build, _AliasGen
    st = active_users._ensure_built()
    from monasql.query import _output_columns
    assert _output_columns(st) == ["id", "name", "email", "dept_id"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
