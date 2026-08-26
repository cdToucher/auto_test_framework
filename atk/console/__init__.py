"""Web 控制台：FastAPI 应用（可选依赖 fastapi/uvicorn）。"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

_STATIC = Path(__file__).parent / "static"


def create_app(project_root: Path | None = None, global_mode: bool = False) -> FastAPI:
    app = FastAPI(title="atk console", docs_url=None, redoc_url=None)
    app.state.project_root = project_root or Path.cwd()
    app.state.global_mode = global_mode

    from . import routes

    routes.setup(app)

    if _STATIC.joinpath("index.html").is_file():

        @app.get("/{spa_path:path}", include_in_schema=False)
        def spa(spa_path: str):
            # 静态资源按名命中，其余一律回 index.html（前端路由接管）
            candidate = (_STATIC / spa_path).resolve()
            if spa_path and candidate.is_file() and candidate.is_relative_to(_STATIC.resolve()):
                return FileResponse(candidate)
            return FileResponse(_STATIC / "index.html")

    return app


def serve(port: int = 8900, global_mode: bool = False, project_root: Path | None = None):
    """注册当前项目（非 -g）并启动 uvicorn。"""
    root = (project_root or Path.cwd()).resolve()
    if not global_mode:
        from .registry import upsert_project

        upsert_project(root)
    import uvicorn

    uvicorn.run(create_app(project_root=root, global_mode=global_mode),
                host="127.0.0.1", port=port, log_level="warning")
