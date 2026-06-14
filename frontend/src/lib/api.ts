// REST + WebSocket client for the backend.

import type {
  CompileResponse,
  ExecuteResponse,
  SchemaResponse,
  TableSchemaResponse,
} from "../types";

// Empty base → use the Vite dev proxy (relative /api). Override with VITE_API_BASE.
const BASE = import.meta.env.VITE_API_BASE ?? "";

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export interface CompilePayload {
  dsl: string;
  tables?: Record<string, string[]>;
  project_id?: string | null;
  dataset?: string | null;
  target?: string | null;
  dialect?: string;
}

export const api = {
  getSchema(projectId?: string | null, demo = true, dataset?: string | null): Promise<SchemaResponse> {
    const p = new URLSearchParams();
    if (projectId) p.set("project_id", projectId);
    if (dataset) p.set("dataset", dataset);
    if (demo) p.set("demo", "true");
    return jsonFetch<SchemaResponse>(`/api/schema?${p.toString()}`);
  },

  getTableColumns(
    dataset: string,
    table: string,
    projectId?: string | null,
    demo = true,
  ): Promise<TableSchemaResponse> {
    const p = new URLSearchParams();
    if (projectId) p.set("project_id", projectId);
    if (demo) p.set("demo", "true");
    return jsonFetch<TableSchemaResponse>(
      `/api/schema/${dataset}/${table}?${p.toString()}`,
    );
  },

  compile(payload: CompilePayload): Promise<CompileResponse> {
    return jsonFetch<CompileResponse>(`/api/compile`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  execute(sql: string, projectId?: string | null, dryRun = false,
          dataset?: string | null): Promise<ExecuteResponse> {
    return jsonFetch<ExecuteResponse>(`/api/execute`, {
      method: "POST",
      body: JSON.stringify({
        sql, project_id: projectId ?? null, dataset: dataset ?? null, dry_run: dryRun,
      }),
    });
  },
};

// ── realtime compile socket ───────────────────────────────────

export type CompileMessage =
  | { type: "result"; sql: string | null; errors: CompileResponse["errors"]; warnings: string[]; queries: Record<string, string>; main?: string | null }
  | { type: "error"; message: string };

export class CompilerSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private onMessage: (m: CompileMessage) => void;
  private queue: string[] = [];

  constructor(onMessage: (m: CompileMessage) => void) {
    this.onMessage = onMessage;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.url = BASE
      ? BASE.replace(/^http/, "ws") + "/api/ws"
      : `${proto}://${location.host}/api/ws`;
  }

  connect() {
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) return;
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => {
      this.queue.forEach((m) => ws.send(m));
      this.queue = [];
    };
    ws.onmessage = (ev) => this.onMessage(JSON.parse(ev.data));
    ws.onclose = () => {
      this.ws = null;
    };
    ws.onerror = () => ws.close();
  }

  send(payload: CompilePayload) {
    const msg = JSON.stringify({ type: "compile", ...payload });
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(msg);
    } else {
      this.queue = [msg]; // keep only the latest
      this.connect();
    }
  }

  get connected() {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  close() {
    this.ws?.close();
    this.ws = null;
  }
}
