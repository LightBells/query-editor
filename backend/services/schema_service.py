"""
Schema providers.

Two implementations behind one interface:

* :class:`BigQuerySchemaService` — pulls real metadata from
  ``INFORMATION_SCHEMA`` (datasets, tables, columns, partitioning).
* :class:`DemoSchemaService` — a built-in sample dataset so the whole IDE
  runs end-to-end **with no GCP project or credentials**.

``get_schema_service()`` picks the real one when ``google-cloud-bigquery`` and
a project are available, otherwise falls back to the demo.
"""
from __future__ import annotations

import time
from typing import Any, Optional, Protocol


# ── demo dataset ──────────────────────────────────────────────

_DEMO: dict[str, dict[str, list[tuple[str, str, bool, str]]]] = {
    # dataset → table → [(column, type, nullable, description)]
    "analytics": {
        "users": [
            ("id", "INT64", False, "Primary key"),
            ("name", "STRING", False, "Display name"),
            ("email", "STRING", True, "Contact email"),
            ("dept_id", "INT64", True, "FK → departments.id"),
            ("created_at", "TIMESTAMP", False, "Sign-up time"),
            ("deleted_at", "TIMESTAMP", True, "Soft-delete marker"),
        ],
        "orders": [
            ("id", "INT64", False, "Primary key"),
            ("user_id", "INT64", False, "FK → users.id"),
            ("total", "FLOAT64", False, "Order total"),
            ("status", "STRING", False, "placed | shipped | refunded"),
            ("created_at", "TIMESTAMP", False, "Order time"),
        ],
        "comments": [
            ("id", "INT64", False, "Primary key"),
            ("user_id", "INT64", False, "FK → users.id"),
            ("body", "STRING", True, "Comment text"),
            ("created_at", "TIMESTAMP", False, "Posted time"),
        ],
        "departments": [
            ("id", "INT64", False, "Primary key"),
            ("name", "STRING", False, "Department name"),
        ],
    },
}


class SchemaProvider(Protocol):
    source: str

    def get_datasets(self) -> list[dict]: ...
    def get_tables(self, dataset: str) -> list[dict]: ...
    def get_columns(self, dataset: str, table: str) -> list[dict]: ...
    def table_column_map(self, dataset: Optional[str] = None) -> dict[str, list[str]]: ...


# ── demo implementation ───────────────────────────────────────

class DemoSchemaService:
    source = "demo"

    def get_datasets(self) -> list[dict]:
        out = []
        for ds, tables in _DEMO.items():
            out.append({
                "id": ds,
                "tables": [
                    {"id": t, "type": "TABLE",
                     "row_count": 1000 * (i + 1), "size_bytes": 52428800}
                    for i, t in enumerate(tables)
                ],
            })
        return out

    def get_tables(self, dataset: str) -> list[dict]:
        tables = _DEMO.get(dataset, {})
        return [{"id": t, "type": "TABLE", "row_count": None, "size_bytes": None}
                for t in tables]

    def get_columns(self, dataset: str, table: str) -> list[dict]:
        cols = _DEMO.get(dataset, {}).get(table, [])
        return [
            {"name": n, "type": ty, "nullable": nul, "description": desc}
            for (n, ty, nul, desc) in cols
        ]

    def table_column_map(self, dataset: Optional[str] = None) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        datasets = [dataset] if dataset else list(_DEMO.keys())
        for ds in datasets:
            for t, cols in _DEMO.get(ds, {}).items():
                out[t] = [c[0] for c in cols]
        return out


# ── BigQuery implementation ───────────────────────────────────

class BigQuerySchemaService:
    source = "bigquery"

    def __init__(self, project_id: str, cache_ttl: int = 300):
        from google.cloud import bigquery  # lazy import
        self.project_id = project_id
        self.client = bigquery.Client(project=project_id)
        self._cache: dict[str, tuple[float, Any]] = {}
        self._ttl = cache_ttl

    # — cached query helper —
    def _cached(self, key: str, sql: str) -> list[dict]:
        now = time.time()
        hit = self._cache.get(key)
        if hit and now - hit[0] < self._ttl:
            return hit[1]
        rows = [dict(r) for r in self.client.query(sql).result()]
        self._cache[key] = (now, rows)
        return rows

    def get_datasets(self) -> list[dict]:
        sql = (
            f"SELECT schema_name FROM `{self.project_id}`."
            "INFORMATION_SCHEMA.SCHEMATA ORDER BY schema_name"
        )
        rows = self._cached("datasets", sql)
        return [{"id": r["schema_name"], "tables": []} for r in rows]

    def get_tables(self, dataset: str) -> list[dict]:
        # INFORMATION_SCHEMA.TABLES has table_name/table_type but NOT
        # row_count/size_bytes — those live in the __TABLES__ metatable.
        sql = f"""
            SELECT table_name, table_type
            FROM `{self.project_id}.{dataset}`.INFORMATION_SCHEMA.TABLES
            ORDER BY table_name
        """
        rows = self._cached(f"tables:{dataset}", sql)
        tables = [
            {"id": r["table_name"], "type": r.get("table_type") or "TABLE",
             "row_count": None, "size_bytes": None}
            for r in rows
        ]
        # best-effort: enrich with row counts / size from __TABLES__
        try:
            meta_sql = (f"SELECT table_id, row_count, size_bytes "
                        f"FROM `{self.project_id}.{dataset}.__TABLES__`")
            meta = {m["table_id"]: m for m in self._cached(f"meta:{dataset}", meta_sql)}
            for t in tables:
                m = meta.get(t["id"])
                if m:
                    t["row_count"] = m.get("row_count")
                    t["size_bytes"] = m.get("size_bytes")
        except Exception:
            pass
        return tables

    def get_columns(self, dataset: str, table: str) -> list[dict]:
        # is_nullable / ordinal_position are on INFORMATION_SCHEMA.COLUMNS
        # (COLUMN_FIELD_PATHS has neither); descriptions come from the latter.
        sql = f"""
            SELECT column_name, data_type, is_nullable
            FROM `{self.project_id}.{dataset}`.INFORMATION_SCHEMA.COLUMNS
            WHERE table_name = '{table}'
            ORDER BY ordinal_position
        """
        rows = self._cached(f"cols:{dataset}.{table}", sql)
        cols = [
            {"name": r["column_name"], "type": r["data_type"],
             "nullable": str(r.get("is_nullable", "YES")).upper() != "NO",
             "description": ""}
            for r in rows
        ]
        # best-effort: top-level column descriptions
        try:
            d_sql = f"""
                SELECT column_name, description
                FROM `{self.project_id}.{dataset}`.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS
                WHERE table_name = '{table}' AND field_path = column_name
            """
            desc = {r["column_name"]: r.get("description")
                    for r in self._cached(f"desc:{dataset}.{table}", d_sql)}
            for c in cols:
                if desc.get(c["name"]):
                    c["description"] = desc[c["name"]]
        except Exception:
            pass
        return cols

    def table_column_map(self, dataset: Optional[str] = None) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        datasets = [dataset] if dataset else [d["id"] for d in self.get_datasets()]
        for ds in datasets:
            for t in self.get_tables(ds):
                out[t["id"]] = [c["name"] for c in self.get_columns(ds, t["id"])]
        return out


# ── factory ───────────────────────────────────────────────────

_DEMO_SINGLETON = DemoSchemaService()
_BQ_CACHE: dict[str, BigQuerySchemaService] = {}


def get_schema_service(project_id: Optional[str] = None,
                       *, force_demo: bool = False) -> SchemaProvider:
    if force_demo or not project_id:
        return _DEMO_SINGLETON
    try:
        if project_id not in _BQ_CACHE:
            _BQ_CACHE[project_id] = BigQuerySchemaService(project_id)
        return _BQ_CACHE[project_id]
    except Exception:
        # google-cloud-bigquery missing, or no credentials → graceful demo
        return _DEMO_SINGLETON
