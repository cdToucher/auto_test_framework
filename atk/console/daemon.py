"""后台控制台生命周期：状态/日志落盘、就绪探测、按进程组停止。

进程组是这里的关键。全局模式下 routes.py 为每个注册项目 Popen 一个独立子控制台，
原来它的 stdout/stderr 进 DEVNULL 且无人回收：父进程一退，子进程就被 launchd 收养
（PPID=1）继续占端口，卸包、排查都发现不了（实测留过 12 天的孤儿）。现在子控制台
自己落日志、自己登记状态，而后台启动的控制台一律 start_new_session 自成会话，
子控制台继承同一进程组，stop 时 killpg 一次全部带走。

状态文件按端口分文件（同时可能有多个控制台在跑）。后台 launcher 写的那条带 pgid；
前台和被"打开"拉起的子控制台自己登记一条 pgid=0 的，好让 --status 看得见它们。
pgid==pid 是"它是我们自己 start_new_session 拉起来的"这一不变量，
stop 据此才敢用 killpg —— 否则会连用户终端的进程组一起杀掉。
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

HOST = "127.0.0.1"
ATK_DIR = ".atk"
# 传给子进程，让它在 atexit 时自己收走状态文件（被 kill -9 时留下 stale 记录，
# 由 status/stop 用进程存活检查兜掉）
STATE_ENV = "ATK_CONSOLE_STATE"


def url_of(port: int) -> str:
    return f"http://{HOST}:{port}"


def state_dir(root: Path, global_mode: bool) -> Path:
    """全局模式记在 ~/.atk，项目模式记在 <root>/.atk（与 last-run.json 同处）。"""
    return Path.home() / ATK_DIR if global_mode else Path(root) / ATK_DIR


def state_file(root: Path, global_mode: bool, port: int) -> Path:
    return state_dir(root, global_mode) / f"console-{port}.json"


def log_file(root: Path, global_mode: bool, port: int) -> Path:
    return state_dir(root, global_mode) / f"console-{port}.log"


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # SO_REUSEADDR 与 uvicorn 的绑定条件对齐：只关心"有没有人在 listen"，
        # 别把上一次会话残留的 TIME_WAIT 误报成端口被占（SO_REUSEADDR 抢不走
        # 正在 listen 的端口，所以判断仍然成立）。
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((HOST, port))
        except OSError:
            return False
    return True


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # 别人的进程：活着，但不是我们能碰的
        return True
    # kill(pid,0) 对"已退出但没人收尸"的僵尸仍然成功，会被误判成杀不掉，
    # 进而升级到 SIGKILL（对僵尸同样无效）并报错误。
    return not _is_zombie(pid)


def _is_zombie(pid: int) -> bool:
    try:
        state = subprocess.run(["ps", "-o", "state=", "-p", str(pid)],
                               capture_output=True, text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return state.startswith("Z")


def health(url: str, timeout: float = 1.0) -> bool:
    try:
        with urlopen(f"{url}/api/health", timeout=timeout) as resp:
            return json.load(resp).get("ok") is True
    except Exception:
        return False


def wait_ready(url: str, pid: int | None = None, timeout: float = 20.0) -> bool:
    """探活。给了 pid 就在进程先退出时立刻失败，不必干等满超时。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pid is not None and not process_alive(pid):
            return False
        if health(url):
            return True
        time.sleep(0.2)
    return False


def read_state(path: Path) -> dict | None:
    """读状态并顺手兜掉 stale 记录（进程已没但文件还在）。"""
    try:
        st = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not st.get("port"):
        return None
    if not process_alive(int(st.get("pid") or 0)):
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return st


def state_owner(path: Path) -> int | None:
    """状态文件记录的、当前仍存活的 pid；stale 或缺失返回 None。"""
    st = read_state(Path(path))
    return int(st["pid"]) if st else None


def list_states(root: Path, global_mode: bool) -> list[dict]:
    out = []
    for f in sorted(state_dir(root, global_mode).glob("console-*.json")):
        st = read_state(f)
        if st:
            out.append(st)
    return out


def tail(path: Path, lines: int = 20) -> str:
    try:
        body = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(body[-lines:])


def child_argv(port: int, global_mode: bool, root: Path, job_timeout: float) -> list[str]:
    argv = [sys.executable, "-m", "atk", "console", "--port", str(port),
            "--project-root", str(root), "--job-timeout", str(job_timeout)]
    if global_mode:
        argv.append("--global")
    return argv


def child_env(state_path: Path) -> dict[str, str]:
    """子进程环境：带上状态文件位置，并把 atk 包所在目录塞进 PYTHONPATH。

    `-m atk` 在 cwd 非仓库根时依赖已安装；wheel 安装时 parents[2] 就是 site-packages
    （本来就在 path 里），所以两种情况都只是保底，不会遮蔽已安装版本。
    """
    pkg_parent = str(Path(__file__).resolve().parents[2])
    old = os.environ.get("PYTHONPATH", "")
    return {**os.environ,
            STATE_ENV: str(state_path),
            "PYTHONPATH": f"{pkg_parent}{os.pathsep}{old}" if old else pkg_parent}


def launch(port: int, global_mode: bool, root: Path, job_timeout: float) -> dict:
    """脱离终端起一个控制台，写好状态文件后立即返回（不等就绪）。"""
    d = state_dir(root, global_mode)
    d.mkdir(parents=True, exist_ok=True)
    sf = state_file(root, global_mode, port)
    lf = log_file(root, global_mode, port)
    argv = child_argv(port, global_mode, root, job_timeout)

    with lf.open("ab") as log:
        proc = subprocess.Popen(
            argv,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(root),
            env=child_env(sf),
            start_new_session=True,  # 自成会话，子控制台同组，见模块 docstring
        )
    state = {
        "pid": proc.pid,
        # 不在父进程里读 os.getpgid：子进程的 setsid 与父进程的读取有竞态，
        # 早读一步拿到的就是父进程自己的组，stop 时会把调用方整组杀掉。
        # start_new_session 保证新组 id 就等于子进程 pid。
        "pgid": proc.pid,
        "port": port,
        "url": url_of(port),
        "global": bool(global_mode),
        "root": None if global_mode else str(root),
        "log": str(lf),
        "state": str(sf),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    sf.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def write_serving_state(path: Path, port: int, global_mode: bool = False,
                        root: Path | None = None) -> None:
    """前台/被 routes.py 拉起的子控制台也登记一条，让 --stop 与残留排查找得到它。

    没有 start_new_session，pgid 属于用户终端 —— 所以故意记 pgid=0，
    terminate() 见此值只会 kill 单个 pid，不会误伤整个进程组。
    """
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "pid": os.getpid(), "pgid": 0, "port": port, "url": url_of(port),
            "global": bool(global_mode),
            "root": None if global_mode or root is None else str(Path(root).resolve()),
            "log": None, "state": str(p),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def unregister(path: Path | None) -> None:
    if path:
        try:
            Path(path).unlink()
        except OSError:
            pass


def terminate(state: dict, timeout: float = 5.0) -> tuple[bool, str]:
    """停一个控制台。返回 (是否已停, 说明)。"""
    pid = int(state.get("pid") or 0)
    pgid = int(state.get("pgid") or 0)
    # 不变量：只有自己 start_new_session 拉起来的才 pgid==pid，才允许 killpg。
    # 前台登记的那条 pgid=0，走单进程 kill，避免连用户终端一起杀。
    group_ok = bool(pgid) and pgid == pid
    if not pid or not process_alive(pid):
        if state.get("state"):
            unregister(Path(state["state"]))
        return True, "进程已不在，清掉记录"

    def signal_all(sig: int) -> None:
        if group_ok:
            os.killpg(pgid, sig)
        else:
            os.kill(pid, sig)

    try:
        signal_all(signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        return False, f"无权限结束 pid={pid}（不属于当前用户？）"

    if not wait_gone(pid, timeout):
        try:
            signal_all(signal.SIGKILL)
        except ProcessLookupError:
            pass
        if not wait_gone(pid, 3.0):
            return False, f"pid={pid} 杀不掉"

    if state.get("state"):
        unregister(Path(state["state"]))
    if not group_ok:
        return True, "已结束（非本 launcher 拉起，只杀了它自己）"
    left = group_members(pgid)
    if left:
        return True, f"已结束，但进程组内仍有 pid：{left}"
    return True, "已结束（含其子控制台）"


def wait_gone(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.2)
    return not process_alive(pid)


def group_members(pgid: int) -> list[int]:
    """进程组里还活着的成员（用 pgrep 查，避免引入 psutil 依赖）。

    僵尸要剔掉：没人收尸的已死进程还在组里，留着会让 --stop 误报"没杀干净"。
    """
    try:
        out = subprocess.run(["pgrep", "-g", str(pgid)], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(x) for x in out.stdout.split()
            if x.strip().isdigit() and not _is_zombie(int(x))]
