"""Pydantic request/response models for the API."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ── /api/compile ──────────────────────────────────────────────

class CompileRequest(BaseModel):
    dsl: str
    dialect: str = "bigquery"
    target: Optional[str] = None
    # table-name → column-names, usually supplied from the editor's schema cache
    tables: dict[str, list[str]] = Field(default_factory=dict)
    # optional: fetch the schema server-side instead of sending ``tables``
    project_id: Optional[str] = None
    dataset: Optional[str] = None


class CompileError(BaseModel):
    line: int = 0
    col: int = 0
    message: str
    severity: str = "error"


class CompileResponse(BaseModel):
    sql: str
    errors: list[CompileError] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    queries: dict[str, str] = Field(default_factory=dict)
    main: Optional[str] = None


# ── /api/execute ──────────────────────────────────────────────

class ExecuteRequest(BaseModel):
    sql: str
    project_id: Optional[str] = None
    dataset: Optional[str] = None      # default dataset → bare `FROM users` resolves here
    dry_run: bool = False
    max_rows: int = 1000


class ColumnInfo(BaseModel):
    name: str
    type: str


class ExecuteResponse(BaseModel):
    columns: list[ColumnInfo] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    total_rows: int = 0
    bytes_processed: int = 0
    cache_hit: bool = False
    dry_run: bool = False
    error: Optional[str] = None


# ── /api/schema ───────────────────────────────────────────────

class TableInfo(BaseModel):
    id: str
    type: str = "TABLE"
    row_count: Optional[int] = None
    size_bytes: Optional[int] = None


class DatasetInfo(BaseModel):
    id: str
    tables: list[TableInfo] = Field(default_factory=list)


class SchemaResponse(BaseModel):
    datasets: list[DatasetInfo] = Field(default_factory=list)
    source: str = "bigquery"        # "bigquery" | "demo"
    # set when a project was requested but we had to fall back to demo data
    error: Optional[str] = None


class DiagnosticsResponse(BaseModel):
    bigquery_installed: bool = False
    authenticated: bool = False
    project: Optional[str] = None
    datasets: Optional[list[str]] = None
    error: Optional[str] = None
    hint: Optional[str] = None


class ColumnDef(BaseModel):
    name: str
    type: str
    nullable: bool = True
    description: str = ""


class TableSchemaResponse(BaseModel):
    dataset: str
    table: str
    columns: list[ColumnDef] = Field(default_factory=list)
    partitioning: Optional[dict] = None
    clustering: list[str] = Field(default_factory=list)
