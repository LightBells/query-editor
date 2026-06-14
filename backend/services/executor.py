"""
BigQuery query executor.

Runs SQL (or a dry-run cost estimate) and normalises the result into the
shape the frontend table view expects.  Importing/initialising BigQuery is
lazy so the server starts fine without the SDK or credentials — execution
just returns a clear error in that case.
"""
from __future__ import annotations

import datetime
import decimal
from typing import Any, Optional


class ExecutionError(Exception):
    pass


def _jsonable(v: Any) -> Any:
    """Make a BigQuery cell value JSON-serialisable."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime, datetime.time)):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    return str(v)


def execute(sql: str, project_id: Optional[str], *, dataset: Optional[str] = None,
            dry_run: bool = False, max_rows: int = 1000) -> dict:
    """Execute *sql* against BigQuery; returns a dict matching ExecuteResponse."""
    if not project_id:
        raise ExecutionError(
            "No project_id supplied — set one in the header to run queries "
            "against BigQuery (compilation works without it)."
        )
    try:
        from google.cloud import bigquery
    except ImportError as e:  # pragma: no cover
        raise ExecutionError(
            "google-cloud-bigquery is not installed; run "
            "`pip install google-cloud-bigquery` to execute queries."
        ) from e

    try:
        client = bigquery.Client(project=project_id)
        job_config = bigquery.QueryJobConfig(dry_run=dry_run, use_query_cache=True)
        # unqualified tables (`FROM users`) resolve against the chosen dataset
        if dataset:
            job_config.default_dataset = bigquery.DatasetReference(project_id, dataset)
        job = client.query(sql, job_config=job_config)

        if dry_run:
            return {
                "columns": [], "rows": [], "total_rows": 0,
                "bytes_processed": int(job.total_bytes_processed or 0),
                "cache_hit": False, "dry_run": True, "error": None,
            }

        result = job.result(max_results=max_rows)
        columns = [{"name": f.name, "type": f.field_type} for f in result.schema]
        rows: list[list[Any]] = []
        for i, row in enumerate(result):
            if i >= max_rows:
                break
            rows.append([_jsonable(v) for v in row.values()])

        return {
            "columns": columns,
            "rows": rows,
            "total_rows": int(result.total_rows or len(rows)),
            "bytes_processed": int(job.total_bytes_processed or 0),
            "cache_hit": bool(job.cache_hit),
            "dry_run": False,
            "error": None,
        }
    except Exception as e:  # surface BigQuery errors to the editor
        raise ExecutionError(str(e)) from e
