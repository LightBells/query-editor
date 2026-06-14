import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { SchemaCache } from "../lib/schemaCache";

// Loads the schema (demo unless a GCP project is set), scoped to the chosen
// dataset, then eagerly fetches columns for those tables so autocomplete +
// compile have full type info.
export function useSchema(projectId: string | null, dataset: string | null) {
  const cacheRef = useRef(new SchemaCache());
  const [version, setVersion] = useState(0);
  const [source, setSource] = useState<string>("demo");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const demo = !projectId;
      const schema = await api.getSchema(projectId, demo, dataset);
      cacheRef.current.setDatasets(schema.datasets);
      setSource(schema.source);
      setError(schema.error ?? null);
      setVersion((v) => v + 1);

      // prefetch columns for the loaded tables (parallel)
      const jobs: Promise<void>[] = [];
      for (const ds of schema.datasets) {
        for (const t of ds.tables) {
          jobs.push(
            api
              .getTableColumns(ds.id, t.id, projectId, demo)
              .then((r) => cacheRef.current.setColumns(t.id, r.columns))
              .catch(() => {}),
          );
        }
      }
      await Promise.all(jobs);
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

  return { cache: cacheRef.current, version, source, loading, error, reload };
}
