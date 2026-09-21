"""atk 命令行入口。"""
import datetime as dt
import hashlib
import importlib.resources as res
import json
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

import typer

from .diff_analyzer.git_diff import (
    changed_files,
    is_git_repository,
    rev_parse,
    uncommitted_files,
)
from .diff_analyzer.modules import classify, load_module_map
from .drafts import DRAFT_TAG, is_draft, review_draft
from . import layout
from .executors.runner import RunReport, Runner
from .generator.context import build_context, commit_log, render_markdown
from .last_run import read_last_run, resolve_run_id, write_last_run
from .skill_install import (
    DEFAULT_UI_TOOL,
    install_skills,
    legacy_skill_paths,
    remove_agents_block,
)
from .reporter.html_reporter import render_html, render_run_html
from .reporter.junit import write_junit
from .run_store import (
    CommitInfo,
    IntentRecord,
    ReviewRecord,
    RunRecord,
    add_evidence,
    create_run,
    load_run,
    review_status,
    save_run,
    summarize,
)
from .store.loader import load_scenarios, select
from .store.models import Priority, Scenario

app = typer.Typer(help="atk：AI 原生双层自动化测试框架（M1：API 冒烟）")

_LAYOUT_HELP = "默认自动探测项目布局（旧: scenarios|config|reports；新: .atk/ 收束）"


def _resolve(p: Optional[Path], default: Path) -> Path:
    """显式传入的路径永远优先；None 走布局探测。"""
    return Path(p) if p is not None else default


def _layout_paths(project: Path | str = ".") -> dict[str, Path]:
    pr = Path(project)
    return {
        "root": layout.scenarios_dir(pr),
        "env_file": layout.env_file(pr),
        "module_map": layout.modules_file(pr),
        "report_dir": layout.reports_dir(pr),
        "reports_dir": layout.reports_dir(pr),
        "runs_dir": layout.runs_dir(pr),
    }


def _parse_set(pairs: Optional[list[str]]) -> Optional[dict[str, str]]:
    """--set key=value 列表 -> dict；格式非法直接报错退出。"""
    if not pairs:
        return None
    out: dict[str, str] = {}
    for p in pairs:
        k, sep, v = p.partition("=")
        if not sep or not k.strip():
            raise typer.BadParameter(f"--set 需要 key=value 形式：{p}")
        out[k.strip()] = v
    return out or None


def _parse_tags(tags: Optional[str]) -> Optional[list[str]]:
    """逗号分隔标签串 -> 列表；空则 None。plan/run/smoke 共用。"""
    if not tags:
        return None
    lst = [t.strip() for t in tags.split(",") if t.strip()]
    return lst or None


def _run_id_arg(run_id: Optional[str], use_last: bool, project: Path) -> str:
    """解析 run_id，失败时给出友好提示并 exit 2（record/report/gate/review 共用）。"""
    try:
        return resolve_run_id(project, run_id, use_last)
    except LookupError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)


def _do_plan(
    base: str,
    head: str,
    repo: Path,
    root: Path,
    module_map: Path,
    tag_list: Optional[list[str]],
    title: str,
    runs_dir: Path,
    priority: Optional[Priority] = None,
) -> tuple[RunRecord, list[str], dict[str, list[str]], list[Scenario], list[str]]:
    """plan 确定性逻辑：影响面 -> 复用检索 -> create_run。抛 RuntimeError(git)。

    priority 与 run 共用口径（仅保留该优先级及更高，P0–P1 全跑），保证
    smoke 的复用清单与实际执行一致，避免 planned_scenarios 脱节。
    """
    if not repo.is_dir():
        raise RuntimeError(f"Git 仓库目录不存在: {repo}")
    git_available = is_git_repository(str(repo))
    files = changed_files(base, head, str(repo)) if git_available else []
    groups = classify(files, load_module_map(module_map))
    affected = sorted(m for m in groups if m != "__unmapped__")
    scs, errors = load_scenarios(root)
    rank = {Priority.P0: 0, Priority.P1: 1, Priority.P2: 2}
    reuse = [
        s
        for s in select(scs)
        if s.module in affected
        and (not tag_list or set(tag_list) <= set(s.tags))
        and (priority is None or rank[s.priority] <= rank[priority])
    ]
    commits: list[CommitInfo] = []
    head_commit = ""
    if git_available:
        try:
            head_commit = rev_parse(head, str(repo))
        except RuntimeError as e:
            typer.secho(f"[警告] 解析 head 失败，gate 将跳过内容一致性检查：{e}",
                        fg=typer.colors.YELLOW, err=True)
        try:
            # 运行记录只保留 short/subject 轻量展示（完整提交正文/文件清单见 atk context），避免 run.yaml 膨胀
            commits = [
                CommitInfo(short=c.get("short", ""), subject=c.get("subject", ""))
                for c in commit_log(base, head, str(repo))
            ]
        except Exception as e:
            typer.secho(f"[警告] 获取提交记录失败，已置空：{e}", fg=typer.colors.YELLOW, err=True)
    rec = create_run(
        base_ref=base if git_available else "",
        head_ref=head if git_available else "",
        head_commit=head_commit,
        affected_files=files,
        affected_modules=affected,
        planned_scenarios=[s.file for s in reuse],
        title=title,
        commits=commits,
        runs_dir=str(runs_dir),
    )
    return rec, affected, groups, reuse, errors


def _do_run(
    env: Optional[str],
    module: Optional[str],
    tag_list: Optional[list[str]],
    priority: Optional[Priority],
    env_file: Path,
    root: Path,
    report_dir: Path,
    junit: Optional[Path],
    on_result: Optional[Callable[[Any], None]] = None,
    render: bool = True,
    skip_ui: bool = False,
    extra_vars: Optional[dict[str, str]] = None,
) -> tuple[RunReport, Optional[Path], Optional[Path]]:
    """run 执行逻辑：Runner 跑场景 + HTML/JUnit 落盘。抛 KeyError(环境缺失)。

    render=False 时只执行不落盘，由调用方合并多模块结果后统一渲染
    （smoke 多模块一次渲染 report-latest.html，避免逐轮覆盖）。
    """
    runner = Runner(env_file=env_file, scenarios_root=root)
    report = runner.run(
        env_name=env,
        module=module,
        tags=tag_list,
        priority=priority,
        on_result=on_result,
        skip_ui=skip_ui,
        extra_vars=extra_vars,
    )
    if not render:
        return report, None, None
    html_path = render_html(report, Path(report_dir) / "report-latest.html")
    junit_path = write_junit(report, junit) if junit else None
    return report, html_path, junit_path


def _junit_for_module(
    junit: Optional[Path], mod: Optional[str], multi: bool
) -> Optional[Path]:
    """多模块共用 --junit 路径时按模块加后缀（如 junit.xml -> junit-demo.xml）。

    单模块/全量执行保持原路径，避免逐轮覆盖丢失他模块结果。
    """
    if not junit or not multi or not mod:
        return junit
    p = Path(junit)
    return p.parent / f"{p.stem}-{mod}{p.suffix}"


def _register_pending_ui_intents(rec: RunRecord, report: RunReport) -> int:
    """把待实测的 UI 场景登记成 pending 意图，供 gate 拦截。

    atk run 不执行 ui: 步骤，若不做登记，这些场景就既不通过也不失败，
    gate 无从判断"测过没有"。同名意图已存在时不覆盖（保留已回填补结论）。
    """
    known = {i.title for i in rec.intents}
    added = 0
    for r in report.results:
        if r.error_class != "ui_pending":
            continue
        name = r.scenario.scenario
        if name in known:
            continue
        rec.intents.append(
            IntentRecord(title=name, status="pending", note="待 AI 浏览器实测后回填")
        )
        known.add(name)
        added += 1
    return added


def _merge_report_into_run(report: RunReport, record_to: str, runs_dir: Path) -> None:
    """把 RunReport 结果并入指定运行记录（同场景重跑覆盖旧结果）。抛 KeyError(记录不存在)。"""
    rec = load_run(record_to, str(runs_dir))
    index = {s.file or s.name: i for i, s in enumerate(rec.scenarios)}
    for sm in (summarize(r) for r in report.results):
        key = sm.file or sm.name
        if key in index:
            rec.scenarios[index[key]] = sm
        else:
            index[key] = len(rec.scenarios)
            rec.scenarios.append(sm)
    _register_pending_ui_intents(rec, report)
    save_run(rec, str(runs_dir))


def _do_report(run_id: str, runs_dir: Path) -> Path:
    """report 渲染逻辑。抛 KeyError(记录不存在)。"""
    rec = load_run(run_id, str(runs_dir))
    return render_run_html(rec, Path(runs_dir) / run_id / "report.html")


def _do_gate(
    run_id: str, head: str, repo: Path, runs_dir: Path
) -> tuple[bool, list[tuple[bool, str, bool]], str, RunRecord]:
    """gate 判定逻辑。抛 KeyError(记录不存在)/RuntimeError(git)。

    返回 (是否放行, verdicts, 确认状态行, 运行记录)。
    verdicts 为 (ok, message, warn)：ok=False 拦截；warn=True 是仅提示项。
    """
    rec = load_run(run_id, str(runs_dir))
    verdicts: list[tuple[bool, str, bool]] = []
    if not rec.scenarios and not rec.intents:
        verdicts.append((False, "运行记录无场景结果也无意图（未执行或选中为空），不可作为门禁依据", False))
    if not rec.base_ref:
        verdicts.append((True, "运行记录未绑定 Git 基线，跳过变更一致性检查", False))
    else:
        current = changed_files(rec.base_ref, head, str(repo))
        if sorted(current) != sorted(rec.affected_files):
            verdicts.append((False, "变更已漂移：当前 diff 与运行记录不一致，请重新 plan", False))
        else:
            verdicts.append((True, f"变更一致（{len(current)} 个文件）", False))
        if rec.head_commit:
            try:
                now_sha = rev_parse(head, str(repo))
            except RuntimeError:
                now_sha = ""
            if now_sha and now_sha != rec.head_commit:
                verdicts.append((False, f"head 已前进：记录建于 {rec.head_commit[:12]}，当前 {now_sha[:12]}，请重新 plan", False))
        elif not rec.head_commit and current:
            verdicts.append((True, "记录无 head commit 信息（旧记录），跳过内容一致性检查", False))
        dirty = uncommitted_files(str(repo))
        if dirty:
            verdicts.append((True, f"⚠ 工作区有 {len(dirty)} 个未提交文件（三点 diff 不含其内容，门禁未覆盖）: "
                                   + ", ".join(dirty[:5]) + (" …" if len(dirty) > 5 else ""), True))
    case_fail = [s for s in rec.scenarios if not s.passed and s.error_class in ("assertion", "config")]
    if case_fail:
        verdicts.append((False, f"用例失败 {len(case_fail)} 个: " + ", ".join(s.name for s in case_fail), False))
    elif rec.scenarios:
        blocked_n = sum(
            1
            for s in rec.scenarios
            if not s.passed
            and s.error_class not in ("assertion", "config", "ui_pending", "skipped")
        )
        verdicts.append((True, f"复用场景 {len(rec.scenarios)} 个无用例失败"
                                + (f"（受阻 {blocked_n} 个）" if blocked_n else ""),
                         blocked_n > 0))
    # 草稿判定 = 运行时记录的 draft 与磁盘现状取并集：
    # 磁盘仍是草稿、或草稿文件已消失（reject 移走）都算未评审——
    # 防止"跑完草稿再 reject"留下未审定结果混过门禁；approve 转正后放行。
    unreviewed = sorted({
        s.name for s in rec.scenarios
        if (s.file and is_draft(s.file)) or (s.draft and s.file and not Path(s.file).exists())
    })
    if unreviewed:
        verdicts.append((False, f"存在未评审AI草稿 {len(unreviewed)} 个: "
                                + ", ".join(unreviewed)
                                + f"（请先 atk review-draft 评审转正，{DRAFT_TAG} tag 未去掉不得参与门禁）", False))
    bad_intents = [i for i in rec.intents if i.status in ("fail", "suspect")]
    if bad_intents:
        verdicts.append((False, "存在未定性结论: " + "; ".join(f"{i.title}[{i.status}]" for i in bad_intents), False))
    # UI 步骤 atk run 不执行，定性只能来自 AI 实测后的 record 回填；
    # 未回填即放行等于"没测过就说通过"，必须拦截。
    pending_intents = [i for i in rec.intents if i.status == "pending"]
    if pending_intents:
        verdicts.append(
            (
                False,
                f"存在未实测回填的 UI 意图 {len(pending_intents)} 条: "
                + "; ".join(i.title for i in pending_intents)
                + "（请用 AI 浏览器实测后 atk record --last --from-json 回填）",
                False,
            )
        )
    pending_ui = [s for s in rec.scenarios if s.error_class == "ui_pending"]
    if pending_ui and not pending_intents:
        verdicts.append(
            (
                True,
                f"UI 待实测场景 {len(pending_ui)} 个已全部回填（仅提示）",
                False,
            )
        )
    blocked = [i for i in rec.intents if i.status == "blocked"]
    if blocked:
        verdicts.append((True, f"受阻意图 {len(blocked)} 条（不拦截，但需关注）", True))
    reviews = getattr(rec, "reviews", []) or []
    status, last = review_status(reviews)
    if status == "rejected":
        assert last is not None
        review_line = f"⚠ 已被开发驳回 by {last.by}（仅告警，不拦截）" + (f"：{last.note}" if last.note else "")
    elif status == "unconfirmed":
        review_line = "⚠ 未经开发确认（仅告警，不拦截）"
    else:
        assert last is not None
        review_line = f"✓ 已获开发确认 by {last.by}"
    passed = all(ok for ok, _, _ in verdicts)
    return passed, verdicts, review_line, rec


def _scenario_json(s: Scenario) -> dict[str, Any]:
    return {
        "name": s.scenario,
        "file": s.file,
        "module": s.module,
        "priority": s.priority.value,
        "tags": s.tags,
        "env": s.env,
    }


def _plan_json(
    rec: RunRecord,
    affected: list[str],
    groups: dict[str, list[str]],
    reuse: list[Scenario],
    errors: list[str],
) -> dict[str, Any]:
    return {
        "run_id": rec.run_id,
        "title": rec.title,
        "base_ref": rec.base_ref,
        "head_ref": rec.head_ref,
        "affected_files": rec.affected_files,
        "affected_modules": affected,
        "groups": groups,
        "reuse": [_scenario_json(s) for s in reuse],
        "planned_scenarios": rec.planned_scenarios,
        "load_errors": errors,
        "next_actions": [
            {
                "owner": "ai",
                "action": "run_reuse_scenarios",
                "command": f"atk run --record-to {rec.run_id}",
            },
            {
                "owner": "ai",
                "action": "execute_uncovered_ui_intents",
                "command": f"atk record {rec.run_id} --from-json <result.json>",
            },
            {
                "owner": "human",
                "action": "review_ai_draft_expectations",
                "command": "atk review-draft <draft.yaml> --by <name> --verdict approve|reject",
            },
            {
                "owner": "human",
                "action": "whole_feature_review",
                "command": f"atk review {rec.run_id} --by <name> --verdict approve|reject",
            },
            {
                "owner": "ci",
                "action": "gate",
                "command": f"atk gate {rec.run_id} --format json",
            },
        ],
    }


def _gate_json(
    passed: bool,
    verdicts: list[tuple[bool, str, bool]],
    review_line: str,
    rec: RunRecord,
) -> dict[str, Any]:
    return {
        "run_id": rec.run_id,
        "passed": passed,
        "exit_code": 0 if passed else 1,
        "verdicts": [{"ok": ok, "message": msg, "warn": warn} for ok, msg, warn in verdicts],
        "review": review_line,
        "blocking": [msg for ok, msg, _ in verdicts if not ok],
        "warnings": [review_line] + [msg for ok, msg, warn in verdicts if ok and warn],
    }


@app.command()
def validate(
    root: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP),
):
    """校验场景库合法性（QA 提交前自查）。退出码 0/1。"""
    root = _resolve(root, _layout_paths()["root"])
    scs, errors = load_scenarios(root)
    for e in errors:
        typer.secho(f"[错误] {e}", fg=typer.colors.RED)
    dup = {n for n, c in Counter(s.scenario for s in scs).items() if c > 1}
    for n in sorted(dup):
        files = [s.file for s in scs if s.scenario == n]
        typer.secho(f"[警告] 场景名重复: {n} -> {', '.join(files)}", fg=typer.colors.YELLOW)
    mismatches = [s for s in scs if s.dir_module and s.module != s.dir_module]
    for s in mismatches[:5]:
        typer.secho(
            f"[警告] module 与目录不一致: {s.file} 目录={s.dir_module} "
            f"module={s.module}（--module 与影响面分析按字段匹配，目录名匹配不到）",
            fg=typer.colors.YELLOW,
        )
    if len(mismatches) > 5:
        typer.secho(
            f"[警告] …另有 {len(mismatches) - 5} 个 module 与目录不一致（同上口径）",
            fg=typer.colors.YELLOW,
        )
    ok = len([s for s in scs if s.scenario not in dup])
    typer.echo(
        f"{len(errors)} 个文件错误，{len(dup)} 个重名，"
        f"{len(mismatches)} 个 module 与目录不一致，{ok} 个场景通过校验"
    )
    raise typer.Exit(code=1 if errors else 0)


@app.command()
def plan(
    base: str = typer.Option("HEAD~1"),
    head: str = typer.Option("HEAD"),
    repo: Path = typer.Option("."),
    root: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP),
    module_map: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP),
    tags: Optional[str] = typer.Option(None, help="复用场景需包含的标签，逗号分隔"),
    title: str = typer.Option("", help="功能标题（整单）"),
    runs_dir: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP),
    project: Path = typer.Option(".", "--project", help="项目根目录（.atk/last-run.json 所在位置）"),
    output_format: str = typer.Option("text", "--format", help="输出格式：text|json"),
):
    """创建即时层运行记录并输出执行计划骨架（复用场景清单 + 待补全意图）。

    同时把 run_id 写入 .atk/last-run.json，后续命令可用 --last 免拼接。
    退出码：0=成功；2=git 失败（与 smoke/context/gate 一致）。
    """
    if output_format not in ("text", "json"):
        raise typer.BadParameter("format 必须是 text|json")
    tag_list = _parse_tags(tags)
    L = _layout_paths(project)
    root = _resolve(root, L["root"])
    module_map = _resolve(module_map, L["module_map"])
    runs_dir = _resolve(runs_dir, L["runs_dir"])
    try:
        rec, affected, groups, reuse, errors = _do_plan(
            base, head, repo, root, module_map, tag_list, title, runs_dir
        )
    except RuntimeError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    write_last_run(project, rec.run_id, base=rec.base_ref, head=rec.head_ref, title=title, created_at=rec.created_at)
    if output_format == "json":
        typer.echo(json.dumps(_plan_json(rec, affected, groups, reuse, errors), ensure_ascii=False, indent=2))
        raise typer.Exit(code=0)
    typer.echo(f"功能：{title}" if title else "功能：（未命名）")
    typer.echo(f"run_id: {rec.run_id}")
    typer.echo(f"affected_modules: {', '.join(affected) if affected else '（无映射命中）'}")
    for e in errors:
        typer.secho(f"[跳过] {e}", fg=typer.colors.YELLOW, err=True)
    for u in groups.get("__unmapped__", []):
        typer.echo(f"unmapped: {u}")
    for s in reuse:
        typer.echo(f"reuse: [{s.priority.value}] {s.scenario} ({s.file})")
    typer.echo(
        f"\n下一步（Agent 编排）：\n"
        f"  1. atk run --record-to {rec.run_id}   # 执行复用场景并入记录\n"
        f"  2. 对无覆盖意图用 ego-browser 实测，然后\n"
        f"     atk record {rec.run_id} --title ... --status pass|fail|suspect|blocked [--evidence 截图]\n"
        f"  3. atk report {rec.run_id}"
    )


_INTENT_STATUS = {"pass", "fail", "suspect", "blocked"}


@app.command()
def record(
    run_id: Optional[str] = typer.Argument(None, help="运行记录 ID；可用 --last 代替"),
    title: Optional[str] = typer.Option(None, help="测试意图标题"),
    status: Optional[str] = typer.Option(
        None, help="必填（或 --from-json 提供）：pass|fail|suspect|blocked"
    ),
    note: str = typer.Option("", help="结论描述/根因猜测"),
    evidence: Optional[str] = typer.Option(None, help="证据文件路径，逗号分隔"),
    from_json: Optional[Path] = typer.Option(
        None, "--from-json", help="从 JSON 文件读取 AI/UI 实测结果"
    ),
    runs_dir: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP),
    last: bool = typer.Option(False, "--last", help="使用最近一次运行记录（无需拼 run_id）"),
    project: Path = typer.Option(".", "--project", help="项目根目录（.atk/last-run.json 所在位置）"),
    output_format: str = typer.Option("text", "--format", help="输出格式：text|json"),
):
    """回填一条即时层意图的执行结果（Agent 用浏览器实测后调用）。

    同名意图就地更新（含 run 自动登记的 pending），不会重复追加。
    """
    if output_format not in ("text", "json"):
        raise typer.BadParameter("format 必须是 text|json")
    run_id = _run_id_arg(run_id, last, project)
    runs_dir = _resolve(runs_dir, _layout_paths(project)["runs_dir"])
    if from_json:
        try:
            payload = json.loads(from_json.read_text(encoding="utf-8"))
        except Exception as e:
            typer.secho(f"读取 --from-json 失败：{e}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        if not isinstance(payload, dict):
            typer.secho("--from-json 顶层必须是对象", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        if payload.get("run_id") and payload["run_id"] != run_id:
            typer.secho(
                f"--from-json run_id 不匹配：{payload['run_id']} != {run_id}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        title = str(payload.get("title") or title or "")
        status = str(payload.get("status") or status)
        note = str(payload.get("note") or note or "")
        ev = payload.get("evidence") or payload.get("evidence_files") or []
        if isinstance(ev, str):
            evidence = ",".join([evidence, ev]) if evidence else ev
        elif isinstance(ev, list):
            joined = ",".join(str(x) for x in ev)
            evidence = ",".join([evidence, joined]) if evidence and joined else (evidence or joined)
        else:
            typer.secho("evidence 必须是字符串或字符串数组", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
    if not title or not title.strip():
        raise typer.BadParameter("缺少 --title，或在 --from-json 中提供 title")
    if not status:
        raise typer.BadParameter(
            "缺少 --status（pass|fail|suspect|blocked），或在 --from-json 中提供 status；"
            "状态必须由实测结论显式给出，不设默认值"
        )
    if status not in _INTENT_STATUS:
        raise typer.BadParameter(f"status 必须是 {'/'.join(sorted(_INTENT_STATUS))}")
    if status in ("fail", "suspect") and not note.strip():
        typer.secho(f"{status} 必须带 --note 说明证据、复现步骤或疑点", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        rec = load_run(run_id, str(runs_dir))
    except KeyError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    sources = [p.strip() for p in evidence.split(",")] if evidence else []
    saved = add_evidence(rec, sources, str(runs_dir))
    missing = len([p for p in sources if p]) - len(saved)
    # 回填语义：同名意图就地更新（含 run 自动登记的 pending），避免重复条目让 gate 误判
    key = title.strip()
    existing = next((i for i in rec.intents if i.title == key), None)
    if existing is not None:
        was = existing.status
        existing.status = status
        existing.note = note
        existing.evidence = list(dict.fromkeys(list(existing.evidence) + saved))
        action = f"已回填：{key} [{was} -> {status}]"
        upserted = True
    else:
        rec.intents.append(IntentRecord(title=key, status=status, note=note, evidence=saved))
        action = f"已记录：{key} [{status}]"
        upserted = False
    save_run(rec, str(runs_dir))
    warn = f"（{missing} 个证据文件不存在已跳过）" if missing else ""
    if output_format != "json":
        typer.echo(f"{action} 证据 {len(saved)} 项{warn}")

    if output_format == "json":
        pending_after = sum(1 for i in rec.intents if i.status == "pending")
        typer.echo(
            json.dumps(
                {
                    "run_id": run_id,
                    "title": key,
                    "status": status,
                    "upserted": upserted,
                    "evidence": saved,
                    "evidence_missing": missing,
                    "pending_intents": pending_after,
                    "next": (
                        ["atk gate --last --format json"]
                        if pending_after == 0
                        else ["atk record --last --title <下一条意图> --status <pass|fail|suspect|blocked>"]
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )


@app.command()
def review(
    run_id: Optional[str] = typer.Argument(None, help="运行记录 ID；可用 --last 代替"),
    by: str = typer.Option(..., help="确认人"),
    verdict: str = typer.Option(..., help="approve|reject"),
    note: str = typer.Option("", help="确认备注；reject 时必填"),
    runs_dir: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP),
    last: bool = typer.Option(False, "--last", help="使用最近一次运行记录（无需拼 run_id）"),
    project: Path = typer.Option(".", "--project", help="项目根目录（.atk/last-run.json 所在位置）"),
    output_format: str = typer.Option("text", "--format", help="输出格式：text|json"),
):
    """开发确认运行记录（整单确认）。reject 必须带 note。

    退出码：0=确认成功；1=reject 缺 --note；2=verdict 非法或记录不存在。
    """
    if output_format not in ("text", "json"):
        raise typer.BadParameter("format 必须是 text|json")
    if verdict not in ("approve", "reject"):
        raise typer.BadParameter("verdict 必须是 approve|reject")
    if verdict == "reject" and not note.strip():
        typer.secho("reject 必须带 --note 说明原因", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    run_id = _run_id_arg(run_id, last, project)
    runs_dir = _resolve(runs_dir, _layout_paths(project)["runs_dir"])
    try:
        rec = load_run(run_id, str(runs_dir))
    except KeyError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    now = dt.datetime.now().isoformat(timespec="seconds")
    rec.reviews.append(ReviewRecord(by=by, verdict=verdict, note=note, at=now))
    save_run(rec, str(runs_dir))
    if output_format != "json":
        typer.echo(f"已确认：{run_id} by {by} [{verdict}]" + (f" {note}" if note else ""))
    if output_format == "json":
        status, _last = review_status(rec.reviews)
        typer.echo(
            json.dumps(
                {
                    "run_id": run_id,
                    "by": by,
                    "verdict": verdict,
                    "review_status": status,
                    "next": [f"atk gate --last --format json"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


@app.command("review-draft")
def review_draft_cmd(
    path: Path = typer.Argument(..., help="草稿场景 YAML 路径"),
    by: str = typer.Option(..., help="评审人"),
    verdict: str = typer.Option(..., help="approve|reject"),
    note: str = typer.Option("", help="评审备注；reject 时必填"),
    reports_dir: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP + "；评审日志与驳回文件目录"),
):
    """评审AI草稿：approve 去 tag 转正，reject 移走留档。

    退出码：0=评审落盘；1=reject 缺 --note；2=verdict 非法或非草稿文件。
    """
    reports_dir = _resolve(reports_dir, _layout_paths()["reports_dir"])
    if verdict not in ("approve", "reject"):
        raise typer.BadParameter("verdict 必须是 approve|reject")
    if verdict == "reject" and not note.strip():
        typer.secho("reject 必须带 --note 说明原因", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        msg = review_draft(path, by, verdict, note, reports_dir)
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    typer.echo(msg)


@app.command()
def run(
    env: Optional[str] = typer.Option(
        None, help="环境名；省略时使用各场景自身的 env 字段"
    ),
    module: Optional[str] = typer.Option(None),
    tags: Optional[str] = typer.Option(None, help="逗号分隔，如 smoke,order"),
    priority: Optional[Priority] = typer.Option(None),
    env_file: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP),
    root: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP),
    report_dir: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP),
    record_to: Optional[str] = typer.Option(None, help="把本次结果并入指定运行记录"),
    record_new: bool = typer.Option(False, "--record-new", help="新建一条运行记录并写入 reports/runs"),
    runs_dir: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP),
    junit: Optional[Path] = typer.Option(None, help="同时输出 JUnit XML 到指定路径"),
    skip_ui: bool = typer.Option(
        False, "--skip-ui", help="跳过 ui: 步骤（不计入结论，也不登记待实测意图）"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="只列出选中的场景与环境，不实际执行"
    ),
    allow_empty: bool = typer.Option(
        False, "--allow-empty", help="允许选中 0 场景仍视为通过（默认 0 选中=受阻 exit 2）"
    ),
    set_vars: Optional[list[str]] = typer.Option(
        None, "--set", help="临时变量 key=value（可多次）；优先级高于 env vars 与 fixture"
    ),
    project: Path = typer.Option(".", "--project", help="项目根目录（.atk/last-run.json 所在位置）"),
    output_format: str = typer.Option("text", "--format", help="输出格式：text|json"),
):
    """执行场景并生成 HTML 报告。

    退出码：0=全部通过；1=存在用例失败或场景加载错误；2=仅环境受阻或选中为空。
    含 ui: 步骤的场景标记为「待实测」，不计失败也不计受阻，由 atk gate 检查是否已回填。
    """
    if output_format not in ("text", "json"):
        raise typer.BadParameter("format 必须是 text|json")
    tag_list = _parse_tags(tags)
    extra_vars = _parse_set(set_vars)
    L = _layout_paths(project)
    env_file = _resolve(env_file, L["env_file"])
    root = _resolve(root, L["root"])
    report_dir = _resolve(report_dir, L["report_dir"])
    runs_dir = _resolve(runs_dir, L["runs_dir"])

    def say(message: str) -> None:
        if output_format != "json":
            typer.echo(message)

    if dry_run:
        scs, errors = load_scenarios(root)
        sel = select(scs, module, tag_list, priority)
        if output_format == "json":
            typer.echo(json.dumps({
                "dry_run": True,
                "selected": [_scenario_json(s) for s in sel],
                "load_errors": errors,
            }, ensure_ascii=False, indent=2))
        else:
            for e in errors:
                typer.secho(f"[跳过] {e}", fg=typer.colors.YELLOW, err=True)
            for s in sel:
                typer.echo(f"selected: [{s.priority.value}] {s.scenario} "
                           f"(module={s.module} env={s.env}) ({s.file})")
            typer.echo(f"共选中 {len(sel)} 个场景（未执行）")
        raise typer.Exit(code=1 if errors else 0)

    def on_result(r):
        if output_format == "json":
            return
        if r.error_class == "ui_pending":
            mark = "…"
        else:
            mark = "✓" if r.passed else "✗"
        last = r.steps[-1].detail if r.steps else r.error_class
        suffix = "" if r.passed else f" —— {r.error_class}: {last[:120]}"
        say(
            f"{mark} [{r.scenario.priority.value}] {r.scenario.scenario}{suffix}"
        )

    try:
        report, path, jp = _do_run(
            env, module, tag_list, priority, env_file, root, report_dir, junit,
            on_result=on_result,
            skip_ui=skip_ui,
            extra_vars=extra_vars,
        )
    except KeyError as e:
        typer.secho(f"环境配置错误：{e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    except FileNotFoundError as e:
        typer.secho(
            f"配置文件不存在：{e.filename}（请在项目根目录运行，或用 --env-file 指定；"
            "未初始化过的项目先跑 atk init——AI 入口在 AGENTS.md，正文在 .atk/skills/）",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(code=2)

    for w in report.env_warnings:
        typer.secho(f"[警告] {w}", fg=typer.colors.YELLOW, err=True)
    if report.total == 0 and not report.load_errors and not allow_empty:
        typer.secho(
            "选中 0 个场景（env/module/tags/priority 过滤后为空）——空跑不算通过；"
            "确认选择条件可用 atk run --dry-run，场景库为空先跑 atk init 或让 AI 按 "
            "atk-authoring 起草；确需空跑加 --allow-empty",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(code=2)

    extra = ""
    if report.pending_ui_count:
        extra += f"，待实测 {report.pending_ui_count}"
    if report.skipped_count:
        extra += f"，跳过 {report.skipped_count}"
    say(
        f"\n结果：通过 {report.passed_count}/{report.total}"
        f"（用例失败 {report.failed_count}，受阻 {report.blocked_count}{extra}，"
        f"加载跳过 {len(report.load_errors)}）\n报告：{path}"
    )
    if report.pending_ui_count and not skip_ui:
        say(
            "提示：UI 步骤由 AI 浏览器实测后回填，"
            "未回填前 atk gate 会拦截（如需忽略请加 --skip-ui）"
        )
    if jp:
        say(f"JUnit：{jp}")
    created_run_id: str | None = None
    if record_new:
        rec = create_run(runs_dir=runs_dir)
        rec.scenarios.extend(summarize(r) for r in report.results)
        _register_pending_ui_intents(rec, report)
        save_run(rec, str(runs_dir))
        write_last_run(project, rec.run_id, env=env or "", created_at=rec.created_at)
        created_run_id = rec.run_id
        say(f"已写入运行记录 {rec.run_id}")
    if record_to:
        try:
            _merge_report_into_run(report, record_to, runs_dir)
        except KeyError as e:
            typer.secho(str(e), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        say(f"已并入运行记录 {record_to}")

    if output_format == "json":
        typer.echo(
            json.dumps(
                {
                    "exit_code": report.exit_code,
                    "env": report.env_name,
                    "scenarios": {
                        "total": report.total,
                        "passed": report.passed_count,
                        "failed": report.failed_count,
                        "blocked": report.blocked_count,
                        "pending_ui": report.pending_ui_count,
                        "skipped": report.skipped_count,
                    },
                    "load_errors": report.load_errors,
                    "report": str(path) if path else None,
                    "junit": str(jp) if jp else None,
                    "run_id": created_run_id or record_to,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    raise typer.Exit(code=report.exit_code)


@app.command()
def context(
    base: str = typer.Option("HEAD~1", help="基线引用"),
    head: str = typer.Option("HEAD", help="目标引用"),
    repo: Path = typer.Option(".", help="被测仓库路径"),
    module_map: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP),
    root: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP + "；场景库根目录"),
    context_out: Optional[Path] = typer.Option(
        None, "--context-out", help="把变更上下文包写入指定文件（默认打印 stdout）"
    ),
    max_patch_lines: int = typer.Option(
        200, "--max-patch", "--max-patch-lines", help="单文件补丁进入上下文的最大行数"
    ),
    output_format: str = typer.Option("markdown", "--format", help="输出格式：markdown|json"),
):
    """输出确定性变更上下文包（提交记录 + 补丁 + 模块归属），供 Agent 按 atk-authoring 协议编写场景。"""
    if output_format not in ("markdown", "json"):
        raise typer.BadParameter("format 必须是 markdown|json")
    L = _layout_paths()
    module_map = _resolve(module_map, L["module_map"])
    root = _resolve(root, L["root"])
    try:
        ctx = build_context(
            str(base), str(head), str(repo), module_map, root,
            max_patch_lines=max_patch_lines,
        )
    except RuntimeError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    if not ctx["files"]:
        typer.echo(f"区间 {base}...{head} 无变更文件，无需生成场景。")
        raise typer.Exit(code=0)

    if output_format == "json":
        body = json.dumps(ctx, ensure_ascii=False, indent=2)
    else:
        body = render_markdown(ctx)
    next_steps = (
        "\n下一步（Agent 编排，参考 atk-authoring 协议）：\n"
        "  1. 阅读上方上下文包，按系统规则编写场景 YAML（禁止重复现有场景）\n"
        "  2. 写入 scenarios/<module>/gen-*.yaml，tags 加 ai-generated\n"
        "  3. atk validate 校验 -> 人工评审 expect -> atk run --tags ai-generated"
    )
    if context_out:
        context_out.parent.mkdir(parents=True, exist_ok=True)
        context_out.write_text(body, encoding="utf-8")
        typer.echo(f"变更上下文包已写入：{context_out}")
        typer.echo(next_steps)
    else:
        typer.echo(body)
        # json 直出 stdout 时省略下一步提示，保持机器可解析（见 test_context_json_format）
        if output_format != "json":
            typer.echo(next_steps)
    raise typer.Exit(code=0)


@app.command()
def last(
    project: Path = typer.Option(".", "--project", help="项目根目录（.atk/last-run.json 所在位置）"),
    output_format: str = typer.Option("text", "--format", help="输出格式：text|json"),
):
    """显示最近一次运行记录上下文，供 Agent 确认当前操作对象。"""
    if output_format not in ("text", "json"):
        raise typer.BadParameter("format 必须是 text|json")
    data = read_last_run(project)
    if data is None:
        typer.secho(
            "暂无最近运行记录，请先执行 atk plan 或 atk smoke", fg=typer.colors.YELLOW, err=True
        )
        raise typer.Exit(code=2)
    if output_format == "json":
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        raise typer.Exit(code=0)
    typer.echo(f"run_id: {data['run_id']}")
    for k in ("title", "base", "head", "env", "created_at"):
        if data.get(k):
            typer.echo(f"{k}: {data[k]}")


@app.command()
def report(
    run_id: Optional[str] = typer.Argument(None, help="运行记录 ID；可用 --last 代替"),
    runs_dir: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP),
    last: bool = typer.Option(False, "--last", help="使用最近一次运行记录（无需拼 run_id）"),
    project: Path = typer.Option(".", "--project", help="项目根目录（.atk/last-run.json 所在位置）"),
    output_format: str = typer.Option("text", "--format", help="输出格式：text|json"),
):
    """渲染运行记录为 HTML 报告。"""
    if output_format not in ("text", "json"):
        raise typer.BadParameter("format 必须是 text|json")
    run_id = _run_id_arg(run_id, last, project)
    runs_dir = _resolve(runs_dir, _layout_paths(project)["runs_dir"])
    try:
        out = _do_report(run_id, runs_dir)
    except KeyError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    if output_format == "json":
        typer.echo(
            json.dumps({"run_id": run_id, "report": str(out)}, ensure_ascii=False, indent=2)
        )
    else:
        typer.echo(f"报告：{out}")


@app.command()
def gate(
    run_id: Optional[str] = typer.Argument(None, help="运行记录 ID；可用 --last 代替"),
    head: str = typer.Option("HEAD", help="待合并的变更引用"),
    repo: Path = typer.Option("."),
    runs_dir: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP),
    last: bool = typer.Option(False, "--last", help="使用最近一次运行记录（无需拼 run_id）"),
    project: Path = typer.Option(".", "--project", help="项目根目录（.atk/last-run.json 所在位置）"),
    output_format: str = typer.Option("text", "--format", help="输出格式：text|json"),
):
    """合并门禁：校验运行记录与当前变更一致且无失败/未定性结论。

    退出码：0=放行；1=拦截；2=记录不存在。
    """
    if output_format not in ("text", "json"):
        raise typer.BadParameter("format 必须是 text|json")
    run_id = _run_id_arg(run_id, last, project)
    runs_dir = _resolve(runs_dir, _layout_paths(project)["runs_dir"])
    try:
        passed, verdicts, review_line, _rec = _do_gate(run_id, head, repo, runs_dir)
    except KeyError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    except RuntimeError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    if output_format == "json":
        typer.echo(json.dumps(_gate_json(passed, verdicts, review_line, _rec), ensure_ascii=False, indent=2))
        raise typer.Exit(code=0 if passed else 1)

    for ok, msg, _warn in verdicts:
        typer.echo(("✓ " if ok else "✗ ") + msg)
    # 第四项只读检查：开发确认状态，仅告警、永不拦截（按最后一条 review 判定）
    typer.echo(review_line)
    typer.echo(f"\n门禁结论：{'放行' if passed else '拦截'}")
    raise typer.Exit(code=0 if passed else 1)


@app.command()
def smoke(
    base: str = typer.Option("HEAD~1", help="基线引用"),
    head: str = typer.Option("HEAD", help="目标引用"),
    repo: Path = typer.Option(".", help="被测仓库路径"),
    env: Optional[str] = typer.Option(None, help="环境名；省略时使用各场景自身的 env 字段"),
    title: str = typer.Option("", help="功能标题（整单）"),
    tags: Optional[str] = typer.Option(None, help="复用/执行场景需包含的标签，逗号分隔"),
    priority: Optional[Priority] = typer.Option(None, help="仅执行该优先级及更高（P0–P1 全跑）"),
    junit: Optional[Path] = typer.Option(None, help="同时输出 JUnit XML 到指定路径"),
    root: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP + "；场景库根目录"),
    module_map: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP + "；模块映射表"),
    env_file: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP + "；环境配置文件"),
    report_dir: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP + "；run 报告输出目录"),
    runs_dir: Optional[Path] = typer.Option(None, help=_LAYOUT_HELP + "；运行记录目录"),
    skip_ui: bool = typer.Option(
        False, "--skip-ui", help="跳过 ui: 步骤（不计入结论，也不登记待实测意图）"
    ),
    allow_empty: bool = typer.Option(
        False, "--allow-empty", help="允许选中 0 场景仍视为通过（默认 0 选中=受阻 exit 2）"
    ),
    set_vars: Optional[list[str]] = typer.Option(
        None, "--set", help="临时变量 key=value（可多次）；优先级高于 env vars 与 fixture"
    ),
    project: Path = typer.Option(".", "--project", help="项目根目录（.atk/last-run.json 所在位置）"),
    output_format: str = typer.Option("text", "--format", help="输出格式：text|json"),
):
    """一键冒烟：plan→run(--record-to)→report→gate（函数复用）。

    UI 实测不进命令：无覆盖意图需后续 AI 浏览器实测后 atk record 回填。
    --format json 输出含 run_id 与 next 建议命令的 manifest，供 Agent 直接消费。
    退出码：0=放行；1=用例失败/门禁拦截；2=环境受阻、空选中或记录缺失。
    """
    if output_format not in ("text", "json"):
        raise typer.BadParameter("format 必须是 text|json")
    tag_list = _parse_tags(tags)
    extra_vars = _parse_set(set_vars)
    L = _layout_paths(project)
    root = _resolve(root, L["root"])
    module_map = _resolve(module_map, L["module_map"])
    env_file = _resolve(env_file, L["env_file"])
    report_dir = _resolve(report_dir, L["report_dir"])
    runs_dir = _resolve(runs_dir, L["runs_dir"])
    # 1) plan 逻辑复用（新建 run_id；priority 与执行侧同口径过滤复用清单）
    try:
        rec, affected, groups, reuse, errors = _do_plan(
            base, head, repo, root, module_map, tag_list, title, runs_dir, priority
        )
    except RuntimeError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    run_id = rec.run_id
    write_last_run(
        project, run_id, base=base, head=head, title=title,
        env=env or "", created_at=rec.created_at,
    )
    # json 模式下只输出最终 manifest，中间过程全部静默，保证机器可解析
    def say(msg: str) -> None:
        if output_format != "json":
            typer.echo(msg)

    say(f"功能：{title}" if title else "功能：（未命名）")
    say(f"run_id: {run_id}")
    say(f"affected_modules: {', '.join(affected) if affected else '（无映射命中）'}")
    for e in errors:
        typer.secho(f"[跳过] {e}", fg=typer.colors.YELLOW, err=True)
    for u in groups.get("__unmapped__", []):
        say(f"unmapped: {u}")
    for s in reuse:
        say(f"reuse: [{s.priority.value}] {s.scenario} ({s.file})")

    # 2) run 逻辑复用：按 affected_modules 逐模块执行并逐个并入记录；
    #    各模块结果内存合并后一次渲染 report-latest.html，junit 按模块分文件
    def on_result(r: Any) -> None:
        if output_format == "json":
            return
        if r.error_class == "ui_pending":
            mark = "…"
        else:
            mark = "✓" if r.passed else "✗"
        last = r.steps[-1].detail if r.steps else r.error_class
        suffix = "" if r.passed else f" —— {r.error_class}: {last[:120]}"
        typer.echo(f"{mark} [{r.scenario.priority.value}] {r.scenario.scenario}{suffix}")

    full_run = not affected
    modules_to_run: list[Optional[str]] = list(affected) if affected else [None]
    multi = len(modules_to_run) > 1
    run_exit = 0
    run_error: Optional[Exception] = None
    module_reports: list[tuple[str, RunReport]] = []
    latest_path: Optional[Path] = None
    empty_run = False
    for mod in modules_to_run:
        label = mod if mod else "全量"
        try:
            report, _, _ = _do_run(
                env, mod, tag_list, priority, env_file, root, report_dir, None,
                on_result=on_result, render=False, skip_ui=skip_ui,
                extra_vars=extra_vars,
            )
        except KeyError as e:
            typer.secho(f"环境配置错误：{e}", fg=typer.colors.RED, err=True)
            run_exit = 2
            run_error = e
            break
        except FileNotFoundError as e:
            typer.secho(
                f"配置文件不存在：{e.filename}（请在项目根目录运行，或用 --env-file 指定；"
                "未初始化过的项目先跑 atk init——AI 入口在 AGENTS.md，正文在 .atk/skills/）",
                fg=typer.colors.RED, err=True,
            )
            run_exit = 2
            run_error = e
            break
        module_junit = _junit_for_module(junit, mod, multi)
        junit_path = write_junit(report, module_junit) if module_junit else None
        say(
            f"\n结果[{label}]：通过 {report.passed_count}/{report.total}"
            f"（用例失败 {report.failed_count}，受阻 {report.blocked_count}，"
            f"待实测 {report.pending_ui_count}，加载跳过 {len(report.load_errors)}）"
        )
        if junit_path:
            say(f"JUnit：{junit_path}")
        module_reports.append((label, report))
        try:
            _merge_report_into_run(report, run_id, runs_dir)
        except KeyError as e:
            typer.secho(str(e), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        say(f"已并入运行记录 {run_id}（模块 {label}）")
        if report.exit_code == 1:
            run_exit = 1
        elif report.exit_code == 2 and run_exit == 0:
            run_exit = 2

    # 空 affected 全量执行后回填 planned_scenarios 为实际执行文件，消脱节
    if full_run and run_error is None:
        try:
            fresh_rec = load_run(run_id, str(runs_dir))
            fresh_rec.planned_scenarios = sorted(
                {s.file for s in fresh_rec.scenarios if s.file}
            )
            save_run(fresh_rec, str(runs_dir))
        except KeyError as e:
            typer.secho(str(e), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)

    # 合并各模块结果一次渲染，不再逐轮覆盖 report-latest.html
    if module_reports:
        combined = RunReport(
            env_name=module_reports[0][1].env_name,
            started_at=module_reports[0][1].started_at,
            results=[r for _, rep in module_reports for r in rep.results],
            load_errors=[e for _, rep in module_reports for e in rep.load_errors],
            env_warnings=[w for _, rep in module_reports for w in rep.env_warnings],
        )
        for w in dict.fromkeys(combined.env_warnings):
            typer.secho(f"[警告] {w}", fg=typer.colors.YELLOW, err=True)
        latest_path = render_html(combined, Path(report_dir) / "report-latest.html")
        say(
            f"\n合计：通过 {combined.passed_count}/{combined.total}"
            f"（用例失败 {combined.failed_count}，受阻 {combined.blocked_count}，"
            f"待实测 {combined.pending_ui_count}，"
            f"加载跳过 {len(combined.load_errors)}）\n报告：{latest_path}"
        )
        if combined.total == 0 and not combined.load_errors and not allow_empty \
                and run_error is None:
            typer.secho(
                "选中 0 个场景（模块/tags/priority 过滤后为空）——空跑不算通过，"
                "门禁已跳过；确需空跑加 --allow-empty",
                fg=typer.colors.RED, err=True,
            )
            run_exit = 2
            empty_run = True

    # 3) report 逻辑复用
    try:
        run_html = _do_report(run_id, runs_dir)
    except KeyError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    say(f"报告：{run_html}")

    # 4) gate 逻辑复用：run 异常（exit=2）时跳过，避免“放行”误读
    gate_exit = 0
    gate_skipped = run_error is not None or empty_run
    if gate_skipped:
        say("门禁已跳过（执行异常或空选中，exit=2，结果不可信）")
        gate_exit = 2
    else:
        try:
            passed, verdicts, review_line, _grec = _do_gate(run_id, head, repo, runs_dir)
        except KeyError as e:
            typer.secho(str(e), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        except RuntimeError as e:
            typer.secho(str(e), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        for ok, msg, _warn in verdicts:
            say(("✓ " if ok else "✗ ") + msg)
        say(review_line)
        say(f"\n门禁结论：{'放行' if passed else '拦截'}")
        gate_exit = 0 if passed else 1
        if run_exit == 2:
            say("⚠ 执行受阻（exit=2），门禁结论仅供参考，请先排查环境/受阻场景")

    # 5) 待办清单（失败场景名 + 无覆盖意图提示 + 需审草稿提示位）
    try:
        fresh = load_run(run_id, str(runs_dir))
    except KeyError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    case_fail = [s for s in fresh.scenarios if not s.passed and s.error_class in ("assertion", "config")]
    pending = [i.title for i in fresh.intents if i.status == "pending"]
    drafts = [
        s.name for s in fresh.scenarios
        if (s.file and is_draft(s.file))
        or (s.draft and s.file and not Path(s.file).exists())
    ]

    if output_format == "json":
        next_steps: list[str] = []
        if pending:
            for t in pending:
                next_steps.append(
                    f'atk record --last --title "{t}" '
                    "--status <pass|fail|suspect|blocked> --note <实测结论>"
                )
            next_steps.append("atk gate --last --format json")
        elif case_fail:
            rerun = f"atk smoke --env {env}" if env else "atk smoke"
            next_steps.append(f"修复失败场景后重跑：{rerun}")
        else:
            next_steps.append("atk review --last --by <姓名> --verdict approve")
            next_steps.append("atk gate --last --format json")
        typer.echo(
            json.dumps(
                {
                    "run_id": run_id,
                    "title": title,
                    "exit_code": run_exit if run_exit else gate_exit,
                    "gate": (None if gate_skipped else (gate_exit == 0)),
                    "gate_skipped": gate_skipped,
                    "report": str(run_html),
                    "latest_report": str(latest_path) if module_reports else None,
                    "scenarios": {
                        "total": len(fresh.scenarios),
                        "failed": len(case_fail),
                        "pending_ui": sum(
                            1 for s in fresh.scenarios if s.error_class == "ui_pending"
                        ),
                    },
                    "failed_scenarios": [s.name for s in case_fail],
                    "intents_pending": pending,
                    "drafts_unreviewed": drafts,
                    "full_run": full_run,
                    "next": next_steps,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        typer.echo("待办清单：")
        if case_fail:
            typer.echo(f"- 失败场景：{', '.join(s.name for s in case_fail)}（{len(case_fail)} 个待修复，见上断言详情）")
        else:
            typer.echo("- 失败场景：无")
        if pending:
            typer.echo(
                f"- 待实测意图 {len(pending)} 条：{', '.join(pending)}"
                f"（AI 浏览器实测后 atk record --last 回填，未回填 gate 拦截）"
            )
        elif not fresh.intents:
            typer.echo("- 无覆盖意图：如需补充 UI 实测，用 atk record --last 回填（--status pass|fail|suspect|blocked）")
        else:
            typer.echo(f"- 意图回填：已回填 {len(fresh.intents)} 条")
        typer.echo("- 草稿评审：如有 scenarios/**/gen-* 草稿场景需人工评审 expect 后入库")
        if full_run:
            typer.echo("- 无模块命中，已全量执行（affected_modules 为空）")
    # 退出语义：run 失败仍走完 report+gate 再透出 run 码；否则透出 gate 码（拦截 1 为正常返回）
    if run_exit != 0:
        raise typer.Exit(code=run_exit)
    raise typer.Exit(code=gate_exit)


def _package_skill_texts() -> dict[str, str]:
    """包内 skill 单一源（init/purge 共用）；资源缺失抛 RuntimeError。"""
    try:
        skills_root = res.files("atk.skills")
        names = sorted(
            p.name for p in skills_root.iterdir() if (p / "SKILL.md").is_file()
        )
        return {
            name: (skills_root / name / "SKILL.md").read_text(encoding="utf-8")
            for name in names
        }
    except Exception as e:
        raise RuntimeError(str(e)) from e


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _manifest_record(root: Path, created_paths: list[str]) -> None:
    """把 init 新建文件（路径+内容指纹）记入 .atk/manifest.json，purge 依据它安全清理。"""
    mf = layout.manifest_file(root)
    data: dict = {"version": 1, "mode": layout.mode(root), "created": {}}
    if mf.is_file():
        try:
            old = json.loads(mf.read_text(encoding="utf-8"))
            if isinstance(old, dict) and isinstance(old.get("created"), dict):
                data = old
        except Exception:
            pass
    rr = Path(root).resolve()
    for s in created_paths:
        p = Path(s)
        try:
            rel = p.resolve().relative_to(rr).as_posix()
        except ValueError:
            continue
        if p.is_file():
            data["created"][rel] = _sha256(p)
    mf.parent.mkdir(parents=True, exist_ok=True)
    mf.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


_INIT_ENV = """\
# 环境配置：run --env <name> 引用；vars 供 ${var} 替换。
# vars 不是必填项——只写场景真正引用的变量；凭据形态按被测项目而定
# （username/password、token、cookie、api_key……），敏感值用 ${env:VAR}
# 从环境变量注入不入库（缺失会显式告警）。
local:
  base_url: "http://127.0.0.1:8000"
  vars: {}
  # 示例（按项目实际用到的取消注释）：
  #   username: testuser
  #   password: "${env:ATK_PASSWORD}"
  #   token: "${env:ATK_TOKEN}"
  #   cookie: "${env:ATK_COOKIE}"
"""

_INIT_MODULES = """\
# 被测代码路径 -> 测试模块（scenarios/<module>/）。fnmatch 通配，首个命中生效。
# 不配置也能用：smoke 无映射命中时自动全量执行（慢但不会漏测）。
modules:
  example:
    - "src/**"
"""

_INIT_SCENARIO = """\
scenario: 示例-健康检查
module: example
priority: P0
tags: [smoke]
steps:
  - api:
      call: "GET /health"
      expect: { status: 200 }
"""


def _yaml_scalar(v: str) -> str:
    """YAML 值统一双引号转义，${env:VAR} 等占位符原样落盘。"""
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _env_text_with_url(env_name: str, url: str, extra_vars: dict[str, str] | None = None) -> str:
    lines = [
        "# 环境配置：run --env <name> 引用；vars 供 ${var} 替换。",
        "# vars 只写场景真正引用的变量（凭据形态按项目而定：用户名密码/token/cookie 非必填）；",
        "# 敏感值用 ${env:VAR} 从环境变量注入不入库，也可 init 时 --var 带上。",
        f"{env_name}:",
        f'  base_url: "{url}"',
    ]
    if extra_vars:
        lines.append("  vars:")
        for k, v in extra_vars.items():
            lines.append(f"    {k}: {_yaml_scalar(v)}")
    else:
        lines.extend([
            "  vars: {}",
            "  # 示例（按项目实际用到的取消注释）：",
            "#   username: testuser",
            '#   password: "${env:ATK_PASSWORD}"',
            '#   token: "${env:ATK_TOKEN}"',
            '#   cookie: "${env:ATK_COOKIE}"',
        ])
    return "\n".join(lines) + "\n"


def _modules_text_with(mods: list[str]) -> str:
    lines = [
        "# 被测代码路径 -> 测试模块（scenarios/<module>/）。fnmatch 通配，首个命中生效。",
        "# 下面的通配是保守猜测，请改成被测仓库真实代码路径；不配置也能用（smoke 全量跑）。",
        "modules:",
    ]
    for m in mods:
        lines.append(f"  {m}:")
        lines.append(f'    - "{m}/**"')
    return "\n".join(lines) + "\n"


def _health_scenario_text(env_name: str, mod: str) -> str:
    return (
        f"scenario: {mod}-健康检查\n"
        f"module: {mod}\n"
        "priority: P0\n"
        "tags: [smoke]\n"
        f"env: {env_name}\n"
        "steps:\n"
        "  - api:\n"
        '      call: "GET /health"\n'
        "      expect:\n"
        '        status: "< 500"   # 起步只要求服务有响应（404 也算通），先让流程转起来再补业务断言\n'
    )


@app.command()
def init(
    root: Path = typer.Option(".", help="项目根目录"),
    ui_tool: str = typer.Option(
        DEFAULT_UI_TOOL, "--ui-tool", help="AI 实测 UI 使用的工具名，写入 skill"
    ),
    url: Optional[str] = typer.Option(
        None, "--url", help="被测服务 base_url，直接写入 environments.yaml（免手改配置）"
    ),
    env_name: str = typer.Option(
        "local", "--env-name", help="环境名（配合 --url 使用）"
    ),
    modules: Optional[str] = typer.Option(
        None, "--modules",
        help="逗号分隔模块名：生成 modules.yaml 骨架并在 scenarios/<模块>/ 放健康检查场景",
    ),
    var_opts: Optional[list[str]] = typer.Option(
        None, "--var",
        help="写入 vars 的键值 key=value（可多次，配合 --url）；"
             "敏感值写占位符如 --var \"token=${env:ATK_TOKEN}\"",
    ),
):
    """初始化 atk 项目结构（只创建缺失文件，绝不覆盖）。

    推荐一步到位：atk init --url http://127.0.0.1:8080 --modules order,coupon
    ——自动生成环境、模块映射与首批可跑场景，validate 后即可 smoke。
    凭据按键值注入：--var token='${env:ATK_TOKEN}'；不给则 vars 留空+注释示例
    （username/password 不是所有项目都需要，写多了反而误导）。

    skill 会同时铺到 .claude/skills、.cursor/rules、AGENTS.md 三种布局，
    换 Agent 不失效；AGENTS.md 按标记块幂等替换，不覆盖已有内容。
    """
    root = Path(root)
    mods = [m.strip() for m in modules.split(",") if m.strip()] if modules else []
    for m in mods:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", m):
            raise typer.BadParameter(f"模块名需为小写字母开头的标识符: {m}")
    init_vars = _parse_set(var_opts)
    if init_vars:
        for k in init_vars:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
                raise typer.BadParameter(f"vars 键需为标识符: {k}")
        if not url:
            raise typer.BadParameter("--var 需配合 --url 使用（否则请手写 environments.yaml）")
    env_text = _env_text_with_url(env_name, url, init_vars) if url else _INIT_ENV
    modules_text = _modules_text_with(mods) if mods else _INIT_MODULES
    legacy = layout.is_legacy(root)
    scen = layout.scenarios_dir(root)
    fixtures_dir = root / "fixtures" if legacy else scen.parent / "fixtures"
    targets: dict[Path, str] = {
        layout.env_file(root): env_text,
        layout.modules_file(root): modules_text,
        fixtures_dir / ".gitkeep": "",
        layout.runs_dir(root) / ".gitkeep": "",
    }
    if mods:
        for m in mods:
            targets[scen / m / "health.yaml"] = _health_scenario_text(
                env_name if url else "local", m
            )
    else:
        targets[scen / "demo" / "example.yaml"] = _INIT_SCENARIO
    created, skipped = [], []
    for path, content in targets.items():
        if path.exists():
            skipped.append(str(path))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(str(path))
    # 新布局的 .gitignore：只忽略可再生产物；.atk/ 下的场景库与配置照常入库
    # （YAML 是唯一事实源，必须进版本管理）。已存在则幂等补缺，不记录进 manifest。
    if not legacy:
        gitignore = root / ".gitignore"
        needed = ["# atk 可再生产物（场景库 .atk/scenarios/ 与配置请照常提交）",
                  ".atk/reports/", ".atk/last-run.json", ".atk/manifest.json"]
        if not gitignore.exists():
            gitignore.write_text("\n".join(needed) + "\n", encoding="utf-8")
            created.append(str(gitignore))
        else:
            existing = gitignore.read_text(encoding="utf-8")
            missing = [n for n in needed if n not in existing]
            if missing:
                sep = "" if existing.endswith("\n") else "\n"
                gitignore.write_text(existing + sep + "\n".join(missing) + "\n",
                                     encoding="utf-8")
    # Agent 工作流 skill：包内单一源 -> .atk/skills/ + AGENTS.md 索引（只建缺失，绝不覆盖）。
    # 先一次性预加载：任一缺失直接 exit 1，避免 config 已建、skill 写一半。
    try:
        skill_texts = _package_skill_texts()
    except RuntimeError as e:
        typer.secho(f"skill 资源缺失，init 中止：{e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if not skill_texts:
        typer.secho("skill 资源为空，init 中止", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    skill_created, skill_skipped = install_skills(root, skill_texts, ui_tool)
    created.extend(skill_created)
    skipped.extend(skill_skipped)
    _manifest_record(root, created)
    for p in created:
        typer.echo(f"创建 {p}")
    for p in skipped:
        typer.echo(f"跳过（已存在） {p}")
    typer.echo(
        "AGENTS.md 已同步 atk 工作流区块——AI 读它并按其索引加载 .atk/skills/ 正文"
        f"（UI 工具：{ui_tool}）"
    )
    run_env = env_name if url else "local"
    if url and mods:
        typer.echo(
            "\n下一步（配置已就绪，直接跑）：\n"
            "  1. atk validate && atk run --dry-run                # 先看选中了什么\n"
            f"  2. atk run --env {run_env}                          # 健康场景应已通过\n"
            "  3. 对 AI 说：为这次改动补测试场景（AGENTS.md 已让 AI 加载 atk-authoring）\n"
            '  4. atk smoke --base main --env <env> --title "功能" --format json\n'
            "  （modules.yaml 的通配路径请改成被测仓库真实代码路径，改动才能命中增量）"
        )
    else:
        typer.echo(
            "\n下一步：\n"
            f"  1. 编辑 {layout.env_file(root).relative_to(root)} 指向你的环境\n"
            f"     （或重跑 atk init --url <base_url> --var 'token=${{env:ATK_TOKEN}}' 一步生成）\n"
            f"  2. 编辑 {layout.modules_file(root).relative_to(root)} 建立代码->模块映射\n"
            f"  3. 在 {scen.relative_to(root)}/ 编写场景\n"
            "  4. atk validate 校验 -> atk run 执行\n"
            "  反悔清理：atk purge（只删 init 生成且未改动的物料）"
        )
    raise typer.Exit(code=0)


@app.command()
def purge(
    project: Path = typer.Option(".", "--project", help="项目根目录"),
    with_reports: bool = typer.Option(
        False, "--with-reports",
        help="连运行记录/报告/评审留档一并删除（不可逆，默认保留）",
    ),
    yes: bool = typer.Option(False, "--yes", help="跳过确认"),
):
    """删除本项目中的 atk 生成物料（init 产物 + skill 拷贝 + AGENTS.md atk 区块）。

    安全边界：
    - 只删 .atk/manifest.json 记录过、且内容指纹未变（init 后没手改过）的文件；
      手工编辑过的配置/场景一律保留并提示。
    - .atk/skills/ 按包内 skill 名单清理（单一布局、命名确定性）。
    - 旧版散落的 .claude/skills、.cursor/rules、根 skills/ 下的 atk 拷贝：
      路径命中命名约定且内容含 atk 标记才删，否则保留提示。
    - scenarios 里你自己新增的场景、reports 历史默认不动（--with-reports 才删）。
    """
    root = Path(project)
    names: list[str] = []
    try:
        names = sorted(_package_skill_texts())
    except RuntimeError:
        typer.secho("包内 skill 资源不可读，skill 清理跳过", fg=typer.colors.YELLOW, err=True)

    to_delete: list[Path] = []
    modified: list[Path] = []

    mf = layout.manifest_file(root)
    if mf.is_file():
        try:
            created = (json.loads(mf.read_text(encoding="utf-8")) or {}).get("created") or {}
        except Exception:
            created = {}
        for rel, sha in created.items():
            p = root / str(rel)
            if not p.is_file():
                continue
            (to_delete if _sha256(p) == str(sha) else modified).append(p)
    else:
        typer.secho(
            "未找到 .atk/manifest.json：仅清理 skill 与 AGENTS.md 区块，不碰配置/场景文件",
            fg=typer.colors.YELLOW, err=True,
        )

    for n in names:
        p = layout.skills_dir(root) / n / "SKILL.md"
        if p.is_file() and p not in to_delete:
            to_delete.append(p)

    for p in legacy_skill_paths(root, names):
        if p in to_delete:
            continue
        try:
            if "atk" in p.read_text(encoding="utf-8", errors="ignore"):
                to_delete.append(p)
            else:
                modified.append(p)
        except Exception:
            modified.append(p)

    # last-run 指针与 manifest 本身永远属于 atk
    for p in (layout.last_run_file(root), mf):
        if p.is_file() and p not in to_delete:
            to_delete.append(p)

    to_rmtree: list[Path] = []
    if with_reports:
        rd = layout.reports_dir(root)
        if rd.is_dir():
            to_rmtree.append(rd)

    agents_changed = remove_agents_block(root / "AGENTS.md")
    if not to_delete and not to_rmtree and not agents_changed and not modified:
        typer.echo("没有发现 atk 生成物料，无需清理")
        raise typer.Exit(code=0)

    typer.echo("将删除：")
    for p in to_delete:
        typer.echo(f"  文件 {p}")
    for d in to_rmtree:
        typer.echo(f"  目录 {d}")
    if agents_changed:
        typer.echo("  AGENTS.md：移除 atk 区块（文件本身保留）")
    for p in modified:
        typer.echo(f"  保留（init 后被手工改动过）：{p}")
    if not yes and not typer.confirm("确认删除以上 atk 生成物料？"):
        typer.echo("已取消")
        raise typer.Exit(code=0)

    import shutil

    for p in to_delete:
        if p.is_file():
            p.unlink()
    for d in to_rmtree:
        shutil.rmtree(d, ignore_errors=True)
    # 自底向上清理空目录（不越过项目根）
    for p in to_delete:
        d = p.parent
        while d != root and d.exists() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent
    typer.echo(
        f"已清理 {len(to_delete)} 个文件"
        + (f"、{len(to_rmtree)} 个目录" if to_rmtree else "")
        + ("、AGENTS.md 区块" if agents_changed else "")
    )
    raise typer.Exit(code=0)


@app.command("console")
def console_cmd(
    port: int = typer.Option(8900, help="监听端口"),
    g: bool = typer.Option(False, "--global", "-g", help="全局聚合模式"),
    project_root: Path = typer.Option(None, help="项目根目录（默认当前目录）"),
    job_timeout: float = typer.Option(300.0, help="单任务超时秒数"),
):
    """启动 Web 控制台（本机 127.0.0.1）。"""
    from .console import serve

    serve(port=port, global_mode=g, project_root=project_root, job_timeout=job_timeout)


def main():
    app()


if __name__ == "__main__":
    main()
