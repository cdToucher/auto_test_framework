"""atk 命令行入口。"""
import json
from pathlib import Path
from typing import Optional

import typer

from .diff_analyzer.git_diff import changed_files
from .diff_analyzer.modules import classify, load_module_map
from .executors.runner import Runner
from .reporter.html_reporter import render_html
from .run_store import IntentRecord, add_evidence, create_run, load_run, save_run, summarize
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
    raise typer.Exit(code=report.exit_code)


def main():
    app()


if __name__ == "__main__":
    main()
