"""
monasql.render - Render a QueryState AST to a SQL string.

Supported dialects:
  * ``tsql``     — SQL Server (CROSS APPLY / OUTER APPLY)
  * ``postgres`` — PostgreSQL (JOIN LATERAL … ON TRUE)
  * ``bigquery`` — BigQuery (LATERAL → pre-aggregated CTEs)
  * ``default``  — generic / ANSI-ish (same as tsql for APPLY)
"""
from __future__ import annotations

from typing import Any

from .expr import (
    AliasExpr, BetweenExpr, BinOp, CaseExpr, CastExpr, Col, ExistsExpr,
    Expr, FuncCall, InExpr, Lit, OrderExpr, RawExpr, Star, SubqueryExpr,
    UnaryOp, WindowExpr,
)
from .query import (
    ApplyClause, CTEDef, FromClause, JoinClause, LateralClause,
    QueryState, SetOp,
)


def render(
    state: QueryState,
    *,
    dialect: str = "bigquery",
    pretty: bool = True,
) -> str:
    # BigQuery needs AST rewriting before rendering
    if dialect.lower() in ("bigquery", "bq"):
        from .rewrite import rewrite_for_bigquery
        state = rewrite_for_bigquery(state)
    return _Renderer(dialect, pretty).render_query(state)


class _Renderer:
    def __init__(self, dialect: str, pretty: bool) -> None:
        self.dialect = dialect.lower()
        self.pretty = pretty
        self._indent = 0

    # ── helpers ───────────────────────────────────────────────

    def _nl(self) -> str:
        if self.pretty:
            return "\n" + "  " * self._indent
        return " "

    def _join_parts(self, parts: list[str]) -> str:
        return self._nl().join(parts)

    # ── query ────────────────────────────────────────────────

    def render_query(self, st: QueryState) -> str:
        parts: list[str] = []

        # CTEs
        if st.ctes:
            cte_strs: list[str] = []
            for cte in st.ctes:
                self._indent += 1
                inner = self.render_query(cte.query)
                self._indent -= 1
                col_list = f" ({', '.join(cte.columns)})" if cte.columns else ""
                cte_strs.append(f"{cte.name}{col_list} AS ({self._nl()}  {inner}{self._nl()})")
            parts.append("WITH " + (f",{self._nl()}     ".join(cte_strs)))

        # SELECT
        kw = "SELECT DISTINCT" if st.distinct else "SELECT"
        if st.select_exprs:
            cols = ", ".join(self._expr(e) for e in st.select_exprs)
        else:
            cols = "*"
        parts.append(f"{kw} {cols}")

        # FROM
        if st.from_clause:
            src = st.from_clause.source
            if isinstance(src, QueryState):
                self._indent += 1
                inner = self.render_query(src)
                self._indent -= 1
                parts.append(
                    f"FROM ({self._nl()}  {inner}{self._nl()}) {st.from_clause.alias}"
                )
            else:
                parts.append(f"FROM {src} {st.from_clause.alias}")

        # JOINs / APPLYs / LATERAL
        for j in st.joins:
            parts.append(self._render_join(j))

        # WHERE
        if st.where_conditions:
            conds = self._and_chain(st.where_conditions)
            parts.append(f"WHERE {conds}")

        # GROUP BY
        if st.group_by_exprs:
            cols = ", ".join(self._expr(e) for e in st.group_by_exprs)
            parts.append(f"GROUP BY {cols}")

        # HAVING
        if st.having_conditions:
            conds = self._and_chain(st.having_conditions)
            parts.append(f"HAVING {conds}")

        # ORDER BY
        if st.order_by_exprs:
            cols = ", ".join(self._order(e) for e in st.order_by_exprs)
            parts.append(f"ORDER BY {cols}")

        # LIMIT / OFFSET
        if st.limit is not None:
            if self.dialect == "tsql" and not st.order_by_exprs:
                parts.append(f"TOP {st.limit}")  # handled in SELECT for TSQL
            else:
                parts.append(f"LIMIT {st.limit}")
        if st.offset is not None:
            parts.append(f"OFFSET {st.offset}")

        base = self._join_parts(parts)

        # Set operations
        for sop in st.set_ops:
            all_s = " ALL" if sop.all_ else ""
            # BigQuery requires DISTINCT keyword for EXCEPT/INTERSECT
            if self.dialect in ("bigquery", "bq") and not sop.all_:
                if sop.op in ("EXCEPT", "INTERSECT"):
                    all_s = " DISTINCT"
            inner = self.render_query(sop.query)
            base += f"{self._nl()}{sop.op}{all_s}{self._nl()}{inner}"

        return base

    # ── join rendering ───────────────────────────────────────

    def _render_join(self, j: Any) -> str:
        if isinstance(j, JoinClause):
            if isinstance(j.table_name, QueryState):
                self._indent += 1
                inner = self.render_query(j.table_name)
                self._indent -= 1
                s = f"{j.kind.value} ({self._nl()}  {inner}{self._nl()}) {j.alias}"
            else:
                s = f"{j.kind.value} {j.table_name} {j.alias}"
            if j.on is not None:
                s += f" ON {self._expr(j.on)}"
            return s

        if isinstance(j, ApplyClause):
            args = ", ".join(self._expr(a) for a in j.args)
            if self.dialect in ("postgres", "postgresql"):
                kw = "LEFT JOIN LATERAL" if j.outer else "JOIN LATERAL"
                return f"{kw} {j.tvf_name}({args}) {j.alias} ON TRUE"
            elif self.dialect in ("bigquery", "bq"):
                if getattr(j, "_bigquery_warning", False):
                    return (
                        f"-- ⚠ BigQuery: correlated TVF not supported\n"
                        f"  CROSS JOIN {j.tvf_name}({args}) {j.alias}"
                    )
                return f"CROSS JOIN {j.tvf_name}({args}) {j.alias}"
            else:
                kw = "OUTER APPLY" if j.outer else "CROSS APPLY"
                return f"{kw} {j.tvf_name}({args}) {j.alias}"

        if isinstance(j, LateralClause):
            self._indent += 1
            inner = self.render_query(j.subquery)
            self._indent -= 1
            if self.dialect in ("postgres", "postgresql"):
                kw = "LEFT JOIN LATERAL" if j.outer else "JOIN LATERAL"
                on = f" ON {self._expr(j.on)}" if j.on else " ON TRUE"
                return f"{kw} ({inner}) {j.alias}{on}"
            else:
                kw = "OUTER APPLY" if j.outer else "CROSS APPLY"
                return f"{kw} ({inner}) {j.alias}"

        raise TypeError(f"Unknown join clause: {type(j).__name__}")

    # ── expression rendering ─────────────────────────────────

    def _expr(self, e: Any) -> str:
        if isinstance(e, Col):
            return f"{e.table_alias}.{e.name}" if e.table_alias else e.name

        if isinstance(e, Lit):
            return self._literal(e.value)

        if isinstance(e, Star):
            return f"{e.table_alias}.*" if e.table_alias else "*"

        if isinstance(e, BinOp):
            l, r = self._expr(e.left), self._expr(e.right)
            if e.op in ("AND", "OR"):
                return f"({l} {e.op} {r})"
            return f"{l} {e.op} {r}"

        if isinstance(e, UnaryOp):
            inner = self._expr(e.operand)
            return f"{inner} {e.op}" if e.postfix else f"{e.op} {inner}"

        if isinstance(e, FuncCall):
            args = ", ".join(self._expr(a) for a in e.args)
            d = "DISTINCT " if e.distinct else ""
            return f"{e.name}({d}{args})"

        if isinstance(e, AliasExpr):
            return f"{self._expr(e.expr)} AS {e.name}"

        if isinstance(e, CastExpr):
            return f"CAST({self._expr(e.expr)} AS {e.type_name})"

        if isinstance(e, InExpr):
            vals = ", ".join(self._expr(v) for v in e.values)
            neg = "NOT " if e.negated else ""
            return f"{self._expr(e.expr)} {neg}IN ({vals})"

        if isinstance(e, BetweenExpr):
            return (
                f"{self._expr(e.expr)} BETWEEN "
                f"{self._expr(e.low)} AND {self._expr(e.high)}"
            )

        if isinstance(e, CaseExpr):
            parts = ["CASE"]
            for cond, then in e.whens:
                parts.append(
                    f"WHEN {self._expr(cond)} THEN {self._expr(then)}"
                )
            if e.else_ is not None:
                parts.append(f"ELSE {self._expr(e.else_)}")
            parts.append("END")
            return " ".join(parts)

        if isinstance(e, WindowExpr):
            base = self._expr(e.expr)
            over: list[str] = []
            if e.partition_by:
                pb = ", ".join(self._expr(x) for x in e.partition_by)
                over.append(f"PARTITION BY {pb}")
            if e.order_by:
                ob = ", ".join(self._order(x) for x in e.order_by)
                over.append(f"ORDER BY {ob}")
            if e.frame:
                over.append(e.frame)
            return f"{base} OVER ({' '.join(over)})"

        if isinstance(e, SubqueryExpr):
            inner = self.render_query(e.query)
            return f"({inner})"

        if isinstance(e, ExistsExpr):
            q = e.query
            if hasattr(q, "_ensure_built"):
                q = q._ensure_built()
            inner = self.render_query(q)
            return f"EXISTS ({inner})"

        if isinstance(e, RawExpr):
            return e.sql

        if isinstance(e, OrderExpr):
            return self._order(e)

        return str(e)

    def _order(self, e: Any) -> str:
        if isinstance(e, OrderExpr):
            return f"{self._expr(e.expr)} {e.direction}"
        return self._expr(e)

    def _and_chain(self, exprs: list[Expr]) -> str:
        rendered = [self._expr(e) for e in exprs]
        if len(rendered) == 1:
            # strip outer parens if the single condition is AND/OR wrapped
            return rendered[0]
        return " AND ".join(rendered)

    # ── literal rendering ────────────────────────────────────

    @staticmethod
    def _literal(value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, str):
            return "'" + value.replace("'", "''") + "'"
        if isinstance(value, (int, float)):
            return str(value)
        return str(value)
