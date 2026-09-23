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
    """plan 时抓的一条提交摘要，只留追溯用得上的两项。"""
    short: str = ""
    subject: str = ""


class ReviewRecord(BaseModel):
    """开发的一次整单确认。多条按时间追加，判定只看最后一条（见 review_status）。"""
    by: str = ""
    verdict: Literal["approve", "reject"] = "approve"
    note: str = ""
    at: str = ""


class IntentRecord(BaseModel):
    """一条覆盖意图的结论，决定 gate 放行还是拦截。

    pending=还没人实测（拦）；fail/suspect=有问题或待定性（拦）；
    pass=成立（放行）；blocked=环境原因没测成（只告警，不拦）。
    """
    title: str
    # pending：由含 ui: 步骤的场景在 run 时自动登记，等 AI 实测后 record 回填定性
    status: Literal["pass", "fail", "suspect", "blocked", "pending"] = "pass"
    note: str = ""
    evidence: list[str] = Field(default_factory=list)


class StepSummary(BaseModel):
    """步骤级摘要，随 run.yaml 落盘。"""
    title: str
    passed: bool
    detail: str = ""
    error_class: str = "assertion"
    # 失败那一步的实际返回（截断+脱敏后）。断言消息只说哪条不对，这里给整份现场，
    # 让人和 AI 不必为了看返回值去重跑一遍。
    response: str = ""


class ScenarioSummary(BaseModel):
    """场景级摘要。

    draft 是**运行时刻**的草稿判定，必须落盘：事后改文件、摘 tag 都不能把
    "跑的时候是未评审草稿"这段历史洗掉，gate 就是按这个时点追溯的。
    """
    name: str
    file: str = ""
    module: str = "default"
    priority: str = "P1"
    passed: bool
    error_class: str = "none"
    env: str = ""
    duration_ms: int = 0
    draft: bool = False  # 运行时是否为 ai-generated 草稿（gate 依据记录时点，不被事后移走/改标绕过）
    steps: list[StepSummary] = Field(default_factory=list)


class RunRecord(BaseModel):
    """一次运行的完整记录，序列化成 reports/runs/<run_id>/run.yaml。

    生命周期：plan/smoke 建骨架（含 head_commit 供 gate 比对内容一致性）→ run 回填
    scenarios → record 追加 intents → review 追加 reviews；report 与 gate 都只读它，
    所以"结论是否可信"完全取决于这份文件，不取决于终端打印过什么。
    """
    run_id: str
    created_at: str
    title: str = ""
    base_ref: str = ""
    head_ref: str = ""
    head_commit: str = ""  # plan 时 head 的 commit SHA，gate 比对内容一致性
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
    head_commit: str = "",
    affected_files: list[str] | None = None,
    affected_modules: list[str] | None = None,
    planned_scenarios: list[str] | None = None,
    title: str = "",
    commits: list[CommitInfo] | None = None,
    reviews: list[ReviewRecord] | None = None,
    runs_dir: Path | str = "reports/runs",
) -> RunRecord:
    """建一条运行记录并立刻落盘。

    run_id 用 `smoke-<时间戳>`，同秒内撞名就追加 -2/-3：同一次需求反复 smoke 是常态，
    撞号会让两次执行的结果互相覆盖。记录从此刻就存在，后续 run/record/review 都是就地追加。
    """
    now = dt.datetime.now()
    base_id = f"smoke-{now.strftime('%Y%m%d-%H%M%S')}"
    run_id, n = base_id, 2
    while (Path(runs_dir) / run_id).exists():
        run_id = f"{base_id}-{n}"
        n += 1
    rec = RunRecord(
        run_id=run_id,
        created_at=now.isoformat(timespec="seconds"),
        title=title,
        base_ref=base_ref,
        head_ref=head_ref,
        head_commit=head_commit,
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
    """读回运行记录。不存在、YAML 损坏、内容为空都抛 KeyError。

    三种情况共用一个异常类型，是为了让 CLI 一律折算成 exit 2（"没测成"），
    而不是让调用方各写一遍分支、把记录问题误报成用例失败。
    """
    f = _run_yaml(run_id, runs_dir)
    if not f.exists():
        raise KeyError(f"运行记录不存在: {run_id}")
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise KeyError(f"运行记录损坏: {run_id}（{e}）")
    if not isinstance(data, dict):
        raise KeyError(f"运行记录为空或损坏: {run_id}")
    return RunRecord(**data)


def save_run(rec: RunRecord, runs_dir: Path | str = "reports/runs") -> Path:
    """整份覆盖写回 run.yaml（记录是单一事实源，不做增量拼接）。"""
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
    """ScenarioResult -> 可序列化摘要。draft 在运行时刻判定，供 gate 追溯。"""
    from .drafts import is_draft

    return ScenarioSummary(
        name=result.scenario.scenario,
        file=result.scenario.file,
        module=result.scenario.module,
        priority=result.scenario.priority.value,
        passed=result.passed,
        error_class=result.error_class,
        env=result.env,
        duration_ms=result.duration_ms,
        draft=bool(result.scenario.file) and is_draft(result.scenario.file),
        steps=[
            StepSummary(title=s.title, passed=s.passed, detail=s.detail,
                        error_class=s.error_class, response=s.response)
            for s in result.steps
        ],
    )
