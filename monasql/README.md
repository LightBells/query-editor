# monasql

A monadic SQL DSL: write queries as Python generators (do-notation) and render
to BigQuery (and other dialects). TVF application, fanout-safe lateral
aggregation, and **sub-query composition** are all first-class.

```python
from monasql import *

users  = Table("users",  ["id", "name", "deleted_at"])
orders = Table("orders", ["id", "user_id", "total"])

@query
def report():
    u  = yield from_(users)
    os = yield agg_lateral(orders,
            join_on = lambda o: o.user_id == u.id,
            aggs    = lambda o: [count(o.id).alias("cnt"), sum_(o.total).alias("rev")],
            outer   = True)
    yield where_(is_active(u))
    yield select(u.name, coalesce(os.cnt, 0).alias("orders"), coalesce(os.rev, 0).alias("revenue"))

print(report.sql())   # → fanout-safe pre-aggregated CTE + LEFT JOIN
```

## Sub-query composition

A `@query` can be reused as a sub-query in `from_()` / `join_()`:

```python
@query
def active_users():
    u = yield from_(users)
    yield where_(is_active(u))
    yield select(u.id, u.name)

@query
def top():
    au = yield from_(active_users)              # FROM (SELECT … FROM users …) au
    yield select(au.name)
```

Sub-queries are rebuilt in the parent's alias space (no collisions) and the
BigQuery rewrite recurses into them, so nested `agg_lateral` stays fanout-safe.

## Modules

| Module | Responsibility |
|--------|----------------|
| `expr.py`   | expression AST, operator overloading (`u.id == o.user_id`) |
| `schema.py` | `Table`, `TVF`, runtime `TableRef` |
| `query.py`  | monadic builder, `QueryState`, sub-query composition |
| `helpers.py`| composable predicates, `agg_lateral` |
| `render.py` | `QueryState` → SQL (tsql / postgres / bigquery) |
| `rewrite.py`| BigQuery `LATERAL`/`APPLY` → CTE + JOIN |

See the repo root `README.md` for the Web IDE and DSL.
