import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { SchemaCache } from "../lib/schemaCache";

// Loads the dataset's *table list* only (names). Columns are fetched lazily via
// `ensureColumns(table)` — when a table is referenced in the editor or expanded
// in the schema tree — so we never query every table up front.
export function useSchema(projectId: string | null, dataset: string | null) {
  const cacheRef = useRef(new SchemaCache());
  const requested = useRef(new Set<string>());
  const [version, setVersion] = useState(0);
  const [source, setSource] = useState<string>("demo");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    cacheRef.current.clear();
    requested.current.clear();
    try {
      const schema = await api.getSchema(projectId, !projectId, dataset);
      cacheRef.current.setDatasets(schema.datasets);
      setSource(schema.source);
      setError(schema.error ?? null);
      setVersion((v) => v + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [projectId, dataset]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // fetch one table's columns on demand (idempotent)
  const ensureColumns = useCallback(
    (table: string) => {
      const info = cacheRef.current.getTableInfo(table);
      if (!info || requested.current.has(table)) return;
      requested.current.add(table);
      api
        .getTableColumns(info.dataset, table, projectId, !projectId)
        .then((r) => {
          cacheRef.current.setColumns(table, r.columns);
          setVersion((v) => v + 1);
        })
        .catch(() => requested.current.delete(table));
    },
    [projectId],
  );

  return { cache: cacheRef.current, version, source, loading, error, reload, ensureColumns };
}
