"""atk agent 状态机：AI 触发 atk 的唯一入口，阶梯每一档都要能验证。

契约重点不是文案，而是三件事：state 可枚举、next 里的命令可直接执行、
需要人判断的那两处必须 requires_human=true（不能靠模型自觉停下）。
"""
import shutil

import pytest
from typer.testing import CliRunner

from atk import cli
from atk.cli import app
from atk.run_store import (IntentRecord, ReviewRecord, RunRecord, ScenarioSummary,
                           save_run)

runner = CliRunner()


@pytest.fixture
def proj(tmp_path, monkeypatch):
    """一个 init 好的新布局项目（说明书 + 技能正文齐备）。"""
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    return tmp_path


def agent_json(project):
    import json

    res = runner.invoke(app, ["agent", "--project", str(project), "--format", "json"])
    assert res.exit_code in (0, 2), res.output
    return res.exit_code, json.loads(res.output)


def scenario(name="下单扣减金额", passed=True, error_class="none", draft=False, file=""):
    return ScenarioSummary(name=name, passed=passed, error_class=error_class,
                           draft=draft, file=file)


def verdict(tmp_path, monkeypatch, *, scenarios=(), intents=(), reviews=(),
            gate_passed=True, gate_blockers=()):
    L = {"runs_dir": tmp_path / "runs"}
    rec = RunRecord(run_id="r1", created_at="2026-09-21T00:00:00",
                    scenarios=list(scenarios), intents=list(intents), reviews=list(reviews))
    save_run(rec, L["runs_dir"])
    verdicts = [(True, "已有结论 ok", False)] + [
        (False, msg, False) for msg in gate_blockers]
    monkeypatch.setattr(cli, "_do_gate",
                        lambda *a, **k: (gate_passed, verdicts, "开发确认：未确认", rec))
    return cli._agent_run_verdict(tmp_path, L, "r1", "HEAD", tmp_path, "staging")


def test_agent_reports_broken_wiring(proj):
    """AGENTS.md 指向的说明书/技能被删（.atk 常不入库）时必须报 blocker，别让 AI 绕圈。"""
    shutil.rmtree(proj / ".atk" / "skills")
    code, out = agent_json(proj)
    assert code == 2
    assert out["state"] == "not_wired"
    assert out["ok"] is False
    assert any("技能正文缺失" in b for b in out["blockers"])
    assert "atk init" in out["next"]


def test_agent_no_run_points_to_smoke(proj):
    code, out = agent_json(proj)
    assert code == 0, out
    assert out["state"] == "no_run"
    assert isinstance(out["scenarios_total"], int) and out["scenarios_total"] > 0
    assert any(cmd.startswith("atk smoke") for cmd in out["next"])
    assert out["blockers"] == []
    assert out["requires_human"] is False


def test_agent_stale_pointer_still_offers_a_way_forward(proj):
    """last-run 指向已删掉的记录（清过 reports/runs 就会这样）：
    给了 blocker 就必须同时给 next，否则 AI 拿到诊断却无路可走，只能原地卡住。"""
    import json

    (proj / ".atk" / "last-run.json").write_text(
        json.dumps({"run_id": "smoke-19700101-000000", "env": "local"}), encoding="utf-8")
    code, out = agent_json(proj)
    assert code == 2
    assert out["state"] == "run_missing"
    assert out["blockers"] and "不存在" in out["blockers"][0]
    assert any(c.startswith("atk smoke") for c in out["next"])


def test_agent_next_steps_are_bare_commands(proj):
    """next 是给机器执行的：不许混进"修复后重跑："这类散文。"""
    _, out = agent_json(proj)
    for cmd in out["next"]:
        assert cmd.startswith("atk "), cmd


def test_ladder_waits_on_unreviewed_drafts(tmp_path, monkeypatch):
    st, nxt, _b, human, prompt = verdict(
        tmp_path, monkeypatch,
        scenarios=[scenario(draft=True, file=str(tmp_path / "gone.yaml"))])
    assert st == "await_expect_review"
    assert human is True and "介入点 1" in prompt
    assert any(cmd.startswith("atk review-draft") for cmd in nxt)


def test_ladder_lists_one_record_per_pending_intent(tmp_path, monkeypatch):
    st, nxt, _b, human, _p = verdict(
        tmp_path, monkeypatch,
        intents=[IntentRecord(title="页面添加待办", status="pending"),
                 IntentRecord(title="页面删除待办", status="pending")])
    assert st == "ui_pending"
    assert len([c for c in nxt if c.startswith("atk record")]) == 2
    assert human is False  # UI 实测是 AI 的活，不该停下来问人


def test_ladder_blocked_run_needs_human(tmp_path, monkeypatch):
    st, _nxt, blockers, human, prompt = verdict(
        tmp_path, monkeypatch,
        scenarios=[scenario(passed=False, error_class="network")])
    assert st == "run_blocked"
    assert blockers and human is True and "环境" in prompt


def test_ladder_assertion_failure_reruns_without_asking_human(tmp_path, monkeypatch):
    st, nxt, _b, human, _ = verdict(
        tmp_path, monkeypatch,
        scenarios=[scenario(name="金额校验", passed=False, error_class="assertion")])
    assert st == "cases_failing"
    assert human is False
    # --record-to 而非 --record-new：重跑要并回原记录，否则已回填的 UI 意图整批作废。
    # 带真实 run_id 而非 --last：那是全局单槽，两个 run 在飞时会串台。
    assert nxt == ["atk run --env staging --record-to r1"]


def test_ladder_gate_blocking_hands_back_to_state_machine(tmp_path, monkeypatch):
    st, nxt, blockers, human, _ = verdict(
        tmp_path, monkeypatch, gate_passed=False, gate_blockers=("head 已前进",))
    assert st == "gate_blocking"
    assert blockers == ["head 已前进"]
    assert nxt == ["atk agent --format json"]
    assert human is False


def test_ladder_passed_but_unconfirmed_is_human_checkpoint(tmp_path, monkeypatch):
    st, nxt, _b, human, prompt = verdict(tmp_path, monkeypatch)
    assert st == "await_run_review"
    assert nxt == ["atk review r1 --by <姓名> --verdict approve"]
    assert human is True and "介入点 2" in prompt


def test_ladder_ready_when_approved(tmp_path, monkeypatch):
    st, nxt, blockers, human, _ = verdict(
        tmp_path, monkeypatch,
        reviews=[ReviewRecord(by="lisi", verdict="approve")])
    assert (st, nxt, blockers, human) == ("ready_to_merge", [], [], False)


def test_ladder_rejected_review_still_asks_human(tmp_path, monkeypatch):
    """reject 之后 AI 不能自己改场景重跑就算完，返工范围得人定。"""
    st, _nxt, _b, human, _ = verdict(
        tmp_path, monkeypatch,
        reviews=[ReviewRecord(by="lisi", verdict="reject", note="断言太松")])
    assert st == "await_run_review" and human is True
