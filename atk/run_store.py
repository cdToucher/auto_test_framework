"""即时层运行记录：plan 创建 -> 执行回填 -> 报告渲染的单一事实源。"""
import datetime as dt
import shutil
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

RUN_YAML = "run.yaml"
EVIDENCE_DIR = "evidence"


class CommitInfo(BaseModel):
    short: str = ""
    subject: str = ""


class ReviewRecord(BaseModel):
    by: str = ""
    verdict: Literal["approve", "reject"] = "approve"
    note: str = ""
    at: str = ""


class IntentRecord(BaseModel):
    title: str
    status: Literal["pass", "fail", "suspect", "blocked"] = "pass"
    note: str = ""
    evidence: list[str] = Field(default_factory=list)


class StepSummary(BaseModel):
    title: str
    passed: bool
    detail: str = ""
    error_class: str = "assertion"


class ScenarioSummary(BaseModel):
    name: str
    file: str = ""
    module: str = "default"
    priority: str = "P1"
    passed: bool
    error_class: str = "none"
    env: str = ""
    duration_ms: int = 0
    steps: list[StepSummary] = Field(default_factory=list)


class RunRecord(BaseModel):
    run_id: str
    created_at: str
    title: str = ""
    base_ref: str = ""
    head_ref: str = ""
    affected_files: list[str] = Field(default_factory=list)
    affected_modules: list[str] = Field(default_factory=list)
    planned_scenarios: list[str] = Field(default_factory=list)
    commits: list[CommitInfo] = Field(default_factory=list)
    reviews: list[ReviewRecord] = Field(default_factory=list)
    scenarios: list[ScenarioSummary] = Field(default_factory=list)
    intents: list[IntentRecord] = Field(default_factory=list)


def review_status(
    reviews: list[ReviewRecord] | None,
) -> tuple[str, ReviewRecord | None]:
    """开发确认状态：按 reviews 最后一条判定（后一条覆盖前一条）。

    返回 (状态, 最后记录)：approved=已获开发确认、rejected=已被开发驳回、
    unconfirmed=未经开发确认（无记录时）。gate 与 report 共用，避免口径漂移。
    """
    if not reviews:
        return ("unconfirmed", None)
    last = reviews[-1]
    if last.verdict == "reject":
        return ("rejected", last)
    return ("approved", last)


def _dump(rec: RunRecord, path: Path) -> None:
    path.write_text(
        yaml.safe_dump(rec.model_dump(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def create_run(
    base_ref: str = "",
    head_ref: str = "",
    affected_files: list[str] | None = None,
    affected_modules: list[str] | None = None,
    planned_scenarios: list[str] | None = None,
    title: str = "",
    commits: list[CommitInfo] | None = None,
    reviews: list[ReviewRecord] | None = None,
    runs_dir: Path | str = "reports/runs",
) -> RunRecord:
    now = dt.datetime.now()
    rec = RunRecord(
        run_id=f"smoke-{now.strftime('%Y%m%d-%H%M%S')}",
        created_at=now.isoformat(timespec="seconds"),
        title=title,
        base_ref=base_ref,
        head_ref=head_ref,
        affected_files=affected_files or [],
        affected_modules=affected_modules or [],
        planned_scenarios=planned_scenarios or [],
        commits=commits or [],
        reviews=reviews or [],
    )
    d = Path(runs_dir) / rec.run_id
    d.mkdir(parents=True, exist_ok=True)
    _dump(rec, d / RUN_YAML)
    return rec


def _run_yaml(run_id: str, runs_dir: Path | str) -> Path:
    return Path(runs_dir) / run_id / RUN_YAML


def load_run(run_id: str, runs_dir: Path | str = "reports/runs") -> RunRecord:
    f = _run_yaml(run_id, runs_dir)
    if not f.exists():
        raise KeyError(f"运行记录不存在: {run_id}")
    return RunRecord(**yaml.safe_load(f.read_text(encoding="utf-8")))


def save_run(rec: RunRecord, runs_dir: Path | str = "reports/runs") -> Path:
    f = _run_yaml(rec.run_id, runs_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    _dump(rec, f)
    return f


def add_evidence(
    rec: RunRecord, sources: list[str], runs_dir: Path | str = "reports/runs"
) -> list[str]:
    """拷贝存在的证据文件进运行目录，返回相对引用名；缺失源静默跳过。"""
    ev = Path(runs_dir) / rec.run_id / EVIDENCE_DIR
    ev.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for s in sources:
        src = Path(s)
        if not src.is_file():
            continue
        dest = ev / src.name
        shutil.copy2(src, dest)
        saved.append(f"{EVIDENCE_DIR}/{src.name}")
    return saved


def summarize(result) -> ScenarioSummary:
    """ScenarioResult -> 可序列化摘要。"""
    return ScenarioSummary(
        name=result.scenario.scenario,
        file=result.scenario.file,
        module=result.scenario.module,
        priority=result.scenario.priority.value,
        passed=result.passed,
        error_class=result.error_class,
        env=result.env,
        duration_ms=result.duration_ms,
        steps=[
            StepSummary(title=s.title, passed=s.passed, detail=s.detail, error_class=s.error_class)
            for s in result.steps
        ],
    )
