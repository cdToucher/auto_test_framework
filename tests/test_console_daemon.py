"""后台控制台生命周期：起停、状态文件、就绪探测、进程组的安全边界。

进程组只允许杀我们自己 start_new_session 拉起来的（pgid==pid）；这条不变量
用 foreground 记录（pgid=0）单独测——写错会连跑测试的进程一起杀掉。
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from atk.cli import app
from atk.console import daemon

runner = CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """HOME 指向 tmp：子进程 upsert_project 才不会写真 ~/.atk。"""
    h = tmp_path / "home"
    (h / ".atk").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    # registry.DB_PATH 是 import 期常量，整套跑起来时可能已经指向真实家目录，显式改
    from atk.console import registry
    monkeypatch.setattr(registry, "DB_PATH", h / ".atk" / "registry.db")
    return h


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"
    (p / ".atk" / "scenarios").mkdir(parents=True)
    return p


def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_port_free_and_free_port():
    port = daemon.free_port()
    assert daemon.port_free(port)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))
        s.listen()
        assert not daemon.port_free(port)


def test_launch_status_stop_cycle(home, proj):
    port = daemon.free_port()
    st = daemon.launch(port, False, proj, 30.0)
    try:
        ready = daemon.wait_ready(st["url"], st["pid"], timeout=40.0)
        assert ready, daemon.tail(Path(st["log"]), 50)
        assert daemon.health(st["url"])
        # 地址确实打印出来了（前台看不到，后台就落在这个日志里）
        assert f"地址：http://127.0.0.1:{port}" in Path(st["log"]).read_text(encoding="utf-8")
        assert [s["pid"] for s in daemon.list_states(proj, False)] == [st["pid"]]
        # 真在 listen 的那个端口必须判为已占用（uvicorn 自己也设了 SO_REUSEADDR，
        # 所以这里是对"设了 SO_REUSEADDR 的监听者"的判定，不能放过）
        assert not daemon.port_free(port)
        # 后台进程自成会话：pgid==pid，才允许 killpg
        assert st["pgid"] == st["pid"] == os.getpgid(st["pid"])
    finally:
        ok, msg = daemon.terminate(st)
    assert ok, msg
    assert not daemon.process_alive(st["pid"])
    assert not daemon.state_file(proj, False, port).exists()
    assert daemon.list_states(proj, False) == []
    assert daemon.port_free(port)


def test_state_dir_split_between_modes(home, proj):
    assert daemon.state_dir(proj, False) == proj / ".atk"
    assert daemon.state_dir(proj, True) == home / ".atk"


def test_launch_fails_fast_when_child_dies(home, proj, monkeypatch):
    """子进程起来就崩时不能干等 20s 超时（原来日志进 DEVNULL，连为什么崩都看不到）。"""
    monkeypatch.setattr(daemon, "child_argv",
                        lambda *a, **k: [sys.executable, "-c", "import sys; sys.exit(3)"])
    port = daemon.free_port()
    st = daemon.launch(port, False, proj, 30.0)
    started = time.monotonic()
    assert not daemon.wait_ready(st["url"], st["pid"], timeout=20.0)
    assert time.monotonic() - started < 5
    ok, msg = daemon.terminate(st)
    assert ok, msg
    assert not daemon.state_file(proj, False, port).exists()


def test_stale_state_is_pruned(proj):
    f = daemon.state_file(proj, False, 9)
    f.write_text(json.dumps({"pid": dead_pid(), "pgid": 0, "port": 9,
                             "url": "http://127.0.0.1:9"}), encoding="utf-8")
    assert daemon.read_state(f) is None
    assert not f.exists()
    assert daemon.list_states(proj, False) == []


def test_group_ok_path_kills_whole_group():
    p = subprocess.Popen(["/bin/sh", "-c", "sleep 30 & sleep 30 & wait"],
                         start_new_session=True)
    pgid = os.getpgid(p.pid)
    # sh 派生两个 sleep 需要时间，整套跑起来时直接断言会偶发数不满
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and len(daemon.group_members(pgid)) < 3:
        time.sleep(0.1)
    assert len(daemon.group_members(pgid)) == 3, "父进程 + 两个 sleep 应同组"
    ok, msg = daemon.terminate({"pid": p.pid, "pgid": pgid, "port": 1, "url": "", "state": ""})
    assert ok, msg
    assert daemon.group_members(pgid) == []


def test_pgid_zero_record_never_killpg(home, proj):
    """前台登记的那条 pgid=0，只能杀自己——它和用户终端在同一进程组。"""
    keeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    assert os.getpgid(keeper.pid) == os.getpgid(os.getpid())  # 同组，killpg 会连我们一起杀
    st = {"pid": keeper.pid, "pgid": 0, "port": 1, "url": "", "state": ""}
    ok, msg = daemon.terminate(st)
    assert ok, msg
    assert keeper.wait(timeout=5) < 0  # 被信号结束
    assert daemon.process_alive(os.getpid())  # 我们没被一起带走


def test_serve_prints_address(home, proj, monkeypatch, capsys):
    """这条就是本次要修的：serve 起来必须把地址打到终端（原来 log_level=warning
    把 uvicorn 那行吞了，用户只看到一个光标）。"""
    from atk.console import serve

    seen: dict = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.update(kw))
    serve(port=8977, global_mode=False, project_root=proj)

    out = capsys.readouterr().out
    assert "地址：http://127.0.0.1:8977" in out
    assert "项目 " in out and "停止" in out
    assert seen["host"] == "127.0.0.1"  # 只绑回环
    st = daemon.read_state(daemon.state_file(proj, False, 8977))
    assert st and st["pgid"] == 0  # 前台登记的那条不许 killpg
    assert Path(str(st["root"])) == proj


def test_serve_ignores_state_inherited_from_parent(home, proj, monkeypatch):
    """全局模式下 routes.py 拉起的子控制台会继承父的 STATE_ENV；照单全收的话，
    子进程退出时删的是父控制台的状态文件。"""
    from atk.console import serve

    keeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        parent_state = proj / "parent.json"
        parent_state.write_text(json.dumps({"pid": keeper.pid, "pgid": keeper.pid,
                                            "port": 8977, "url": ""}), encoding="utf-8")
        monkeypatch.setenv(daemon.STATE_ENV, str(parent_state))
        monkeypatch.setattr("uvicorn.run", lambda app, **kw: None)
        serve(port=8977, global_mode=False, project_root=proj)

        assert parent_state.exists()
        assert daemon.state_file(proj, False, 8977).exists()
    finally:
        keeper.terminate()
        keeper.wait(timeout=5)


def test_cli_status_and_stop_when_nothing_runs(home, proj, monkeypatch):
    monkeypatch.chdir(proj)
    res = runner.invoke(app, ["console", "--status"])
    assert res.exit_code == 1
    assert "没有在跑的控制台" in res.output
    res = runner.invoke(app, ["console", "--stop"])
    assert res.exit_code == 0
    res = runner.invoke(app, ["console", "--stop", "--port", "8900"])
    assert res.exit_code == 1
    assert "没有 atk 登记的后台控制台" in res.output


def test_cli_background_then_stop(home, proj, monkeypatch):
    monkeypatch.chdir(proj)
    port = daemon.free_port()
    res = runner.invoke(app, ["console", "--background", "--port", str(port)])
    assert res.exit_code == 0, res.output
    assert f"地址：http://127.0.0.1:{port}" in res.output
    try:
        st = daemon.list_states(proj, False)
        assert len(st) == 1 and st[0]["port"] == port
        res = runner.invoke(app, ["console", "--status"])
        assert res.exit_code == 0
        assert f"http://127.0.0.1:{port}" in res.output
    finally:
        res = runner.invoke(app, ["console", "--stop"])
    assert res.exit_code == 0, res.output
    assert daemon.list_states(proj, False) == []


def test_cli_background_reports_failure_and_cleans_up(home, proj, monkeypatch):
    """端口被人占了就报错退出，不留半截后台进程。"""
    monkeypatch.chdir(proj)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        res = runner.invoke(app, ["console", "--background", "--port", str(port)])
    assert res.exit_code == 2
    assert "已被占用" in res.output
    assert daemon.list_states(proj, False) == []
