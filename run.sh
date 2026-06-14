#!/usr/bin/env bash
# Dev convenience: start the FastAPI backend and the Vite frontend together.
# Ctrl+C stops both.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

BACKEND_PORT="${BACKEND_PORT:-8000}"

echo "▶ backend  : http://localhost:${BACKEND_PORT}"
echo "▶ frontend : http://localhost:5173"
echo

# --- backend -------------------------------------------------------------
if [ ! -d backend/.venv ]; then
  echo "Creating backend venv & installing deps…"
  python3 -m venv backend/.venv
  ./backend/.venv/bin/pip install -q -r backend/requirements.txt
fi
./backend/.venv/bin/uvicorn backend.main:app --reload --port "${BACKEND_PORT}" &
BACKEND_PID=$!

# --- frontend ------------------------------------------------------------
if [ ! -d frontend/node_modules ]; then
  echo "Installing frontend deps…"
  (cd frontend && npm install)
fi
(cd frontend && npm run dev) &
FRONTEND_PID=$!

cleanup() {
  echo; echo "Stopping…"
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait
