"""GET /api/schema — datasets / tables / columns from BigQuery (or demo)."""
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


def _datasets(svc) -> list[DatasetInfo]:
    out = []
    for ds in svc.get_datasets():
        tables = ds.get("tables") or svc.get_tables(ds["id"])
        out.append(DatasetInfo(id=ds["id"], tables=[TableInfo(**t) for t in tables]))
    return out


@router.get("/schema", response_model=SchemaResponse)
def get_schema(project_id: Optional[str] = Query(default=None),
               dataset: Optional[str] = Query(default=None),
               demo: bool = Query(default=False)) -> SchemaResponse:
    # Demo path (no project, or explicitly requested).
    if demo or not project_id:
        svc = DemoSchemaService()
        return SchemaResponse(datasets=_datasets(svc), source="demo")

    # Real BigQuery.
    try:
        svc = BigQuerySchemaService(project_id)
    except Exception as e:  # SDK missing / no creds → demo, but say why
        return SchemaResponse(
            datasets=_datasets(DemoSchemaService()), source="demo",
            error=f"Could not connect to BigQuery for '{project_id}', showing demo data: {e}",
        )

    try:
        if dataset:
            try:
                tables = svc.get_tables(dataset)
            except Exception as e:
                # dataset missing/inaccessible → still connected; list the
                # available dataset names so the user can pick the right one.
                available = [d["id"] for d in svc.get_datasets()]
                return SchemaResponse(
                    datasets=[DatasetInfo(id=d, tables=[]) for d in available],
                    source="bigquery",
                    error=(f"Dataset '{dataset}' not found in '{project_id}'. "
                           f"Available: {', '.join(available) or '(none)'} — {e}"),
                )
            return SchemaResponse(
                datasets=[DatasetInfo(id=dataset, tables=[TableInfo(**t) for t in tables])],
                source="bigquery",
            )
        return SchemaResponse(datasets=_datasets(svc), source="bigquery")
    except Exception as e:  # API disabled, permissions, etc.
        return SchemaResponse(
            datasets=_datasets(DemoSchemaService()), source="demo",
            error=f"Could not reach BigQuery for '{project_id}', showing demo data: {e}",
        )


@router.get("/schema/{dataset}/{table}", response_model=TableSchemaResponse)
def get_table_schema(dataset: str, table: str,
                     project_id: Optional[str] = Query(default=None),
                     demo: bool = Query(default=False)) -> TableSchemaResponse:
    svc = get_schema_service(project_id, force_demo=demo)
    cols = svc.get_columns(dataset, table)
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
