"""A 批修复回归测试：console 注册 / base_url ${env:} / plan exit2 /
smoke 作用域 / review_draft 校验 / 小修边界。"""
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from atk.cli import app
from atk.store.models import Priority

runner = CliRunner()


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


SC_OK = (
    "scenario: 登录冒烟\nmodule: demo\npriority: P0\ntags: [smoke]\nenv: local\n"
    "steps:\n  - api:\n      call: \"GET /ping\"\n      expect: {status: 200}\n"
)


def _make_repo(base, scenario_text=SC_OK, modules='modules:\n  demo: ["src/demo/**"]\n'):
    base.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=base)
    (base / "config").mkdir()
    (base / "config" / "modules.yaml").write_text(modules, encoding="utf-8")
    sc = base / "scenarios" / "demo"
    sc.mkdir(parents=True)
    (sc / "login.yaml").write_text(scenario_text, encoding="utf-8")
    (base / "reports").mkdir()
    _git("add", ".", cwd=base)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=base)
    d = base / "src" / "demo"
    d.mkdir(parents=True)
    (d / "logic.py").write_text("x=1")
    _git("add", ".", cwd=base)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "change demo", cwd=base)
    return base


def _write_env(repo, mock_base_url):
    (repo / "config" / "environments.yaml").write_text(
        f"local:\n  base_url: {mock_base_url}\n", encoding="utf-8"
    )


def _run_id(output):
    m = re.search(r"run_id: (\S+)", output)
    assert m, output
    return m.group(1)


# ---- A1: console 命令必须可用 ----

def test_a1_console_help_available():
    # 必须走子进程复现 `python -m atk.cli console` 入口：main() 在模块
    # 中段执行时 console 若注册在 main() 调用之后即报 No such command。
    # 同进程 CliRunner 因模块已完全加载而测不出此回归。
    import sys
    proc = subprocess.run(
        [sys.executable, "-m", "atk.cli", "console", "--help"],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent,
    )
    assert proc.returncode == 0, proc.stderr
    assert "监听端口" in proc.stdout or "port" in proc.stdout.lower()


def test_a1_command_count_is_12():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0, r.output
    for name in ["validate", "plan", "record", "review", "review-draft", "run",
                 "context", "report", "gate", "smoke", "init", "console"]:
        assert name in r.output, f"缺命令: {name}"


# ---- A2: base_url 支持 ${env:} ----

def test_a2_load_env_base_url_env_ref(tmp_path, monkeypatch):
    from atk.executors.env import load_env
    monkeypatch.setenv("ATK_BASE_URL", "http://ci-host:9999")
    p = tmp_path / "environments.yaml"
    p.write_text("staging:\n  base_url: '${env:ATK_BASE_URL}'\n", encoding="utf-8")
    assert load_env(p, "staging").base_url == "http://ci-host:9999"


def test_a2_load_env_base_url_missing_env_empty(tmp_path, monkeypatch):
    from atk.executors.env import load_env
    monkeypatch.delenv("ATK_NO_SUCH_XYZ", raising=False)
    p = tmp_path / "environments.yaml"
    p.write_text("s:\n  base_url: 'http://${env:ATK_NO_SUCH_XYZ}/api'\n", encoding="utf-8")
    assert load_env(p, "s").base_url == "http:///api"


# ---- A3: plan git 失败 exit 2 ----

def test_a3_plan_git_failure_exits_2(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["plan", "--base", "HEAD~1", "--head", "HEAD",
                            "--repo", str(tmp_path / "no-such-repo")])
    assert r.exit_code == 2, r.output


# ---- A4: smoke 作用域 ----

def _make_tag_priority_repo(base, mock_base_url):
    base.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=base)
    (base / "config").mkdir()
    (base / "config" / "modules.yaml").write_text(
        'modules:\n  demo: ["src/demo/**"]\n', encoding="utf-8"
    )
    (base / "config" / "environments.yaml").write_text(
        f"local:\n  base_url: {mock_base_url}\n", encoding="utf-8"
    )
    sc = base / "scenarios" / "demo"
    sc.mkdir(parents=True)
    (sc / "a.yaml").write_text(
        "scenario: 标签A\nmodule: demo\npriority: P0\ntags: [smoke]\nenv: local\n"
        "steps:\n  - api:\n      call: \"GET /ping\"\n      expect: {status: 200}\n",
        encoding="utf-8",
    )
    (sc / "b.yaml").write_text(
        "scenario: 标签B\nmodule: demo\npriority: P2\ntags: [smoke]\nenv: local\n"
        "steps:\n  - api:\n      call: \"GET /ping\"\n      expect: {status: 200}\n",
        encoding="utf-8",
    )
    (base / "reports").mkdir()
    _git("add", ".", cwd=base)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=base)
    d = base / "src" / "demo"
    d.mkdir(parents=True)
    (d / "logic.py").write_text("x=1")
    _git("add", ".", cwd=base)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "change demo", cwd=base)
    return base


def test_a4_do_plan_priority_filters_reuse(tmp_path, monkeypatch):
    repo = _make_tag_priority_repo(tmp_path / "prio-plan", "http://127.0.0.1:1")
    monkeypatch.chdir(repo)
    from pathlib import Path

    from atk.cli import _do_plan
    rec, affected, _groups, reuse, _errors = _do_plan(
        "HEAD~1", "HEAD", Path("."), Path("scenarios"), Path("config/modules.yaml"),
        ["smoke"], "", Path("reports/runs"), priority=Priority.P0,
    )
    assert affected == ["demo"]
    assert [s.scenario for s in reuse] == ["标签A"]
    assert rec.planned_scenarios == ["scenarios/demo/a.yaml"]


def test_a4_smoke_priority_planned_equals_executed(tmp_path, mock_base_url, monkeypatch):
    repo = _make_tag_priority_repo(tmp_path / "prio-smoke", mock_base_url)
    monkeypatch.chdir(repo)
    r = runner.invoke(app, ["smoke", "--base", "HEAD~1", "--head", "HEAD",
                            "--env", "local", "--priority", "P0"])
    assert r.exit_code == 0, r.output
    from atk.run_store import load_run
    rec = load_run(_run_id(r.output), runs_dir=repo / "reports" / "runs")
    executed = sorted(s.file for s in rec.scenarios)
    assert executed == ["scenarios/demo/a.yaml"]
    assert sorted(rec.planned_scenarios) == executed


def test_a4_smoke_empty_affected_backfills_planned(tmp_path, mock_base_url, monkeypatch):
    repo = _make_repo(tmp_path / "nomod", SC_OK)
    monkeypatch.chdir(repo)
    _write_env(repo, mock_base_url)
    (repo / "README.md").write_text("hello")
    _git("add", ".", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "change readme", cwd=repo)
    r = runner.invoke(app, ["smoke", "--base", "HEAD~1", "--head", "HEAD", "--env", "local"])
    assert r.exit_code == 0, r.output
    from atk.run_store import load_run
    rec = load_run(_run_id(r.output), runs_dir=repo / "reports" / "runs")
    executed = sorted(s.file for s in rec.scenarios)
    assert len(executed) == 1
    assert sorted(rec.planned_scenarios) == executed


def _make_multi_repo(base, mock_base_url):
    base.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=base)
    (base / "config").mkdir()
    (base / "config" / "modules.yaml").write_text(
        'modules:\n  demo: ["src/demo/**"]\n  other: ["src/other/**"]\n', encoding="utf-8"
    )
    (base / "config" / "environments.yaml").write_text(
        f"local:\n  base_url: {mock_base_url}\n", encoding="utf-8"
    )
    for mod, name in (("demo", "demo-冒烟"), ("other", "other-冒烟")):
        sc = base / "scenarios" / mod
        sc.mkdir(parents=True)
        (sc / f"{mod}.yaml").write_text(
            f"scenario: {name}\nmodule: {mod}\npriority: P0\ntags: [smoke]\nenv: local\n"
            "steps:\n  - api:\n      call: \"GET /ping\"\n      expect: {status: 200}\n",
            encoding="utf-8",
        )
    (base / "reports").mkdir()
    _git("add", ".", cwd=base)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=base)
    for mod in ("demo", "other"):
        d = base / "src" / mod
        d.mkdir(parents=True)
        (d / "logic.py").write_text("x=1")
    _git("add", ".", cwd=base)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "change both", cwd=base)
    return base


def test_a4_smoke_multi_module_single_report_split_junit(tmp_path, mock_base_url, monkeypatch):
    repo = _make_multi_repo(tmp_path / "multi", mock_base_url)
    monkeypatch.chdir(repo)
    r = runner.invoke(app, ["smoke", "--base", "HEAD~1", "--head", "HEAD",
                            "--env", "local", "--junit", "reports/junit.xml"])
    assert r.exit_code == 0, r.output
    from atk.run_store import load_run
    rec = load_run(_run_id(r.output), runs_dir=repo / "reports" / "runs")
    assert sorted(s.name for s in rec.scenarios) == ["demo-冒烟", "other-冒烟"]
    # junit 按模块分文件，不再逐轮覆盖同一路径
    assert (repo / "reports" / "junit-demo.xml").exists()
    assert (repo / "reports" / "junit-other.xml").exists()
    assert not (repo / "reports" / "junit.xml").exists()
    # report-latest.html 一次渲染，含两模块结果
    latest = (repo / "reports" / "report-latest.html").read_text(encoding="utf-8")
    assert "demo-冒烟" in latest and "other-冒烟" in latest


def test_a4_smoke_prints_unmapped(tmp_path, mock_base_url, monkeypatch):
    repo = _make_repo(tmp_path / "unmapped", SC_OK)
    monkeypatch.chdir(repo)
    _write_env(repo, mock_base_url)
    (repo / "README.md").write_text("hello")
    _git("add", ".", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "change readme", cwd=repo)
    r = runner.invoke(app, ["smoke", "--base", "HEAD~1", "--head", "HEAD", "--env", "local"])
    assert r.exit_code == 0, r.output
    assert "unmapped: README.md" in r.output


# ---- A5: review_draft 非法 verdict ----

DRAFT = """scenario: 草稿场景
module: demo
priority: P1
tags: [smoke, ai-generated]
steps:
  - api:
      call: "GET /ping"
      expect: {status: 200}
"""


def test_a5_review_draft_invalid_verdict_raises(tmp_path):
    from atk.drafts import review_draft
    f = tmp_path / "gen-x-1.yaml"
    f.write_text(DRAFT, encoding="utf-8")
    with pytest.raises(ValueError):
        review_draft(f, "qa", "bogus", reports_dir=tmp_path / "reports")
    assert f.exists()  # 非法值不得移动文件


# ---- A7: 小修 ----

def test_a7_load_run_empty_file_raises_keyerror(tmp_path):
    from atk.run_store import load_run
    d = tmp_path / "smoke-empty"
    d.mkdir()
    (d / "run.yaml").write_text("", encoding="utf-8")
    with pytest.raises(KeyError):
        load_run("smoke-empty", runs_dir=tmp_path)


def test_a7_create_run_collision_suffix(tmp_path):
    from atk.run_store import create_run
    a = create_run(runs_dir=tmp_path)
    b = create_run(runs_dir=tmp_path)
    assert a.run_id != b.run_id
    assert b.run_id == f"{a.run_id}-2"


def test_a7_is_draft_non_list_tags(tmp_path):
    from atk.drafts import is_draft, review_draft
    f = tmp_path / "s.yaml"
    f.write_text("scenario: x\nmodule: d\nsteps:\n  - api:\n      call: 'GET /p'\n"
                 "      expect: {status: 200}\ntags: ai-generated\n", encoding="utf-8")
    assert is_draft(f) is False
    with pytest.raises(ValueError):
        review_draft(f, "qa", "approve", reports_dir=tmp_path / "reports")


def test_a7_append_log_non_list_tolerated(tmp_path, monkeypatch):
    from atk.drafts import review_draft
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "scenarios" / "demo" / "gen-a-1.yaml"
    f.parent.mkdir(parents=True)
    f.write_text(DRAFT, encoding="utf-8")
    log = tmp_path / "reports" / "draft-reviews.yaml"
    log.parent.mkdir(parents=True)
    log.write_text("{broken: true}\n", encoding="utf-8")  # 非列表旧日志
    r = runner.invoke(app, ["review-draft", str(f), "--by", "qa", "--verdict", "approve"])
    assert r.exit_code == 0, r.output
    items = yaml.safe_load(log.read_text(encoding="utf-8"))
    assert isinstance(items, list) and items[-1]["verdict"] == "approve"
