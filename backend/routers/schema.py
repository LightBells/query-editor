"""GET /api/schema — datasets / tables / columns from BigQuery (or demo).

Everything is lazy so huge projects don't hang:
  * GET /api/schema      → dataset *names* (+ tables for the selected dataset)
  * GET /api/tables      → tables for one dataset (on tree-expand)
  * GET /api/schema/{ds}/{t} → columns for one table (on tree-expand / reference)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from ..models.schemas import (
    SchemaResponse, DatasetInfo, TableInfo, TableSchemaResponse, ColumnDef,
    DiagnosticsResponse,
)
from ..services.schema_service import (
    BigQuerySchemaService, DemoSchemaService, get_schema_service,
)

router = APIRouter()


def _demo_datasets() -> list[DatasetInfo]:
    svc = DemoSchemaService()
    out = []
    for ds in svc.get_datasets():
        out.append(DatasetInfo(id=ds["id"], tables=[TableInfo(**t) for t in ds["tables"]]))
    return out


@router.get("/schema", response_model=SchemaResponse)
def get_schema(project_id: Optional[str] = Query(default=None),
               dataset: Optional[str] = Query(default=None),
               demo: bool = Query(default=False)) -> SchemaResponse:
    if demo or not project_id:
        return SchemaResponse(datasets=_demo_datasets(), source="demo")

    # one cheap SCHEMATA query for dataset *names*; tables are loaded lazily.
    try:
        svc = BigQuerySchemaService(project_id)
        ds_names = [d["id"] for d in svc.get_datasets()]
    except Exception as e:
        return SchemaResponse(
            datasets=_demo_datasets(), source="demo",
            error=f"Could not connect to BigQuery for '{project_id}', showing demo data: {e}",
        )

    error: Optional[str] = None
    out: list[DatasetInfo] = []
    for name in ds_names:
        tables: list[TableInfo] = []
        if name == dataset:                      # eagerly load only the selected one
            try:
                tables = [TableInfo(**t) for t in svc.get_tables(name)]
            except Exception as e:
                error = f"Could not list tables in '{dataset}': {e}"
        out.append(DatasetInfo(id=name, tables=tables))

    if dataset and dataset not in ds_names:
        error = (f"Dataset '{dataset}' not found in '{project_id}'. "
                 f"Available: {', '.join(ds_names) or '(none)'}")
    return SchemaResponse(datasets=out, source="bigquery", error=error)


@router.get("/tables", response_model=list[TableInfo])
def get_tables(dataset: str = Query(...),
               project_id: Optional[str] = Query(default=None),
               demo: bool = Query(default=False)) -> list[TableInfo]:
    """Tables for a single dataset — called when a dataset node is expanded."""
    svc = get_schema_service(project_id, force_demo=demo)
    try:
        return [TableInfo(**t) for t in svc.get_tables(dataset)]
    except Exception:
        return []


@router.get("/schema/{dataset}/{table}", response_model=TableSchemaResponse)
def get_table_schema(dataset: str, table: str,
                     project_id: Optional[str] = Query(default=None),
                     demo: bool = Query(default=False)) -> TableSchemaResponse:
    svc = get_schema_service(project_id, force_demo=demo)
    try:
        cols = svc.get_columns(dataset, table)
    except Exception:
        cols = []
    return TableSchemaResponse(
        dataset=dataset, table=table,
        columns=[ColumnDef(**c) for c in cols],
    )


@router.get("/diagnostics", response_model=DiagnosticsResponse)
def diagnostics(project_id: Optional[str] = Query(default=None)) -> DiagnosticsResponse:
    """One-stop 'is BigQuery wired up?' check for the given project."""
    r = DiagnosticsResponse(project=project_id)
    try:
        from google.cloud import bigquery
        r.bigquery_installed = True
    except ImportError:
        r.error = "google-cloud-bigquery is not installed"
        r.hint = "pip install google-cloud-bigquery  (in the backend's Python env), then restart uvicorn"
        return r

    if not project_id:
        r.hint = "Enter a GCP project in the IDE header to query BigQuery."
        return r

    try:
        client = bigquery.Client(project=project_id)
        ds = [d.dataset_id for d in client.list_datasets()]
        r.authenticated = True
        r.datasets = ds
        if not ds:
            r.hint = (f"Connected ✅ but project '{project_id}' has no BigQuery datasets. "
                      f"Create one, or query a public dataset.")
    except Exception as e:
        r.error = str(e)
        r.hint = ("Authenticate with `gcloud auth application-default login`, "
                  "enable the BigQuery API, and check the project id.")
    return r
