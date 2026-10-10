"""atk 测 atk：把框架自己当成被测系统跑一遍真实冒烟。

此前 `scenarios/` 里没有任何一条场景描述 atk 自己的行为，自举只到 Python 测试层。
控制台是 atk 唯一能被 HTTP 观测的自身表面，所以这里起一个真控制台、用真 CLI 跑
`scenarios/atk_self/`，而不是在进程内调函数。
"""
import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from typer.testing import CliRunner

from atk import layout
from atk.cli import app
from atk.console import create_app

cli = CliRunner()


@pytest.fixture(scope="module", autouse=True)
def _real_project_untouched():
    """哨兵：本模块的自举用例跑完后，真实仓库的运行状态必须一个字节都没动。

    自举的意义是"拿 atk 自己的场景库测 atk"，不是"拿开发者的工作区当草稿纸"。
    已实证的污染：跑一遍套件，`.atk/last-run.json` 被指向一条测试造的记录，
    该记录 base_ref 为空——开发者随后敲 atk gate 必被 _unbound_record 拦，
    atk agent 报 gate_blocking 且 next 指向自己（死环）。
    """
    repo = Path(__file__).resolve().parents[1]
    watched_files = [repo / ".atk" / "last-run.json", repo / ".atk" / "deps.yaml"]
    watched_dirs = [repo / "reports" / "runs", repo / "scenarios"]

    def snap():
        return (
            [p.read_bytes() for p in watched_files],
            [sorted(str(q.relative_to(d)) for q in d.rglob("*")) for d in watched_dirs],
        )

    before = snap()
    yield
    after = snap()
    assert before == after, (
        "自举用例改动了真实仓库状态（.atk/last-run.json / .atk/deps.yaml / "
        "reports/runs / scenarios 之一变了）：自举必须跑在隔离的临时项目里")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def dogfood_project(tmp_path, monkeypatch):
    """隔离的临时项目：场景库与环境配置从真实仓库拷来，之后全程在 tmp 里跑。

    自举要测的是"atk 跑真实的场景文件"，不是"atk 改写开发者的工作区"：
    last-run 指针、reports/runs、.atk/deps.yaml 一旦落进真实仓库，开发者
    随后敲的 atk last / atk gate / atk agent 读到的就是测试留下的残骸。
    """
    import shutil

    repo = Path(__file__).resolve().parents[1]
    shutil.copytree(repo / "scenarios", tmp_path / "scenarios")
    (tmp_path / "config").mkdir()
    shutil.copy2(repo / "config" / "environments.yaml",
                 tmp_path / "config" / "environments.yaml")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def console(dogfood_project, monkeypatch):
    """真起一个 uvicorn（127.0.0.1 随机端口，服务根指向隔离项目），等 /api/health 通了再交给我。"""
    from pathlib import Path

    root = dogfood_project
    port = _free_port()
    server_app = create_app(project_root=root)
    config = uvicorn.Config(server_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    import httpx

    deadline = time.time() + 25
    ready = False
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/api/health", timeout=1.0).status_code == 200:
                ready = True
                break
        except Exception:
            time.sleep(0.1)
    if not ready:      # 起不来就是失败，不 skip：skip 会把这个表面永久遮成"没测"
        raise AssertionError("atk 控制台未能启动，自举冒烟无法进行")
    monkeypatch.setenv("ATK_SELF_BASE_URL", base)
    try:
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_atk_self_scenarios_exist_and_are_selectable(console):
    """自举场景能被 atk 自己按模块选中——不是只在进程内调函数。"""
    res = cli.invoke(app, ["run", "--dry-run", "--module", "atkself"])
    assert res.exit_code == 0, res.output
    assert "控制台健康检查可用" in res.output


def test_atk_run_passes_against_its_own_console(console):
    res = cli.invoke(app, ["run", "--module", "atkself", "--format", "json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["scenarios"]["total"] == 2, payload["scenarios"]
    assert payload["scenarios"]["failed"] == 0, payload.get("failed_steps")


def test_console_reports_the_run_it_just_observed(console):
    """控制台的历史视图必须能看见 atk 刚跑出来的记录——两个表面得看同一份事实源。"""
    run = cli.invoke(app, ["run", "--module", "atkself", "--record-new", "--format", "json"])
    assert run.exit_code == 0, run.output
    rid = json.loads(run.stdout)["run_id"]
    import httpx

    body = httpx.get(f"{console}/api/runs", timeout=5.0).json()
    ids = [r.get("run_id") for r in (body if isinstance(body, list) else body.get("runs", []))]
    assert rid in ids, f"刚跑的记录没出现在控制台历史里：{rid} not in {ids}"


def test_a_broken_scenario_is_visible_as_a_failure_not_a_crash(console, dogfood_project, monkeypatch):
    """写坏断言时必须是"用例失败 exit 1"，而不是把执行器打崩。"""
    from pathlib import Path

    bad = Path(layout.scenarios_dir(dogfood_project)) / "atk_self" / "_tmp_bad.yaml"
    bad.write_text(
        "scenario: 自举-故意断错\nmodule: atkself\npriority: P0\nenv: atkself\n"
        'steps:\n  - api:\n      call: "GET /api/health"\n'
        "      expect:\n        status: 200\n        ok: 绝不存在的取值\n",
        encoding="utf-8")
    try:
        res = cli.invoke(app, ["run", "--module", "atkself", "--format", "json"])
        assert res.exit_code == 1, (res.exit_code, res.output)
        payload = json.loads(res.stdout)
        assert payload["scenarios"]["failed"] == 1
        detail = json.dumps(payload.get("failed_steps"), ensure_ascii=False)
        assert "自举-故意断错" in detail and "ok" in detail
    finally:
        bad.unlink(missing_ok=True)
