"""审批账本与"AI 不得自批"的机械证明。

这一组不是测功能，是测**做不到**：
1. 摘掉 ai-generated tag 不等于转正；
2. 没有交互式终端就铸不出凭证，因而写不进审批记录；
3. 改一个断言，旧审批自动失效；改注释不会；
4. 一枚凭证只能核销一次、只对它绑定的那份内容有效；
5. 评审人不能是 AI 身份、不能是作者本人。
"""
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from atk import approvals
from atk.cli import app

cli = CliRunner()

DRAFT = ("scenario: 下单返券\nmodule: order\npriority: P0\ntags: [ai-generated]\n"
         "env: local\nsteps:\n  - api:\n      call: \"POST /api/checkout\"\n"
         "      expect:\n        status: 200\n        data.payable: \"< 90\"\n")


def _git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def proj(tmp_path, monkeypatch):
    (tmp_path / "scenarios" / "order").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "modules.yaml").write_text(
        'modules:\n  order:\n    files:\n      - "src/*"\n', encoding="utf-8")
    _git("init", "-q", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base",
         "--allow-empty", cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def draft(proj):
    f = proj / "scenarios" / "order" / "gen.yaml"
    f.write_text(DRAFT, encoding="utf-8")
    return f


# ---------------------------------------------------------------- 断言面指纹

def test_assertion_edit_changes_the_fingerprint(proj, draft):
    before = approvals.expect_hash(draft)
    draft.write_text(DRAFT.replace("< 90", "< 80"), encoding="utf-8")
    after = approvals.expect_hash(draft)
    assert before and after and before != after


def test_cosmetic_edit_keeps_the_fingerprint(proj, draft):
    before = approvals.expect_hash(draft)
    draft.write_text(DRAFT.replace("priority: P0", "priority: P2")
                     + "\n# 注释加一行\n", encoding="utf-8")
    assert approvals.expect_hash(draft) == before


def test_expect_reordering_is_still_the_same_assertion_set(proj, draft):
    a = proj / "a.yaml"
    b = proj / "b.yaml"
    a.write_text('scenario: s\nsteps:\n  - api:\n      call: "GET /x"\n'
                 '      expect:\n        status: 200\n        data.id: not_null\n',
                 encoding="utf-8")
    b.write_text('scenario: s\nsteps:\n  - api:\n      call: "GET /x"\n'
                 '      expect:\n        data.id: not_null\n        status: 200\n',
                 encoding="utf-8")
    assert approvals.expect_hash(a) == approvals.expect_hash(b)


def test_unreadable_file_has_no_fingerprint(tmp_path):
    assert approvals.expect_hash(tmp_path / "nope.yaml") == ""


# ---------------------------------------------------------------- 凭证与核销

def test_mint_refuses_a_non_interactive_shell(proj, draft, monkeypatch):
    """这是整套机制的支点：AI 驱动的是管道 shell，isatty 为 False。"""
    monkeypatch.setattr(approvals, "human_present", lambda: False)
    with pytest.raises(RuntimeError) as e:
        approvals.mint(target=str(draft), key=approvals.expect_hash(draft), project=proj)
    assert "交互式终端" in str(e.value)


def test_token_is_single_use(proj, draft, monkeypatch):
    monkeypatch.setattr(approvals, "human_present", lambda: True)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    key = approvals.expect_hash(draft)
    token, _ = approvals.mint(target=str(draft), key=key, project=proj)
    ok, _why = approvals.consume(token=token, target=approvals._rel(draft, proj),
                                 key=key, project=proj)
    assert ok
    again, why = approvals.consume(token=token, target=approvals._rel(draft, proj),
                                   key=key, project=proj)
    assert not again and "核销" in why


def test_token_is_bound_to_one_piece_of_content(proj, draft, monkeypatch):
    monkeypatch.setattr(approvals, "human_present", lambda: True)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    token, _ = approvals.mint(target=str(draft), key=approvals.expect_hash(draft),
                              project=proj)
    draft.write_text(DRAFT.replace("< 90", "< 80"), encoding="utf-8")
    ok, why = approvals.consume(token=token, target=approvals._rel(draft, proj),
                                key=approvals.expect_hash(draft), project=proj)
    assert not ok and "不符" in why


def test_review_draft_without_token_does_not_create_a_record(proj, draft, monkeypatch):
    """没凭证也能记录评审动作，但**不产生审批记录**——这是"AI 自批"被堵住的地方。"""
    monkeypatch.setattr(approvals, "human_present", lambda: False)
    r = cli.invoke(app, ["review-draft", str(draft), "--by", "lisi", "--verdict", "approve"])
    assert r.exit_code == 0, r.output
    assert "未入账" in r.output
    assert approvals.load_approvals(approvals.approvals_file(proj)) == []


def test_review_draft_with_token_records_the_approval(proj, draft, monkeypatch, capsys):
    monkeypatch.setattr(approvals, "human_present", lambda: True)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    key = approvals.expect_hash(draft)
    token, _ = approvals.mint(target=str(draft), key=key, project=proj)
    r = cli.invoke(app, ["review-draft", str(draft), "--by", "lisi", "--verdict",
                         "approve", "--approval", token])
    assert r.exit_code == 0, r.output
    items = approvals.load_approvals(approvals.approvals_file(proj))
    assert len(items) == 1 and items[0].by == "lisi" and items[0].key == key


def test_token_cannot_be_replayed_via_cli(proj, draft, monkeypatch):
    monkeypatch.setattr(approvals, "human_present", lambda: True)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    token, _ = approvals.mint(target=str(draft), key=approvals.expect_hash(draft),
                              project=proj)
    first = cli.invoke(app, ["review-draft", str(draft), "--by", "lisi", "--verdict",
                            "approve", "--approval", token])
    assert first.exit_code == 0, first.output
    draft.write_text(DRAFT.replace("< 90", "< 70"), encoding="utf-8")
    again = cli.invoke(app, ["review-draft", str(draft), "--by", "lisi", "--verdict",
                            "approve", "--approval", token])
    assert again.exit_code == 2
    assert "审批未落账" in again.output


# ---------------------------------------------------------------- 身份

@pytest.mark.parametrize("name", ["ai", "AI", "qoder-bot", "claude helper", "agent-2",
                                  "llm"])
def test_ai_sounding_reviewers_are_rejected(proj, name):
    assert approvals.check_reviewer(name, proj)


def test_reviewer_must_not_be_the_author(proj):
    assert approvals.check_reviewer("lisi", proj, author="lisi")
    assert approvals.check_reviewer("lisi", proj, author="other") is None


def test_unregistered_name_passes_until_an_allowlist_exists(proj):
    assert approvals.check_reviewer("zhang", proj) is None
    (proj / "config" / "reviewers.yaml").write_text(
        "humans:\n  - name: zhang@corp\n    aliases: [zhang]\n", encoding="utf-8")
    assert approvals.check_reviewer("zhang@corp", proj) is None
    assert approvals.check_reviewer("zhang", proj) is None      # 别名也算登记过
    assert approvals.check_reviewer("wang", proj)               # 不在名单


def test_empty_reviewer_is_rejected(proj):
    assert approvals.check_reviewer("   ", proj)


# ---------------------------------------------------------------- 沿袭 vs 新增

def test_untouched_existing_scenario_is_grandfathered(proj, monkeypatch):
    old = proj / "scenarios" / "order" / "old.yaml"
    old.write_text(DRAFT.replace("ai-generated", "smoke"), encoding="utf-8")
    _git("add", "-A", cwd=proj)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "add old",
         cwd=proj)
    needed, why = approvals.needs_approval(target=old, project=proj, touched=set(),
                                           repo=proj, base_ref="HEAD")
    assert not needed and "沿袭" in why


def test_file_created_after_the_base_always_needs_approval(proj):
    """先 commit 再 plan 不能自助转正：基线处没这个文件就是新增。"""
    new = proj / "scenarios" / "order" / "brand-new.yaml"
    new.write_text(DRAFT.replace("ai-generated", "smoke"), encoding="utf-8")
    needed, why = approvals.needs_approval(target=new, project=proj, touched=set(),
                                           repo=proj, base_ref="HEAD")
    assert needed and "基线之后才出现" in why


def test_touched_file_needs_approval(proj, draft):
    rel = approvals._rel(draft, proj)
    needed, why = approvals.needs_approval(target=draft, project=proj, touched={rel})
    assert needed and "变更碰过" in why


def test_approved_content_clears_the_requirement(proj, draft, monkeypatch):
    monkeypatch.setattr(approvals, "human_present", lambda: True)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    key = approvals.expect_hash(draft)
    token, _ = approvals.mint(target=str(draft), key=key, project=proj)
    approvals.record_approval(target=str(draft), key=key, by="lisi", note="看过了",
                              token=token, project=proj)
    needed, why = approvals.needs_approval(target=draft, project=proj,
                                           touched={approvals._rel(draft, proj)})
    assert not needed and why == "已批准"


# ---------------------------------------------------------------- 事件流水

def test_record_writes_an_intent_transition_event(proj, draft, tmp_path):
    from atk.run_store import IntentRecord, create_run, save_run

    runs = proj / "reports" / "runs"
    rec = create_run(runs_dir=runs)
    rec.intents.append(IntentRecord(title="页面下单", status="pending"))
    save_run(rec, runs_dir=runs)
    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG")
    r = cli.invoke(app, ["record", rec.run_id, "--title", "页面下单", "--status", "pass",
                        "--note", "订单在列表可见", "--evidence", str(shot),
                        "--runs-dir", str(runs), "--project", str(proj)])
    assert r.exit_code == 0, r.output
    from atk import events

    log = events.read(events.events_path(runs, rec.run_id))
    kinds = [e["kind"] for e in log]
    assert "intent" in kinds and "save" in kinds
    intent = [e for e in log if e["kind"] == "intent"][-1]
    assert intent["from"] == "pending" and intent["to"] == "pass"
    assert intent["at"] and intent["target"] == "页面下单"


def test_tampering_with_run_yaml_breaks_the_gate(proj):
    """手改 run.yaml 的结论必须被查出，而不是被无条件相信。"""
    from atk.run_store import ScenarioSummary, StepSummary, create_run, save_run
    import yaml

    runs = proj / "reports" / "runs"
    rec = create_run(runs_dir=runs)
    rec.scenarios.append(ScenarioSummary(name="s", module="order", passed=True,
                                         error_class="none",
                                         steps=[StepSummary(title="t", passed=True)]))
    save_run(rec, runs_dir=runs)
    f = runs / rec.run_id / "run.yaml"
    data = yaml.safe_load(f.read_text(encoding="utf-8"))
    data["scenarios"][0]["error_class"] = "assertion"     # 把失败伪装成没发生
    data["scenarios"][0]["passed"] = False
    f.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    r = cli.invoke(app, ["gate", rec.run_id, "--repo", str(proj),
                         "--runs-dir", str(runs)])
    assert r.exit_code == 1, r.output
    assert "被直接改过" in r.output


def test_legacy_record_without_events_only_warns(proj):
    """升级前就存在的记录不该一夜之间全变红。"""
    from atk.run_store import ScenarioSummary, StepSummary, create_run, save_run

    runs = proj / "reports" / "runs"
    rec = create_run(runs_dir=runs, base_ref="HEAD", head_ref="HEAD")
    rec.scenarios.append(ScenarioSummary(name="s", module="order", passed=True,
                                         error_class="none",
                                         steps=[StepSummary(title="t", passed=True)]))
    save_run(rec, runs_dir=runs)
    # 真正的"旧记录"：文件在、事件流水不在（升级前建立的记录就是这样）
    (runs / rec.run_id / "events.yaml").unlink(missing_ok=True)
    r = cli.invoke(app, ["gate", rec.run_id, "--repo", str(proj),
                         "--runs-dir", str(runs)])
    assert r.exit_code == 0, r.output
    assert "无事件流水" in r.output
