"""atk 命令行入口。"""
from pathlib import Path
from typing import Optional

import typer

from .executors.runner import Runner
from .reporter.html_reporter import render_html
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
