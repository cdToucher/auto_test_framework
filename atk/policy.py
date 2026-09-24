"""判定口径的唯一定义处：error_class → 档位 → 退出码，以及 gate 的检查表。

为什么单独一个模块：`error_class` 是全套的枢纽，"失败/受阻/待实测"三个去向决定
退出码、gate 拦截与 junit 节点类型。这套口径原先在 runner、gate、agent、smoke、
控制台各算一份（过滤条件逐字相同却仍是五份），改一处就漏四处。这里只允许存在
两份定义：执行期的 `executors.runner.RunReport`，与记录期的 `tally()`。

方向约定（fail-safe）：一切"拿不准"都往**更保守**的一侧倒——未知归类算受阻
（宁可 exit 2 说"没测成"，绝不悄悄算通过），非拦截项只降级为告警不删消息。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .diff_analyzer.git_diff import changed_files, rev_parse, uncommitted_files
from .drafts import DRAFT_TAG, is_draft
from .run_store import review_status

TIER_PASS = "pass"
TIER_FAIL = "fail"
TIER_BLOCKED = "blocked"
TIER_PENDING = "pending"

#: 执行器能产出的全部归类 → 它落在哪个档位。
#: 新增 error_class 必须同时登记这里，否则只会被下面的兜底吞成 blocked。
TIER: dict[str, str] = {
    "none": TIER_PASS,
    "assertion": TIER_FAIL,      # 断言不成立：真实用例失败
    "config": TIER_FAIL,         # 配置错误：同属"这次改动不能合"
    "environment": TIER_BLOCKED,  # 网络/连接/超时：环境事故，不是代码问题
    "ui_pending": TIER_PENDING,   # 等 AI 浏览器实测回填，交 gate 判
    "skipped": TIER_PENDING,      # --skip-ui 后无可执行步骤
}


def tier_of(passed: bool, error_class: str) -> str:
    """一条结果落在哪个档位。未登记的归类按 blocked 处理，不按 pass。"""
    if passed:
        return TIER_PASS
    if error_class == "none":
        # 声称没错却没通过：记录自相矛盾，保守当作受阻，不给通过。
        return TIER_BLOCKED
    return TIER.get(error_class, TIER_BLOCKED)


@dataclass(frozen=True)
class Tally:
    """一组结果的计数。档位互斥且完备：total == passed+failed+blocked+pending。"""

    total: int = 0
    passed: int = 0
    failed: int = 0
    blocked: int = 0
    pending: int = 0

    def __bool__(self) -> bool:  # pragma: no cover - 可读性用
        return self.total > 0


_BY_TIER = {
    TIER_PASS: "passed",
    TIER_FAIL: "failed",
    TIER_BLOCKED: "blocked",
    TIER_PENDING: "pending",
}


def tally(rows: Any) -> Tally:
    """对任何带 passed/error_class 两个属性的条目求计数。

    ScenarioResult（执行期）、ScenarioSummary（记录期）与直接从 run.yaml 读出的
    裸 dict（控制台列表页，不想为渲染就反序列化整份记录）都走这里，
    这样 gate、agent、smoke、控制台不必各写一遍过滤条件。
    """
    counts = {"passed": 0, "failed": 0, "blocked": 0, "pending": 0}
    total = 0
    for row in rows:
        total += 1
        if isinstance(row, dict):
            passed, error_class = row.get("passed"), str(row.get("error_class") or "none")
        else:
            passed, error_class = row.passed, row.error_class
        counts[_BY_TIER[tier_of(bool(passed), error_class)]] += 1
    return Tally(total=total, **counts)


def project(t: Tally, load_errors: int = 0) -> int:
    """档位 → 进程退出码：1 用例失败 · 2 受阻或场景库加载错误 · 0 其余。

    两条"待实测不计失败"的例外：
    - pending 档不进任何非零分支。定性由 gate 检查意图是否已 record 回填，
      否则一次纯 API 冒烟会被无关的 UI 意图拖成 exit 1。
    - 场景库里有文件加载不了归到 2 而不是 1：坏 YAML 不是这次改动的产品回归，
      报成 exit 1 会让 CI 把库问题算到被评审的提交头上。这条对 run 和 plan
      统一生效，此前两处各按各的理解写（runner 判 1，契约文档说 2）。
    """
    if t.failed:
        return 1
    if t.blocked or load_errors:
        return 2
    return 0


#: error_class → junit 节点类型。JUnit 只有 failure/error/skipped，没有第三态，
#: 所以取"最不误导"的一侧：环境受阻进 <error>（JUnit 里它本就是指执行期出错，
#: 不是断言不成立），待实测与跳过进 <skipped>。
#: 旧实现只把 environment 当 skipped、其余全塞 failure，于是 exit 0 的一次
#: 纯 API 冒烟只要库里有个 UI 场景就会在 CI 里变红——两个出口互相打脸。
JUNIT_NODE: dict[str, str] = {
    "none": "ok",
    "assertion": "failure",
    "config": "failure",
    "environment": "error",
    "ui_pending": "skipped",
    "skipped": "skipped",
}


def junit_node(error_class: str) -> str:
    """未知归类进 skipped：宁可 CI 显示"未测"，不可显示"这次改动坏了"。"""
    return JUNIT_NODE.get(error_class, "skipped")


def junit_counts(t: Tally) -> dict[str, str]:
    """testsuite 的四个计数属性，与逐用例节点类型同源于一次 tally。

    这样"套件写了 failures=3 而用例只有 2 个 <failure>"这类自相矛盾不可能发生——
    旧实现里 skipped 属性填的是 blocked_count，就是这个性质的破口。
    """
    return {
        "tests": str(t.total),
        "failures": str(t.failed),
        "errors": str(t.blocked),
        "skipped": str(t.pending),
    }


# ---------------------------------------------------------------------------
# gate 检查表
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Note:
    """一条检查的一个结论。ok=False 表示违规；warn=True 表示只提示。"""

    message: str
    ok: bool = True
    warn: bool = False


@dataclass
class GateContext:
    """检查项能看到的一切。

    changed/dirty 惰性缓存：三项检查都要 git 事实，逐项各起一个子进程会把
    一次 gate 变成三四次 `git diff`。
    """

    rec: Any
    repo: Path
    head: str
    runs_dir: Path
    root: Path = field(default_factory=lambda: Path("."))
    allow_unbound: bool = False
    _changed: Optional[list[str]] = field(default=None, repr=False)
    _dirty: Optional[list[str]] = field(default=None, repr=False)

    def changed(self) -> list[str]:
        if self._changed is None:
            self._changed = changed_files(self.rec.base_ref, self.head, str(self.repo))
        return self._changed

    def dirty(self) -> list[str]:
        if self._dirty is None:
            self._dirty = uncommitted_files(str(self.repo))
        return self._dirty


@dataclass(frozen=True)
class GateCheck:
    """一项门禁检查。

    blocking=False 不是"这条不重要"，而是"当前只提示不拦"——写进数据里，
    免得它像以前一样只是"某段代码恰好没把 ok 设成 False"的巧合。
    """

    id: str
    blocking: bool
    fn: Callable[[GateContext], list[Note]]


# --- 共用判据。gate / agent / smoke 三处此前各写一遍同样的过滤条件，改一处漏两处。 ---


def draft_names(summaries: Any) -> list[str]:
    """记录里仍未过人工评审的草稿名。

    运行时记录的 draft 与磁盘现状取并集：磁盘仍是草稿、或草稿文件已消失
    （reject 移走）都算未评审——防止"跑完草稿再 reject"让未审定结果混过门禁。
    """
    return sorted({
        s.name for s in summaries
        if (s.file and is_draft(s.file)) or (s.draft and s.file and not Path(s.file).exists())
    })


def failed_names(summaries: Any) -> list[str]:
    """真实用例失败（断言/配置）的场景名；受阻不算。"""
    return [
        s.name for s in summaries
        if tier_of(bool(s.passed), s.error_class) == TIER_FAIL
    ]


def blocked_names(summaries: Any) -> list[str]:
    """受阻（环境类或归类不明）的场景名。"""
    return [
        s.name for s in summaries
        if tier_of(bool(s.passed), s.error_class) == TIER_BLOCKED
    ]


def pending_titles(intents: Any) -> list[str]:
    """未回填实测结论的 UI 意图标题。"""
    return [i.title for i in intents if i.status == "pending"]


# --- 各项检查。顺序即历史 verdicts 的输出顺序，也是 agent 状态梯子的依据顺序。 ---


def _empty_record(ctx: GateContext) -> list[Note]:
    if not ctx.rec.scenarios and not ctx.rec.intents:
        return [Note("运行记录无场景结果也无意图（未执行或选中为空），不可作为门禁依据",
                     ok=False)]
    return []


def _no_base_ref(ctx: GateContext) -> list[Note]:
    if not ctx.rec.base_ref:
        return [Note("运行记录未绑定 Git 基线，跳过变更一致性检查")]
    return []


def _unbound_record(ctx: GateContext) -> list[Note]:
    """git 可用、记录却没绑定任何基线 → 这条记录不能当合并依据。

    没有这一项，`atk run --record-new` 跑出一条与改动毫无关系的记录就能让 gate 变绿：
    覆盖检查要求 affected 非空、审批检查要求 base_ref，两者都会因为"没绑基线"而静默跳过。
    非 git 项目或有意为之的一次性试跑，用 `atk gate --allow-unbound` 显式放行。
    """
    if ctx.rec.base_ref:
        return []
    if getattr(ctx, "allow_unbound", False):
        # 显式放行也要留痕：一条与任何变更无关的记录不该被读成"门禁过了"
        return [Note(f"记录 {ctx.rec.run_id} 未绑定基线，已按 --allow-unbound 放行："
                     "本判定不构成合并依据", warn=True)]
    try:
        from .diff_analyzer.git_diff import is_git_repository
        git_ok = is_git_repository(str(ctx.repo))
    except OSError:
        git_ok = False
    if not git_ok:
        return [Note("记录未绑定 Git 基线且当前目录不是 git 仓库，跳过变更一致性检查",
                     warn=True)]
    return [Note(f"运行记录 {ctx.rec.run_id} 未绑定任何 Git 基线：它没有描述"
                 "「哪次变更被测过」，不能作为合并门禁依据。用 atk plan --base <基线> 建记录，"
                 "或（明知故犯时）atk gate --allow-unbound", ok=False)]


def _change_drift(ctx: GateContext) -> list[Note]:
    if not ctx.rec.base_ref:
        return []
    current = ctx.changed()
    if sorted(current) != sorted(ctx.rec.affected_files):
        return [Note("变更已漂移：当前 diff 与运行记录不一致，请重新 plan", ok=False)]
    return [Note(f"变更一致（{len(current)} 个文件）")]


def _head_advanced(ctx: GateContext) -> list[Note]:
    if not ctx.rec.base_ref or not ctx.rec.head_commit:
        return []
    try:
        now_sha = rev_parse(ctx.head, str(ctx.repo))
    except RuntimeError:
        now_sha = ""
    if now_sha and now_sha != ctx.rec.head_commit:
        return [Note(f"head 已前进：记录建于 {ctx.rec.head_commit[:12]}，"
                     f"当前 {now_sha[:12]}，请重新 plan", ok=False)]
    return []


def _legacy_head_commit(ctx: GateContext) -> list[Note]:
    if not ctx.rec.base_ref or ctx.rec.head_commit or not ctx.changed():
        return []
    return [Note("记录无 head commit 信息（旧记录），跳过内容一致性检查")]


def _dirty_tree(ctx: GateContext) -> list[Note]:
    if not ctx.rec.base_ref:
        return []
    dirty = ctx.dirty()
    if not dirty:
        return []
    shown = ", ".join(dirty[:5]) + (" …" if len(dirty) > 5 else "")
    return [Note(f"⚠ 工作区有 {len(dirty)} 个未提交文件（三点 diff 不含其内容，"
                 f"门禁未覆盖）: {shown}", warn=True)]


def _case_fail(ctx: GateContext) -> list[Note]:
    names = failed_names(ctx.rec.scenarios)
    if not names:
        return []
    return [Note(f"用例失败 {len(names)} 个: {', '.join(names)}", ok=False)]


def _scenario_tally(ctx: GateContext) -> list[Note]:
    """无失败时的正报。受阻只提示：环境事故不该被读成"这次改动没问题"。"""
    t = tally(ctx.rec.scenarios)
    if not t.total or t.failed:
        return []
    extra = f"（受阻 {t.blocked} 个）" if t.blocked else ""
    return [Note(f"复用场景 {t.total} 个无用例失败{extra}", warn=bool(t.blocked))]


def _unreviewed_drafts(ctx: GateContext) -> list[Note]:
    unreviewed = draft_names(ctx.rec.scenarios)
    if not unreviewed:
        return []
    return [Note(f"存在未评审AI草稿 {len(unreviewed)} 个: " + ", ".join(unreviewed)
                 + f"（请先 atk review-draft 评审转正，{DRAFT_TAG} tag 未去掉不得参与门禁）",
                 ok=False)]


def _unresolved_intents(ctx: GateContext) -> list[Note]:
    bad = [i for i in ctx.rec.intents if i.status in ("fail", "suspect")]
    if not bad:
        return []
    return [Note("存在未定性结论: " + "; ".join(f"{i.title}[{i.status}]" for i in bad),
                 ok=False)]


def _pending_intents(ctx: GateContext) -> list[Note]:
    """UI 步骤 atk run 不执行，定性只能来自 AI 实测后的 record 回填；
    未回填即放行等于"没测过就说通过"，必须拦截。"""
    pending = pending_titles(ctx.rec.intents)
    if not pending:
        return []
    return [Note(f"存在未实测回填的 UI 意图 {len(pending)} 条: "
                 + "; ".join(pending)
                 + f"（请用 AI 浏览器实测后 atk record {ctx.rec.run_id} --from-json 回填）",
                 ok=False)]


def _ui_backfilled(ctx: GateContext) -> list[Note]:
    pending_ui = [s for s in ctx.rec.scenarios if s.error_class == "ui_pending"]
    if not pending_ui or pending_titles(ctx.rec.intents):
        return []
    return [Note(f"UI 待实测场景 {len(pending_ui)} 个已全部回填（仅提示）")]


def _blocked_intents(ctx: GateContext) -> list[Note]:
    blocked = [i for i in ctx.rec.intents if i.status == "blocked"]
    if not blocked:
        return []
    return [Note(f"受阻意图 {len(blocked)} 条（不拦截，但需关注）", warn=True)]


def _change_covered(ctx: GateContext) -> list[Note]:
    """gate 一直只证明"这些场景在这个 commit 上通过了"，从不证明"这些场景覆盖这次改动"。

    这一项补的就是后半句：影响面算得出来（mode=module_map）、确实落在已知模块上，
    但跑过的场景里没有一条属于这些模块——那不是"没问题"，那是"没人测"。
    判据用**已执行**的场景模块而非 planned_scenarios：分开跑 plan 与 run 时
    planned 只是计划，真正该问的是最后跑掉了什么。
    """
    mode = getattr(ctx.rec, "selection_mode", "module_map")
    affected = set(getattr(ctx.rec, "affected_modules", []) or [])
    if mode != "module_map" or not affected:
        return []
    if {s.module for s in ctx.rec.scenarios} & affected:
        return []
    return [Note(f"变更落在模块 {', '.join(sorted(affected))}，但本次执行的场景没有一条属于"
                 "这些模块；补 modules.yaml 映射或让 AI 按 atk-authoring 起草场景", ok=False)]


def _unmapped_note(ctx: GateContext) -> list[Note]:
    """未映射文件本身只在"没因此跑全量"时才需要提醒。

    有行为相关文件落不进模块时，plan 已经改成跑全库了，那种情况不必再喊；
    剩下的都是改文档/改图片一类，提示一句"这些文件没有任何场景声称覆盖它"。
    """
    mode = getattr(ctx.rec, "selection_mode", "module_map")
    unmapped = getattr(ctx.rec, "unmapped_files", []) or []
    if not unmapped or mode != "module_map":
        return []
    shown = ", ".join(unmapped[:5]) + (" …" if len(unmapped) > 5 else "")
    return [Note(f"⚠ {len(unmapped)} 个变更文件未映射到模块（不影响运行结果才允许只提示）: "
                 f"{shown}", warn=True)]


def _unapproved_expects(ctx: GateContext) -> list[Note]:
    """本次变更碰过的场景，断言面必须有一条人工审批记录。

    为什么不看 ai-generated tag：那个字符串是 AI 自己写进 YAML 的，删掉就等于"已转正"。
    这里看**内容**：以断言面 sha256 为键查 config/approvals.yaml，而那条记录只能凭
    交互式终端铸出的一次性凭证写入。改一个断言 → 指纹变 → 必须重审。

    没有 git 基线时跳过并说明：这条判据靠"相对基线有没有碰过"区分新写与既有，缺基线会
    退化成"全部要审批"、误伤所有旧项目——宁可显式降级，不静默拦。
    """
    if not ctx.rec.base_ref:
        return []
    from .approvals import needs_approval

    touched = {str(f).lstrip("./") for f in (ctx.rec.affected_files or [])}
    touched |= {str(p).lstrip("./") for p in ctx.dirty()}
    bad: list[str] = []
    for s in ctx.rec.scenarios:
        if not s.file:
            continue
        needed, why = needs_approval(target=s.file, project=ctx.root, touched=touched,
                                     repo=ctx.repo, base_ref=ctx.rec.base_ref)
        if needed:
            bad.append(f"{s.name}（{why}）")
    if not bad:
        return []
    from .approvals import approvals_file, load_approvals, valid_approvals
    good, invalid = valid_approvals(approvals_file(ctx.root))
    tail = ""
    if invalid and not good:
        tail = (f"；账本里有 {len(invalid)} 条签名无效的条目"
                "（手改过或来自另一把密钥，一律不认）")
    elif invalid:
        tail = f"；另有 {len(invalid)} 条签名无效的条目未被采信"
    return [Note(f"{len(bad)} 份场景的断言未经人工审批: " + "; ".join(bad) + tail
                 + "（开发者在终端 atk approvals mint <文件> 铸凭证，再 "
                   "atk review-draft <文件> --by <人> --verdict approve --approval <码>）",
                 ok=False)]


def _ledger_consistency(ctx: GateContext) -> list[Note]:
    """磁盘上的 run.yaml 必须等于最后一次合法落盘的那份。

    记录是可直接编辑的 YAML；没这道校验，手改一条 status 或倒填时间戳就等于改了结论。
    旧记录（没有 events.yaml）只提示不拦——否则升级那一刻所有历史记录齐变红。
    """
    from . import events

    chain_ok, why = events.verify_chain(ctx.runs_dir, ctx.rec.run_id)
    if not chain_ok:
        legacy = not events.read(events.events_path(ctx.runs_dir, ctx.rec.run_id))
        if legacy and "为空" in why:
            return [Note("记录无事件流水（旧记录），跳过一致性校验", warn=True)]
        return [Note(f"事件流水不可信：{why}", ok=False)]
    digest = events.last_save_sha(ctx.runs_dir, ctx.rec.run_id)
    if digest is None:
        return [Note("记录无事件流水（旧记录），跳过一致性校验", warn=True)]
    current = events.sha_of(Path(ctx.runs_dir) / ctx.rec.run_id / "run.yaml")
    if current == digest:
        return []
    return [Note("run.yaml 与事件流水不符：记录在落盘之后被直接改过，结论不可信"
                 f"（当前 {current[:12]}…，最后合法落盘 {digest[:12]}…）"
                 "——请重跑 atk run/record 而不是手改 run.yaml", ok=False)]


def _deps_stale(ctx: GateContext) -> list[Note]:
    """计划里有的场景，账本却显示这条运行记录没真跑过它。

    没有这条，"plan 选了 12 个、实际只跑了 8 个"看上去仍是全绿：gate 只看执行结果，
    不看计划与执行是否脱节（smoke 按模块分多轮，某一轮静默失败就会这样）。
    """
    from . import deps

    planned = list(getattr(ctx.rec, "planned_scenarios", []) or [])
    if not planned or not ctx.rec.scenarios:
        return []
    ledger = deps.load_ledger(deps.ledger_path(ctx.root))
    if not ledger:
        return [Note("依赖账本不存在，无法核对计划与执行是否一致（首次 plan 后跑一次 "
                     "atk run 即可建立）", warn=True)]
    stale = deps.stale_planned(ledger, planned, ctx.rec.run_id)
    if not stale:
        return []
    shown = ", ".join(stale[:5]) + (" …" if len(stale) > 5 else "")
    return [Note(f"计划了 {len(stale)} 个场景但本次没有真正执行它们: {shown}"
                 f"（用 atk run --record-to {ctx.rec.run_id} 补齐，或重新 plan）",
                 ok=False)]


#: 门禁检查表。每行都能单独测"会不会拦"，这是把它从一串 if 变成数据的全部理由：
#: 此前 9 项检查里只有 4 项有"证明会拦截"的用例。
GATE_CHECKS: tuple[GateCheck, ...] = (
    GateCheck("empty_record", True, _empty_record),
    GateCheck("no_base_ref", False, _no_base_ref),
    GateCheck("unbound_record", True, _unbound_record),
    GateCheck("change_drift", True, _change_drift),
    GateCheck("head_advanced", True, _head_advanced),
    GateCheck("legacy_no_head_commit", False, _legacy_head_commit),
    GateCheck("dirty_tree", False, _dirty_tree),
    GateCheck("case_fail", True, _case_fail),
    GateCheck("scenario_tally", False, _scenario_tally),
    GateCheck("change_covered", True, _change_covered),
    GateCheck("deps_stale", True, _deps_stale),
    GateCheck("unmapped_note", False, _unmapped_note),
    GateCheck("ledger_consistency", True, _ledger_consistency),
    GateCheck("unreviewed_drafts", True, _unreviewed_drafts),
    GateCheck("unapproved_expects", True, _unapproved_expects),
    GateCheck("unresolved_intents", True, _unresolved_intents),
    GateCheck("pending_intents", True, _pending_intents),
    GateCheck("ui_backfilled", False, _ui_backfilled),
    GateCheck("blocked_intents", False, _blocked_intents),
)


def review_line(rec: Any) -> str:
    """开发整单确认的展示行。

    单独一个函数而不是并入 verdicts：它一直是 gate 返回值的第四项，也是报告里
    warnings 的第一行，混进检查表会让人工确认状态在两个地方各显示一次。
    """
    status, last = review_status(getattr(rec, "reviews", []) or [])
    if status == "rejected":
        assert last is not None
        return f"⚠ 已被开发驳回 by {last.by}（仅告警，不拦截）" + (
            f"：{last.note}" if last.note else "")
    if status == "unconfirmed":
        return "⚠ 未经开发确认（仅告警，不拦截）"
    assert last is not None
    return f"✓ 已获开发确认 by {last.by}"


def run_checks(ctx: GateContext) -> tuple[bool, list[tuple[bool, str, bool]]]:
    """跑完检查表，产出与历史一致的 verdicts 形状 (ok, message, warn)。

    非拦截项的违规渲染成 ok=True + warn=True：消息不消失，但不参与放行判定。
    """
    verdicts: list[tuple[bool, str, bool]] = []
    for check in GATE_CHECKS:
        for note in check.fn(ctx):
            if not note.ok and check.blocking:
                verdicts.append((False, note.message, False))
            else:
                verdicts.append((True, note.message, note.warn or not note.ok))
    return all(ok for ok, _, _ in verdicts), verdicts
