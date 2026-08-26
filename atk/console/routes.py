"""控制台全部路由（原型期单文件）。每次 setup 生成独立 router，避免跨应用闭包污染。"""
import json
import subprocess
import sys
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import repo
from .jobs import BusyError, JobManager


class SaveBody(BaseModel):
    data: dict
    move_to: str | None = None
    if_mtime: int | None = None
    force: bool = False


def _run_summaries(root: Path) -> list[dict]:
    runs_dir = root / "reports" / "runs"
    out = []
    if not runs_dir.exists():
        return out
    for d in sorted(runs_dir.iterdir(), reverse=True):
        f = d / "run.yaml"
        if not f.is_file():
            continue
        try:
            rec = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        scs = rec.get("scenarios") or []
        out.append({
            "run_id": rec.get("run_id", d.name),
            "created_at": rec.get("created_at"),
            "pass_n": sum(1 for s in scs if s.get("passed")),
            "fail_n": sum(1 for s in scs if not s.get("passed")),
            "scenarios": [
                {
                    "name": s.get("name"), "file": s.get("file"),
                    "module": s.get("module"), "priority": s.get("priority"),
                    "passed": s.get("passed"), "error_class": s.get("error_class"),
                    "duration_ms": s.get("duration_ms"),
                    "steps": [
                        {"title": st.get("title"), "passed": st.get("passed"),
                         "detail": st.get("detail")}
                        for st in (s.get("steps") or [])
                    ],
                } for s in scs
            ],
        })
    return out


def setup(app):
    root = lambda: app.state.project_root  # noqa: E731
    jobs = app.state.jobs
    r = APIRouter(prefix="/api")

    @r.get("/health")
    def health():
        return {"ok": True, "global": bool(app.state.global_mode)}

    # ---------- 全局模式：项目注册表 ----------

    if app.state.global_mode:
        from . import registry as reg

        @r.get("/projects")
        def projects():
            reg.rebuild_index()
            return {"projects": reg.list_projects()}

        @r.post("/projects")
        def add_project(body: dict):
            p = Path(str(body.get("path", ""))).expanduser().resolve()
            if not (p / "scenarios").is_dir():
                raise HTTPException(422, f"{p} 不是有效的 atk 工程（缺少 scenarios/）")
            reg.upsert_project(p)
            reg.rebuild_index()
            return {"ok": True}

        @r.delete("/projects")
        def del_project(path: str):
            reg.remove_project(str(Path(path).resolve()))
            return {"ok": True}

        @r.post("/projects/open")
        def open_project(body: dict):
            """为指定项目起独立端口子服务，返回可打开的 URL。"""
            import socket

            p = Path(str(body.get("path", ""))).resolve()
            if str(p) not in {x["path"] for x in reg.list_projects()}:
                raise HTTPException(404, "未注册的项目")
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            argv = [sys.executable, "-m", "atk", "console",
                    "--port", str(port), "--project-root", str(p)]
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"url": f"http://127.0.0.1:{port}"}

    @r.get("/tree")
    def tree():
        return repo.scan_tree(root())

    @r.get("/scenarios/{rel:path}")
    def detail(rel: str):
        try:
            data, mtime = repo.load_scenario(root(), rel)
        except repo.YamlError as e:
            raise HTTPException(422, str(e))
        except (FileNotFoundError, ValueError):
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

    @r.post("/parse")
    def parse(body: dict):
        """YAML 文本 → dict（编辑器源码模式转表单用）。"""
        try:
            data = yaml.safe_load(body.get("raw") or "")
        except yaml.YAMLError as e:
            line = getattr(getattr(e, "problem_mark", None), "line", None)
            raise HTTPException(422, f"YAML 解析失败 line={line + 1 if line is not None else '?'}")
        return {"data": data if isinstance(data, dict) else {}}

    @r.post("/render")
    def render(body: dict):
        """dict → YAML 文本（表单转源码用）。"""
        return {"raw": repo.render_yaml(body.get("data") or {})}

    # ---------- 执行 ----------

    class RunBody(BaseModel):
        env: str
        module: str | None = None

    @r.post("/run")
    def run(body: RunBody):
        argv = [sys.executable, "-m", "atk.cli", "run", "--env", body.env]
        if body.module:
            argv += ["--module", body.module]
        try:
            jid = jobs.start(argv, cwd=str(root()))
        except BusyError as e:
            return JSONResponse(status_code=409, content={"detail": str(e)})
        return {"job_id": jid}

    @r.get("/jobs/{job_id}/stream")
    def stream(job_id: str):
        def gen():
            try:
                for ev in jobs.stream(job_id):
                    yield f"event: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
            except KeyError:
                yield f"event: done\ndata: {json.dumps({'type': 'done', 'exit_code': -1})}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    # ---------- 历史 ----------

    @r.get("/runs")
    def runs(limit: int = 20):
        return _run_summaries(root())[:limit]

    @r.get("/runs/{run_id}/report")
    def run_report(run_id: str):
        p = root() / "reports" / "runs" / run_id / "report.html"
        if not p.is_file():
            raise HTTPException(404, run_id)
        return FileResponse(p)

    @r.get("/runs/{run_id}/evidence/{name:path}")
    def evidence(run_id: str, name: str):
        base = (root() / "reports" / "runs" / run_id).resolve()
        p = (base / name).resolve()
        if not p.is_file() or not p.is_relative_to(base):
            raise HTTPException(404, name)
        return FileResponse(p)

    # ---------- 配置 ----------

    @r.get("/environments")
    def get_environments():
        f = root() / "config" / "environments.yaml"
        return {"raw": f.read_text(encoding="utf-8") if f.exists() else ""}

    @r.put("/environments")
    def put_environments(body: dict):
        f = root() / "config" / "environments.yaml"
        yaml.safe_load(body["raw"])  # 语法门禁
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body["raw"], encoding="utf-8")
        return {"ok": True}

    @r.get("/modules")
    def get_modules():
        f = root() / "config" / "modules.yaml"
        return {"raw": f.read_text(encoding="utf-8") if f.exists() else ""}

    @r.put("/modules")
    def put_modules(body: dict):
        f = root() / "config" / "modules.yaml"
        yaml.safe_load(body["raw"])
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body["raw"], encoding="utf-8")
        return {"ok": True}

    # ---------- 定时任务 ----------

    @r.get("/schedules")
    def get_schedules():
        from . import schedules as sch

        tasks = [dict(t, next_run=sch.next_run_of(t)) for t in sch.load_tasks(root())]
        return {"tasks": tasks}

    @r.put("/schedules")
    def put_schedules(body: dict):
        from . import schedules as sch
        from . import reschedule

        tasks = body.get("tasks") or []
        try:
            sch.save_tasks(root(), tasks)
        except sch.ScheduleError as e:
            raise HTTPException(422, str(e))
        reschedule.reschedule(app)  # 立即按新配置重建调度
        return {"ok": True}

    @r.post("/schedules/{name}/trigger")
    def trigger_schedule(name: str):
        from . import schedules as sch

        task = next((t for t in sch.load_tasks(root()) if t.get("name") == name), None)
        if not task:
            raise HTTPException(404, name)
        try:
            jid = jobs.start(
                [sys.executable, "-m", "atk", "run", "--env", str(task["env"])]
                + (["--module", str(task["module"])] if task.get("module") else []),
                cwd=str(root()),
            )
        except BusyError as e:
            return JSONResponse(status_code=409, content={"detail": str(e)})
        return {"job_id": jid}

    app.include_router(r)
