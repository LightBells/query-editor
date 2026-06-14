import { useCallback, useEffect, useRef, useState } from "react";
import { CompilerSocket, type CompilePayload, type CompileMessage } from "../lib/api";
import type { CompileResponse } from "../types";

const EMPTY: CompileResponse = { sql: "", errors: [], warnings: [], queries: {}, main: null };

// Realtime DSL→SQL over WebSocket, debounced on each change.
export function useCompiler() {
  const socketRef = useRef<CompilerSocket | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [result, setResult] = useState<CompileResponse>(EMPTY);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const sock = new CompilerSocket((m: CompileMessage) => {
      if (m.type === "result") {
        setResult({
          sql: m.sql ?? "",
          errors: m.errors ?? [],
          warnings: m.warnings ?? [],
          queries: m.queries ?? {},
          main: m.main ?? null,
        });
      }
      setConnected(true);
    });
    socketRef.current = sock;
    sock.connect();
    const ping = setInterval(() => setConnected(sock.connected), 1000);
    return () => {
      clearInterval(ping);
      sock.close();
    };
  }, []);

  const compile = useCallback((payload: CompilePayload, debounceMs = 250) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      socketRef.current?.send(payload);
    }, debounceMs);
  }, []);

  return { result, connected, compile };
}
