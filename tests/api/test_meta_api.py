# -*- coding: utf-8 -*-
"""배포 probe 계약 테스트."""
from contextlib import nullcontext

from fastapi.testclient import TestClient

import app.main as main_module


class _Connection:
    def execute(self, statement):
        assert str(statement) == "SELECT 1"


class _ReadyEngine:
    def connect(self):
        return nullcontext(_Connection())


class _UnavailableEngine:
    def connect(self):
        raise RuntimeError("credential must not leak")


def test_health_is_liveness_only():
    response = TestClient(main_module.app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_checks_database(monkeypatch):
    monkeypatch.setattr(main_module, "engine", _ReadyEngine())
    response = TestClient(main_module.app).get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}


def test_ready_hides_database_error(monkeypatch):
    monkeypatch.setattr(main_module, "engine", _UnavailableEngine())
    response = TestClient(main_module.app).get("/ready")
    assert response.status_code == 503
    assert response.json() == {
        "detail": "데이터베이스 연결을 확인할 수 없습니다"
    }
    assert "credential" not in response.text


def test_version_exposes_deployment_identity(monkeypatch):
    monkeypatch.setattr(main_module._settings, "app_version", "a" * 40)
    monkeypatch.setattr(main_module._settings, "deployment_slot", "green")
    response = TestClient(main_module.app).get("/version")
    assert response.status_code == 200
    assert response.json() == {"version": "a" * 40, "slot": "green"}
