from typer.testing import CliRunner

from atk.cli import app
from atk.reporter.html_reporter import render_run_html
from atk.run_store import (
    IntentRecord,
    ScenarioSummary,
    StepSummary,
    create_run,
)


def _record(tmp_path):
    rec = create_run(
        base_ref="HEAD~1",
        head_ref="HEAD",
        affected_files=["src/a.py"],
        affected_modules=["demo"],
        runs_dir=tmp_path / "reports" / "runs",
    )
    rec.scenarios.append(
        ScenarioSummary(
            name="登录冒烟",
            file="scenarios/demo/login.yaml",
            module="demo",
            priority="P0",
            passed=True,
            error_class="none",
            env="local",
            duration_ms=33,
            steps=[StepSummary(title="GET /ping", passed=True, detail="200")],
        )
    )
    rec.intents.append(
        IntentRecord(
            title="订单页 UI 探索",
            status="suspect",
            note="金额显示疑似未刷新",
            evidence=["evidence/s.png"],
        )
    )
    from atk.run_store import save_run

    save_run(rec, runs_dir=tmp_path / "reports" / "runs")
    return rec


def test_render_run_html_contains_all_sections(tmp_path):
    rec = _record(tmp_path)
    out = render_run_html(rec, tmp_path / "reports" / "runs" / rec.run_id / "report.html")
    html = out.read_text(encoding="utf-8")
    assert rec.run_id in html and "HEAD~1" in html
    assert "登录冒烟" in html and "scenarios/demo/login.yaml" in html
    assert "订单页 UI 探索" in html and "疑似" in html and "金额显示疑似未刷新" in html
    assert "evidence/s.png" in html and "<img" in html and "src/a.py" in html


def test_report_command(tmp_path, monkeypatch):
    rec = _record(tmp_path)
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["report", rec.run_id])
    assert r.exit_code == 0, r.output
    assert "报告：" in r.output


def test_report_missing_run_exits_2(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["report", "smoke-none"])
    assert r.exit_code == 2
