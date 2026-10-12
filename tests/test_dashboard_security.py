"""Regression tests for the AEGIS dashboard deployment boundary."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aegis_v3.realtime_telemetry import app


def test_health_and_readiness_probes_are_public(monkeypatch):
    monkeypatch.setenv("AEGIS_ENV", "production")
    monkeypatch.delenv("AEGIS_DASHBOARD_USERNAME", raising=False)
    monkeypatch.delenv("AEGIS_DASHBOARD_PASSWORD", raising=False)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code in {200, 503}


def test_production_dashboard_fails_closed_without_credentials(monkeypatch):
    monkeypatch.setenv("AEGIS_ENV", "production")
    monkeypatch.delenv("AEGIS_DASHBOARD_USERNAME", raising=False)
    monkeypatch.delenv("AEGIS_DASHBOARD_PASSWORD", raising=False)
    with TestClient(app) as client:
        response = client.get("/api/status")
    assert response.status_code == 503
    assert "credentials" in response.json()["detail"].lower()


def test_configured_basic_auth_allows_dashboard_api(monkeypatch):
    monkeypatch.setenv("AEGIS_ENV", "production")
    monkeypatch.setenv("AEGIS_DASHBOARD_USERNAME", "operator")
    monkeypatch.setenv("AEGIS_DASHBOARD_PASSWORD", "test-only-password")
    with TestClient(app) as client:
        denied = client.get("/api/status")
        allowed = client.get("/api/status", auth=("operator", "test-only-password"))
    assert denied.status_code == 401
    assert allowed.status_code == 200


def test_production_pipeline_api_is_disabled_by_default(monkeypatch):
    monkeypatch.setenv("AEGIS_ENV", "production")
    monkeypatch.setenv("AEGIS_DASHBOARD_USERNAME", "operator")
    monkeypatch.setenv("AEGIS_DASHBOARD_PASSWORD", "test-only-password")
    monkeypatch.delenv("AEGIS_ENABLE_PIPELINE_API", raising=False)
    with TestClient(app) as client:
        response = client.post("/api/pipeline/run", auth=("operator", "test-only-password"))
    assert response.status_code == 503


def test_pipeline_rejects_non_demo_target(monkeypatch):
    monkeypatch.setenv("AEGIS_ENV", "development")
    monkeypatch.delenv("AEGIS_DASHBOARD_USERNAME", raising=False)
    monkeypatch.delenv("AEGIS_DASHBOARD_PASSWORD", raising=False)
    with TestClient(app) as client:
        response = client.post("/api/pipeline/run?target_file=../../outside.py")
    assert response.status_code == 400
    assert "demo target" in response.json()["detail"]


def test_websocket_rejects_missing_production_credentials(monkeypatch):
    monkeypatch.setenv("AEGIS_ENV", "production")
    monkeypatch.delenv("AEGIS_DASHBOARD_USERNAME", raising=False)
    monkeypatch.delenv("AEGIS_DASHBOARD_PASSWORD", raising=False)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/telemetry"):
                pass



def test_websocket_accepts_configured_basic_auth(monkeypatch):
    import base64
    import json

    monkeypatch.setenv("AEGIS_ENV", "production")
    monkeypatch.setenv("AEGIS_DASHBOARD_USERNAME", "operator")
    monkeypatch.setenv("AEGIS_DASHBOARD_PASSWORD", "test-only-password")
    token = base64.b64encode(b"operator:test-only-password").decode("ascii")
    with TestClient(app) as client:
        with client.websocket_connect(
            "/ws/telemetry",
            headers={"Authorization": f"Basic {token}"},
        ) as websocket:
            message = json.loads(websocket.receive_text())
            assert message["title"] == "CONNECTED"
