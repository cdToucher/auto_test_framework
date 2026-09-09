"""Web 控制台：FastAPI 应用（可选依赖 fastapi/uvicorn）。"""
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.responses import FileResponse

from .jobs import JobManager

_STATIC = Path(__file__).parent / "static"


def create_app(
    project_root: Path | None = None,
    global_mode: bool = False,
    token: str | None = None,
    job_timeout: float = 300.0,
) -> FastAPI:
    import os

    from fastapi.responses import JSONResponse

    resolved_token = token or os.getenv("ATK_CONSOLE_TOKEN") or None
    app = FastAPI(title="atk console", docs_url=None, redoc_url=None)
    app.state.project_root = project_root or Path.cwd()
    app.state.global_mode = global_mode
    app.state.auth_token = resolved_token
    app.state.jobs = JobManager(timeout=job_timeout)
    app.state.scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    if resolved_token:
        @app.middleware("http")
        async def _auth(request, call_next):
            if request.url.path.startswith("/api"):
                if request.headers.get("X-Auth-Token") != resolved_token:
                    return JSONResponse(status_code=401, content={"detail": "未授权"})
            return await call_next(request)

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
           token: str | None = None, job_timeout: float = 300.0):
    """注册当前项目（非 -g）并启动 uvicorn。"""
    import logging
    import os

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore", "uvicorn.access", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    resolved_token = token or os.getenv("ATK_CONSOLE_TOKEN") or None
    if not resolved_token:
        logging.getLogger("atk.console").warning("仅回环无鉴权：未设置 --token/ATK_CONSOLE_TOKEN，仅监听 127.0.0.1")

    root = (project_root or Path.cwd()).resolve()
    if not global_mode:
        from .registry import upsert_project

        upsert_project(root)
    import uvicorn

    uvicorn.run(create_app(project_root=root, global_mode=global_mode,
                           token=resolved_token, job_timeout=job_timeout),
                host="127.0.0.1", port=port, log_level="warning")
