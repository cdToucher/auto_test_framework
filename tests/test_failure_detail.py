"""失败现场：断言不通过时必须看到"实际返回了什么"，否则只能改代码重跑猜。

覆盖三层：执行器留痕（含脱敏与截断）、终端与报告渲染、run --format json 供 AI 直读。
"""
import json

import pytest
import yaml
from typer.testing import CliRunner

from atk.cli import CONSOLE_BODY_LIMIT, _failure_lines, app
from atk.executors.api_executor import BODY_LIMIT, ApiExecutor, StepResult, body_snippet
from atk.reporter.html_reporter import render_run_html
from atk.run_store import RunRecord, ScenarioSummary, StepSummary
from atk.store.models import ApiStep, parse_expect_map

runner = CliRunner()


class FakeResp:
    def __init__(self, status_code: int = 200, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if payload is None else json.dumps(payload, ensure_ascii=False)
        self.content = self.text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def test_body_snippet_masks_credential_like_keys():
    """响应会被写进 run.yaml 和 HTML 报告（会提交、会贴 CI），凭据值不能原样落盘。"""
    resp = FakeResp(200, {"data": {"token": "tok_secret", "orderNo": "SO1"},
                          "Authorization": "Bearer x", "items": [{"api_key": "k"}]})
    out = body_snippet(resp)
    assert "HTTP 200 · json" in out
    assert out.count("***") == 3
    assert "tok_secret" not in out and "Bearer x" not in out and '"k"' not in out
    assert "SO1" in out, "只屏蔽疑似凭据，业务字段要留着"


def test_body_snippet_handles_non_json_and_caps_length():
    long_text = "x" * (BODY_LIMIT + 500)
    assert "已截断" in body_snippet(FakeResp(500, text=long_text))
    assert "text ·" in body_snippet(FakeResp(500, text="boom"))
    assert len(body_snippet(FakeResp(500, text=long_text))) < BODY_LIMIT + 200


def test_executor_failure_keeps_detail_and_response():
    step = ApiStep(call="POST /api/login",
                   expect=parse_expect_map({"status": 200, "data.role": "admin"}))
    client = type("C", (), {"request": lambda *a, **k: FakeResp(
        200, {"data": {"token": "tok_1", "role": "user"}, "msg": "角色不足"})})()
    res = ApiExecutor(client, {}).execute(step)
    assert not res.passed
    assert "实际" in res.detail and "data.role" in res.detail, "断言消息要带实际值"
    assert "role" in res.response and "tok_1" not in res.response
    assert res.response.startswith("HTTP 200")


def test_executor_success_keeps_no_response():
    from atk.store.models import parse_expect_map

    step = ApiStep(call="GET /ping", expect=parse_expect_map({"status": 200}))
    client = type("C", (), {"request": lambda *a, **k: FakeResp(200, {"ok": True})})()
    res = ApiExecutor(client, {}).execute(step)
    assert res.passed and res.response == "", "通过的步骤不必留存整份响应"


def test_failure_lines_show_both_and_truncate_for_terminal():
    steps = [
        StepResult("GET /a", True, "200"),
        StepResult("GET /b", False, "data.total: 3（实际 data.total=1）",
                   response="HTTP 200 · json · " + ("y" * (CONSOLE_BODY_LIMIT + 100))),
    ]
    out = "\n".join(_failure_lines(steps))
    assert "GET /b" in out and "实际 data.total=1" in out
    assert "实际返回:" in out and "完整响应见报告" in out
    assert "GET /a" not in out, "通过的步骤不占失败现场"


def test_record_report_renders_failure_steps(tmp_path):
    """atk report 用的那份记录报告，原来只有一行结论，失败原因全丢。"""
    rec = RunRecord(
        run_id="r1", created_at="2026-09-22T00:00:00",
        scenarios=[ScenarioSummary(
            name="下单金额扣减", file="scenarios/x.yaml", passed=False,
            error_class="assertion",
            steps=[StepSummary(title="POST /api/orders", passed=False,
                               detail="data.payAmount: 89（实际 data.payAmount=99）",
                               response="HTTP 200 · json · {\"data\":{\"payAmount\":99}}")]),
        ],
    )
    html = render_run_html(rec, tmp_path / "r.html").read_text(encoding="utf-8")
    assert "data.payAmount=99" in html
    assert "payAmount" in html and "<pre class=body>" in html


@pytest.fixture
def project(tmp_path, mock_base_url):
    (tmp_path / "config").mkdir()
    lib = tmp_path / "scenarios" / "demo"
    lib.mkdir(parents=True)
    (tmp_path / "config" / "environments.yaml").write_text(
        yaml.safe_dump({"local": {"base_url": mock_base_url, "vars": {}}}),
        encoding="utf-8")
    (lib / "wrong.yaml").write_text(
        "scenario: 断言故意写错\nmodule: demo\npriority: P1\ntags: [smoke]\nenv: local\n"
        "steps:\n"
        "  - api:\n"
        "      call: \"POST /api/login\"\n"
        "      body: {username: alice, password: secret123}\n"
        "      expect: {status: 200, data.role: admin}\n", encoding="utf-8")
    return tmp_path


def test_run_json_carries_failure_scene(project):
    """AI 拿到的 JSON 里就得有实际返回，否则它只能报"失败了"。"""
    r = runner.invoke(app, ["run", "--env", "local", "--project", str(project),
                            "--format", "json"])
    assert r.exit_code == 1, r.output
    out = json.loads(r.output)
    assert out["failed_steps"], "run --format json 应带失败现场"
    first = out["failed_steps"][0]
    assert first["scenario"] == "断言故意写错"
    assert "实际" in first["detail"]
    assert first["response"].startswith("HTTP 200")
    assert "tok_demo123" not in json.dumps(out, ensure_ascii=False), "凭据不得进 JSON"


def test_run_console_prints_failure_scene(project):
    r = runner.invoke(app, ["run", "--env", "local", "--project", str(project)])
    assert r.exit_code == 1, r.output
    assert "实际 data.role" in r.output or "字段缺失" in r.output
    assert "实际返回:" in r.output
