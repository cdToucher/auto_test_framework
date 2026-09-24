"""逐项证明 gate 检查表里每一行"会不会拦"。

此前 9 项检查只有 4 项有拦截用例，其余只测了通过侧——等于"会不会拦"这件事
没人管。检查表化之后，每一行都能被单独钉住。
"""
import subprocess
from pathlib import Path

import pytest

from atk import policy
from atk.drafts import DRAFT_TAG
from atk.run_store import IntentRecord, RunRecord, ScenarioSummary, StepSummary


def _git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "f.py").write_text("x=1", encoding="utf-8")
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "one", cwd=tmp_path)
    (tmp_path / "f.py").write_text("x=2", encoding="utf-8")
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "two", cwd=tmp_path)
    return tmp_path


def _rec(**kw) -> RunRecord:
    kw.setdefault("scenarios", [_ok()])
    kw.setdefault("base_ref", "HEAD~1")
    kw.setdefault("head_ref", "HEAD")
    # head_commit 默认留空（旧记录形状）：填了假 SHA 的话，这些用例会依赖"当前目录
    # 恰好是不是一个 git 仓库"，换台机器结论就变。真要测 head 的用例自己传。
    kw.setdefault("head_commit", "")
    kw.setdefault("affected_files", ["src/x.py"])
    return RunRecord(run_id="smoke-test-1", created_at="2026-09-24T00:00:00", **kw)


def _ok(name="s", **kw) -> ScenarioSummary:
    kw.setdefault("steps", [StepSummary(title="t", passed=True, detail="200")])
    return ScenarioSummary(name=name, passed=True, error_class="none", **kw)


def _bad(name="s", cls="assertion", **kw) -> ScenarioSummary:
    kw.setdefault("steps", [StepSummary(title="t", passed=False, detail="期望 200")])
    return ScenarioSummary(name=name, passed=False, error_class=cls, **kw)


def _ctx(rec, changed=("src/x.py",), dirty=(), **kw):
    return policy.GateContext(
        rec=rec, repo=kw.pop("repo", Path(".")), head=kw.pop("head", "HEAD"),
        runs_dir=kw.pop("runs_dir", Path(".")), root=kw.pop("root", Path(".")),
        _changed=list(changed) if changed is not None else None,
        _dirty=list(dirty),
    )


def _blocking(ctx):
    passed, verdicts = policy.run_checks(ctx)
    return passed, [m for ok, m, _ in verdicts if not ok]


# --- 表本身 ---

def test_check_ids_unique():
    ids = [c.id for c in policy.GATE_CHECKS]
    assert len(ids) == len(set(ids))


def test_which_checks_block_is_a_deliberate_choice():
    """拦/不拦是写进数据的决定，改动它必须同时改这条测试。

    dirty_tree 与 dev_confirmation 维持仅告警是本轮的明确取舍，
    不是"某段代码恰好没把 ok 设成 False"。
    """
    assert {c.id for c in policy.GATE_CHECKS if c.blocking} == {
        "empty_record", "change_drift", "head_advanced", "case_fail",
        "change_covered", "deps_stale", "ledger_consistency", "unreviewed_drafts",
        "unapproved_expects", "unresolved_intents", "pending_intents",
    }


def test_change_without_any_covering_scenario_blocks():
    """gate 此前只证明"这些场景在这个 commit 上通过了"，从不证明覆盖这次改动。"""
    ctx = _ctx(_rec(affected_modules=["order"], planned_scenarios=[]))
    passed, blocks = _blocking(ctx)
    assert not passed and any("没有一条属于" in b for b in blocks)


def test_full_suite_selection_is_not_flagged_as_uncovered():
    """算不清影响面时 plan 已经跑了全库，不该再被"没覆盖"拦一次。"""
    ctx = _ctx(_rec(selection_mode="full_suite", affected_modules=["order"],
                    planned_scenarios=[], unmapped_files=["src/new.py"]))
    passed, blocks = _blocking(ctx)
    assert passed and not blocks


def test_inert_unmapped_files_only_warn():
    """只改文档：未映射清单要报出来，但不许因此拦下合并。"""
    ctx = _ctx(_rec(affected_files=["README.md"], unmapped_files=["README.md"],
                    planned_scenarios=["scenarios/a.yaml"]),
               changed=["README.md"])
    passed, blocks = _blocking(ctx)
    assert passed and not blocks, blocks
    assert any("未映射到模块" in m for _, m, _ in policy.run_checks(ctx)[1])


# --- 每一项违规都被单独证明 ---

def test_empty_record_blocks():
    ctx = _ctx(_rec(scenarios=[], intents=[]))
    passed, blocks = _blocking(ctx)
    assert not passed and any("无场景结果也无意图" in b for b in blocks)


def test_unbound_base_ref_is_informational():
    ctx = _ctx(_rec(base_ref=""), changed=None)
    passed, blocks = _blocking(ctx)
    assert passed and not blocks


def test_drift_blocks_and_matching_diff_passes():
    passed, blocks = _blocking(_ctx(_rec(), changed=["src/other.py"]))
    assert not passed and any("变更已漂移" in b for b in blocks)

    ctx = _ctx(_rec(), changed=["src/x.py"])
    ok, verdicts = policy.run_checks(ctx)
    assert ok
    assert any(m == "变更一致（1 个文件）" for _, m, _ in verdicts)


def test_head_advanced_blocks(git_repo):
    rec = _rec(head_commit="0" * 40)
    ctx = _ctx(rec, repo=git_repo)
    passed, blocks = _blocking(ctx)
    assert not passed and any("head 已前进" in b for b in blocks)
    # 用真实 SHA 时不拦
    sha = subprocess.run(["git", "-C", str(git_repo), "rev-parse", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    passed2, blocks2 = _blocking(_ctx(_rec(head_commit=sha), repo=git_repo))
    assert passed2 and not blocks2


def test_legacy_record_without_head_commit_only_informs(git_repo):
    ctx = _ctx(_rec(head_commit=""), repo=git_repo)
    passed, blocks = _blocking(ctx)
    assert passed and not blocks
    assert any("旧记录" in m for _, m, _ in policy.run_checks(ctx)[1])


def test_dirty_tree_warns_but_does_not_block():
    ctx = _ctx(_rec(), dirty=["a.py", "b.py"])
    passed, blocks = _blocking(ctx)
    assert passed and not blocks
    assert [w for ok, m, w in policy.run_checks(ctx)[1]
            if w and "未提交文件" in m]


def test_case_fail_and_config_error_both_block():
    for cls in ("assertion", "config"):
        ctx = _ctx(_rec(scenarios=[_bad("s1", cls)]))
        passed, blocks = _blocking(ctx)
        assert not passed, cls
        assert any("用例失败 1 个: s1" in b for b in blocks), cls


def test_blocked_scenarios_warn_but_never_block():
    ctx = _ctx(_rec(scenarios=[_ok("a"), _bad("b", "environment")]))
    passed, blocks = _blocking(ctx)
    assert passed and not blocks
    assert any("复用场景 2 个无用例失败（受阻 1 个）" in m for _, m, _ in
               policy.run_checks(ctx)[1])


def test_draft_on_disk_blocks(tmp_path):
    f = tmp_path / "draft.yaml"
    f.write_text(f"scenario: d\nmodule: m\ntags: [{DRAFT_TAG}]\n", encoding="utf-8")
    ctx = _ctx(_rec(scenarios=[_ok("d", file=str(f), draft=True)]))
    passed, blocks = _blocking(ctx)
    assert not passed and any("未评审AI草稿 1 个: d" in b for b in blocks)


def test_vanished_draft_still_blocks():
    """跑完草稿再 reject 移走文件，不能把未审定结果洗成干净记录。"""
    ctx = _ctx(_rec(scenarios=[_ok("d", file="/nope/gone.yaml", draft=True)]))
    passed, blocks = _blocking(ctx)
    assert not passed and any("未评审AI草稿" in b for b in blocks)


@pytest.mark.parametrize("status", ["fail", "suspect"])
def test_unresolved_intent_blocks(status):
    ctx = _ctx(_rec(intents=[IntentRecord(title="页面下单", status=status, note="x")]))
    passed, blocks = _blocking(ctx)
    assert not passed and any(f"页面下单[{status}]" in b for b in blocks)


def test_pending_intent_blocks_and_names_the_run():
    """回填提示必须带真实 run_id：--last 是全局单槽，两个 run 在飞时会指错记录。"""
    ctx = _ctx(_rec(intents=[IntentRecord(title="页面下单", status="pending")]))
    passed, blocks = _blocking(ctx)
    assert not passed and any("未实测回填的 UI 意图 1 条" in b for b in blocks)
    msg = [m for ok, m, _ in policy.run_checks(ctx)[1] if not ok][0]
    assert "smoke-test-1" in msg and "--last" not in msg


def test_backfilled_ui_is_reported_as_resolved():
    ctx = _ctx(_rec(scenarios=[_bad("u", "ui_pending")],
                    intents=[IntentRecord(title="u", status="pass", note="实测通过",
                                          evidence=["evidence/s.png"])]))
    passed, blocks = _blocking(ctx)
    assert passed and not blocks
    assert any("已全部回填" in m for _, m, _ in policy.run_checks(ctx)[1])


def test_blocked_intent_warns_only():
    ctx = _ctx(_rec(intents=[IntentRecord(title="t", status="blocked", note="环境挂了")]))
    passed, blocks = _blocking(ctx)
    assert passed and not blocks
    assert any("受阻意图 1 条" in m for _, m, _ in policy.run_checks(ctx)[1])


# --- 共存条件：此前没有任何用例同时构造两个以上问题 ---

def test_several_violations_are_all_reported():
    """一条记录里多个问题必须全说，不能只报第一个就让人以为其余不存在。"""
    ctx = _ctx(_rec(
        scenarios=[_ok("a"), _bad("失败单", "assertion"), _bad("草稿", "none",
                                                               draft=True,
                                                               file="/nope/x.yaml")],
        intents=[IntentRecord(title="页面下单", status="pending")],
        head_commit="0" * 40,
    ))
    passed, blocks = _blocking(ctx)
    assert not passed
    joined = " | ".join(blocks)
    assert "用例失败" in joined and "未评审AI草稿" in joined and "未实测回填" in joined


def test_empty_selection_is_blocked_through_change_covered():
    """计划里一条场景都没有，且确实有已知模块被改到 → 拦。

    计划文件把这条单列成 empty_selection；在"未映射的行为相关文件 → 先跑全量"的规则下，
    它和 change_covered 是同一判据，所以合成一项而不是再写一条会互相矛盾的分支。
    """
    ctx = _ctx(_rec(affected_modules=["order"], planned_scenarios=[], scenarios=[]),
               changed=["src/x.py"])
    passed, blocks = _blocking(ctx)
    assert not passed and any("没有一条属于" in b for b in blocks)


def test_docs_only_change_selecting_nothing_is_not_blocked():
    """只改文档时"没选中场景"是正确结果，不许拦——否则这条规则三周内就会被所有人绕过。"""
    ctx = _ctx(_rec(affected_modules=[], affected_files=["README.md"],
                    unmapped_files=["README.md"], planned_scenarios=[],
                    scenarios=[_ok("s")]), changed=["README.md"])
    passed, blocks = _blocking(ctx)
    assert passed and not blocks, blocks


def test_blocked_never_outweighs_a_real_failure():
    ctx = _ctx(_rec(scenarios=[_bad("真失败", "assertion"),
                               _bad("环境", "environment")]))
    assert policy.project(policy.tally(ctx.rec.scenarios)) == 1
