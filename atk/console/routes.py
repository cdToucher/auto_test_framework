"""控制台全部路由（原型期单文件）。每次 setup 生成独立 router，避免跨应用闭包污染。"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import repo


class SaveBody(BaseModel):
    data: dict
    move_to: str | None = None
    if_mtime: int | None = None
    force: bool = False


def setup(app):
    root = lambda: app.state.project_root  # noqa: E731
    r = APIRouter(prefix="/api", dependencies=[])

    @r.get("/health")
    def health():
        return {"ok": True}

    @r.get("/tree")
    def tree():
        return repo.scan_tree(root())

    @r.get("/scenarios/{rel:path}")
    def detail(rel: str):
        try:
            data, mtime = repo.load_scenario(root(), rel)
        except repo.YamlError as e:
            raise HTTPException(422, str(e))
        except FileNotFoundError:
            raise HTTPException(404, rel)
        return {"data": data, "mtime": mtime}

    @r.put("/scenarios/{rel:path}")
    def save(rel: str, body: SaveBody):
        try:
            final = repo.save_scenario(
                root(), rel, body.data,
                move_to=body.move_to, if_mtime=body.if_mtime, force=body.force,
            )
        except repo.ConflictError as e:
            return JSONResponse(status_code=409, content={"detail": str(e)})
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"ok": True, "path": final.relative_to(root() / "scenarios").as_posix()}

    @r.post("/validate")
    def validate(body: dict):
        errs = repo.validate_scenario(body.get("data") or {})
        return {"ok": not errs, "errors": errs}

    app.include_router(r)
