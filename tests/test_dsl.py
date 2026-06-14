"""
Tests for the function-style Web-IDE DSL.

No Python ``yield``/``lambda``: statements are assignments (``u = from(users)``)
and calls (``where(...)``, ``select(...)``) with method chaining and operators.
"""
from __future__ import annotations

import re
import pytest

from backend.parser import compile_dsl, tokenize


SCHEMA = {
    "users":    ["id", "name", "email", "dept_id", "deleted_at", "created_at"],
    "orders":   ["id", "user_id", "total", "created_at"],
    "comments": ["id", "user_id", "body"],
    "departments": ["id", "name"],
}


def norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def sql_of(dsl: str, **kw) -> str:
    r = compile_dsl(dsl, schema_tables=SCHEMA, **kw)
    assert r.errors == [], r.errors
    return norm(r.sql)


# ── lexer ─────────────────────────────────────────────────────

def test_lexer_positions_and_newlines():
    toks = tokenize("QUERY q:\n  u = from(users)\n  select(u.id)")
    assert toks[0].type == "KEYWORD" and toks[0].value == "QUERY"
    # newlines are emitted at depth 0 (statement separators)
    assert any(t.type == "NEWLINE" for t in toks)


def test_lexer_suppresses_newlines_inside_parens():
    toks = tokenize("QUERY q:\n  select(\n    u.id,\n    u.name\n  )")
    # no NEWLINE tokens between '(' and ')'
    depth = 0
    inside = []
    for t in toks:
        if t.value == "(":
            depth += 1
        elif t.value == ")":
            depth -= 1
        elif t.type == "NEWLINE":
            inside.append(depth)
    assert all(d == 0 for d in inside)


# ── basics ────────────────────────────────────────────────────

def test_simple_select():
    s = sql_of("QUERY q:\n  u = from(users)\n  select(u.id, u.name)")
    assert s == "SELECT u.id, u.name FROM users u"


def test_self_referential_join():
    dsl = """
    QUERY q:
      u = from(users)
      o = join(orders, on = o.user_id == u.id)
      select(u.name, o.total)
    """
    assert "INNER JOIN orders o ON o.user_id = u.id" in sql_of(dsl)


def test_left_join_via_how():
    dsl = """
    QUERY q:
      u = from(users)
      o = join(orders, on = o.user_id == u.id, how = 'left')
      select(u.name)
    """
    assert "LEFT JOIN orders o" in sql_of(dsl)


def test_where_and_predicates():
    dsl = """
    QUERY q:
      u = from(users)
      o = join(orders, on = o.user_id == u.id)
      where(is_active(u) and in_date_range(o.created_at, start='2024-01-01', end='2024-12-31'))
      select(u.name)
    """
    s = sql_of(dsl)
    assert "u.deleted_at IS NULL" in s
    assert "o.created_at >= '2024-01-01'" in s
    assert "o.created_at <= '2024-12-31'" in s


def test_group_having_order_limit_offset():
    dsl = """
    QUERY q:
      u = from(users)
      o = join(orders, on = o.user_id == u.id)
      group_by(u.name)
      having(count(o.id) > 5)
      select(u.name, count(o.id).alias('n'))
      order_by(n.desc())
      limit(10)
      offset(20)
    """
    s = sql_of(dsl)
    assert "GROUP BY u.name" in s
    assert "HAVING COUNT(o.id) > 5" in s
    assert "ORDER BY n DESC" in s
    assert "LIMIT 10" in s and "OFFSET 20" in s


def test_method_chaining_alias_and_agg():
    s = sql_of("QUERY q:\n o = from(orders)\n select(sum(o.total).alias('rev'), count().alias('c'))")
    assert "SUM(o.total) AS rev" in s
    assert "COUNT(*) AS c" in s


def test_count_distinct():
    s = sql_of("QUERY q:\n o = from(orders)\n select(count(o.user_id, distinct=true).alias('u'))")
    assert "COUNT(DISTINCT o.user_id) AS u" in s


def test_case_expression():
    dsl = """
    QUERY q:
      o = from(orders)
      select(case(when(o.total > 100, 'big'), else_='small').alias('bucket'))
    """
    assert "CASE WHEN o.total > 100 THEN 'big' ELSE 'small' END AS bucket" in sql_of(dsl)


def test_window_function():
    dsl = """
    QUERY q:
      u = from(users)
      o = join(orders, on = o.user_id == u.id)
      select(row_number().over(partition_by=[u.dept_id], order_by=[o.total.desc()]).alias('rn'))
    """
    assert "ROW_NUMBER() OVER (PARTITION BY u.dept_id ORDER BY o.total DESC) AS rn" in sql_of(dsl)


def test_in_between_like_methods():
    dsl = """
    QUERY q:
      u = from(users)
      where(u.dept_id.in_(1, 2, 3) and u.id.between(10, 20) and u.name.like('A%') and u.dept_id.not_in(9))
      select(u.id)
    """
    s = sql_of(dsl)
    assert "u.dept_id IN (1, 2, 3)" in s
    assert "u.id BETWEEN 10 AND 20" in s
    assert "u.name LIKE 'A%'" in s
    assert "u.dept_id NOT IN (9)" in s


def test_cast_method():
    s = sql_of("QUERY q:\n o = from(orders)\n select(o.total.cast('FLOAT64').alias('t'))")
    assert "CAST(o.total AS FLOAT64) AS t" in s


def test_arithmetic_and_precedence():
    dsl = "QUERY q:\n o = from(orders)\n where(o.total > 1 + 2 * 3 or o.user_id == 5 and o.id > 0)\n select(o.id)"
    s = sql_of(dsl)
    assert "o.total > 1 + 2 * 3" in s
    assert "(o.user_id = 5 AND o.id > 0)" in s
    assert "OR (o.user_id = 5 AND o.id > 0)" in s


def test_generic_function_passthrough():
    s = sql_of("QUERY q:\n o = from(orders)\n select(date_trunc(o.created_at, 'MONTH').alias('m'))")
    assert "DATE_TRUNC(o.created_at, 'MONTH') AS m" in s


# ── fanout-safe AGG_LATERAL ───────────────────────────────────

def test_agg_lateral():
    dsl = """
    QUERY dashboard:
      u = from(users)
      os = agg_lateral(orders, join_on = os.user_id == u.id,
                       aggs = [count(os.id).alias('order_count'), sum(os.total).alias('revenue')],
                       outer = true)
      select(u.name, coalesce(os.order_count, 0).alias('orders'))
    """
    s = sql_of(dsl)
    assert "WITH _agg_os AS" in s
    assert "GROUP BY t1.user_id" in s
    assert "LEFT JOIN _agg_os os ON u.id = os.user_id" in s


# ── composition & predicates ──────────────────────────────────

def test_query_referencing_query():
    dsl = """
    QUERY active_users:
      u = from(users)
      where(is_active(u))
      select(u.id, u.name, u.email, u.dept_id)

    QUERY report:
      au = from(active_users)
      d = join(departments, on = au.dept_id == d.id)
      group_by(d.name)
      select(d.name, count(au.id).alias('user_count'))
    """
    s = sql_of(dsl)
    assert "FROM ( SELECT u.id, u.name, u.email, u.dept_id FROM users u WHERE u.deleted_at IS NULL ) au" in s
    assert "INNER JOIN departments d ON au.dept_id = d.id" in s


def test_user_predicate_alias_substitution():
    dsl = """
    PREDICATE is_premium(x):
      is_active(x) and x.email.is_not_null()

    QUERY q:
      cust = from(users)
      where(is_premium(cust))
      select(cust.id)
    """
    assert "(cust.deleted_at IS NULL AND cust.email IS NOT NULL)" in sql_of(dsl)


def test_union():
    dsl = """
    QUERY a:
      u = from(users)
      select(u.id)

    QUERY b:
      u = from(users)
      where(u.dept_id == 1)
      select(u.id)
      union(a)
    """
    assert "UNION" in sql_of(dsl, target="b")


def test_qualified_table_dataset_dot_table():
    # dataset.table → back-ticked qualified reference
    s = sql_of("QUERY q:\n  u = from(analysis_test.users)\n  select(u.name, u.id)")
    assert "FROM `analysis_test.users` u" in s
    assert "SELECT u.name, u.id" in s


def test_qualified_table_project_dataset_table():
    s = sql_of("QUERY q:\n  u = from(myproj.analysis_test.users)\n  select(u.id)")
    assert "FROM `myproj.analysis_test.users` u" in s


def test_qualified_table_string_form():
    # hyphenated project ids can use the string form
    s = sql_of("QUERY q:\n  u = from('my-proj.analysis_test.users')\n  select(u.id)")
    assert "FROM `my-proj.analysis_test.users` u" in s


def test_qualified_table_backtick_form():
    # BigQuery-style backtick quoting — handles hyphenated project ids cleanly
    s = sql_of("QUERY q:\n  u = from(`my-proj.analysis_test.users`)\n  select(u.name)")
    assert "FROM `my-proj.analysis_test.users` u" in s


def test_backtick_lexes_and_resolves_columns():
    # column lookup uses the last path segment, so u.name resolves
    s = sql_of("QUERY q:\n  u = from(`lightbells-x.analytics.users`)\n  where(u.dept_id == 1)\n  select(u.name)")
    assert "FROM `lightbells-x.analytics.users` u" in s
    assert "WHERE u.dept_id = 1" in s


def test_qualified_join():
    dsl = """
    QUERY q:
      u = from(analytics.users)
      o = join(analytics.orders, on = o.user_id == u.id)
      select(u.name, o.total)
    """
    s = sql_of(dsl)
    assert "FROM `analytics.users` u" in s
    assert "INNER JOIN `analytics.orders` o ON o.user_id = u.id" in s


def test_apply_tvf():
    dsl = """
    QUERY q:
      u = from(users)
      s = apply(get_user_stats, u.id, '2024-01-01')
      select(u.name, s.total)
    """
    assert "get_user_stats(u.id, '2024-01-01')" in sql_of(dsl)


# ── error reporting ───────────────────────────────────────────

def test_parse_error_has_position():
    r = compile_dsl("QUERY q:\n  u = from(\n  select(1)", schema_tables=SCHEMA)
    assert r.errors
    assert r.errors[0]["line"] >= 1


def test_unknown_table_warns():
    r = compile_dsl("QUERY q:\n n = from(nope)\n select(n.x)", schema_tables=SCHEMA)
    assert any("unknown table" in w for w in r.warnings)


def test_semantic_error_position():
    # '.foo' on a non-table expression
    r = compile_dsl("QUERY q:\n u = from(users)\n select(count(u.id).bogus())", schema_tables=SCHEMA)
    assert r.errors
    assert "bogus" in r.errors[0]["message"]


def test_compile_never_raises():
    r = compile_dsl("@@@ not valid @@@", schema_tables=SCHEMA)
    assert r.errors
    assert r.sql == ""


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
