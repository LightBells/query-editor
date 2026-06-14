"""
Compiler / interpreter:  function-style DSL  →  monasql builder  →  SQL.

Each ``QUERY`` block becomes a monasql generator: assignments yield source
instructions (``from``/``join``/``agg_lateral``) and bind the returned
``TableRef``; bare calls (``where``/``select``/…) yield the matching
instruction.  Expressions are interpreted directly onto monasql's overloaded
operators, so ``o.user_id == u.id`` builds a ``BinOp`` and
``count(o.id).alias('n')`` builds an aliased aggregate.

Self-referential joins work without lambdas: ``o = join(orders, on=o.user_id ==
u.id)`` is compiled by handing monasql a callback that evaluates the ``on``
expression with ``o`` bound to the freshly-created join ref.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from monasql import (  # noqa: E402
    Table, TVF, query,
    from_, join_, apply_, lateral_, where_, select, group_by, having_,
    order_by, limit_, offset_, distinct_, union, intersect, except_, with_cte,
    count, sum_, avg, min_, max_, string_agg, coalesce, nullif,
    greatest, least, concat, func, lit, col, star, raw, case, when, exists,
    row_number, rank, dense_rank, ntile, lag, lead, first_value, last_value,
    is_active, in_date_range, no_outliers, value_in_range, has_value,
    agg_lateral, agg_lateral_grouped,
)
from monasql.schema import TableRef  # noqa: E402
from monasql.expr import Expr, UnaryOp as MUnaryOp  # noqa: E402

from . import ast_nodes as A
from .parser import parse, ParseError


class SemanticError(Exception):
    def __init__(self, message: str, line: int = 0, col: int = 0):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col


# ── name tables ───────────────────────────────────────────────

# builder ops (return monasql Instr) — accept `_` suffix for Python parity
_BUILDERS = {
    "from": "from_", "from_": "from_",
    "join": "join_", "join_": "join_",
    "agg_lateral": "agg_lateral", "agg_lateral_grouped": "agg_lateral_grouped",
    "apply": "apply_", "apply_": "apply_",
    "lateral": "lateral_", "lateral_": "lateral_",
    "where": "where_", "where_": "where_",
    "select": "select",
    "group_by": "group_by",
    "having": "having_", "having_": "having_",
    "order_by": "order_by",
    "limit": "limit_", "limit_": "limit_",
    "offset": "offset_", "offset_": "offset_",
    "distinct": "distinct_", "distinct_": "distinct_",
    "union": "union", "intersect": "intersect",
    "except": "except_", "except_": "except_",
    "with_cte": "with_cte",
}

_BUILTIN_PRED = {"is_active", "in_date_range", "no_outliers", "value_in_range", "has_value"}


# ── result / context ──────────────────────────────────────────

@dataclass
class CompileResult:
    sql: str
    errors: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    queries: dict[str, str] = field(default_factory=dict)
    main: Optional[str] = None


class Context:
    def __init__(self, schema_tables: Optional[dict[str, list[str]]] = None):
        self.tables: dict[str, Table] = {}
        if schema_tables:
            for name, cols in schema_tables.items():
                self.tables[name] = Table(name, list(cols))
        self.predicates: dict[str, A.PredicateDef] = {}
        self.queries: dict[str, Any] = {}
        self.tvfs: dict[str, TVF] = {}
        self.warnings: list[str] = []

    def resolve_source(self, node: A.ExprNode):
        # bare name → known table / composed query / unknown table
        if isinstance(node, A.Name):
            name = node.id
            if name in self.queries:
                return self.queries[name]
            if name in self.tables:
                return self.tables[name]
            self.warnings.append(f"Line {node.line}: unknown table or query '{name}'")
            t = Table(name)
            self.tables[name] = t
            return t
        # back-ticked path:  `my-proj.dataset.table`  (BigQuery style)
        if isinstance(node, A.Quoted):
            return self._qualified_table(node.value.split("."))
        # dotted reference:  dataset.table  /  project.dataset.table
        path = _dotted_path(node)
        if path is not None:
            return self._qualified_table(path)
        # string form (also fine for hyphenated project ids): 'my-proj.ds.table'
        if isinstance(node, A.Literal) and isinstance(node.value, str):
            return self._qualified_table(node.value.split("."))
        raise SemanticError(
            "expected a table or query name (e.g. users, dataset.table, or 'proj.ds.table')",
            getattr(node, "line", 0), getattr(node, "col", 0))

    def _qualified_table(self, segments: list[str]) -> Table:
        qualified = ".".join(segments)
        # back-tick the path so hyphenated projects / reserved words are valid SQL
        cols = self.tables[segments[-1]].columns if segments[-1] in self.tables else []
        return Table(f"`{qualified}`", cols)

    def resolve_query(self, node: A.ExprNode):
        if isinstance(node, A.Name) and node.id in self.queries:
            return self.queries[node.id]
        raise SemanticError("expected a previously-defined QUERY name",
                            getattr(node, "line", 0), getattr(node, "col", 0))

    def resolve_tvf(self, name: str) -> TVF:
        if name not in self.tvfs:
            self.tvfs[name] = TVF(name)
        return self.tvfs[name]


# ── entry point ───────────────────────────────────────────────

def compile_dsl(text: str, *, schema_tables=None, dialect="bigquery",
                target: Optional[str] = None) -> CompileResult:
    try:
        program = parse(text)
    except ParseError as e:
        return CompileResult("", errors=[_err(e.message, e.line, e.col)])

    ctx = Context(schema_tables)
    result = CompileResult("")
    last: Optional[str] = None

    for stmt in program.statements:
        if isinstance(stmt, A.PredicateDef):
            ctx.predicates[stmt.name] = stmt
        elif isinstance(stmt, A.QueryDef):
            try:
                q = _build_query(stmt, ctx)
                ctx.queries[stmt.name] = q
                result.queries[stmt.name] = q.sql(dialect=dialect)
                last = stmt.name
            except (SemanticError, ParseError) as e:
                result.errors.append(_err(e.message, getattr(e, "line", stmt.line),
                                          getattr(e, "col", stmt.col)))
            except Exception as e:  # pragma: no cover - defensive
                result.errors.append(_err(f"{type(e).__name__}: {e}", stmt.line, stmt.col))

    result.warnings = ctx.warnings
    chosen = target or last
    if chosen and chosen in result.queries:
        result.sql = result.queries[chosen]
        result.main = chosen
    return result


def _err(message: str, line: int, col: int, severity: str = "error") -> dict:
    return {"line": line, "col": col, "message": message, "severity": severity}


# ── QUERY → monasql Query ─────────────────────────────────────

def _build_query(qdef: A.QueryDef, ctx: Context):
    def gen():
        env: dict[str, Any] = {}
        qstate = {"has_from": False}
        for stmt in qdef.body:
            if isinstance(stmt, A.Assign):
                instr = _eval_builder(stmt.value, env, ctx, stmt.target, qstate)
                ref = yield instr
                env[stmt.target] = ref
            else:
                instr = _eval_builder(stmt.value, env, ctx, None, qstate)
                if instr is not None:
                    yield instr
    return query(gen)


def _source_arg(args: list, env: dict, ctx: Context, node: A.Call):
    """Resolve a from()/join() source, rejecting an already-bound local ref."""
    if not args:
        raise SemanticError("expected a table or query name", node.line, node.col)
    a0 = args[0]
    if isinstance(a0, A.Name) and a0.id in env:
        raise SemanticError(
            f"'{a0.id}' is already a table in this query — from()/join() need a NEW "
            f"table or query (to join two, write `x = join(other, on = ...)`)",
            a0.line, a0.col)
    return ctx.resolve_source(a0)


def _eval_builder(node: A.ExprNode, env: dict, ctx: Context, target: Optional[str],
                  qstate: dict):
    if not isinstance(node, A.Call) or not isinstance(node.func, A.Name):
        raise SemanticError("expected a query operation like from(...), where(...), select(...)",
                            getattr(node, "line", 0), getattr(node, "col", 0))
    fname = node.func.id
    canon = _BUILDERS.get(fname)
    if canon is None:
        raise SemanticError(f"'{fname}' is not a query operation", node.line, node.col)
    args, kw = node.args, node.keywords
    alias = _ident(kw["alias"]) if "alias" in kw else target

    if canon == "from_":
        if qstate["has_from"]:
            raise SemanticError(
                "from() can only appear once per QUERY — use join(...) to add another table",
                node.line, node.col)
        qstate["has_from"] = True
        return from_(_source_arg(args, env, ctx, node), alias=alias)

    if canon == "join_":
        if not qstate["has_from"]:
            raise SemanticError("join() needs a from(...) before it", node.line, node.col)
        if len(args) != 1:
            raise SemanticError(
                "join() takes exactly one table/query to add to the current FROM. "
                "To join two sources write `x = join(other, on = ...)`.",
                node.line, node.col)
        how = _ident(kw["how"]) if "how" in kw else "inner"
        return join_(_source_arg(args, env, ctx, node),
                     on=_deferred(kw.get("on"), env, ctx, target),
                     how=how, alias=alias)

    if canon == "agg_lateral":
        return agg_lateral(
            _source_arg(args, env, ctx, node),
            join_on=_deferred(_require(kw, "join_on", node), env, ctx, target),
            aggs=_deferred_list(_require(kw, "aggs", node), env, ctx, target),
            where=_deferred(kw["where"], env, ctx, target) if "where" in kw else None,
            outer=_pyval(kw["outer"]) if "outer" in kw else True,
            alias=alias)

    if canon == "agg_lateral_grouped":
        return agg_lateral_grouped(
            _source_arg(args, env, ctx, node),
            join_on=_deferred(_require(kw, "join_on", node), env, ctx, target),
            group_cols=_deferred_list(_require(kw, "group_cols", node), env, ctx, target),
            aggs=_deferred_list(_require(kw, "aggs", node), env, ctx, target),
            where=_deferred(kw["where"], env, ctx, target) if "where" in kw else None,
            outer=_pyval(kw["outer"]) if "outer" in kw else True,
            alias=alias)

    if canon == "apply_":
        if not args or not isinstance(args[0], A.Name):
            raise SemanticError("apply() needs a TVF name as its first argument", node.line, node.col)
        tvf = ctx.resolve_tvf(args[0].id)
        rest = [_eval(a, env, ctx) for a in args[1:]]
        outer = _pyval(kw["outer"]) if "outer" in kw else False
        return apply_(tvf, *rest, outer=outer, alias=alias)

    if canon == "lateral_":
        sub = ctx.resolve_query(args[0])
        on = None
        if "on" in kw:
            env2 = dict(env)
            if alias:
                env2[target] = TableRef("lateral", alias)
            on = _eval(kw["on"], env2, ctx)
        outer = _pyval(kw["outer"]) if "outer" in kw else False
        return lateral_(sub, on=on, outer=outer, alias=alias)

    if canon == "where_":
        return where_(_eval(_require_arg(args, node), env, ctx))
    if canon == "select":
        return select(*[_eval(a, env, ctx) for a in args])
    if canon == "group_by":
        return group_by(*[_eval(a, env, ctx) for a in args])
    if canon == "having_":
        return having_(_eval(_require_arg(args, node), env, ctx))
    if canon == "order_by":
        return order_by(*[_eval(a, env, ctx) for a in args])
    if canon == "limit_":
        return limit_(int(_pyval(_require_arg(args, node))))
    if canon == "offset_":
        return offset_(int(_pyval(_require_arg(args, node))))
    if canon == "distinct_":
        return distinct_()
    if canon in ("union", "intersect", "except_"):
        other = ctx.resolve_query(args[0])
        all_ = bool(_pyval(kw["all_"])) if "all_" in kw else False
        return {"union": union, "intersect": intersect, "except_": except_}[canon](other, all_=all_)
    if canon == "with_cte":
        name = _ident(args[0])
        sub = ctx.resolve_query(args[1])
        return with_cte(name, sub)

    raise SemanticError(f"unsupported operation '{fname}'", node.line, node.col)  # pragma: no cover


# ── deferred callbacks (self-referential on=/join_on=/aggs=) ──

def _deferred(node, env, ctx, target):
    if node is None:
        return None

    def cb(ref, _n=node, _env=env, _t=target):
        local = dict(_env)
        if _t is not None:
            local[_t] = ref
        return _eval(_n, local, ctx)
    return cb


def _deferred_list(node, env, ctx, target):
    elts = node.elts if isinstance(node, A.ListLit) else [node]

    def cb(ref, _elts=elts, _env=env, _t=target):
        local = dict(_env)
        if _t is not None:
            local[_t] = ref
        return [_eval(e, local, ctx) for e in _elts]
    return cb


# ── expression interpreter ────────────────────────────────────

def _eval(node: A.ExprNode, env: dict, ctx: Context) -> Any:
    if isinstance(node, A.Literal):
        return lit(node.value)
    if isinstance(node, A.Quoted):
        return raw(f"`{node.value}`")          # back-ticked identifier in an expression
    if isinstance(node, A.Name):
        return env[node.id] if node.id in env else col(node.id)
    if isinstance(node, A.ListLit):
        return [_eval(e, env, ctx) for e in node.elts]
    if isinstance(node, A.Attribute):
        recv = _eval(node.value, env, ctx)
        if isinstance(recv, TableRef):
            return getattr(recv, node.attr)
        raise SemanticError(f"cannot read '.{node.attr}' — '{_describe(node.value)}' is not a table",
                            node.line, node.col)
    if isinstance(node, A.UnaryOp):
        if node.op == "not":
            return ~_eval(node.operand, env, ctx)
        # unary minus
        if isinstance(node.operand, A.Literal) and isinstance(node.operand.value, (int, float)):
            return lit(-node.operand.value)
        return MUnaryOp("-", _eval(node.operand, env, ctx))
    if isinstance(node, A.BinOp):
        return _binop(node.op, _eval(node.left, env, ctx), _eval(node.right, env, ctx))
    if isinstance(node, A.Call):
        return _eval_call(node, env, ctx)
    raise SemanticError("cannot evaluate this expression",
                        getattr(node, "line", 0), getattr(node, "col", 0))


_BINOPS = {
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b, "<>": lambda a, b: a != b,
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    "+": lambda a, b: a + b, "-": lambda a, b: a - b,
    "*": lambda a, b: a * b, "/": lambda a, b: a / b, "%": lambda a, b: a % b,
    "and": lambda a, b: a & b, "or": lambda a, b: a | b,
}


def _binop(op: str, left: Any, right: Any) -> Any:
    return _BINOPS[op](left, right)


def _eval_call(node: A.Call, env: dict, ctx: Context) -> Any:
    if isinstance(node.func, A.Attribute):
        recv = _eval(node.func.value, env, ctx)
        return _invoke_method(recv, node.func.attr, node, env, ctx)

    fname = node.func.id
    if fname in ctx.predicates:
        return _expand_predicate(ctx.predicates[fname], node, env, ctx)
    return _call_func(fname, node, env, ctx)


_METHODS = {"alias", "asc", "desc", "cast", "is_null", "is_not_null",
            "like", "ilike", "in_", "not_in", "between", "over"}


def _invoke_method(recv: Any, method: str, node: A.Call, env: dict, ctx: Context) -> Any:
    if method not in _METHODS:
        raise SemanticError(f"unknown method '.{method}()'", node.line, node.col)
    if not isinstance(recv, Expr):
        raise SemanticError(f"'.{method}()' can only be called on an expression", node.line, node.col)
    args = node.args
    if method == "alias":
        return recv.alias(_ident(args[0]))
    if method == "asc":
        return recv.asc()
    if method == "desc":
        return recv.desc()
    if method == "cast":
        return recv.cast(_ident(args[0]))
    if method == "is_null":
        return recv.is_null()
    if method == "is_not_null":
        return recv.is_not_null()
    if method == "like":
        return recv.like(_pyval(args[0]))
    if method == "ilike":
        return recv.ilike(_pyval(args[0]))
    if method == "in_":
        return recv.in_(*[_eval(a, env, ctx) for a in args])
    if method == "not_in":
        return recv.not_in(*[_eval(a, env, ctx) for a in args])
    if method == "between":
        return recv.between(_eval(args[0], env, ctx), _eval(args[1], env, ctx))
    if method == "over":
        kw = node.keywords
        pb = _eval(kw["partition_by"], env, ctx) if "partition_by" in kw else None
        ob = _eval(kw["order_by"], env, ctx) if "order_by" in kw else None
        frame = _pyval(kw["frame"]) if "frame" in kw else None
        return recv.over(partition_by=pb, order_by=ob, frame=frame)
    raise SemanticError(f"unknown method '.{method}()'", node.line, node.col)  # pragma: no cover


def _call_func(fname: str, node: A.Call, env: dict, ctx: Context) -> Any:
    key = fname.lower()
    args = node.args
    kw = node.keywords
    ev = [_eval(a, env, ctx) for a in args]

    # aggregates
    if key == "count":
        distinct = bool(_pyval(kw["distinct"])) if "distinct" in kw else False
        return count(ev[0] if ev else None, distinct=distinct)
    if key in ("sum", "sum_"):
        return sum_(ev[0])
    if key == "avg":
        return avg(ev[0])
    if key in ("min", "min_"):
        return min_(ev[0])
    if key in ("max", "max_"):
        return max_(ev[0])
    if key == "string_agg":
        return string_agg(ev[0], _pyval(args[1]) if len(args) > 1 else ",")

    # scalar
    if key == "coalesce":
        return coalesce(*ev)
    if key == "nullif":
        return nullif(ev[0], ev[1])
    if key == "greatest":
        return greatest(*ev)
    if key == "least":
        return least(*ev)
    if key == "concat":
        return concat(*ev)

    # window
    if key == "row_number":
        return row_number()
    if key == "rank":
        return rank()
    if key == "dense_rank":
        return dense_rank()
    if key == "ntile":
        return ntile(int(_pyval(args[0])))
    if key == "lag":
        return lag(ev[0], *[int(_pyval(a)) for a in args[1:2]], *[_pyval(a) for a in args[2:3]])
    if key == "lead":
        return lead(ev[0], *[int(_pyval(a)) for a in args[1:2]], *[_pyval(a) for a in args[2:3]])
    if key == "first_value":
        return first_value(ev[0])
    if key == "last_value":
        return last_value(ev[0])

    # built-in predicates / helpers
    if key in _BUILTIN_PRED:
        pkw = {k: _pyval(v) for k, v in kw.items()}
        if key == "is_active":
            return is_active(ev[0], **pkw)
        if key == "in_date_range":
            return in_date_range(ev[0], **pkw)
        if key == "no_outliers":
            return no_outliers(ev[0], **pkw)
        if key == "value_in_range":
            return value_in_range(ev[0], **pkw)
        if key == "has_value":
            return has_value(ev[0])

    # primitives / conditionals
    if key == "col":
        return col(_ident(args[0]), table=_ident(kw["table"]) if "table" in kw else None)
    if key == "lit":
        return lit(_pyval(args[0]))
    if key == "star":
        return star(_ident(args[0]) if args else None)
    if key == "raw":
        return raw(_pyval(args[0]))
    if key == "exists":
        return exists(ctx.resolve_query(args[0]))
    if key == "when":
        return when(ev[0], ev[1])
    if key == "case":
        else_ = _eval(kw["else_"], env, ctx) if "else_" in kw else None
        return case(*ev, else_=else_)
    if key == "func":
        return func(_ident(args[0]), *ev[1:])

    # unknown → emit as a raw BigQuery function call
    return func(fname, *ev)


def _expand_predicate(pdef: A.PredicateDef, node: A.Call, env: dict, ctx: Context) -> Any:
    if len(node.args) != len(pdef.params):
        raise SemanticError(
            f"predicate '{pdef.name}' expects {len(pdef.params)} argument(s), got {len(node.args)}",
            node.line, node.col)
    local = dict(env)
    for p, a in zip(pdef.params, node.args):
        local[p] = _eval(a, env, ctx)
    return _eval(pdef.body, local, ctx)


# ── small helpers ─────────────────────────────────────────────

def _pyval(node: A.ExprNode) -> Any:
    if isinstance(node, A.Literal):
        return node.value
    if isinstance(node, A.UnaryOp) and node.op == "-" and isinstance(node.operand, A.Literal):
        return -node.operand.value
    raise SemanticError("expected a constant value (number/string/true/false)",
                        getattr(node, "line", 0), getattr(node, "col", 0))


def _ident(node: A.ExprNode) -> str:
    if isinstance(node, A.Name):
        return node.id
    if isinstance(node, A.Literal) and isinstance(node.value, str):
        return node.value
    raise SemanticError("expected a name or string", getattr(node, "line", 0), getattr(node, "col", 0))


def _require(kw: dict, name: str, node: A.Call):
    if name not in kw:
        raise SemanticError(f"missing required argument '{name}='", node.line, node.col)
    return kw[name]


def _require_arg(args: list, node: A.Call):
    if not args:
        raise SemanticError("this operation needs an argument", node.line, node.col)
    return args[0]


def _describe(node: A.ExprNode) -> str:
    if isinstance(node, A.Name):
        return node.id
    if isinstance(node, A.Attribute):
        return f"{_describe(node.value)}.{node.attr}"
    return "value"


def _dotted_path(node: A.ExprNode) -> Optional[list[str]]:
    """``Attribute(Attribute(Name(a), b), c)`` → ``['a', 'b', 'c']``; else None."""
    parts: list[str] = []
    while isinstance(node, A.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, A.Name):
        parts.append(node.id)
        parts.reverse()
        return parts
    return None
