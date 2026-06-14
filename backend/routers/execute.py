"""POST /api/execute — run SQL on BigQuery (or dry-run cost estimate)."""
from __future__ import annotations

from fastapi import APIRouter

from ..models.schemas import ExecuteRequest, ExecuteResponse
from ..services.executor import execute, ExecutionError

router = APIRouter()


@router.post("/execute", response_model=ExecuteResponse)
def execute_endpoint(req: ExecuteRequest) -> ExecuteResponse:
    try:
        data = execute(
            req.sql,
            req.project_id,
            dataset=req.dataset,
            dry_run=req.dry_run,
            max_rows=req.max_rows,
        )
        return ExecuteResponse(**data)
    except ExecutionError as e:
        return ExecuteResponse(error=str(e))
