from atk.executors.api_executor import StepResult
from atk.executors.runner import RunReport, ScenarioResult
from atk.reporter.html_reporter import render_html
from atk.store.models import Scenario


def _fake_report() -> RunReport:
    rep = RunReport(env_name="local", started_at="2026-08-24T10:00:00")
    sc = Scenario(scenario="下单链路", module="order")
    rep.results.append(
        ScenarioResult(
            scenario=sc,
            passed=False,
            error_class="assertion",
            steps=[
                StepResult("POST /api/login", True, "200"),
                StepResult("POST /api/orders", False, "status 期望 200 实际 500"),
            ],
        )
    )
    return rep


def test_render_contains_evidence(tmp_path):
    path = render_html(_fake_report(), tmp_path / "r.html")
    html = path.read_text(encoding="utf-8")
    assert "下单链路" in html
    assert "status 期望 200 实际 500" in html
    assert "通过 0" in html and "未通过 1" in html
    assert "失败" in html


def test_render_creates_parent_dirs(tmp_path):
    path = render_html(_fake_report(), tmp_path / "deep/nested/r.html")
    assert path.exists()
