"""
monasql.rewrite - Dialect-specific AST transformations.

BigQuery doesn't support CROSS APPLY / LATERAL JOIN, so we rewrite
lateral subqueries into pre-aggregated CTEs with regular JOINs::

    -- Before (T-SQL / Postgres)
    OUTER APPLY (
        SELECT COUNT(id) AS cnt FROM orders WHERE user_id = u.id
    ) os

    -- After (BigQuery)
    WITH _agg_os AS (
        SELECT user_id, COUNT(id) AS cnt
        FROM orders
        GROUP BY user_id
    )
    ... LEFT JOIN _agg_os os ON u.id = os.user_id
"""
from __future__ import annotations

import copy
from dataclasses import field
from typing import Any

from .expr import BinOp, Col, Expr, Lit, Star
from .query import (
    ApplyClause, CTEDef, FromClause, JoinClause, JoinKind,
    LateralClause, QueryState,
)


def rewrite_for_bigquery(state: QueryState) -> QueryState:
    """Rewrite LATERAL/APPLY joins into CTEs for BigQuery compatibility.

    Returns a new QueryState (original is untouched).
    """
    st = copy.deepcopy(state)

    # Recurse into composed sub-queries first (FROM / JOIN / CTE / set-op),
    # so any LATERAL/APPLY nested inside them is rewritten too.
    if st.from_clause and isinstance(st.from_clause.source, QueryState):
        st.from_clause.source = rewrite_for_bigquery(st.from_clause.source)
    for cte in st.ctes:
        cte.query = rewrite_for_bigquery(cte.query)
    for sop in st.set_ops:
        sop.query = rewrite_for_bigquery(sop.query)

    new_joins: list = []
    for j in st.joins:
        if isinstance(j, LateralClause):
            _rewrite_lateral(st, j, new_joins)
        elif isinstance(j, ApplyClause):
            _rewrite_apply(st, j, new_joins)
        else:
            if isinstance(j, JoinClause) and isinstance(j.table_name, QueryState):
                j.table_name = rewrite_for_bigquery(j.table_name)
            new_joins.append(j)

    st.joins = new_joins
    return st


# ── lateral subquery → CTE + JOIN ────────────────────────────

def _rewrite_lateral(
    st: QueryState,
    lat: LateralClause,
    new_joins: list,
) -> None:
    """Convert a LateralClause into a CTE + JoinClause."""
    inner: QueryState = lat.subquery
    cte_name = f"_agg_{lat.alias}"

    # 1. Collect aliases defined inside the inner query
    inner_aliases = _collect_aliases(inner)

    # 2. Separate correlated vs local predicates in inner WHERE
    correlated, remaining = _extract_correlations(
        inner.where_conditions, inner_aliases,
    )

    if not correlated:
        # No correlation found — just lift as a plain CTE
        st.ctes.append(CTEDef(cte_name, inner))
        kind = JoinKind.LEFT if lat.outer else JoinKind.INNER
        new_joins.append(JoinClause(kind, cte_name, lat.alias, lat.on))
        return

    # 3. Build the rewritten inner query:
    #    - Remove correlated predicates from WHERE
    #    - Add inner correlation columns to SELECT and GROUP BY
    inner.where_conditions = remaining

    # Collect the inner-side columns for GROUP BY and the join ON
    join_on_parts: list[Expr] = []
    for inner_col, outer_col in correlated:
        # Add to GROUP BY if not already present
        if not _col_in_list(inner_col, inner.group_by_exprs):
            inner.group_by_exprs.append(inner_col)
        # Add to SELECT so it's available for JOIN ON
        # Use the bare column name (without inner alias) in the CTE
        cte_col = Col(inner_col.name, lat.alias)
        if not _col_in_select(inner_col, inner.select_exprs):
            inner.select_exprs.insert(0, inner_col)
        # Build the JOIN ON: outer_col = cte.inner_col_name
        join_on_parts.append(BinOp("=", outer_col, cte_col))

    # Combine JOIN ON parts with AND
    join_on: Expr = join_on_parts[0]
    for part in join_on_parts[1:]:
        join_on = BinOp("AND", join_on, part)

    # 4. Register as CTE and add a regular JOIN
    st.ctes.append(CTEDef(cte_name, inner))
    kind = JoinKind.LEFT if lat.outer else JoinKind.INNER
    new_joins.append(JoinClause(kind, cte_name, lat.alias, join_on))


# ── TVF APPLY → CTE or comma-join ────────────────────────────

def _rewrite_apply(
    st: QueryState,
    ap: ApplyClause,
    new_joins: list,
) -> None:
    """Convert an ApplyClause (TVF) for BigQuery.

    BigQuery supports TVFs in FROM but not correlated ones.
    - Non-correlated: ``CROSS JOIN tvf(args)``
    - Correlated: wrap in a CTE with the correlated args factored out.
      (This is a best-effort heuristic.)
    """
    # Check if any arg references an outer alias
    outer_refs = _find_outer_col_refs(ap.args, _outer_context_aliases(st, ap))
    if not outer_refs:
        # Non-correlated → simple comma-join / CROSS JOIN
        args_str_placeholder = ap  # keep as-is, renderer handles
        new_joins.append(ap)
        return

    # Correlated TVF — BigQuery can't do this directly.
    # Fall back to keeping the ApplyClause and let the renderer
    # emit a comment warning.
    ap._bigquery_warning = True  # type: ignore[attr-defined]
    new_joins.append(ap)


# ── helpers ───────────────────────────────────────────────────

def _collect_aliases(st: QueryState) -> set[str]:
    """Collect all table aliases defined inside a QueryState."""
    aliases: set[str] = set()
    if st.from_clause:
        aliases.add(st.from_clause.alias)
    for j in st.joins:
        if isinstance(j, JoinClause):
            aliases.add(j.alias)
        elif isinstance(j, ApplyClause):
            aliases.add(j.alias)
        elif isinstance(j, LateralClause):
            aliases.add(j.alias)
    return aliases


def _extract_correlations(
    conditions: list[Expr],
    inner_aliases: set[str],
) -> tuple[list[tuple[Col, Col]], list[Expr]]:
    """Split WHERE conditions into correlated pairs and remaining.

    A correlated pair is ``inner_col = outer_col`` where inner_col's
    table_alias ∈ inner_aliases and outer_col's table_alias ∉ inner_aliases.
    """
    correlated: list[tuple[Col, Col]] = []
    remaining: list[Expr] = []

    for cond in conditions:
        pair = _try_extract_correlation(cond, inner_aliases)
        if pair:
            correlated.append(pair)
        else:
            remaining.append(cond)

    return correlated, remaining


def _try_extract_correlation(
    expr: Expr, inner_aliases: set[str],
) -> tuple[Col, Col] | None:
    """If expr is ``col_a = col_b`` with one side inner and one outer,
    return ``(inner_col, outer_col)``."""
    if not isinstance(expr, BinOp) or expr.op != "=":
        return None
    left, right = expr.left, expr.right
    if not (isinstance(left, Col) and isinstance(right, Col)):
        return None
    l_inner = left.table_alias in inner_aliases if left.table_alias else False
    r_inner = right.table_alias in inner_aliases if right.table_alias else False
    if l_inner and not r_inner:
        return (left, right)    # left=inner, right=outer
    if r_inner and not l_inner:
        return (right, left)    # right=inner, left=outer
    return None


def _col_in_list(col: Col, exprs: list[Expr]) -> bool:
    for e in exprs:
        if isinstance(e, Col) and e.name == col.name and e.table_alias == col.table_alias:
            return True
    return False


def _col_in_select(col: Col, exprs: list[Expr]) -> bool:
    """Check if a column (or aliased version) is already in SELECT."""
    for e in exprs:
        if isinstance(e, Col) and e.name == col.name and e.table_alias == col.table_alias:
            return True
    return False


def _find_outer_col_refs(exprs: list[Expr], outer_aliases: set[str]) -> list[Col]:
    """Find Col references in exprs that point to outer-query aliases."""
    refs: list[Col] = []
    for e in exprs:
        if isinstance(e, Col) and e.table_alias in outer_aliases:
            refs.append(e)
    return refs


def _outer_context_aliases(st: QueryState, current_join: Any) -> set[str]:
    """Get aliases from the outer query context (FROM + joins before current)."""
    aliases: set[str] = set()
    if st.from_clause:
        aliases.add(st.from_clause.alias)
    for j in st.joins:
        if j is current_join:
            break
        if hasattr(j, "alias"):
            aliases.add(j.alias)
    return aliases
