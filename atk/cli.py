"""atk 命令行入口。"""
import json
from collections import Counter
from pathlib import Path
from typing import Optional

import typer

from .diff_analyzer.git_diff import changed_files
from .diff_analyzer.modules import classify, load_module_map
from .executors.runner import Runner
from .reporter.html_reporter import render_html, render_run_html
from .reporter.junit import write_junit
from .run_store import IntentRecord, add_evidence, create_run, load_run, save_run, summarize
from .solidify.doctor import diagnose as _diagnose
from .solidify.exporter import export_scenario
from .store.loader import load_scenarios, select
from .store.models import Priority

app = typer.Typer(help="atk：AI 原生双层自动化测试框架（M1：API 冒烟）")


@app.command("list")
def list_cmd(
    module: Optional[str] = typer.Option(None, help="按模块过滤"),
    priority: Optional[Priority] = typer.Option(None, help="按优先级上限过滤"),
    root: Path = typer.Option("scenarios", help="场景库根目录"),
):
    """列出场景库中的场景。"""
    scs, errors = load_scenarios(root)
    for e in errors:
        typer.secho(f"[跳过] {e}", fg=typer.colors.YELLOW, err=True)
    scs = select(scs, module=module, priority=priority)
    if not scs:
        typer.echo("（场景库为空）")
        return
    for s in scs:
        typer.echo(f"{s.priority.value}  [{s.module}]  {s.scenario}  ({s.file})")
    typer.echo(f"共 {len(scs)} 个场景")


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
def diff(
    base: str = typer.Option("HEAD~1", help="基线引用"),
    head: str = typer.Option("HEAD", help="目标引用"),
    repo: Path = typer.Option(".", help="被测仓库路径"),
    module_map: Path = typer.Option("config/modules.yaml"),
):
    """输出变更文件及模块归属 JSON，供 AI 分析影响面。"""
    files = changed_files(base, head, str(repo))
    groups = classify(files, load_module_map(module_map))
    typer.echo(
        json.dumps({"base": base, "head": head, "groups": groups}, ensure_ascii=False, indent=2)
    )


@app.command()
def plan(
    base: str = typer.Option("HEAD~1"),
    head: str = typer.Option("HEAD"),
    repo: Path = typer.Option("."),
    root: Path = typer.Option("scenarios"),
    module_map: Path = typer.Option("config/modules.yaml"),
    tags: Optional[str] = typer.Option(None, help="复用场景需包含的标签，逗号分隔"),
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
    rec = create_run(
        base_ref=base,
        head_ref=head,
        affected_files=files,
        affected_modules=affected,
        planned_scenarios=[s.file for s in reuse],
        runs_dir=str(runs_dir),
    )
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
def export(
    scenario: Path = typer.Argument(..., help="场景 YAML 路径"),
    out_dir: Path = typer.Option("generated", help="固化脚本输出目录"),
    env: str = typer.Option("local", help="导出时用于解析 ${var} 的环境"),
    env_file: Path = typer.Option("config/environments.yaml"),
    base_url: Optional[str] = typer.Option(None, help="覆盖 base_url（默认取环境配置）"),
):
    """把场景固化为自包含 pytest 脚本（API 确定性，UI 步骤留待 Agent 填充）。"""
    from .executors.env import load_env as _load_env

    try:
        cfg = _load_env(env_file, env)
    except KeyError as e:
        typer.secho(f"环境配置错误：{e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    path = export_scenario(
        scenario,
        out_dir=out_dir,
        base_url=base_url or cfg.base_url,
        variables=dict(cfg.vars),
    )
    typer.echo(f"已生成：{path}\n运行：pytest {path}")


@app.command()
def doctor(
    path: Path = typer.Argument(..., help="固化脚本（或目录）路径"),
):
    """诊断固化脚本：healthy/pending/repairable/suspect_bug/broken。

    退出码：0=healthy|pending；1=repairable|suspect_bug|broken；2=文件不存在。
    """
    if not Path(path).exists():
        typer.secho(f"路径不存在: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    result = _diagnose(path)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    raise typer.Exit(code=0 if result["status"] in ("healthy", "pending") else 1)


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
        verdicts.append((True, f"复用场景 {len(rec.scenarios)} 个全部通过"))

    bad_intents = [i for i in rec.intents if i.status in ("fail", "suspect")]
    if bad_intents:
        verdicts.append((False, "存在未定性结论: " + "; ".join(f"{i.title}[{i.status}]" for i in bad_intents)))
    blocked = [i for i in rec.intents if i.status == "blocked"]
    if blocked:
        verdicts.append((True, f"受阻意图 {len(blocked)} 条（不拦截，但需关注）"))

    for ok, msg in verdicts:
        typer.echo(("✓ " if ok else "✗ ") + msg)
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
