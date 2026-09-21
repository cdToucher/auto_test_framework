"""Web 控制台：FastAPI 应用（可选依赖 fastapi/uvicorn）。"""
import sys
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.responses import FileResponse

from .jobs import JobManager

_STATIC = Path(__file__).parent / "static"


def create_app(
    project_root: Path | None = None,
    global_mode: bool = False,
    job_timeout: float = 300.0,
) -> FastAPI:
    app = FastAPI(title="atk console", docs_url=None, redoc_url=None)
    app.state.project_root = project_root or Path.cwd()
    app.state.global_mode = global_mode
    app.state.jobs = JobManager(timeout=job_timeout)
    app.state.scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    from . import routes

    routes.setup(app)

    @app.on_event("startup")
    def _start_scheduler():
        if not app.state.global_mode:
            from .reschedule import reschedule

            reschedule(app)
        app.state.scheduler.start()

    @app.on_event("shutdown")
    def _stop_scheduler():
        try:
            app.state.scheduler.shutdown(wait=False)
        except Exception:
            pass

    if _STATIC.joinpath("index.html").is_file():

        @app.get("/{spa_path:path}", include_in_schema=False)
        def spa(spa_path: str):
            # 静态资源按名命中，其余一律回 index.html（前端路由接管）
            candidate = (_STATIC / spa_path).resolve()
            if spa_path and candidate.is_file() and candidate.is_relative_to(_STATIC.resolve()):
                return FileResponse(candidate)
            return FileResponse(_STATIC / "index.html")

    return app


def serve(port: int = 8900, global_mode: bool = False, project_root: Path | None = None,
          job_timeout: float = 300.0):
    """注册当前项目（非 -g）并启动 uvicorn。

    地址必须自己打印：uvicorn 那行 "Uvicorn running on ..." 是 INFO，会被下面的
    log_level="warning" 吞掉，用户执行完只看到一个光标。
    """
    import atexit
    import logging
    import os

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore", "uvicorn.access", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root = (project_root or Path.cwd()).resolve()
    if not global_mode:
        from .registry import upsert_project

        upsert_project(root)

    from . import daemon

    # 后台 launcher 用环境变量指定它已写好的状态文件（带 pgid，供 killpg 用）。
    # 只认 pid 等于自己的那一份：routes.py 拉起的子控制台会继承同一个环境变量，
    # 若照单全收，它退出时就把父控制台的状态文件删了。
    handed = os.environ.get(daemon.STATE_ENV)
    if handed and daemon.state_owner(Path(handed)) != os.getpid():
        handed = None
    state_path = Path(handed) if handed else daemon.state_file(root, global_mode, port)
    if not handed:
        daemon.write_serving_state(state_path, port, global_mode=global_mode, root=root)
    atexit.register(daemon.unregister, state_path)

    url = daemon.url_of(port)
    if not daemon.port_free(port):
        _die_port_in_use(port, global_mode, root, state_path)

    print("atk console 已启动", flush=True)
    print(f"  地址：{url}   （只监听 127.0.0.1，不对局域网开放）", flush=True)
    if global_mode:
        from .registry import DB_PATH

        print(f"  模式：全局聚合（项目注册表 {DB_PATH}）", flush=True)
    else:
        print(f"  模式：项目 {root}", flush=True)
    if handed:
        print(f"  日志：{daemon.log_file(root, global_mode, port)}", flush=True)
        print(f"  停止：atk console --stop --port {port}", flush=True)
    else:
        print("  停止：Ctrl-C（或另开终端 atk console --stop）", flush=True)

    import uvicorn

    # host 写死字面量是有意的：test_b_hardening.py 用源码里的 'host="127.0.0.1"'
    # 当 tripwire，防止以后有人改成 0.0.0.0 把只该本机访问的控制台暴露到局域网。
    uvicorn.run(create_app(project_root=root, global_mode=global_mode, job_timeout=job_timeout),
                host="127.0.0.1", port=port, log_level="warning")


def _die_port_in_use(port: int, global_mode: bool, root: Path, state_path: Path) -> None:
    """端口被占时说清"谁在占、怎么停"，别丢一个 traceback。"""
    from . import daemon

    holder = next((s for s in daemon.list_states(root, global_mode)
                   if int(s.get("port") or 0) == port), None)
    print(f"端口 {port} 已被占用，控制台起不来。", file=sys.stderr)
    if holder:
        print(f"  占用者：atk console pid={holder.get('pid')} 启动于 {holder.get('started_at')}",
              file=sys.stderr)
    else:
        print("  不是 atk 登记的进程，可能是别的程序（lsof -nP -iTCP 可查）。", file=sys.stderr)
    print(f"  停掉它：atk console --stop --port {port}", file=sys.stderr)
    print("  换端口：atk console --port 0 --background", file=sys.stderr)
    print(f"  状态文件：{state_path}", file=sys.stderr)
    raise SystemExit(2)
