"""UI 步骤语义：待实测既非失败也非受阻，定性由 gate 检查意图回填情况。"""
import json

import pytest
import yaml
from typer.testing import CliRunner

from atk.cli import app
from atk.executors.runner import Runner
from atk.run_store import IntentRecord, load_run
from atk.store.models import Scenario

runner_cli = CliRunner()

API_SCENARIO = """
scenario: 登录成功
module: todo
priority: P0
tags: [smoke]
env: local
steps:
  - api:
      call: "GET /ping"
      expect: { status: 200 }
"""

UI_SCENARIO = """
scenario: 页面下单后列表显示待支付
module: order
priority: P0
tags: [e2e]
env: local
steps:
  - ui:
      action: 打开订单创建页面并提交
      expect: 列表出现该订单，状态为待支付
"""


def _mk(tmp_path, name, content):
    d = tmp_path / "scenarios"
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text(yaml.safe_load(content) and content, encoding="utf-8")
    return d


@pytest.fixture()
def proj(tmp_path):
    """建一个含 API 场景与 UI 场景的最小项目。"""
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "api.yaml").write_text(API_SCENARIO, encoding="utf-8")
    (tmp_path / "scenarios" / "ui.yaml").write_text(UI_SCENARIO, encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "environments.yaml").write_text(
        "local:\n  base_url: 'http://127.0.0.1:1'\n  vars: {}\n", encoding="utf-8"
    )
    return tmp_path


# ------------------------------------------------------------------ Runner 层

def test_ui_step_marked_pending_not_blocked(proj):
    """核心：UI 场景不再是 blocked，也不导致 exit 2。"""
    rep = Runner(
        env_file=proj / "config" / "environments.yaml",
        scenarios_root=proj / "scenarios",
    ).run(env_name="local", tags=["e2e"])
    assert rep.total == 1
    assert rep.pending_ui_count == 1
    assert rep.blocked_count == 0
    assert rep.failed_count == 0


def test_ui_step_detail_points_to_record(proj):
    rep = Runner(
        env_file=proj / "config" / "environments.yaml",
        scenarios_root=proj / "scenarios",
    ).run(env_name="local", tags=["e2e"])
    step = rep.results[0].steps[0]
    assert step.error_class == "ui_pending"
    assert "atk record" in step.detail


def test_skip_ui_marks_scenario_skipped(proj):
    rep = Runner(
        env_file=proj / "config" / "environments.yaml",
        scenarios_root=proj / "scenarios",
    ).run(env_name="local", tags=["e2e"], skip_ui=True)
    assert rep.pending_ui_count == 0
    assert rep.skipped_count == 1
    assert rep.blocked_count == 0
    assert rep.exit_code == 0


def test_empty_scenario_is_skipped_not_blocked():
    sc = Scenario(scenario="空场景", steps=[])
    rep = Runner(env_file="config/environments.yaml", scenarios_root="scenarios")
    result = Runner._run_one(None, sc)
    assert result.error_class == "skipped"


# ------------------------------------------------------------------ 意图登记与 gate

def test_ui_scenario_registers_pending_intent(proj, monkeypatch):
    from atk import cli as cli_mod
    from atk.executors.runner import RunReport, ScenarioResult
    from atk.run_store import RunRecord, create_run, save_run

    # 直接构造一条 ui_pending 结果，验证登记逻辑
    sc = Scenario.from_raw(yaml.safe_load(UI_SCENARIO))
    report = RunReport(results=[ScenarioResult(sc, False, "ui_pending")])
    rec = create_run(runs_dir=proj / "reports" / "runs")
    added = cli_mod._register_pending_ui_intents(rec, report)
    assert added == 1
    assert rec.intents[0].title == "页面下单后列表显示待支付"
    assert rec.intents[0].status == "pending"


def test_register_pending_intent_is_idempotent(proj):
    from atk import cli as cli_mod
    from atk.executors.runner import RunReport, ScenarioResult
    from atk.run_store import create_run

    sc = Scenario.from_raw(yaml.safe_load(UI_SCENARIO))
    report = RunReport(results=[ScenarioResult(sc, False, "ui_pending")])
    rec = create_run(runs_dir=proj / "reports" / "runs")
    cli_mod._register_pending_ui_intents(rec, report)
    assert cli_mod._register_pending_ui_intents(rec, report) == 0
    assert len(rec.intents) == 1


def test_record_upserts_instead_of_duplicating(proj, tmp_path):
    """回填同名意图应就地更新，否则 gate 看到的 pending 永远消不掉。"""
    from atk import cli as cli_mod
    from atk.executors.runner import RunReport, ScenarioResult
    from atk.run_store import create_run, save_run

    runs = proj / "reports" / "runs"
    sc = Scenario.from_raw(yaml.safe_load(UI_SCENARIO))
    report = RunReport(results=[ScenarioResult(sc, False, "ui_pending")])
    rec = create_run(runs_dir=runs)
    cli_mod._register_pending_ui_intents(rec, report)
    save_run(rec, runs)

    payload = tmp_path / "ui.json"
    payload.write_text(
        json.dumps(
            {"title": "页面下单后列表显示待支付", "status": "pass", "note": "已在列表查到该订单"}
        ),
        encoding="utf-8",
    )
    res = runner_cli.invoke(
        app,
        ["record", rec.run_id, "--from-json", str(payload), "--runs-dir", str(runs)],
    )
    assert res.exit_code == 0, res.output
    after = load_run(rec.run_id, runs)
    assert len(after.intents) == 1
    assert after.intents[0].status == "pass"
    assert "已回填" in res.output


def test_gate_blocks_unfilled_pending_intent(proj):
    from atk import cli as cli_mod
    from atk.run_store import create_run, save_run

    runs = proj / "reports" / "runs"
    rec = create_run(runs_dir=runs)
    rec.intents.append(IntentRecord(title="页面下单后列表显示待支付", status="pending"))
    save_run(rec, runs)

    passed, verdicts, _line, _r = cli_mod._do_gate(rec.run_id, "HEAD", proj, runs)
    assert passed is False
    assert any("未实测回填" in v for _ok, v in verdicts)


def test_gate_passes_after_intent_filled(proj):
    from atk import cli as cli_mod
    from atk.run_store import create_run, save_run

    runs = proj / "reports" / "runs"
    rec = create_run(runs_dir=runs)
    rec.intents.append(
        IntentRecord(title="页面下单后列表显示待支付", status="pass")
    )
    save_run(rec, runs)

    passed, verdicts, _line, _r = cli_mod._do_gate(rec.run_id, "HEAD", proj, runs)
    assert not any("未实测回填" in v for _ok, v in verdicts)


def test_gate_still_blocks_suspect(proj):
    from atk import cli as cli_mod
    from atk.run_store import create_run, save_run

    runs = proj / "reports" / "runs"
    rec = create_run(runs_dir=runs)
    rec.intents.append(IntentRecord(title="下单流程", status="suspect", note="金额对不上"))
    save_run(rec, runs)

    passed, verdicts, _line, _r = cli_mod._do_gate(rec.run_id, "HEAD", proj, runs)
    assert passed is False
    assert any("未定性结论" in v for _ok, v in verdicts)
