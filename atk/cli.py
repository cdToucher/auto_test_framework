"""atk 命令行入口。"""
import datetime as dt
import json
from collections import Counter
from pathlib import Path
from typing import Optional

import typer

from .diff_analyzer.git_diff import changed_files
from .diff_analyzer.modules import classify, load_module_map
from .executors.runner import Runner
from .generator.context import build_context, commit_log, render_markdown
from .reporter.html_reporter import render_html, render_run_html
from .reporter.junit import write_junit
from .run_store import (
    CommitInfo,
    IntentRecord,
    ReviewRecord,
    add_evidence,
    create_run,
    load_run,
    save_run,
    summarize,
)
from .store.loader import load_scenarios, select
from .store.models import Priority

app = typer.Typer(help="atk：AI 原生双层自动化测试框架（M1：API 冒烟）")


@app.command()
def validate(
    root: Path = typer.Option("scenarios"),
):
    """校验场景库合法性（QA 提交前自查）。退出码 0/1。"""
    scs, errors = load_scenarios(root)
    for e in errors:
        typer.secho(f"[错误] {e}", fg=typer.colors.RED)
    dup = {n for n, c in Counter(s.scenario for s in scs).items() if c > 1}
    for n in sorted(dup):
        files = [s.file for s in scs if s.scenario == n]
        typer.secho(f"[警告] 场景名重复: {n} -> {', '.join(files)}", fg=typer.colors.YELLOW)
    ok = len([s for s in scs if s.scenario not in dup])
    typer.echo(f"{len(errors)} 个文件错误，{len(dup)} 个重名，{ok} 个场景通过校验")
    raise typer.Exit(code=1 if errors else 0)


@app.command()
def plan(
    base: str = typer.Option("HEAD~1"),
    head: str = typer.Option("HEAD"),
    repo: Path = typer.Option("."),
    root: Path = typer.Option("scenarios"),
    module_map: Path = typer.Option("config/modules.yaml"),
    tags: Optional[str] = typer.Option(None, help="复用场景需包含的标签，逗号分隔"),
    title: str = typer.Option("", help="功能标题"),
    runs_dir: Path = typer.Option("reports/runs"),
):
    """创建即时层运行记录并输出执行计划骨架（复用场景清单 + 待补全意图）。"""
    files = changed_files(base, head, str(repo))
    groups = classify(files, load_module_map(module_map))
    affected = sorted(m for m in groups if m != "__unmapped__")
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    scs, errors = load_scenarios(root)
    reuse = [
        s
        for s in select(scs)
        if s.module in affected and (not tag_list or set(tag_list) <= set(s.tags))
    ]
    try:
        commits = [
            CommitInfo(short=c.get("short", ""), subject=c.get("subject", ""))
            for c in commit_log(base, head, str(repo))
        ]
    except Exception:
        commits = []
    rec = create_run(
        base_ref=base,
        head_ref=head,
        affected_files=files,
        affected_modules=affected,
        planned_scenarios=[s.file for s in reuse],
        title=title,
        commits=commits,
        runs_dir=str(runs_dir),
    )
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
    run_id: str,
    title: str = typer.Option(..., help="测试意图标题"),
    status: str = typer.Option("pass", help="pass|fail|suspect|blocked"),
    note: str = typer.Option("", help="结论描述/根因猜测"),
    evidence: Optional[str] = typer.Option(None, help="证据文件路径，逗号分隔"),
    runs_dir: Path = typer.Option("reports/runs"),
):
    """回填一条即时层意图的执行结果（Agent 用 ego-browser 实测后调用）。"""
    if status not in _INTENT_STATUS:
        raise typer.BadParameter(f"status 必须是 {'/'.join(sorted(_INTENT_STATUS))}")
    try:
        rec = load_run(run_id, str(runs_dir))
    except KeyError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    sources = [p.strip() for p in evidence.split(",")] if evidence else []
    saved = add_evidence(rec, sources, str(runs_dir))
    missing = len([p for p in sources if p]) - len(saved)
    rec.intents.append(IntentRecord(title=title, status=status, note=note, evidence=saved))
    save_run(rec, str(runs_dir))
    warn = f"（{missing} 个证据文件不存在已跳过）" if missing else ""
    typer.echo(f"已记录：{title} [{status}] 证据 {len(saved)} 项{warn}")


@app.command()
def review(
    run_id: str,
    by: str = typer.Option(..., help="确认人"),
    verdict: str = typer.Option(..., help="approve|reject"),
    note: str = typer.Option("", help="确认备注；reject 时必填"),
    runs_dir: Path = typer.Option("reports/runs"),
):
    """开发确认运行记录（整单确认）。reject 必须带 note。"""
    if verdict not in ("approve", "reject"):
        raise typer.BadParameter("verdict 必须是 approve|reject")
    if verdict == "reject" and not note.strip():
        typer.secho("reject 必须带 --note 说明原因", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        rec = load_run(run_id, str(runs_dir))
    except KeyError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    now = dt.datetime.now().isoformat(timespec="seconds")
    rec.reviews.append(ReviewRecord(by=by, verdict=verdict, note=note, at=now))
    save_run(rec, str(runs_dir))
    typer.echo(f"已确认：{run_id} by {by} [{verdict}]" + (f" {note}" if note else ""))


@app.command()
def run(
    env: Optional[str] = typer.Option(
        None, help="环境名；省略时使用各场景自身的 env 字段"
    ),
    module: Optional[str] = typer.Option(None),
    tags: Optional[str] = typer.Option(None, help="逗号分隔，如 smoke,order"),
    priority: Optional[Priority] = typer.Option(None),
    env_file: Path = typer.Option("config/environments.yaml"),
    root: Path = typer.Option("scenarios"),
    report_dir: Path = typer.Option("reports"),
    record_to: Optional[str] = typer.Option(None, help="把本次结果并入指定运行记录"),
    record_new: bool = typer.Option(False, "--record-new", help="新建一条运行记录并写入 reports/runs"),
    runs_dir: Path = typer.Option("reports/runs"),
    junit: Optional[Path] = typer.Option(None, help="同时输出 JUnit XML 到指定路径"),
):
    """执行场景并生成 HTML 报告。

    退出码：0=全部通过；1=存在用例失败或场景加载错误；2=仅环境受阻。
    """
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    runner = Runner(env_file=env_file, scenarios_root=root)

    def on_result(r):
        mark = "✓" if r.passed else "✗"
        last = r.steps[-1].detail if r.steps else r.error_class
        suffix = "" if r.passed else f" —— {r.error_class}: {last[:120]}"
        typer.echo(
            f"{mark} [{r.scenario.priority.value}] {r.scenario.scenario}{suffix}"
        )

    try:
        report = runner.run(
            env_name=env,
            module=module,
            tags=tag_list,
            priority=priority,
            on_result=on_result,
        )
    except KeyError as e:
        typer.secho(f"环境配置错误：{e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    path = render_html(report, Path(report_dir) / "report-latest.html")
    typer.echo(
        f"\n结果：通过 {report.passed_count}/{report.total}"
        f"（用例失败 {report.failed_count}，受阻 {report.blocked_count}，"
        f"加载跳过 {len(report.load_errors)}）\n报告：{path}"
    )
    if junit:
        jp = write_junit(report, junit)
        typer.echo(f"JUnit：{jp}")
    if record_new:
        rec = create_run(runs_dir=runs_dir)
        rec.scenarios.extend(summarize(r) for r in report.results)
        save_run(rec, str(runs_dir))
        typer.echo(f"已写入运行记录 {rec.run_id}")
    if record_to:
        try:
            rec = load_run(record_to, str(runs_dir))
        except KeyError as e:
            typer.secho(str(e), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        rec.scenarios.extend(summarize(r) for r in report.results)
        save_run(rec, str(runs_dir))
        typer.echo(f"已并入运行记录 {record_to}")
    raise typer.Exit(code=report.exit_code)


@app.command()
def context(
    base: str = typer.Option("HEAD~1", help="基线引用"),
    head: str = typer.Option("HEAD", help="目标引用"),
    repo: Path = typer.Option(".", help="被测仓库路径"),
    module_map: Path = typer.Option("config/modules.yaml"),
    root: Path = typer.Option("scenarios", help="场景库根目录"),
    context_out: Optional[Path] = typer.Option(
        None, "--context-out", help="把变更上下文包写入指定文件（默认打印 stdout）"
    ),
    max_patch_lines: int = typer.Option(
        200, "--max-patch", "--max-patch-lines", help="单文件补丁进入上下文的最大行数"
    ),
    output_format: str = typer.Option("markdown", "--format", help="输出格式：markdown|json"),
):
    """输出确定性变更上下文包（提交记录 + 补丁 + 模块归属），供 Agent 按 atk-gen skill 编写场景。"""
    if output_format not in ("markdown", "json"):
        raise typer.BadParameter("format 必须是 markdown|json")
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
        "\n下一步（Agent 编排，参考 atk-gen skill）：\n"
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
def report(
    run_id: str,
    runs_dir: Path = typer.Option("reports/runs"),
):
    """渲染运行记录为 HTML 报告。"""
    try:
        rec = load_run(run_id, str(runs_dir))
    except KeyError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    out = render_run_html(rec, Path(runs_dir) / run_id / "report.html")
    typer.echo(f"报告：{out}")


@app.command()
def gate(
    run_id: str,
    head: str = typer.Option("HEAD", help="待合并的变更引用"),
    repo: Path = typer.Option("."),
    runs_dir: Path = typer.Option("reports/runs"),
):
    """合并门禁：校验运行记录与当前变更一致且无失败/未定性结论。

    退出码：0=放行；1=拦截；2=记录不存在。
    """
    try:
        rec = load_run(run_id, str(runs_dir))
    except KeyError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    verdicts: list[tuple[bool, str]] = []  # (是否放行, 说明)

    current = changed_files(rec.base_ref, head, str(repo))
    if sorted(current) != sorted(rec.affected_files):
        verdicts.append((False, "变更已漂移：当前 diff 与运行记录不一致，请重新 plan"))
    else:
        verdicts.append((True, f"变更一致（{len(current)} 个文件）"))

    case_fail = [s for s in rec.scenarios if not s.passed and s.error_class in ("assertion", "config")]
    if case_fail:
        verdicts.append((False, f"用例失败 {len(case_fail)} 个: " + ", ".join(s.name for s in case_fail)))
    elif rec.scenarios:
        blocked_n = len(rec.scenarios) - sum(1 for s in rec.scenarios if s.passed)
        verdicts.append((True, f"复用场景 {len(rec.scenarios)} 个无用例失败"
                                + (f"（受阻 {blocked_n} 个）" if blocked_n else "")))

    bad_intents = [i for i in rec.intents if i.status in ("fail", "suspect")]
    if bad_intents:
        verdicts.append((False, "存在未定性结论: " + "; ".join(f"{i.title}[{i.status}]" for i in bad_intents)))
    blocked = [i for i in rec.intents if i.status == "blocked"]
    if blocked:
        verdicts.append((True, f"受阻意图 {len(blocked)} 条（不拦截，但需关注）"))

    for ok, msg in verdicts:
        typer.echo(("✓ " if ok else "✗ ") + msg)
    # 第四项只读检查：开发确认状态，仅告警、永不拦截
    reviews = getattr(rec, "reviews", []) or []
    rejects = [r for r in reviews if r.verdict == "reject"]
    approves = [r for r in reviews if r.verdict == "approve"]
    if rejects:
        last = rejects[-1]
        typer.echo(f"⚠ 已被开发驳回 by {last.by}（仅告警，不拦截）" + (f"：{last.note}" if last.note else ""))
    elif not approves:
        typer.echo("⚠ 未经开发确认（仅告警，不拦截）")
    else:
        typer.echo(f"✓ 已获开发确认 by {approves[-1].by}")
    typer.echo(f"\n门禁结论：{'放行' if all(ok for ok, _ in verdicts) else '拦截'}")
    raise typer.Exit(code=0 if all(ok for ok, _ in verdicts) else 1)


_INIT_ENV = """\
# 环境配置：run --env <name> 引用；vars 供 ${var} 替换。
# 敏感值建议由 CI 注入临时文件，勿提交真实口令。
local:
  base_url: "http://127.0.0.1:8000"
  vars:
    username: testuser
    password: testpass
"""

_INIT_MODULES = """\
# 被测代码路径 -> 测试模块（scenarios/<module>/）。fnmatch 通配，首个命中生效。
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


@app.command()
def init(
    root: Path = typer.Option(".", help="项目根目录"),
):
    """初始化 atk 项目结构（只创建缺失文件，绝不覆盖）。"""
    root = Path(root)
    targets: dict[Path, str] = {
        root / "config/environments.yaml": _INIT_ENV,
        root / "config/modules.yaml": _INIT_MODULES,
        root / "scenarios/demo/example.yaml": _INIT_SCENARIO,
        root / "fixtures/.gitkeep": "",
        root / "reports/runs/.gitkeep": "",
    }
    created, skipped = [], []
    for path, content in targets.items():
        if path.exists():
            skipped.append(str(path))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(str(path))
    for p in created:
        typer.echo(f"创建 {p}")
    for p in skipped:
        typer.echo(f"跳过（已存在） {p}")
    typer.echo(
        "\n下一步：\n"
        "  1. 编辑 config/environments.yaml 指向你的环境\n"
        "  2. 编辑 config/modules.yaml 建立代码->模块映射\n"
        "  3. 在 scenarios/ 编写场景（参考 scenarios/demo/example.yaml）\n"
        "  4. atk validate 校验 -> atk run 执行"
    )
    raise typer.Exit(code=0)


def main():
    app()


if __name__ == "__main__":
    main()


@app.command("console")
def console_cmd(
    port: int = typer.Option(8900, help="监听端口"),
    g: bool = typer.Option(False, "--global", "-g", help="全局聚合模式"),
    project_root: Path = typer.Option(None, help="项目根目录（默认当前目录）"),
):
    """启动 Web 控制台（本机 127.0.0.1）。"""
    from .console import serve

    serve(port=port, global_mode=g, project_root=project_root)
