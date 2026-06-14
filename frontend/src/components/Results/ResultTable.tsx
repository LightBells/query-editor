import type { ExecuteResponse } from "../../types";

interface Props {
  result: ExecuteResponse | null;
  loading: boolean;
}

function toCsv(result: ExecuteResponse): string {
  const esc = (v: unknown) => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const header = result.columns.map((c) => esc(c.name)).join(",");
  const body = result.rows.map((r) => r.map(esc).join(",")).join("\n");
  return `${header}\n${body}`;
}

function download(result: ExecuteResponse) {
  const blob = new Blob([toCsv(result)], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "results.csv";
  a.click();
  URL.revokeObjectURL(url);
}

function bytes(n: number): string {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(n) / Math.log(1024));
  return `${(n / 1024 ** i).toFixed(1)} ${u[i]}`;
}

export function ResultTable({ result, loading }: Props) {
  return (
    <div className="flex h-full flex-col border-t border-edge bg-panelAlt">
      <div className="flex items-center gap-3 border-b border-edge px-3 py-1.5">
        <span className="text-xs font-semibold text-slate-200">Query Results</span>
        {result && !result.error && (
          <span className="text-[11px] text-slate-400">
            {result.total_rows.toLocaleString()} rows · {bytes(result.bytes_processed)} processed
            {result.cache_hit ? " · cache hit" : ""}
            {result.dry_run ? " · dry run" : ""}
          </span>
        )}
        {result && !result.error && result.rows.length > 0 && (
          <button
            onClick={() => download(result)}
            className="ml-auto rounded bg-edge px-2 py-0.5 text-xs text-slate-200 hover:bg-accent hover:text-slate-900"
          >
            Export CSV
          </button>
        )}
      </div>
      <div className="flex-1 overflow-auto">
        {loading && <div className="p-3 text-xs text-slate-400">Running…</div>}
        {result?.error && (
          <div className="m-2 rounded border border-red-500/40 bg-red-500/10 p-2 font-mono text-xs text-red-300">
            {result.error}
          </div>
        )}
        {result && !result.error && result.dry_run && (
          <div className="p-3 text-xs text-slate-300">
            Dry run — this query will process <b>{bytes(result.bytes_processed)}</b>.
          </div>
        )}
        {result && !result.error && !result.dry_run && (
          <table className="w-full border-collapse text-xs">
            <thead className="sticky top-0 bg-panel">
              <tr>
                {result.columns.map((c) => (
                  <th
                    key={c.name}
                    className="border-b border-edge px-3 py-1.5 text-left font-semibold text-slate-200"
                  >
                    {c.name}
                    <span className="ml-1 text-[10px] font-normal text-slate-500">{c.type}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row, i) => (
                <tr key={i} className="odd:bg-panel/40 hover:bg-panel">
                  {row.map((cell, j) => (
                    <td key={j} className="border-b border-edge/50 px-3 py-1 font-mono text-slate-300">
                      {cell == null ? <span className="text-slate-600">NULL</span> : String(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {!result && !loading && (
          <div className="p-3 text-xs text-slate-500">
            Run a query (▶ or ⌘/Ctrl+Enter) to see results. Requires a BigQuery project; compile works offline.
          </div>
        )}
      </div>
    </div>
  );
}
