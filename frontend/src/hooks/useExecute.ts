import { useCallback, useState } from "react";
import { api } from "../lib/api";
import type { ExecuteResponse } from "../types";

export function useExecute() {
  const [result, setResult] = useState<ExecuteResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const run = useCallback(
    async (sql: string, projectId: string | null, dataset: string | null, dryRun = false) => {
      if (!sql.trim()) return;
      setLoading(true);
      try {
        const r = await api.execute(sql, projectId, dryRun, dataset);
        setResult(r);
      } catch (e) {
        setResult({
          columns: [], rows: [], total_rows: 0, bytes_processed: 0,
          cache_hit: false, dry_run: dryRun,
          error: e instanceof Error ? e.message : String(e),
        });
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  return { result, loading, run };
}
