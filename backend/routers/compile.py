"""POST /api/compile — DSL → SQL."""
from __future__ import annotations

from fastapi import APIRouter

from ..models.schemas import CompileRequest, CompileResponse
from ..parser import compile_dsl

router = APIRouter()


def run_compile(req: CompileRequest) -> CompileResponse:
    """Shared by the REST endpoint and the WebSocket realtime compiler.

    This is **pure CPU** — it never calls BigQuery.  The editor supplies the
    schema via ``req.tables`` (table → column names); unknown tables just produce
    a warning.  (Enumerating a whole dataset's columns here previously blocked
    the WebSocket event loop on large projects.)
    """
    result = compile_dsl(
        req.dsl,
        schema_tables=dict(req.tables),
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
