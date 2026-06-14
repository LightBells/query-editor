import { useMemo, useState } from "react";
import type { SchemaCache } from "../../lib/schemaCache";

interface Props {
  cache: SchemaCache;
  version: number;          // bump to re-render when the cache mutates
  source: string;
  loading: boolean;
  onInsert: (text: string) => void;
  onLoadColumns: (table: string) => void;   // lazy column fetch on expand
}

export function SchemaTree({ cache, version, source, loading, onInsert, onLoadColumns }: Props) {
  const [openDs, setOpenDs] = useState<Set<string>>(new Set());
  const [openTbl, setOpenTbl] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");

  // re-derive whenever the cache version changes
  const datasets = useMemo(() => cache.datasets, [cache, version]);

  const toggle = (set: Set<string>, key: string, setter: (s: Set<string>) => void) => {
    const next = new Set(set);
    next.has(key) ? next.delete(key) : next.add(key);
    setter(next);
  };

  const drag = (text: string) => (e: React.DragEvent) => {
    e.dataTransfer.setData("text/plain", text);
  };

  const match = (s: string) => s.toLowerCase().includes(filter.toLowerCase());

  return (
    <div className="flex h-full flex-col bg-panelAlt text-sm text-muted">
      <div className="flex items-center justify-between border-b border-edge px-3 py-2">
        <span className="font-semibold text-slate-200">Schema</span>
        <span className="rounded bg-edge px-1.5 py-0.5 text-[10px] uppercase tracking-wide">
          {source}
        </span>
      </div>
      <div className="border-b border-edge p-2">
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter tables…"
          className="w-full rounded bg-panel px-2 py-1 text-xs text-slate-200 outline-none ring-1 ring-edge focus:ring-accent"
        />
      </div>
      <div className="flex-1 overflow-auto p-1">
        {loading && <div className="px-2 py-1 text-xs">Loading…</div>}
        {datasets.map((ds) => (
          <div key={ds.id}>
            <button
              className="flex w-full items-center gap-1 rounded px-2 py-1 text-left hover:bg-panel"
              onClick={() => toggle(openDs, ds.id, setOpenDs)}
            >
              <span className="text-xs">{openDs.has(ds.id) ? "▾" : "▸"}</span>
              <span>📁</span>
              <span className="text-slate-200">{ds.id}</span>
            </button>
            {openDs.has(ds.id) &&
              ds.tables.filter((t) => match(t.id)).map((t) => {
                const cols = cache.getColumns(t.id);
                const open = openTbl.has(t.id);
                return (
                  <div key={t.id} className="ml-3">
                    <div
                      className="group flex cursor-grab items-center gap-1 rounded px-2 py-1 hover:bg-panel"
                      draggable
                      onDragStart={drag(t.id)}
                    >
                      <button
                        className="text-xs"
                        onClick={() => {
                          if (!open) onLoadColumns(t.id);   // fetch columns on first open
                          toggle(openTbl, t.id, setOpenTbl);
                        }}
                      >
                        {open ? "▾" : "▸"}
                      </button>
                      <span>📋</span>
                      <button
                        className="text-slate-200 hover:text-accent"
                        onClick={() => onInsert(t.id)}
                        title="Insert table name"
                      >
                        {t.id}
                      </button>
                      {t.row_count != null && (
                        <span className="ml-auto text-[10px] text-slate-500">
                          {t.row_count.toLocaleString()}
                        </span>
                      )}
                    </div>
                    {open && !cache.hasColumns(t.id) && (
                      <div className="ml-7 px-2 py-0.5 text-[10px] text-slate-500">loading…</div>
                    )}
                    {open &&
                      cols.map((c) => (
                        <div
                          key={c.name}
                          className="ml-7 flex cursor-grab items-center gap-2 rounded px-2 py-0.5 hover:bg-panel"
                          draggable
                          onDragStart={drag(c.name)}
                          onClick={() => onInsert(c.name)}
                          title={c.description || c.type}
                        >
                          <span className="text-slate-300">{c.name}</span>
                          <span className="ml-auto text-[10px] text-slate-500">{c.type}</span>
                        </div>
                      ))}
                  </div>
                );
              })}
          </div>
        ))}
      </div>
    </div>
  );
}
