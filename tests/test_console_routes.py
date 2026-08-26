"""控制台 API 测试：骨架、路由。"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atk.console import create_app


@pytest.fixture
def client(tmp_path: Path):
    return TestClient(create_app(project_root=tmp_path))


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}
