"""B批安全加固回归测试（TDD RED先行）。"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------- B1 ----------
def test_b1_run_report_traversal_blocked(tmp_path: Path):
    from atk.console import create_app
    c = TestClient(create_app(project_root=tmp_path))
    # 诱饵文件：若无越界校验，run_id=".." 会命中它返回200；用 %2e%2e 避免客户端规范化
    (tmp_path / "reports").mkdir(parents=True)
    (tmp_path / "reports" / "report.html").write_text("SECRET", encoding="utf-8")
    r = c.get("/api/runs/%2e%2e/report")
    assert r.status_code == 404
    assert "SECRET" not in r.text


# ---------- B2 ----------
def test_b2_run_html_escapes_run_id_created_at(tmp_path: Path):
    from atk.reporter.html_reporter import render_run_html
    from atk.run_store import RunRecord
    rec = RunRecord(run_id='<script>alert(1)</script>', created_at='<img src=x onerror=alert(2)>')
    out = render_run_html(rec, tmp_path / "r.html")
    html = out.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x onerror=alert(2)>" not in html


def test_b2_evidence_security_headers(tmp_path: Path):
    from atk.console import create_app
    rd = tmp_path / "reports" / "runs" / "r1"
    (rd / "evidence").mkdir(parents=True)
    (rd / "run.yaml").write_text("run_id: r1\nscenarios: []\n", encoding="utf-8")
    (rd / "evidence" / "a.txt").write_text("hi", encoding="utf-8")
    c = TestClient(create_app(project_root=tmp_path))
    r = c.get("/api/runs/r1/evidence/evidence/a.txt")
    assert r.status_code == 200
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert "sandbox" in r.headers.get("Content-Security-Policy", "")


# ---------- B3 ----------
def test_b3_fixture_traversal_is_config_not_crash(tmp_path: Path, mock_base_url):
    from atk.executors.runner import Runner
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "environments.yaml").write_text(
        f"it:\n  base_url: {mock_base_url}\n", encoding="utf-8")
    sc_dir = tmp_path / "scenarios"
    sc_dir.mkdir()
    (sc_dir / "evil.yaml").write_text(
        'scenario: 越界\nenv: it\ndata: ../../etc/passwd\nsteps:\n'
        '  - api:\n      call: "GET /ping"\n      expect: {status: 200}\n',
        encoding="utf-8")
    (sc_dir / "ok.yaml").write_text(
        'scenario: 正常\nenv: it\nsteps:\n'
        '  - api:\n      call: "GET /ping"\n      expect: {status: 200}\n',
        encoding="utf-8")
    report = Runner(
        env_file=tmp_path / "config/environments.yaml",
        scenarios_root=sc_dir).run(env_name=None)
    assert report.total == 2
    evil = next(r for r in report.results if r.scenario.scenario == "越界")
    assert evil.error_class == "config" and not evil.passed
    assert any(r.passed for r in report.results)


def test_b3_absolute_fixture_outside_root_is_config(tmp_path: Path, mock_base_url):
    from atk.executors.runner import Runner
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "environments.yaml").write_text(
        f"it:\n  base_url: {mock_base_url}\n", encoding="utf-8")
    sc_dir = tmp_path / "scenarios"
    sc_dir.mkdir()
    (sc_dir / "evil.yaml").write_text(
        'scenario: 绝对越界\nenv: it\ndata: /etc/passwd\nsteps:\n'
        '  - api:\n      call: "GET /ping"\n      expect: {status: 200}\n',
        encoding="utf-8")
    report = Runner(
        env_file=tmp_path / "config/environments.yaml",
        scenarios_root=sc_dir).run(env_name=None)
    assert report.results[0].error_class == "config"


# ---------- B4 ----------
def test_b4_token_auth(tmp_path: Path):
    from atk.console import create_app
    c = TestClient(create_app(project_root=tmp_path, token="s3cret"))
    assert c.get("/api/health").status_code == 401
    assert c.get("/api/health", headers={"X-Auth-Token": "wrong"}).status_code == 401
    assert c.get("/api/health", headers={"X-Auth-Token": "s3cret"}).status_code == 200


def test_b4_no_token_open(tmp_path: Path):
    from atk.console import create_app
    c = TestClient(create_app(project_root=tmp_path))
    assert c.get("/api/health").status_code == 200


def test_b4_cli_has_token_option_and_env():
    import inspect
    import os
    from atk import cli
    from atk.console import serve
    assert "token" in inspect.signature(cli.console_cmd).parameters
    assert "token" in inspect.signature(serve).parameters
    src = inspect.getsource(serve)
    assert "ATK_CONSOLE_TOKEN" in src
    src2 = inspect.getsource(create_app_fn := __import__("atk.console", fromlist=["create_app"]).create_app)
    assert "token" in inspect.signature(create_app_fn).parameters


# ---------- B5 ----------
def test_b5_non_http_exception_is_config(mock_client):
    from atk.executors.api_executor import ApiExecutor
    from atk.store.models import ApiStep

    class Boom:
        def request(self, *a, **kw):
            raise ValueError("bad url")

    ex = ApiExecutor(Boom(), {})
    r = ex.execute(ApiStep(call="GET /ping"))
    assert not r.passed and r.error_class == "config"


def test_b5_runner_survives_config_exception(tmp_path, mock_base_url):
    from atk.executors.runner import Runner
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "environments.yaml").write_text(
        f"it:\n  base_url: {mock_base_url}\n", encoding="utf-8")
    sc_dir = tmp_path / "scenarios"
    sc_dir.mkdir()
    (sc_dir / "a.yaml").write_text(
        'scenario: A\nenv: it\nsteps:\n  - api:\n      call: "GET /ping"\n      expect: {status: 200}\n',
        encoding="utf-8")
    report = Runner(
        env_file=tmp_path / "config/environments.yaml",
        scenarios_root=sc_dir).run(env_name=None)
    assert report.total == 1


# ---------- B6 ----------
def test_b6_retries_bounds():
    import pydantic
    from atk.store.models import ApiStep
    with pytest.raises(pydantic.ValidationError):
        ApiStep(call="GET /x", retries=-1)
    with pytest.raises(pydantic.ValidationError):
        ApiStep(call="GET /x", retries=11)
    assert ApiStep(call="GET /x", retries=0).retries == 0
    assert ApiStep(call="GET /x", retries=10).retries == 10


# ---------- B7 ----------
def test_b7_job_timeout_kills():
    import sys
    from atk.console.jobs import JobManager
    jm = JobManager(timeout=0.5)
    jid = jm.start([sys.executable, "-c", "import time; time.sleep(10)"])
    import time
    t0 = time.time()
    last = None
    for ev in jm.stream(jid):
        last = ev
        if ev["type"] == "done":
            break
    assert time.time() - t0 < 5
    assert last and last["type"] == "done"


def test_b7_events_bounded():
    from atk.console.jobs import JobManager, _Job
    jm = JobManager(timeout=30)
    assert jm.max_events == 1000
    # 满丢最旧：无消费时缓冲不超过上限
    import sys, time
    jid = jm.start([sys.executable, "-c", "for i in range(1500): print(i)"])
    # 等待完成但先不消费，让生产者填满缓冲
    for _ in range(100):
        if jm._current.done:
            break
        time.sleep(0.05)
    assert len(jm._current.events) <= 1001
    n = 0
    for ev in jm.stream(jid):
        n += 1
        if ev["type"] == "done":
            break
    assert n <= 1501  # 消费跟得上时可全量，不强制丢弃已消费


# ---------- B8 ----------
def test_b8_put_env_missing_raw_400(tmp_path: Path):
    from atk.console import create_app
    c = TestClient(create_app(project_root=tmp_path))
    assert c.put("/api/environments", json={}).status_code == 400
    assert c.put("/api/modules", json={}).status_code == 400


def test_b8_put_env_bad_yaml_422_and_schema_400(tmp_path: Path):
    from atk.console import create_app
    c = TestClient(create_app(project_root=tmp_path))
    assert c.put("/api/environments", json={"raw": ":\n: bad: ["}).status_code in (400, 422)
    assert c.put("/api/environments", json={"raw": "- a\n- b\n"}).status_code == 400


def test_b8_run_summaries_mtime_limit(tmp_path: Path):
    import time
    from atk.console import routes
    runs = tmp_path / "reports" / "runs"
    for i in range(5):
        d = runs / f"run-{i}"
        d.mkdir(parents=True)
        (d / "run.yaml").write_text(
            f"run_id: run-{i}\ncreated_at: '2026-01-01T00:00:00'\nscenarios: []\n",
            encoding="utf-8")
        # 让 run-4 最旧、run-0 最新（与名字逆序相反，验证按mtime）
        import os
        ts = time.time() - (4 - i) * 100 if i < 4 else time.time() + 100
        # run-0 最新
        ts = time.time() + (4 - i)
        os.utime(d / "run.yaml", (ts, ts))
        os.utime(d, (ts, ts))
    out = routes._run_summaries(tmp_path, limit=2)
    assert len(out) == 2
    assert out[0]["run_id"] == "run-0"


def test_b8_projects_open_limit(monkeypatch, tmp_path: Path):
    from atk.console import create_app, registry as reg
    monkeypatch.setattr(reg, "list_projects", lambda db=None: [{"path": f"/p{i}"} for i in range(50)])
    c = TestClient(create_app(project_root=tmp_path, global_mode=True))
    r = c.post("/api/projects/open", json={"path": "/p0"})
    assert r.status_code == 429


def test_b8_schedules_malformed_no_crash(tmp_path: Path):
    from atk.console import schedules as sch
    assert sch.next_run_of({"name": "x", "env": "e", "daily_at": "not-a-time", "enabled": True}) is None
    assert sch.cron_trigger_of({"name": "x", "env": "e", "daily_at": "not-a-time", "enabled": True}) is None
    # 文件含畸形 daily_at 时 GET 不500
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "schedules.yaml").write_text(
        "tasks:\n- name: bad\n  env: e\n  daily_at: nope\n  enabled: true\n",
        encoding="utf-8")
    from atk.console import create_app
    c = TestClient(create_app(project_root=tmp_path))
    assert c.get("/api/schedules").status_code == 200


def test_b8_reschedule_partial_failure(tmp_path: Path):
    from atk.console import reschedule as rs
    from atk.console import schedules as sch

    class FakeSched:
        def __init__(self):
            self.added = []
        def remove_all_jobs(self):
            pass
        def add_job(self, fn, trigger=None, id=None, args=None, replace_existing=None):
            if "bad" in id:
                raise RuntimeError("boom")
            self.added.append(id)

    class FakeApp:
        pass
    app = FakeApp()
    app.state = type("S", (), {})()
    app.state.scheduler = FakeSched()
    app.state.project_root = tmp_path
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    sch.save_tasks(tmp_path, [
        {"name": "bad", "env": "e", "cron": "* * * * *", "enabled": True},
        {"name": "good", "env": "e", "cron": "* * * * *", "enabled": True},
    ])
    rs.reschedule(app)  # 不应抛
    assert any("good" in i for i in app.state.scheduler.added)


def test_b8_repo_scan_skips_outside_symlink(tmp_path: Path):
    from atk.console import repo
    (tmp_path / "scenarios").mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text("scenario: 外部\nsteps:\n  - api:\n      call: 'GET /x'\n", encoding="utf-8")
    link = tmp_path / "scenarios" / "evil.yaml"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink 不可用")
    tree = repo.scan_tree(tmp_path)
    texts = str(tree)
    assert "外部" not in texts


# ---------- B9 ----------
def test_b9_logging_default_warning():
    import inspect
    from atk.console import serve
    src = inspect.getsource(serve)
    assert "WARNING" in src
    assert "level=logging.DEBUG" not in src


def test_b9_registry_blocked_semantics(tmp_path: Path):
    from atk.console import registry as reg
    db = tmp_path / "reg.db"
    proj = tmp_path / "proj"
    rd = proj / "reports" / "runs" / "r1"
    rd.mkdir(parents=True)
    (rd / "run.yaml").write_text(
        "run_id: r1\ncreated_at: '2026-01-01T00:00:00'\nscenarios:\n"
        "- name: a\n  passed: true\n  error_class: none\n"
        "- name: b\n  passed: false\n  error_class: assertion\n"
        "- name: c\n  passed: false\n  error_class: environment\n",
        encoding="utf-8")
    reg.upsert_project(proj, db=db)
    assert reg.rebuild_index(db=db) == 1
    import sqlite3
    c = sqlite3.connect(db)
    row = c.execute("SELECT pass_n, fail_n, blocked_n FROM runs_index").fetchone()
    assert row[0] == 1 and row[1] == 1 and row[2] == 1
