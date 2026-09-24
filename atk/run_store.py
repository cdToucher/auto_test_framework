"""即时层运行记录：plan 创建 -> 执行回填 -> 报告渲染的单一事实源。"""
import datetime as dt
import hashlib
import os
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

    时间戳与操作者是这套机制的承重墙：没有它们，"suspect 24h 内必须定性"就只是一句
    散文，也无从判断某条 pass 是哪一次、由谁回填的。当前态仍留在 run.yaml（单一事实源），
    完整迁移史在旁边的 events.yaml。
    """
    title: str
    # pending：由含 ui: 步骤的场景在 run 时自动登记，等 AI 实测后 record 回填定性
    status: Literal["pass", "fail", "suspect", "blocked", "pending"] = "pass"
    note: str = ""
    evidence: list[str] = Field(default_factory=list)
    first_seen_at: str = ""   # 登记为 pending 的时刻
    at: str = ""              # 最近一次状态迁移
    by: str = ""              # 最近一次下结论的人/AI 标识


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
    # 选择依据：module_map=按影响面选出的子集；full_suite=有影响面算不清，保守跑全库。
    # unmapped_files 非空却仍是 module_map，等于"有些改动没被任何场景覆盖"，gate 要拦。
    selection_mode: str = "module_map"
    unmapped_files: list[str] = Field(default_factory=list)
    # 选择过程的告警（未映射文件、映射表缺失、非 git 仓库）。走 JSON 字段而不是 stderr：
    # --format json 的输出必须能被直接管道给 jq，AI 也不该去抓终端文本才知道跑偏了。
    selection_warnings: list[str] = Field(default_factory=list)
    planned_scenarios: list[str] = Field(default_factory=list)
    # 证据文件名 -> sha256。"pass 必须有证据"要的是文件真在、内容就是这一份；
    # 只存路径的话，一个空文件或被替换的文件照样能骗过检查。
    evidence_sha: dict[str, str] = Field(default_factory=dict)
    commits: list[CommitInfo] = Field(default_factory=list)
    reviews: list[ReviewRecord] = Field(default_factory=list)
    scenarios: list[ScenarioSummary] = Field(default_factory=list)
    intents: list[IntentRecord] = Field(default_factory=list)
    # 每次整份重写自增；配合 events.yaml 里的 save 事件，gate 才能查出
    # "磁盘上的 run.yaml 是不是最后一次合法落盘的那份"（手改/倒填会被拦）。
    revision: int = 0
    updated_at: str = ""


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
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        yaml.safe_dump(rec.model_dump(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def save_run(rec: RunRecord, runs_dir: Path | str = "reports/runs", *,
             actor: str = "", log_event: bool = True) -> Path:
    """整份覆盖写回 run.yaml（记录是单一事实源，不做增量拼接）。

    写是原子的（tmp + os.replace）：半个文件被 load_run 读到会抛 KeyError，
    进而被 CLI 折算成 exit 2——一个读到撕裂记录的"没测成"比一次崩溃更难查。
    当前态留在 run.yaml，迁移史追加进同目录的 events.yaml。
    """
    f = _run_yaml(rec.run_id, runs_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    rec.revision = int(rec.revision or 0) + 1
    rec.updated_at = dt.datetime.now().isoformat(timespec="seconds")
    _dump(rec, f)
    if log_event:
        from . import events

        events.record_save(runs_dir, rec.run_id, actor=actor)
    return f


def create_run(
    base_ref: str = "",
    head_ref: str = "",
    head_commit: str = "",
    affected_files: list[str] | None = None,
    affected_modules: list[str] | None = None,
    selection_mode: str = "module_map",
    unmapped_files: list[str] | None = None,
    selection_warnings: list[str] | None = None,
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
        selection_mode=selection_mode,
        unmapped_files=unmapped_files or [],
        selection_warnings=selection_warnings or [],
        planned_scenarios=planned_scenarios or [],
        commits=commits or [],
        reviews=reviews or [],
    )
    # 目录本身就是锁：mkdir 撞车会抛 FileExistsError，比"先 exists 再 mkdir"可靠——
    # 同一秒内两次 create_run 用 exists() 预判会双双通过，然后互相覆盖。
    d = Path(runs_dir) / rec.run_id
    while True:
        try:
            d.mkdir(parents=True)
            break
        except FileExistsError:
            rec.run_id = f"{base_id}-{n}"
            n += 1
            d = Path(runs_dir) / rec.run_id
    save_run(rec, runs_dir, actor="plan")
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


def add_evidence(
    rec: RunRecord, sources: list[str], runs_dir: Path | str = "reports/runs"
) -> tuple[list[str], list[str]]:
    """拷贝证据进运行目录，返回 (已保存引用, 缺失源)。

    缺失不再静默跳过：一个不存在的截图路径过去只在终端提示一句，写进 run.yaml 的
    却是一条看起来完整的 evidence 列表——"pass 必须有证据"要求的是证据真在。
    """
    ev = Path(runs_dir) / rec.run_id / EVIDENCE_DIR
    ev.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    missing: list[str] = []
    for s in sources:
        src = Path(s)
        if not src.is_file():
            missing.append(str(src))
            continue
        dest = ev / src.name
        shutil.copy2(src, dest)
        saved.append(f"{EVIDENCE_DIR}/{src.name}")
        rec.evidence_sha[dest.name] = hashlib.sha256(dest.read_bytes()).hexdigest()
    return saved, missing


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
