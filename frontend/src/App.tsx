import { useEffect, useRef, useState } from "react";
import { DslEditor, type EditorApi } from "./components/Editor/DslEditor";
import { SchemaTree } from "./components/SchemaPanel/SchemaTree";
import { SqlViewer } from "./components/SqlPreview/SqlViewer";
import { ResultTable } from "./components/Results/ResultTable";
import { useSchema } from "./hooks/useSchema";
import { useCompiler } from "./hooks/useCompiler";
import { useExecute } from "./hooks/useExecute";
import { setSchemaAccess } from "./lib/monasqlLang";
import { buildAliasMap } from "./lib/aliasResolver";

const DEFAULT_DSL = `-- monasql DSL — function style: assignment + function calls, no yield/lambda.
-- Drag tables/columns from the left, or click to insert.

QUERY dashboard:
  u = from(users)

  -- fanout-safe: each agg_lateral becomes a pre-aggregated CTE
  os = agg_lateral(orders,
         join_on = os.user_id == u.id,
         aggs = [count(os.id).alias('order_count'),
                 sum(os.total).alias('revenue')],
         outer = true)

  cs = agg_lateral(comments,
         join_on = cs.user_id == u.id,
         aggs = [count(cs.id).alias('comment_count')],
         outer = true)

  where(is_active(u))
  select(
    u.name,
    coalesce(os.order_count, 0).alias('orders'),
    coalesce(os.revenue, 0).alias('revenue'),
    coalesce(cs.comment_count, 0).alias('comments'),
  )
  order_by(revenue.desc())
  limit(100)
`;

export default function App() {
  const [dsl, setDsl] = useState(DEFAULT_DSL);
  const [projectId, setProjectId] = useState("");
  const [dataset, setDataset] = useState("analytics");
  const editorApi = useRef<EditorApi | null>(null);

  const schema = useSchema(projectId || null, dataset || null);
  const compiler = useCompiler();
  const exec = useExecute();

  // keep the editor autocomplete pointed at the live schema cache
  useEffect(() => {
    setSchemaAccess({
      tables: () =>
        schema.cache.getAllTables().map((t) => ({ name: t.name, rowCount: t.rowCount })),
      columns: (table) => schema.cache.getColumns(table),
    });
  }, [schema.cache, schema.version]);

  // lazily fetch columns only for tables actually referenced in the editor
  useEffect(() => {
    const referenced = new Set(Object.values(buildAliasMap(dsl)));
    referenced.forEach((t) => schema.ensureColumns(t));
  }, [dsl, schema.version, schema.ensureColumns]);

  // realtime compile on every DSL / schema change.
  // Depend on the *stable* compile fn (not the whole compiler object), so a
  // WebSocket result updating state doesn't re-trigger an endless compile loop.
  useEffect(() => {
    compiler.compile({
      dsl,
      tables: schema.cache.columnMap(),
      project_id: projectId || null,
      dataset: dataset || null,
    });
  }, [dsl, schema.version, projectId, dataset, schema.cache, compiler.compile]);

  const run = (dryRun = false) => {
    if (compiler.result.sql) {
      exec.run(compiler.result.sql, projectId || null, dataset || null, dryRun);
    }
  };

  return (
    <div className="flex h-screen flex-col bg-panel text-slate-200">
      {/* header */}
      <header className="flex items-center gap-3 border-b border-edge bg-panelAlt px-4 py-2">
        <h1 className="font-mono text-sm font-bold text-accent">monasql</h1>
        <span className="text-xs text-slate-500">Monadic SQL IDE</span>
        <div className="ml-4 flex items-center gap-2">
          <input
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            placeholder="GCP project (optional)"
            className="w-48 rounded bg-panel px-2 py-1 text-xs ring-1 ring-edge focus:ring-accent"
          />
          <input
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
            placeholder="dataset"
            className="w-32 rounded bg-panel px-2 py-1 text-xs ring-1 ring-edge focus:ring-accent"
          />
        </div>
        <div className="ml-auto flex items-center gap-3">
          <span
            className={`flex items-center gap-1 text-[11px] ${
              compiler.connected ? "text-emerald-400" : "text-slate-500"
            }`}
            title="WebSocket realtime compiler"
          >
            <span className={`h-2 w-2 rounded-full ${compiler.connected ? "bg-emerald-400" : "bg-slate-600"}`} />
            {compiler.connected ? "live" : "offline"}
          </span>
          <button
            onClick={() => run(true)}
            className="rounded border border-edge px-3 py-1 text-xs hover:bg-edge"
          >
            Dry run
          </button>
          <button
            onClick={() => run(false)}
            disabled={exec.loading}
            className="rounded bg-accent px-4 py-1 text-xs font-semibold text-slate-900 hover:bg-blue-300 disabled:opacity-50"
          >
            ▶ Run
          </button>
        </div>
      </header>

      {/* schema connection notice (e.g. project set but BigQuery unreachable) */}
      {schema.error && (
        <div className="border-b border-amber-500/30 bg-amber-500/10 px-4 py-1 text-xs text-amber-300">
          ⚠ {schema.error}
        </div>
      )}

      {/* body */}
      <div className="flex min-h-0 flex-1">
        <aside className="w-64 shrink-0 border-r border-edge">
          <SchemaTree
            cache={schema.cache}
            version={schema.version}
            source={schema.source}
            loading={schema.loading}
            onInsert={(t) => editorApi.current?.insertText(t)}
            onLoadColumns={schema.ensureColumns}
          />
        </aside>

        <main className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-[3]">
            <DslEditor
              value={dsl}
              onChange={setDsl}
              errors={compiler.result.errors}
              onReady={(api) => (editorApi.current = api)}
              onRun={() => run(false)}
            />
          </div>
          <div className="min-h-0 flex-[2]">
            <SqlViewer
              sql={compiler.result.sql}
              queries={compiler.result.queries}
              main={compiler.result.main}
              warnings={compiler.result.warnings}
            />
          </div>
          <div className="min-h-0 flex-[2]">
            <ResultTable result={exec.result} loading={exec.loading} />
          </div>
        </main>
      </div>
    </div>
  );
}
