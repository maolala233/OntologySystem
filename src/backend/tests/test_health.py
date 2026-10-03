# tests/test_health.py - /api/system/health 五灯端点（mock 探测，不打真实中间件）
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_five_lights_ok(monkeypatch):
    for name in ("_check_mysql", "_check_neo4j", "_check_milvus", "_check_redis", "_check_minio"):
        monkeypatch.setattr("app.api.system." + name, lambda: {"status": "ok", "latency_ms": 1})
    resp = client.get("/api/system/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    for light in ("mysql", "neo4j", "milvus", "redis", "minio"):
        assert body["checks"][light]["status"] == "ok"
    # oxigraph 为嵌入式，M4 接入前固定 pending
    assert body["checks"]["oxigraph"] == {"status": "pending"}


def test_health_degraded_when_one_down(monkeypatch):
    def _down():
        raise ConnectionError("connection refused")

    for name in ("_check_mysql", "_check_neo4j", "_check_milvus", "_check_redis", "_check_minio"):
        monkeypatch.setattr("app.api.system." + name, lambda: {"status": "ok", "latency_ms": 1})
    monkeypatch.setattr("app.api.system._check_redis", _down)
    resp = client.get("/api/system/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"]["status"] == "down"
