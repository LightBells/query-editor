// Shared API/domain types (mirror backend/models/schemas.py).

export interface ColumnDef {
  name: string;
  type: string;
  nullable?: boolean;
  description?: string;
}

export interface TableInfo {
  id: string;
  type?: string;
  row_count?: number | null;
  size_bytes?: number | null;
}

export interface DatasetInfo {
  id: string;
  tables: TableInfo[];
}

export interface SchemaResponse {
  datasets: DatasetInfo[];
  source: string; // "bigquery" | "demo"
  error?: string | null;
}

export interface TableSchemaResponse {
  dataset: string;
  table: string;
  columns: ColumnDef[];
}

export interface CompileError {
  line: number;
  col: number;
  message: string;
  severity: string;
}

export interface CompileResponse {
  sql: string;
  errors: CompileError[];
  warnings: string[];
  queries: Record<string, string>;
  main?: string | null;
}

export interface ExecColumn {
  name: string;
  type: string;
}

export interface ExecuteResponse {
  columns: ExecColumn[];
  rows: unknown[][];
  total_rows: number;
  bytes_processed: number;
  cache_hit: boolean;
  dry_run: boolean;
  error?: string | null;
}
