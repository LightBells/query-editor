# monasql — Monadic SQL Transpiler & Web IDE

Build BigQuery SQL from a clean, composable DSL — either in **Python**
(generator-based do-notation) or in a **browser IDE** (a no-Python surface
language with autocomplete, live SQL preview and query execution).

This repo implements the [`monasql_spec.md`](./monasql_spec.md) in full:

| # | Feature | Where |
|---|---------|-------|
| 1 | **Sub-query composition** — reuse a `@query` inside `from_()` / `join_()` | [`monasql/`](./monasql) |
| 2 | **Web IDE** — Monaco editor, schema browser, live SQL, results | [`frontend/`](./frontend) + [`backend/`](./backend) |
| 3 | **BigQuery schema integration** — tables/columns from `INFORMATION_SCHEMA` | [`backend/services/schema_service.py`](./backend/services/schema_service.py) |

Plus the glue that makes the IDE possible: a **hand-written DSL transpiler**
(lexer → parser → compiler) that turns the clean DSL into the monasql engine,
so the editor reuses *everything* — sub-query composition, fanout-safe
`AGG_LATERAL`, and the BigQuery `LATERAL → CTE` rewrite.

```
┌──────────── Browser (React + TS + Monaco) ──────────────┐
│  Schema browser │ DSL editor │ SQL preview │ Results     │
└───────┬──────────────────────────────────────────────────┘
        │ HTTP + WebSocket (realtime compile)
┌───────▼──────────── FastAPI ────────────────────────────┐
│  /api/compile  /api/execute  /api/schema  /api/ws        │
│  DSL ──lex──▶ parse ──▶ compile ──▶ monasql ──▶ SQL      │
└───────┬──────────────────────────────────────────────────┘
        │ google-cloud-bigquery (optional)
┌───────▼──────────── BigQuery ───────────────────────────┐
│  INFORMATION_SCHEMA (schema) · jobs.query (execute)      │
└──────────────────────────────────────────────────────────┘
```

> **Runs fully offline.** With no GCP project the IDE serves a built-in demo
> dataset (`analytics.users/orders/comments/departments`) and compiles DSL → SQL
> with no credentials. BigQuery is only needed to *execute* queries.

---

## Repo layout

```
monadic-query-editor/
├── monasql/              # the transpiler engine (Python)
│   ├── expr.py           #   expression AST + operator overloading
│   ├── schema.py         #   Table / TVF / TableRef
│   ├── query.py          #   monadic builder · sub-query composition (feature 1)
│   ├── helpers.py        #   predicates · agg_lateral (fanout-safe)
│   ├── render.py         #   QueryState → SQL
│   └── rewrite.py        #   BigQuery LATERAL → CTE rewrite
├── backend/              # FastAPI server
│   ├── parser/           #   DSL lexer · parser · compiler  ← the Web-IDE language
│   ├── services/         #   schema_service (feature 3) · executor
│   ├── routers/          #   compile · execute · schema
│   └── main.py           #   app + WebSocket realtime compile
├── frontend/             # React + TypeScript + Monaco + Tailwind (feature 2)
├── tests/                # pytest — 51 tests (engine + DSL + API)
└── monasql_spec.md       # the specification
```

---

## Quick start

### 1. Backend  (Python 3.11+)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# run from the repo root so `import monasql` resolves:
cd .. && uvicorn backend.main:app --reload --port 8000
```

The API is now on `http://localhost:8000` (try `GET /api/health`,
`GET /api/schema?demo=true`).

### 2. Frontend  (Node 20+)

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173  (proxies /api → :8000)
```

Open the editor, start typing a `QUERY` block, and watch the SQL appear live.
Drag tables/columns in from the schema panel; press **⌘/Ctrl+Enter** to run.

### Or just:

```bash
./run.sh             # starts backend + frontend together
```

---

## The Web-IDE DSL

The browser language is **function-style** — it reads like the Python monasql
API with the `yield` / `lambda` boilerplate removed. Statements are assignments
(`u = from(users)`) and calls (`where(...)`, `select(...)`), with method chaining
(`.alias()`, `.desc()`) and ordinary operators (`==`, `and`, `or`, `not`, `+`…).

```python
QUERY user_activity:
  u = from(users)
  o = join(orders, on = o.user_id == u.id)        # the new alias may be used in `on`
  where(is_active(u) and in_date_range(o.created_at, start='2024-01-01'))
  group_by(u.id, u.name)
  select(
    u.name,
    count(o.id).alias('order_count'),
    sum(o.total).alias('revenue'),
  )
  order_by(revenue.desc())
  limit(100)
```

**Sub-query composition** — reference a `QUERY` by name:

```python
QUERY report:
  ua = from(user_activity)        # ← inlined as FROM (SELECT …) ua
  where(ua.revenue > 1000)
  select(ua.name, ua.revenue)
```

**Fanout-safe aggregation** — `agg_lateral` compiles to pre-aggregated CTEs,
so joining two child tables never double-counts:

```python
QUERY dashboard:
  u = from(users)
  os = agg_lateral(orders,
         join_on = os.user_id == u.id,
         aggs = [count(os.id).alias('order_count'), sum(os.total).alias('revenue')],
         outer = true)
  cs = agg_lateral(comments,
         join_on = cs.user_id == u.id,
         aggs = [count(cs.id).alias('comment_count')])
  select(u.name,
         coalesce(os.order_count, 0).alias('orders'),
         coalesce(cs.comment_count, 0).alias('comments'))
```

→

```sql
WITH _agg_os AS (
  SELECT t1.user_id, COUNT(t1.id) AS order_count, SUM(t1.total) AS revenue
  FROM orders t1 GROUP BY t1.user_id
),
     _agg_cs AS (
  SELECT t2.user_id, COUNT(t2.id) AS comment_count
  FROM comments t2 GROUP BY t2.user_id
)
SELECT u.name, COALESCE(os.order_count, 0) AS orders, COALESCE(cs.comment_count, 0) AS comments
FROM users u
LEFT JOIN _agg_os os ON u.id = os.user_id
LEFT JOIN _agg_cs cs ON u.id = cs.user_id
```

**Reusable predicates:**

```python
PREDICATE is_premium(u):
  is_active(u) and u.email.is_not_null()
```

Operations: `from` `join` `agg_lateral` `apply` `lateral` `where` `select`
`group_by` `having` `order_by` `limit` `offset` `distinct` `union` `intersect`
`except`. Expressions: aggregates (`count`/`sum`/`avg`/…), window funcs with
`.over(partition_by=[…], order_by=[…])`, `case(when(…), else_=…)`, `.cast()`,
`.in_()`/`.between()`/`.like()`/`.is_null()`, predicates (`is_active`,
`in_date_range`, …), and any unknown name falls through to a raw SQL function.
See [`backend/parser/`](./backend/parser) for the lexer/parser/compiler.

> The spec sketched a SQL-keyword DSL; per the request to "write things like
> `where()`", the implemented surface language is this function-call style
> instead (it maps 1:1 onto the Python API, minus `yield`/`lambda`).

---

## The Python API (feature 1: sub-query composition)

```python
from monasql import *

users       = Table("users",       ["id", "name", "email", "dept_id", "deleted_at"])
departments = Table("departments", ["id", "name"])

@query
def active_users():
    u = yield from_(users)
    yield where_(is_active(u))
    yield select(u.id, u.name, u.email, u.dept_id)

@query
def dept_summary():
    au = yield from_(active_users)               # ← a @query used as a sub-query
    d  = yield join_(departments, on=lambda d: au.dept_id == d.id)
    yield group_by(d.name)
    yield select(d.name, count(au.id).alias("user_count"))

print(dept_summary.sql())                        # dialect="bigquery" by default
```

A `@query` may be passed to **both** `from_()` and `join_()`; each use is rebuilt
in the parent's alias space so inner/outer aliases never collide. Nested
sub-queries that use `agg_lateral` are rewritten recursively, so a composed
sub-query keeps its fanout-safety.

---

## Testing

```bash
pip install pytest
pytest -q          # 54 passed
```

* `tests/test_monasql.py` — engine + sub-query composition (feature 1)
* `tests/test_dsl.py`     — lexer/parser/compiler (the Web-IDE language)
* `tests/test_api.py`     — FastAPI endpoints + WebSocket (offline/demo)

---

## Connecting to BigQuery

Compilation works fully offline; **executing** queries and pulling **real
schemas** needs three things:

**1. Install the SDK into the *same* Python env that runs the backend**
(it's in `requirements.txt`, but if you started uvicorn with a different
interpreter, install it there too) — then **restart uvicorn**:

```bash
pip install google-cloud-bigquery
```

**2. Authenticate** (one of):

```bash
gcloud auth application-default login                 # ADC (recommended)
# or:  export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

**3. Enter your GCP project (and dataset) in the IDE header.** The schema badge
flips from `demo` to `bigquery`, the tree shows that dataset's tables, **Run**
executes via `jobs.query`, and **Dry run** reports the bytes scanned. The
**dataset** is also the default for unqualified tables — `from(users)` resolves
to `project.dataset.users`. Reference other datasets/projects explicitly:

| form | example |
|------|---------|
| bare (uses default dataset) | `from(users)` |
| dataset-qualified | `from(other_dataset.table)` |
| project-qualified | `from(project.dataset.table)` |
| **back-ticked** (BigQuery style — best for hyphenated project ids) | `` from(`my-proj.dataset.table`) `` |
| string | `from('my-proj.dataset.table')` |

If the dataset name is wrong, the schema panel lists the available datasets so
you can pick the right one.

**Verify the connection** any time:

```bash
curl "http://localhost:8000/api/diagnostics?project_id=YOUR_PROJECT"
# → { bigquery_installed, authenticated, datasets: [...], hint }
```

If a project is set but BigQuery can't be reached, `/api/schema` keeps showing
demo data **and** returns an `error` explaining why (so it never silently looks
"connected").

**No datasets yet?** Seed the sample `analytics` tables (users/orders/comments/
departments) into your own project so the default `dashboard` query runs on real
data — tiny + free, re-runnable, removable with `bq rm -r -f PROJECT:analytics`:

```bash
python backend/scripts/seed_demo_dataset.py YOUR_PROJECT analytics US
```
