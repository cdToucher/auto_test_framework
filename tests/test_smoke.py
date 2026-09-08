"""atk smoke 一键命令：plan→run(--record-to)→report→gate 函数复用。"""
import inspect
import re
import subprocess

import pytest
from typer.testing import CliRunner

from atk.cli import app

SCENARIO_OK = (
    "scenario: 登录冒烟\nmodule: demo\npriority: P0\ntags: [smoke]\nenv: local\n"
    "steps:\n  - api:\n      call: \"GET /ping\"\n      expect: {status: 200}\n"
)

SCENARIO_FAIL = (
    "scenario: 登录冒烟\nmodule: demo\npriority: P0\ntags: [smoke]\nenv: local\n"
    "steps:\n  - api:\n      call: \"GET /ping\"\n      expect: {status: 500}\n"
)


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_repo(tmp_path, scenario_text):
    tmp_path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=tmp_path)
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "modules.yaml").write_text('modules:\n  demo: ["src/demo/**"]\n', encoding="utf-8")
    sc = tmp_path / "scenarios" / "demo"
    sc.mkdir(parents=True)
    (sc / "login.yaml").write_text(scenario_text, encoding="utf-8")
    (tmp_path / "reports").mkdir()
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=tmp_path)
    d = tmp_path / "src" / "demo"
    d.mkdir(parents=True)
    (d / "logic.py").write_text("x=1")
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "change demo", cwd=tmp_path)
    return tmp_path


def test_smoke_green_path_run_id_parseable(tmp_path, mock_base_url, monkeypatch):
    repo = _make_repo(tmp_path / "ok", SCENARIO_OK)
    monkeypatch.chdir(repo)
    (repo / "config" / "environments.yaml").write_text(
        f"local:\n  base_url: {mock_base_url}\n", encoding="utf-8"
    )
    r = CliRunner().invoke(
        app,
        ["smoke", "--base", "HEAD~1", "--head", "HEAD", "--env", "local",
         "--title", "登录改造"],
    )
    assert r.exit_code == 0, r.output
    m = re.search(r"run_id: (\S+)", r.output)
    assert m, r.output
    run_id = m.group(1)
    from atk.run_store import load_run

    rec = load_run(run_id, runs_dir=repo / "reports" / "runs")
    assert rec.title == "登录改造"
    assert len(rec.scenarios) == 1 and rec.scenarios[0].passed is True
    assert (repo / "reports" / "runs" / run_id / "report.html").exists()
    assert "门禁结论：放行" in r.output
    assert "待办清单" in r.output


def test_smoke_run_failure_still_reports_and_exits_1(tmp_path, mock_base_url, monkeypatch):
    repo = _make_repo(tmp_path / "fail", SCENARIO_FAIL)
    monkeypatch.chdir(repo)
    (repo / "config" / "environments.yaml").write_text(
        f"local:\n  base_url: {mock_base_url}\n", encoding="utf-8"
    )
    r = CliRunner().invoke(
        app, ["smoke", "--base", "HEAD~1", "--head", "HEAD", "--env", "local"]
    )
    assert r.exit_code == 1, r.output
    m = re.search(r"run_id: (\S+)", r.output)
    assert m, r.output
    run_id = m.group(1)
    # run 失败仍出 report（运行记录报告已渲染）
    assert "报告：" in r.output
    assert (repo / "reports" / "runs" / run_id / "report.html").exists()
    assert "门禁结论：拦截" in r.output
    # 待办清单含失败场景名
    assert "登录冒烟" in r.output
    assert "待办清单" in r.output


def test_smoke_reuses_create_run_without_duplication():
    import atk.cli as cli_mod

    assert hasattr(cli_mod, "smoke"), "atk.cli 需提供 smoke 命令函数"
    src = inspect.getsource(cli_mod.smoke)
    # smoke 必须复用 plan/create_run 逻辑，而非自造 run_id
    assert ("_do_plan" in src) or ("create_run" in src), "smoke 应复用 plan/create_run 逻辑"
    assert "smoke-%Y" not in src and 'run_id=f"' not in src, "smoke 不得重复 run_id 生成代码"
    # 全模块仅一处 run_id 生成（create_run 内），无重复代码
    text = inspect.getsource(cli_mod)
    assert text.count("smoke-%Y") <= 1
