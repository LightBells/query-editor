"""POST /api/compile — DSL → SQL."""
from __future__ import annotations

from fastapi import APIRouter

from ..models.schemas import CompileRequest, CompileResponse
from ..parser import compile_dsl
from ..services.schema_service import get_schema_service

router = APIRouter()


def run_compile(req: CompileRequest) -> CompileResponse:
    """Shared by the REST endpoint and the WebSocket realtime compiler."""
    tables = dict(req.tables)
    # If the editor didn't send a cached schema, fetch one (demo or BigQuery).
    if not tables and (req.project_id or req.dataset):
        svc = get_schema_service(req.project_id)
        try:
            tables = svc.table_column_map(req.dataset)
        except Exception:
            tables = {}

    result = compile_dsl(
        req.dsl,
        schema_tables=tables,
        dialect=req.dialect,
        target=req.target,
    )
    return CompileResponse(
        sql=result.sql,
        errors=result.errors,          # list[dict] → coerced to CompileError
        warnings=result.warnings,
        queries=result.queries,
        main=result.main,
    )


@router.post("/compile", response_model=CompileResponse)
def compile_endpoint(req: CompileRequest) -> CompileResponse:
    return run_compile(req)
