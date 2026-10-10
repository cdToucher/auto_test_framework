"""atk smoke 一键命令：plan→run(按模块)→report→gate 函数复用。"""
import json
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


def test_smoke_dry_run_only_lists_and_never_executes(tmp_path, mock_base_url,
                                                     monkeypatch, approve_library):
    """--dry-run 只列会跑什么：不建运行记录、不发请求、不碰 gate。

    smoke 与 run 一样需要"先看清会跑什么再真跑"；此前 smoke 只有真跑一条路，
    想看选中了什么得先跑一遍（顺带建一条记录）。
    """
    repo = _make_multi_repo(tmp_path / "dry", mock_base_url)
    approve_library(repo)
    monkeypatch.chdir(repo)
    r = CliRunner().invoke(app, ["smoke", "--base", "HEAD", "--head", "HEAD",
                                 "--dry-run", "--format", "json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.stdout)
    assert payload["dry_run"] is True
    assert payload["run_id"] is None, "预演不应创建运行记录"
    selected = [s["scenario"] for s in payload["selected"]]
    assert selected, payload
    assert not (repo / "reports" / "runs").exists() or not any(
        (repo / "reports" / "runs").iterdir())


def test_smoke_module_scopes_execution(tmp_path, mock_base_url, monkeypatch,
                                       approve_library):
    """--module 只跑指定模块：smoke 与 run 的过滤口径一致。

    多模块库里只关心一个模块时，此前只能整库跑或自己挑 tags。
    """
    repo = _make_multi_repo(tmp_path / "mod", mock_base_url)
    approve_library(repo)
    monkeypatch.chdir(repo)
    r = CliRunner().invoke(app, ["smoke", "--base", "HEAD", "--head", "HEAD",
                                 "--env", "local", "--module", "demo", "--format", "json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.stdout)
    assert payload["scenarios"]["total"] == 1, payload["scenarios"]
    from atk.run_store import load_run

    rec = load_run(payload["run_id"], runs_dir=repo / "reports" / "runs")
    assert [s.name for s in rec.scenarios] == ["demo-冒烟"], rec.scenarios
    # 计划与执行一致，否则 gate 的 deps_stale 会拦"计划了没跑"
    assert sorted(rec.planned_scenarios) == sorted(s.file for s in rec.scenarios)
def test_smoke_green_path_run_id_parseable(tmp_path, mock_base_url, monkeypatch,
                                            approve_library):
    repo = _make_repo(tmp_path / "ok", SCENARIO_OK)
    approve_library(repo)
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
        "steps:\n  - api:\n      call: \"GET /api/orders\"\n      expect: {status: 200}\n",
        encoding="utf-8",
    )
    (base / "reports").mkdir()
    _git("add", ".", cwd=base)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=base)
    return base


def test_smoke_selection_is_conservative_once_then_scoped(tmp_path, mock_base_url, monkeypatch,
                                                     approve_library):
    """无观测条目的场景先跑一次（testmon 兜底），建立账本后才按影响面子集排除。

    两条都要测：只测"排除"会放过一个静默漏测的洞——从没跑过的场景没有任何证据
    说明它与本次改动无关。
    """
    repo = _make_multi_repo(tmp_path / "multi", mock_base_url)
    approve_library(repo)
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
    from atk.deps import ledger_path, load_ledger

    led = load_ledger(ledger_path(repo))
    assert len(led) == 2, f"首轮应把两条场景都跑掉并建立账本，实际 {led}"
    assert "other-冒烟" in r.output, "没有观测条目时不许把另一模块排除掉"

    # 再来一次同样的改动：账本已有条目、other 与本次影响面无关 → 只跑 demo
    # 只 add 被测代码：把 atk 自己的 reports/*.yaml 提交进去会被判成"未映射的代码变更"，
    # 于是全量兜底先触发，测不到子集选择这一支（真实项目由 atk init 写好的 .gitignore 隔开）
    (d / "logic.py").write_text("x=2")
    _git("add", "src/demo/logic.py", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "change demo again",
         cwd=repo)
    r2 = CliRunner().invoke(
        app, ["smoke", "--base", "HEAD~1", "--head", "HEAD", "--env", "local"]
    )
    assert r2.exit_code == 0, r2.output
    run_id = _run_id(r2.output)
    from atk.run_store import load_run

    rec = load_run(run_id, runs_dir=repo / "reports" / "runs")
    assert [s.module for s in rec.scenarios] == ["demo"], rec.scenarios
    assert rec.affected_modules == ["demo"]
    assert "other-冒烟" not in r2.output
    assert "demo-冒烟" in r2.output
    _assert_todo_three_lines(r2.output, run_id)


def test_smoke_never_selects_zero_when_map_is_missing(tmp_path, mock_base_url, monkeypatch,
                                                     approve_library):
    """映射表被删 = 影响面算不清 = 跑全库，绝不能"选 0 个然后按通过算"。"""
    repo = _make_multi_repo(tmp_path / "nomap", mock_base_url)
    approve_library(repo)
    (repo / "config" / "modules.yaml").unlink()
    _git("add", "-A", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "drop map", cwd=repo)
    monkeypatch.chdir(repo)
    r = CliRunner().invoke(
        app, ["smoke", "--base", "HEAD~1", "--head", "HEAD", "--env", "local",
              "--format", "json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["scenarios"]["total"] == 2, payload["scenarios"]


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


def test_smoke_tags_passthrough(tmp_path, mock_base_url, monkeypatch, approve_library):
    repo = _make_tag_priority_repo(tmp_path / "tags", mock_base_url)
    approve_library(repo)
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


def test_smoke_priority_passthrough(tmp_path, mock_base_url, monkeypatch, approve_library):
    repo = _make_tag_priority_repo(tmp_path / "prio", mock_base_url)
    approve_library(repo)
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


def test_smoke_no_module_hit_runs_all_and_notes(tmp_path, mock_base_url, monkeypatch,
                                              approve_library):
    repo = _make_repo(tmp_path / "nomod", SCENARIO_OK)
    approve_library(repo)
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


def test_smoke_plan_run_reuse_consistent_single_record(tmp_path, mock_base_url, monkeypatch,
                                                    approve_library):
    """行为断言替代源码字符串检查：复用清单与执行结果一致，且只建一条记录。"""
    repo = _make_repo(tmp_path / "reuse", SCENARIO_OK)
    approve_library(repo)
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
