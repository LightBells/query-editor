// In-memory cache of table metadata + columns, shared by the schema panel,
// the compiler (table→columns map) and the editor autocomplete.

import type { ColumnDef, DatasetInfo, TableInfo } from "../types";

export interface CachedTable {
  dataset: string;
  name: string;
  rowCount?: number | null;
  columns?: ColumnDef[];
}

export class SchemaCache {
  private tables = new Map<string, CachedTable>();
  datasets: DatasetInfo[] = [];

  clear() {
    this.tables.clear();
    this.datasets = [];
  }

  setDatasets(datasets: DatasetInfo[]) {
    this.datasets = datasets;
    for (const ds of datasets) {
      for (const t of ds.tables) {
        const existing = this.tables.get(t.id);
        this.tables.set(t.id, {
          dataset: ds.id,
          name: t.id,
          rowCount: t.row_count,
          columns: existing?.columns,
        });
      }
    }
  }

  setColumns(table: string, columns: ColumnDef[]) {
    const t = this.tables.get(table);
    if (t) t.columns = columns;
    else this.tables.set(table, { dataset: "", name: table, columns });
  }

  getColumns(table: string): ColumnDef[] {
    return this.tables.get(table)?.columns ?? [];
  }

  // columns fetched yet?  (undefined = not loaded; [] = loaded-but-empty)
  hasColumns(table: string): boolean {
    return this.tables.get(table)?.columns !== undefined;
  }

  getAllTables(): CachedTable[] {
    return [...this.tables.values()];
  }

  getTableInfo(table: string): CachedTable | undefined {
    return this.tables.get(table);
  }

  // table → column names, for the /api/compile request body.
  // All known table *names* are included (so the compiler doesn't warn about
  // "unknown table"); columns are filled in lazily as they're fetched.
  columnMap(): Record<string, string[]> {
    const map: Record<string, string[]> = {};
    for (const t of this.tables.values()) {
      map[t.name] = t.columns?.map((c) => c.name) ?? [];
    }
    return map;
  }
}

export function tableInfoLabel(t: TableInfo): string {
  if (t.row_count != null) return `${t.row_count.toLocaleString()} rows`;
  return t.type ?? "TABLE";
}
