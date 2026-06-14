"""End-to-end tests for the FastAPI backend (offline / demo mode)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

DSL = """
QUERY dashboard:
  u = from(users)
  os = agg_lateral(orders, join_on = os.user_id == u.id,
                   aggs = [count(os.id).alias('order_count'), sum(os.total).alias('revenue')],
                   outer = true)
  select(u.name, coalesce(os.order_count, 0).alias('orders'))
"""


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_schema_demo():
    r = client.get("/api/schema", params={"demo": True})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "demo"
    names = {d["id"] for d in body["datasets"]}
    assert "analytics" in names
    tables = {t["id"] for d in body["datasets"] for t in d["tables"]}
    assert {"users", "orders", "comments", "departments"} <= tables


def test_table_schema_demo():
    r = client.get("/api/schema/analytics/users", params={"demo": True})
    body = r.json()
    cols = {c["name"]: c["type"] for c in body["columns"]}
    assert cols["id"] == "INT64"
    assert cols["deleted_at"] == "TIMESTAMP"


def test_compile_with_inline_tables():
    r = client.post("/api/compile", json={
        "dsl": DSL,
        "tables": {
            "users": ["id", "name", "dept_id", "deleted_at"],
            "orders": ["id", "user_id", "total"],
        },
    })
    body = r.json()
    assert body["errors"] == []
    assert "WITH _agg_os AS" in body["sql"]
    assert "LEFT JOIN _agg_os os" in body["sql"]
    assert body["main"] == "dashboard"


def test_compile_fetches_demo_schema():
    # no `tables`, but dataset given → backend pulls the demo schema
    r = client.post("/api/compile", json={"dsl": DSL, "dataset": "analytics"})
    body = r.json()
    assert body["errors"] == []
    assert "_agg_os" in body["sql"]


def test_compile_reports_errors():
    r = client.post("/api/compile", json={"dsl": "QUERY q:\n u = from(\n select(1)"})
    body = r.json()
    assert body["errors"]
    assert body["errors"][0]["line"] >= 1


def test_diagnostics_endpoint():
    r = client.get("/api/diagnostics")
    assert r.status_code == 200
    body = r.json()
    assert "bigquery_installed" in body
    assert body["project"] is None
    assert body["hint"]  # tells you to set a project


def test_execute_without_project_is_graceful():
    r = client.post("/api/execute", json={"sql": "SELECT 1", "project_id": None})
    body = r.json()
    assert body["error"] is not None
    assert "project_id" in body["error"]


def test_websocket_realtime_compile():
    with client.websocket_connect("/api/ws") as ws:
        ws.send_json({
            "type": "compile",
            "dsl": "QUERY q:\n u = from(users)\n select(u.id, u.name)",
            "tables": {"users": ["id", "name"]},
        })
        msg = ws.receive_json()
        assert msg["type"] == "result"
        assert msg["errors"] == []
        assert "SELECT u.id, u.name" in msg["sql"]


def test_websocket_reports_errors():
    with client.websocket_connect("/api/ws") as ws:
        ws.send_json({"type": "compile", "dsl": "garbage @@@"})
        msg = ws.receive_json()
        assert msg["type"] == "result"
        assert msg["sql"] is None
        assert msg["errors"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
