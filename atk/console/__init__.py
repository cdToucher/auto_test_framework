"""Web 控制台：FastAPI 应用（可选依赖 fastapi/uvicorn）。"""
from pathlib import Path

from fastapi import FastAPI


def create_app(project_root: Path | None = None, global_mode: bool = False) -> FastAPI:
    app = FastAPI(title="atk console", docs_url=None, redoc_url=None)
    app.state.project_root = project_root or Path.cwd()

    from . import routes

    routes.setup(app)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    return app
