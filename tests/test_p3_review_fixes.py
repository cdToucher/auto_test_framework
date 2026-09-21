"""评审修复项回归：空选拦截 / dry-run / gate 强化 / 变量健壮性 / record 约束。"""
import subprocess
import sys
import time

import pytest
from typer.testing import CliRunner

from atk.cli import app

runner = CliRunner()

PASS_SCEN = (
    "scenario: 通过场景\nmodule: demo\npriority: P0\ntags: [smoke]\nenv: local\n"
    "steps:\n  - api:\n      call: \"GET /ping\"\n      expect: {status: 200}\n"
)


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, mock_base_url):
    _git("init", "-q", cwd=tmp_path)
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "modules.yaml").write_text(
        'modules:\n  demo: ["src/demo/**"]\n', encoding="utf-8"
    )
    (cfg / "environments.yaml").write_text(
        f"local:\n  base_url: {mock_base_url}\n", encoding="utf-8"
    )
    sc = tmp_path / "scenarios" / "demo"
    sc.mkdir(parents=True)
    (sc / "login.yaml").write_text(PASS_SCEN, encoding="utf-8")
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=tmp_path)
    d = tmp_path / "src" / "demo"
    d.mkdir(parents=True)
    (d / "logic.py").write_text("x=1", encoding="utf-8")
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "change demo", cwd=tmp_path)
    return tmp_path


def _runs_dir(repo):
    return repo / "reports" / "runs"


# ---------- A1/A2：空选不算绿、dry-run ----------

def test_run_empty_selection_is_blocked(repo, monkeypatch):
    monkeypatch.chdir(repo)
    r = runner.invoke(app, ["run", "--module", "不存在的模块"])
    assert r.exit_code == 2, r.output
    assert "选中 0 个场景" in r.output
    ok = runner.invoke(app, ["run", "--module", "不存在的模块", "--allow-empty"])
    assert ok.exit_code == 0, ok.output


def test_dry_run_lists_without_executing(repo, monkeypatch):
    monkeypatch.chdir(repo)
    r = runner.invoke(app, ["run", "--dry-run", "--module", "demo"])
    assert r.exit_code == 0, r.output
    assert "通过场景" in r.output and "未执行" in r.output
    assert not (repo / "reports" / "report-latest.html").exists()


# ---------- A 组：gate 空记录 / head SHA 漂移 ----------

def test_gate_blocks_empty_record(repo, monkeypatch):
    from atk.run_store import create_run

    monkeypatch.chdir(repo)
    rec = create_run(runs_dir=_runs_dir(repo))
    r = runner.invoke(app, ["gate", rec.run_id])
    assert r.exit_code == 1, r.output
    assert "无场景结果也无意图" in r.output


def test_gate_detects_head_sha_drift(repo, monkeypatch):
    import re

    monkeypatch.chdir(repo)
    p = runner.invoke(app, ["plan", "--base", "HEAD~1", "--head", "HEAD"])
    assert p.exit_code == 0, p.output
    run_id = re.search(r"run_id: (\S+)", p.output).group(1)
    # 新提交只改同一文件：文件清单不变（绕过旧检查），但 head SHA 前进
    (repo / "src" / "demo" / "logic.py").write_text("x=2", encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "amend-like", cwd=repo)
    g = runner.invoke(app, ["gate", run_id])
    assert g.exit_code == 1, g.output
    assert "head 已前进" in g.output


# ---------- A3：草稿运行时刻判定 ----------

def test_draft_reject_after_run_still_blocks(repo, monkeypatch):
    from atk.run_store import create_run, load_run

    monkeypatch.chdir(repo)
    draft = repo / "scenarios" / "demo" / "gen-abc-1.yaml"
    draft.write_text(
        PASS_SCEN.replace("tags: [smoke]", "tags: [smoke, ai-generated]")
        .replace("通过场景", "AI草稿场景"),
        encoding="utf-8",
    )
    rec = create_run(runs_dir=_runs_dir(repo))
    r = runner.invoke(app, ["run", "--module", "demo", "--record-to", rec.run_id])
    assert r.exit_code == 0, r.output
    got = load_run(rec.run_id, runs_dir=_runs_dir(repo))
    assert any(s.draft for s in got.scenarios), "summarize 应记录运行时草稿标记"
    # reject 移走文件后，gate 仍应拦截（旧实现会被绕过）
    rej = runner.invoke(
        app, ["review-draft", str(draft), "--by", "qa", "--verdict", "reject", "--note", "断言不实"]
    )
    assert rej.exit_code == 0, rej.output
    assert not draft.exists()
    g = runner.invoke(app, ["gate", rec.run_id])
    assert g.exit_code == 1, g.output
    assert "未评审AI草稿" in g.output


# ---------- A5/A6：record-to 幂等、record 必须显式 status ----------

def test_record_to_upserts_same_scenario(repo, monkeypatch):
    from atk.run_store import create_run, load_run

    monkeypatch.chdir(repo)
    rec = create_run(runs_dir=_runs_dir(repo))
    for _ in range(2):
        r = runner.invoke(app, ["run", "--module", "demo", "--record-to", rec.run_id])
        assert r.exit_code == 0, r.output
    got = load_run(rec.run_id, runs_dir=_runs_dir(repo))
    assert len(got.scenarios) == 1, "重跑应覆盖旧结果而非重复追加"


def test_record_requires_explicit_status(repo, monkeypatch):
    from atk.run_store import create_run

    monkeypatch.chdir(repo)
    rec = create_run(runs_dir=_runs_dir(repo))
    r = runner.invoke(app, ["record", rec.run_id, "--title", "某意图"])
    assert r.exit_code == 2, r.output
    assert "缺少 --status" in r.output


# ---------- B：配置缺失友好报错、变量健壮性 ----------

def test_missing_env_file_friendly_error(repo, monkeypatch):
    monkeypatch.chdir(repo)
    r = runner.invoke(app, ["run", "--module", "demo", "--env-file", "nope.yaml"])
    assert r.exit_code == 2, r.output
    assert "配置文件不存在" in r.output
    assert "Traceback" not in r.output and "FileNotFoundError" not in r.output


def test_unresolved_variable_config_error(tmp_path, mock_base_url, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "environments.yaml").write_text(
        f"local:\n  base_url: {mock_base_url}\n", encoding="utf-8"
    )
    sc = tmp_path / "scenarios"
    sc.mkdir()
    (sc / "bad.yaml").write_text(
        'scenario: 未解析变量\nmodule: m\nenv: local\nsteps:\n'
        '  - api:\n      call: "GET /ping?x=${nope}"\n      expect: {status: 200}\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["run"])
    assert r.exit_code == 1, r.output
    assert "变量未解析" in r.output and "${nope}" in r.output


def test_missing_env_var_warns(tmp_path, mock_base_url, monkeypatch):
    monkeypatch.delenv("ATK_DEFINITELY_MISSING", raising=False)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "environments.yaml").write_text(
        f"local:\n  base_url: {mock_base_url}\n"
        "  vars:\n    tok: \"${env:ATK_DEFINITELY_MISSING}\"\n",
        encoding="utf-8",
    )
    sc = tmp_path / "scenarios"
    sc.mkdir()
    (sc / "ok.yaml").write_text(PASS_SCEN.replace("module: demo", "module: m"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["run"])
    assert r.exit_code == 0, r.output
    assert "环境变量未设置" in r.output and "ATK_DEFINITELY_MISSING" in r.output


# ---------- A2：module 缺省按一级目录兜底 ----------

def test_module_dir_fallback_and_validate_warning(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    sc = tmp_path / "scenarios" / "pay"
    sc.mkdir(parents=True)
    (sc / "a.yaml").write_text(
        'scenario: 无module字段\npriority: P0\ntags: [smoke]\nenv: local\n'
        'steps:\n  - api:\n      call: "GET /ping"\n      expect: {status: 200}\n',
        encoding="utf-8",
    )
    (sc / "b.yaml").write_text(
        'scenario: 字段与目录不一致\nmodule: zzz\nenv: local\n'
        'steps:\n  - api:\n      call: "GET /ping"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    v = runner.invoke(app, ["validate"])
    assert v.exit_code == 0, v.output
    assert "module 与目录不一致" in v.output
    d = runner.invoke(app, ["run", "--dry-run", "--module", "pay"])
    assert d.exit_code == 0, d.output
    assert "无module字段" in d.output and "字段与目录不一致" not in d.output


# ---------- console：job 状态路由 ----------

def test_console_job_status_route(tmp_path):
    from fastapi.testclient import TestClient

    from atk.console import create_app

    client = TestClient(create_app(project_root=tmp_path))
    jid = client.app.state.jobs.start([sys.executable, "-c", "pass"])
    for _ in range(50):
        s = client.get(f"/api/jobs/{jid}")
        assert s.status_code == 200
        if s.json()["done"]:
            break
        time.sleep(0.05)
    assert s.json()["done"] and s.json()["exit_code"] == 0
    assert client.get("/api/jobs/does-not-exist").status_code == 404
