"""`atk agent` 状态梯子的优先级：**用真 gate 跑**，不 stub。

原先 test_agent_state.py 把 `_do_gate` 整块换成一个固定返回值，于是每个用例只孤立地
测一个条件——而梯子的语义恰恰是"多个条件同时成立时先报哪一个"。此前没有任何测试
同时构造过两个共存条件，优先级（就是 cli.py 里 if 的书写顺序）从未被证明过。

这一组用真实的 gate + 真实的记录 + 真实的 git 仓库跑，把顺序钉住。
"""
import json
import subprocess
from datetime import datetime, timedelta

import pytest
from typer.testing import CliRunner

from atk import approvals
from atk.cli import app
from atk.run_store import IntentRecord, ReviewRecord, ScenarioSummary, StepSummary

cli = CliRunner()

SCEN_OK = ('scenario: 登录冒烟\nmodule: demo\npriority: P0\ntags: [smoke]\nenv: local\n'
           'steps:\n  - api:\n      call: "GET /ping"\n      expect: { status: 200 }\n')


def _git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch, mock_base_url, approve_library):
    (tmp_path / "scenarios" / "demo").mkdir(parents=True)
    (tmp_path / "scenarios" / "demo" / "login.yaml").write_text(SCEN_OK, encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "environments.yaml").write_text(
        f"local:\n  base_url: '{mock_base_url}'\n  vars: {{}}\n", encoding="utf-8")
    (tmp_path / "config" / "modules.yaml").write_text(
        'modules:\n  demo:\n    files:\n      - "src/*"\n', encoding="utf-8")
    (tmp_path / ".gitignore").write_text("reports/\n.atk/\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    # atk agent 会先检查"接入是否完整"（AGENTS.md 指针 + 技能正文），缺了就 not_wired。
    # 不用 atk init：它会顺手往 scenarios/ 塞一条示例场景，污染这里的选择断言。
    from atk.skill_install import install_skills, package_skill_texts
    install_skills(tmp_path, package_skill_texts())
    _git("init", "-q", cwd=tmp_path)
    _git("add", "-A", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base", cwd=tmp_path)
    # 沿袭豁免已删除：记录里每个场景都要有签名审批，gate 才放行。
    # 梯子测的是优先级，不是审批——存量场景按新契约一次性批掉，别让它挡住每个用例。
    approve_library(tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _record(repo, *, scenarios=(), intents=(), reviews=(), planned=(), base="HEAD"):
    from atk.run_store import create_run, save_run

    runs = repo / "reports" / "runs"
    rec = create_run(base_ref=base, head_ref="HEAD", runs_dir=runs,
                     planned_scenarios=list(planned))
    rec.scenarios = list(scenarios)
    rec.intents = list(intents)
    rec.reviews = list(reviews)
    save_run(rec, runs_dir=runs)
    # atk agent 是通过 .atk/last-run.json 找"当前该判哪条记录"的，测试要照着这条路径走
    from atk.last_run import write_last_run
    write_last_run(repo, rec.run_id, base=base, head="HEAD")
    return rec


def _summ(name, *, passed=True, cls="none", module="demo", file=""):
    return ScenarioSummary(name=name, module=module, file=file, passed=passed,
                           error_class=cls,
                           steps=[StepSummary(title="t", passed=passed, detail="200")])


def agent(project):
    r = cli.invoke(app, ["agent", "--project", str(project), "--format", "json"])
    return r.exit_code, json.loads(r.stdout)


def _draft_file(repo, name="gen.yaml"):
    from atk.drafts import DRAFT_TAG

    f = repo / "scenarios" / "demo" / name
    f.write_text(SCEN_OK.replace("[smoke]", f"[smoke, {DRAFT_TAG}]"), encoding="utf-8")
    return f


# ------------------------------------------------------------------ 共存条件

def test_draft_outranks_pending_ui_and_case_failure(repo):
    """三个问题同时存在时，必须先让人审断言：草稿没过审，跑成什么样都不算数。"""
    f = _draft_file(repo)
    rec = _record(repo, planned=["scenarios/demo/login.yaml"], scenarios=[
        _summ("登录冒烟", file="scenarios/demo/login.yaml"),
        _summ("草稿场景", passed=False, cls="assertion", file=str(f)),
    ], intents=[IntentRecord(title="页面下单", status="pending")])
    code, payload = agent(repo)
    assert payload["state"] == "await_expect_review", payload
    assert payload["requires_human"] is True and "介入点 1" in payload["human_prompt"]
    assert any("review-draft" in c for c in payload["next"])
    assert code == 2  # 有 blocker

    # 草稿处理完 → 才轮到未回填的 UI 意图（那是 AI 的活，不该停下来问人）
    f.unlink()
    rec.scenarios = [s for s in rec.scenarios if s.name != "草稿场景"]
    from atk.run_store import save_run
    save_run(rec, runs_dir=repo / "reports" / "runs")
    _code, p2 = agent(repo)
    assert p2["state"] == "ui_pending", p2
    assert p2["requires_human"] is False


def test_ui_pending_outranks_blocked_and_failing(repo):
    """待实测排第一（AI 能自己推进），受阻第二（要人排查环境），断言失败第三。"""
    _record(repo, scenarios=[
        _summ("页面下单", passed=False, cls="ui_pending"),
        _summ("网络超时", passed=False, cls="environment"),
        _summ("金额校验", passed=False, cls="assertion"),
    ], intents=[IntentRecord(title="页面下单", status="pending")])
    assert agent(repo)[1]["state"] == "ui_pending"

    rec = _record(repo, scenarios=[
        _summ("网络超时", passed=False, cls="environment"),
        _summ("金额校验", passed=False, cls="assertion"),
    ])
    assert agent(repo)[1]["state"] == "run_blocked"
    assert agent(repo)[1]["requires_human"] is True


def test_planned_but_not_executed_is_surfaced_as_a_real_blocker(repo):
    """计划里有的场景没真跑，要能说清是哪一条，而不是笼统一句"门禁拦截"。

    这里用 atk 自己造这个状态：plan 收了两个模块，然后只 `run --module demo`。
    这正是修 smoke 之前它每天会产生的形状。
    """
    other = repo / "scenarios" / "other"
    other.mkdir()
    (other / "o.yaml").write_text(
        SCEN_OK.replace("登录冒烟", "订单冒烟").replace("module: demo", "module: other")
        .replace('GET /ping', 'GET /api/orders'), encoding="utf-8")
    (repo / "config" / "modules.yaml").write_text(
        'modules:\n  demo:\n    files:\n      - "src/*"\n'
        '  other:\n    files:\n      - "src/*"\n', encoding="utf-8")
    plan = json.loads(cli.invoke(app, ["plan", "--base", "HEAD", "--format", "json"]).stdout)
    rid = plan["run_id"]
    assert len(plan["reuse"]) == 2, plan["reuse"]
    assert cli.invoke(app, ["run", "--module", "demo", "--record-to", rid]).exit_code == 0
    code, payload = agent(repo)
    assert payload["state"] == "gate_blocking", payload
    assert any("没有真正执行" in b and "scenarios/other/o.yaml" in b
               for b in payload["blockers"]), payload["blockers"]


def test_suspect_intent_blocks_before_review_request(repo):
    """有未定性结论时不该先去问"可以合并吗"——那是跳过定性的假放行。"""
    _record(repo, scenarios=[_summ("登录冒烟", file="scenarios/demo/login.yaml")],
            intents=[IntentRecord(title="页面下单", status="suspect", note="文案可疑")])
    code, payload = agent(repo)
    assert payload["state"] == "gate_blocking"
    assert any("未定性" in b for b in payload["blockers"])


def test_clean_run_reaches_the_human_confirmation_checkpoint(repo):
    _record(repo, scenarios=[_summ("登录冒烟", file="scenarios/demo/login.yaml")])
    code, payload = agent(repo)
    assert payload["state"] == "await_run_review", payload
    assert payload["requires_human"] is True and "介入点 2" in payload["human_prompt"]
    assert payload["next"] == [f"atk review {payload['run_id']} --by <姓名> --verdict approve"]


def test_ready_to_merge_needs_both_gate_and_confirmation(repo):
    rec = _record(repo, scenarios=[_summ("登录冒烟", file="scenarios/demo/login.yaml")])
    from atk.run_store import save_run
    rec.reviews.append(ReviewRecord(by="lisi", verdict="approve", at="2026-09-24T00:00:00"))
    save_run(rec, runs_dir=repo / "reports" / "runs")
    code, payload = agent(repo)
    assert (code, payload["state"], payload["next"]) == (0, "ready_to_merge", [])


def test_no_run_and_broken_pointer_still_offer_a_next_step(repo):
    """指针指向已不存在的记录时，必须给出可执行的 next，否则 AI 会原地卡死。"""
    from atk.last_run import write_last_run
    write_last_run(repo, "smoke-gone-9999")
    code, payload = agent(repo)
    assert payload["state"] == "run_missing"
    assert payload["next"] and payload["next"][0].startswith("atk smoke")


def test_empty_library_is_reported_not_silently_passing(repo):
    import shutil
    shutil.rmtree(repo / "scenarios")
    code, payload = agent(repo)
    assert payload["state"] == "no_scenarios" and code == 2
