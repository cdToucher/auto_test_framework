"""atk smoke 一键命令：plan→run(按模块)→report→gate 函数复用。"""
import re
import subprocess

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


def _write_env(repo, mock_base_url):
    (repo / "config" / "environments.yaml").write_text(
        f"local:\n  base_url: {mock_base_url}\n", encoding="utf-8"
    )


def _run_id(output):
    m = re.search(r"run_id: (\S+)", output)
    assert m, output
    return m.group(1)


def _assert_todo_three_lines(output, run_id):
    assert "待办清单" in output
    assert "失败场景" in output
    assert ("无覆盖意图" in output) or ("意图回填" in output)
    assert "草稿评审" in output
    assert run_id in output


def test_smoke_green_path_run_id_parseable(tmp_path, mock_base_url, monkeypatch):
    repo = _make_repo(tmp_path / "ok", SCENARIO_OK)
    monkeypatch.chdir(repo)
    _write_env(repo, mock_base_url)
    r = CliRunner().invoke(
        app,
        ["smoke", "--base", "HEAD~1", "--head", "HEAD", "--env", "local",
         "--title", "登录改造"],
    )
    assert r.exit_code == 0, r.output
    run_id = _run_id(r.output)
    from atk.run_store import load_run

    rec = load_run(run_id, runs_dir=repo / "reports" / "runs")
    assert rec.title == "登录改造"
    assert len(rec.scenarios) == 1 and rec.scenarios[0].passed is True
    assert (repo / "reports" / "runs" / run_id / "report.html").exists()
    assert "门禁结论：放行" in r.output
    _assert_todo_three_lines(r.output, run_id)


def test_smoke_run_failure_still_reports_and_exits_1(tmp_path, mock_base_url, monkeypatch):
    repo = _make_repo(tmp_path / "fail", SCENARIO_FAIL)
    monkeypatch.chdir(repo)
    _write_env(repo, mock_base_url)
    r = CliRunner().invoke(
        app, ["smoke", "--base", "HEAD~1", "--head", "HEAD", "--env", "local"]
    )
    assert r.exit_code == 1, r.output
    run_id = _run_id(r.output)
    # run 失败仍出 report（运行记录报告已渲染）
    assert "报告：" in r.output
    assert (repo / "reports" / "runs" / run_id / "report.html").exists()
    assert "门禁结论：拦截" in r.output
    # 待办清单含失败场景名
    assert "登录冒烟" in r.output
    _assert_todo_three_lines(r.output, run_id)


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
    sc_demo = base / "scenarios" / "demo"
    sc_other = base / "scenarios" / "other"
    sc_demo.mkdir(parents=True)
    sc_other.mkdir(parents=True)
    (sc_demo / "login.yaml").write_text(
        "scenario: demo-冒烟\nmodule: demo\npriority: P0\ntags: [smoke]\nenv: local\n"
        "steps:\n  - api:\n      call: \"GET /ping\"\n      expect: {status: 200}\n",
        encoding="utf-8",
    )
    (sc_other / "other.yaml").write_text(
        "scenario: other-冒烟\nmodule: other\npriority: P0\ntags: [smoke]\nenv: local\n"
        "steps:\n  - api:\n      call: \"GET /ping\"\n      expect: {status: 200}\n",
        encoding="utf-8",
    )
    (base / "reports").mkdir()
    _git("add", ".", cwd=base)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=base)
    return base


def test_smoke_runs_only_affected_module(tmp_path, mock_base_url, monkeypatch):
    repo = _make_multi_repo(tmp_path / "multi", mock_base_url)
    monkeypatch.chdir(repo)
    d = repo / "src" / "demo"
    d.mkdir(parents=True)
    (d / "logic.py").write_text("x=1")
    _git("add", ".", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "change demo", cwd=repo)
    r = CliRunner().invoke(
        app, ["smoke", "--base", "HEAD~1", "--head", "HEAD", "--env", "local"]
    )
    assert r.exit_code == 0, r.output
    run_id = _run_id(r.output)
    from atk.run_store import load_run

    rec = load_run(run_id, runs_dir=repo / "reports" / "runs")
    assert rec.affected_modules == ["demo"]
    assert len(rec.scenarios) == 1, rec.scenarios
    assert rec.scenarios[0].module == "demo"
    assert rec.scenarios[0].name == "demo-冒烟"
    assert "other-冒烟" not in r.output
    assert "demo-冒烟" in r.output
    _assert_todo_three_lines(r.output, run_id)


def test_smoke_env_missing_exits_2_and_skips_gate(tmp_path, mock_base_url, monkeypatch):
    repo = _make_repo(tmp_path / "envmiss", SCENARIO_OK)
    monkeypatch.chdir(repo)
    _write_env(repo, mock_base_url)
    r = CliRunner().invoke(
        app, ["smoke", "--base", "HEAD~1", "--head", "HEAD", "--env", "no-such-env"]
    )
    assert r.exit_code == 2, r.output
    run_id = _run_id(r.output)
    assert "环境配置错误" in r.output
    # run 异常仍渲染 report，但跳过 gate，避免“放行”误读
    assert "报告：" in r.output
    assert (repo / "reports" / "runs" / run_id / "report.html").exists()
    assert "门禁已跳过" in r.output
    assert "门禁结论：放行" not in r.output
    _assert_todo_three_lines(r.output, run_id)


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
        "scenario: 标签B\nmodule: demo\npriority: P2\ntags: [other]\nenv: local\n"
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


def test_smoke_tags_passthrough(tmp_path, mock_base_url, monkeypatch):
    repo = _make_tag_priority_repo(tmp_path / "tags", mock_base_url)
    monkeypatch.chdir(repo)
    r = CliRunner().invoke(
        app, ["smoke", "--base", "HEAD~1", "--head", "HEAD", "--env", "local",
              "--tags", "smoke"]
    )
    assert r.exit_code == 0, r.output
    run_id = _run_id(r.output)
    from atk.run_store import load_run

    rec = load_run(run_id, runs_dir=repo / "reports" / "runs")
    names = [s.name for s in rec.scenarios]
    assert names == ["标签A"], names
    assert "标签B" not in r.output


def test_smoke_priority_passthrough(tmp_path, mock_base_url, monkeypatch):
    repo = _make_tag_priority_repo(tmp_path / "prio", mock_base_url)
    monkeypatch.chdir(repo)
    r = CliRunner().invoke(
        app, ["smoke", "--base", "HEAD~1", "--head", "HEAD", "--env", "local",
              "--priority", "P0"]
    )
    assert r.exit_code == 0, r.output
    run_id = _run_id(r.output)
    from atk.run_store import load_run

    rec = load_run(run_id, runs_dir=repo / "reports" / "runs")
    names = [s.name for s in rec.scenarios]
    assert names == ["标签A"], names


def test_smoke_no_module_hit_runs_all_and_notes(tmp_path, mock_base_url, monkeypatch):
    repo = _make_repo(tmp_path / "nomod", SCENARIO_OK)
    monkeypatch.chdir(repo)
    _write_env(repo, mock_base_url)
    # 改动落在模块映射之外，affected 为空
    (repo / "README.md").write_text("hello")
    _git("add", ".", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "change readme", cwd=repo)
    r = CliRunner().invoke(
        app, ["smoke", "--base", "HEAD~1", "--head", "HEAD", "--env", "local"]
    )
    assert r.exit_code == 0, r.output
    run_id = _run_id(r.output)
    assert "无模块命中，已全量执行" in r.output
    from atk.run_store import load_run

    rec = load_run(run_id, runs_dir=repo / "reports" / "runs")
    assert len(rec.scenarios) == 1
    _assert_todo_three_lines(r.output, run_id)


def test_smoke_plan_run_reuse_consistent_single_record(tmp_path, mock_base_url, monkeypatch):
    """行为断言替代源码字符串检查：复用清单与执行结果一致，且只建一条记录。"""
    repo = _make_repo(tmp_path / "reuse", SCENARIO_OK)
    monkeypatch.chdir(repo)
    _write_env(repo, mock_base_url)
    runs_dir = repo / "reports" / "runs"
    before = set(p.name for p in runs_dir.iterdir()) if runs_dir.exists() else set()
    r = CliRunner().invoke(
        app, ["smoke", "--base", "HEAD~1", "--head", "HEAD", "--env", "local"]
    )
    assert r.exit_code == 0, r.output
    run_id = _run_id(r.output)
    from atk.run_store import load_run

    rec = load_run(run_id, runs_dir=runs_dir)
    # plan 复用清单 == 实际并入场景文件（无脱节）
    executed_files = sorted(s.file for s in rec.scenarios)
    assert sorted(rec.planned_scenarios) == executed_files
    assert len(executed_files) == 1
    # smoke 只新建一条运行记录（无重复 run_id 生成）
    after = set(p.name for p in runs_dir.iterdir() if p.is_dir())
    assert after - before == {run_id}
    reuse_lines = [ln for ln in r.output.splitlines() if ln.startswith("reuse:")]
    assert len(reuse_lines) == len(rec.scenarios)
