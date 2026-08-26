"""控制台 API 测试：骨架、路由。"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atk.console import create_app


@pytest.fixture
def client(tmp_path: Path):
    return TestClient(create_app(project_root=tmp_path))


def test_health(client):
    assert client.get("/api/health").json()["ok"] is True


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
    # httpx 会规范化字面 ../，用 %2e%2e 确保打到后端
    assert c.get("/api/scenarios/%2e%2e/config/environments.yaml").status_code in (400, 404, 422)
    assert c.get("/api/scenarios/..%2fconfig%2fenvironments.yaml").status_code in (400, 404, 422)


RUN_YAML = """\
run_id: smoke-test-1
created_at: '2026-08-26T10:00:00'
scenarios:
- name: 用例A
  file: scenarios/x/a.yaml
  module: x
  priority: P0
  passed: true
  duration_ms: 5
  steps:
  - title: GET /a
    passed: true
    detail: ok
- name: 用例B
  file: scenarios/x/b.yaml
  module: x
  priority: P1
  passed: false
  error_class: assertion
  duration_ms: 8
  steps:
  - title: GET /b
    passed: false
    detail: 'expect 200 got 500'
"""


def test_runs_history_and_evidence(c, proj):
    rd = proj / "reports" / "runs" / "smoke-test-1"
    rd.mkdir(parents=True)
    (rd / "run.yaml").write_text(RUN_YAML, encoding="utf-8")
    ev = rd / "evidence"
    ev.mkdir()
    (ev / "shot.png").write_bytes(b"\x89PNG fake")

    runs = c.get("/api/runs").json()
    assert runs[0]["run_id"] == "smoke-test-1"
    assert runs[0]["pass_n"] == 1 and runs[0]["fail_n"] == 1

    r = c.get("/api/runs/smoke-test-1/evidence/evidence/shot.png")
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
    # 越界拒绝（%2e%2e 编码穿越，确保到达后端守卫）
    r = c.get("/api/runs/smoke-test-1/evidence/%2e%2e/%2e%2e/config/environments.yaml")
    assert r.status_code == 404


def test_run_endpoint_starts_job(c, proj, monkeypatch):
    # 不真跑 atk：mock JobManager.start 返回假 job，stream 立即 done
    from atk.console import routes

    class FakeJM:
        def start(self, argv, cwd=None):
            self.argv = argv
            return "job123"

        def stream(self, jid):
            yield {"type": "log", "line": "fake"}
            yield {"type": "done", "exit_code": 0}

    monkeypatch.setattr(routes, "JobManager", FakeJM)
    # 重建 client 使 setup 使用 FakeJM
    from atk.console import create_app as cap
    c2 = TestClient(cap(project_root=proj))
    r = c2.post("/api/run", json={"env": "local"})
    assert r.status_code == 200 and r.json()["job_id"] == "job123"

    events = []
    with c2.stream("GET", "/api/jobs/job123/stream") as resp:
        for line in resp.iter_lines():
            if line.startswith("data:"):
                import json
                events.append(json.loads(line[5:]))
    assert events[-1] == {"type": "done", "exit_code": 0}


def test_config_endpoints(c, proj):
    (proj / "config").mkdir()
    (proj / "config" / "environments.yaml").write_text(
        "local:\n  base_url: http://x\n  vars:\n    t: '${env:TOK}'\n", encoding="utf-8")
    body = c.get("/api/environments").json()
    assert body["raw"].startswith("local:")

    new_raw = "staging:\n  base_url: http://y\n"
    assert c.put("/api/environments", json={"raw": new_raw}).status_code == 200
    assert "staging" in c.get("/api/environments").json()["raw"]
