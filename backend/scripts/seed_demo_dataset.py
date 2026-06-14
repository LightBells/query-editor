"""
Seed a small `analytics` dataset (users / orders / comments / departments) into
a real BigQuery project, so the IDE's default `dashboard` query runs on live
data.  Tiny + free; uses batch loads (WRITE_TRUNCATE) so it's safe to re-run.

Usage::

    python backend/scripts/seed_demo_dataset.py <project_id> [dataset] [location]
    python backend/scripts/seed_demo_dataset.py lightbells-watch-party analytics US

Remove it later with:  bq rm -r -f <project_id>:analytics
"""
from __future__ import annotations

import sys

from google.cloud import bigquery

F = bigquery.SchemaField


def _schema(*cols: tuple[str, str]) -> list[bigquery.SchemaField]:
    return [F(name, type_, mode="NULLABLE") for name, type_ in cols]


TABLES = {
    "departments": (
        _schema(("id", "INT64"), ("name", "STRING")),
        [
            {"id": 1, "name": "Engineering"},
            {"id": 2, "name": "Sales"},
            {"id": 3, "name": "Support"},
        ],
    ),
    "users": (
        _schema(("id", "INT64"), ("name", "STRING"), ("email", "STRING"),
                ("dept_id", "INT64"), ("created_at", "TIMESTAMP"), ("deleted_at", "TIMESTAMP")),
        [
            {"id": 1, "name": "Alice", "email": "alice@example.com", "dept_id": 1,
             "created_at": "2024-01-05T10:00:00Z", "deleted_at": None},
            {"id": 2, "name": "Bob", "email": "bob@example.com", "dept_id": 2,
             "created_at": "2024-02-11T09:30:00Z", "deleted_at": None},
            {"id": 3, "name": "Carol", "email": "carol@example.com", "dept_id": 1,
             "created_at": "2024-03-02T14:15:00Z", "deleted_at": None},
            {"id": 4, "name": "Dave (deleted)", "email": None, "dept_id": 3,
             "created_at": "2024-01-20T08:00:00Z", "deleted_at": "2024-06-01T00:00:00Z"},
        ],
    ),
    "orders": (
        _schema(("id", "INT64"), ("user_id", "INT64"), ("total", "FLOAT64"),
                ("status", "STRING"), ("created_at", "TIMESTAMP")),
        [
            {"id": 1, "user_id": 1, "total": 120.5, "status": "shipped", "created_at": "2024-04-01T12:00:00Z"},
            {"id": 2, "user_id": 1, "total": 80.0, "status": "placed", "created_at": "2024-04-15T12:00:00Z"},
            {"id": 3, "user_id": 2, "total": 250.0, "status": "shipped", "created_at": "2024-05-01T12:00:00Z"},
            {"id": 4, "user_id": 3, "total": 42.0, "status": "refunded", "created_at": "2024-05-20T12:00:00Z"},
            {"id": 5, "user_id": 1, "total": 15.0, "status": "shipped", "created_at": "2024-06-02T12:00:00Z"},
        ],
    ),
    "comments": (
        _schema(("id", "INT64"), ("user_id", "INT64"), ("body", "STRING"),
                ("created_at", "TIMESTAMP")),
        [
            {"id": 1, "user_id": 1, "body": "Great!", "created_at": "2024-04-02T12:00:00Z"},
            {"id": 2, "user_id": 2, "body": "Thanks", "created_at": "2024-05-02T12:00:00Z"},
            {"id": 3, "user_id": 1, "body": "Nice", "created_at": "2024-06-03T12:00:00Z"},
        ],
    ),
}


def main(project: str, dataset: str = "analytics", location: str = "US") -> None:
    client = bigquery.Client(project=project)

    ds_ref = bigquery.Dataset(f"{project}.{dataset}")
    ds_ref.location = location
    client.create_dataset(ds_ref, exists_ok=True)
    print(f"dataset ready: {project}.{dataset} ({location})")

    for name, (schema, rows) in TABLES.items():
        table_id = f"{project}.{dataset}.{name}"
        cfg = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
        client.load_table_from_json(rows, table_id, job_config=cfg).result()
        print(f"  loaded {len(rows):>2} rows → {name}")

    print("\nDone ✅  In the IDE header set project =", project, "and dataset =", dataset)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    main(*sys.argv[1:])
