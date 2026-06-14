import { useState } from "react";

interface Props {
  sql: string;
  queries: Record<string, string>;
  main?: string | null;
  warnings: string[];
}

export function SqlViewer({ sql, queries, main, warnings }: Props) {
  const [copied, setCopied] = useState(false);
  const names = Object.keys(queries);
  const [selected, setSelected] = useState<string | null>(null);
  const shown = (selected && queries[selected]) || sql;

  const copy = async () => {
    await navigator.clipboard.writeText(shown);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };

  return (
    <div className="flex h-full flex-col border-t border-edge bg-panel">
      <div className="flex items-center gap-2 border-b border-edge px-3 py-1.5">
        <span className="text-xs font-semibold text-slate-200">Generated SQL</span>
        {names.length > 1 && (
          <select
            className="rounded bg-panelAlt px-1.5 py-0.5 text-xs text-slate-200 ring-1 ring-edge"
            value={selected ?? main ?? names[names.length - 1]}
            onChange={(e) => setSelected(e.target.value)}
          >
            {names.map((n) => (
              <option key={n} value={n}>
                {n}
                {n === main ? "  (main)" : ""}
              </option>
            ))}
          </select>
        )}
        {warnings.length > 0 && (
          <span
            className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] text-amber-300"
            title={warnings.join("\n")}
          >
            ⚠ {warnings.length}
          </span>
        )}
        <button
          onClick={copy}
          className="ml-auto rounded bg-edge px-2 py-0.5 text-xs text-slate-200 hover:bg-accent hover:text-slate-900"
        >
          {copied ? "Copied ✓" : "Copy"}
        </button>
      </div>
      <pre className="flex-1 overflow-auto whitespace-pre px-3 py-2 font-mono text-xs leading-relaxed text-emerald-200">
        {shown || "-- start typing a QUERY block…"}
      </pre>
    </div>
  );
}
