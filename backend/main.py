"""
FastAPI entry point for the monasql Web IDE.

Run from the project root::

    uvicorn backend.main:app --reload --port 8000

Endpoints
---------
* ``POST /api/compile``                 DSL → SQL
* ``POST /api/execute``                 SQL → BigQuery
* ``GET  /api/schema``                  datasets + tables
* ``GET  /api/schema/{dataset}/{table}``  columns
* ``WS   /api/ws``                       realtime compile (debounced by client)
"""
from __future__ import annotations

import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from .models.schemas import CompileRequest
from .routers import compile as compile_router
from .routers import execute as execute_router
from .routers import schema as schema_router

app = FastAPI(title="monasql Web IDE", version="0.1.0")

# CORS — the Vite dev server runs on :5173 by default.
_origins = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(compile_router.router, prefix="/api")
app.include_router(execute_router.router, prefix="/api")
app.include_router(schema_router.router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.websocket("/api/ws")
async def ws_compile(ws: WebSocket) -> None:
    """Realtime compile.  Client sends ``{"type":"compile","dsl":...}`` on each
    (debounced) keystroke; we reply with ``{"type":"result", ...}``."""
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") != "compile":
                continue
            try:
                req = CompileRequest(**{k: v for k, v in msg.items() if k != "type"})
            except ValidationError as e:
                await ws.send_json({"type": "error", "message": str(e)})
                continue
            resp = compile_router.run_compile(req)
            await ws.send_json({
                "type": "result",
                "sql": resp.sql or None,
                "errors": [e.model_dump() for e in resp.errors],
                "warnings": resp.warnings,
                "queries": resp.queries,
                "main": resp.main,
            })
    except WebSocketDisconnect:
        return
