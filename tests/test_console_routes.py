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


GOOD = """\
scenario: 下单
module: order
priority: P0
tags: [smoke]
env: local
steps:
  - api:
      call: "POST /orders"
      expect: { status: 200 }
"""


@pytest.fixture
def proj(tmp_path: Path):
    d = tmp_path / "scenarios" / "order"
    d.mkdir(parents=True)
    (d / "create.yaml").write_text(GOOD, encoding="utf-8")
    return tmp_path


@pytest.fixture
def c(proj):
    return TestClient(create_app(project_root=proj))


def test_tree(c):
    r = c.get("/api/tree")
    assert r.status_code == 200
    body = r.json()
    assert any(s["path"] == "order/create.yaml" for s in body["scenarios"])


def test_detail_and_save_with_lock(c, proj):
    detail = c.get("/api/scenarios/order/create.yaml").json()
    assert detail["data"]["scenario"] == "下单"
    assert detail["mtime"] > 0

    data = dict(detail["data"], priority="P1")
    r = c.put("/api/scenarios/order/create.yaml", json={
        "data": data, "if_mtime": detail["mtime"],
    })
    assert r.status_code == 200

    # 过期锁 → 409；force 覆盖成功
    r = c.put("/api/scenarios/order/create.yaml", json={
        "data": data, "if_mtime": detail["mtime"],
    })
    assert r.status_code == 409
    r = c.put("/api/scenarios/order/create.yaml", json={
        "data": data, "if_mtime": detail["mtime"], "force": True,
    })
    assert r.status_code == 200


def test_validate_endpoint(c):
    bad = {"module": "x", "steps": []}
    assert c.post("/api/validate", json={"data": bad}).json()["ok"] is False
    import yaml
    good = yaml.safe_load(GOOD)
    assert c.post("/api/validate", json={"data": good}).json()["ok"] is True


def test_path_traversal_blocked(c):
    assert c.get("/api/scenarios/../config/environments.yaml").status_code in (400, 404)
