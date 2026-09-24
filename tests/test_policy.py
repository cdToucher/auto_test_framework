"""policy 表的行为：档位划分、退出码投影、兜底方向。

这一组守的不是"算得对"，而是"任何没想到的情况都往保守侧倒"。
"""
import pathlib
import re

import pytest

from atk import policy

ROOT = pathlib.Path(__file__).resolve().parents[1]


class _Row:
    """最小替身：tally 只看 passed 与 error_class 两个属性。"""

    def __init__(self, passed, error_class):
        self.passed = passed
        self.error_class = error_class


def test_runner_emits_only_registered_classes():
    """runner 里出现的 error_class 字面量必须都已登记进 TIER。

    漏登记不会被立刻发现——它会静默落进 blocked 兜底，把退出码从 0 改成 2。
    所以直接扫源码对齐，而不是靠人记得同步。
    """
    src = (ROOT / "atk" / "executors" / "runner.py").read_text(encoding="utf-8")
    literals = set(re.findall(r'error_class\s*[=:]\s*"(\w+)"', src))
    assert literals, "runner 里应当能扫到 error_class 字面量"
    assert literals <= set(policy.TIER), f"未登记的归类: {sorted(literals - set(policy.TIER))}"


def test_tier_table_is_the_documented_six():
    assert set(policy.TIER) == {
        "none", "assertion", "config", "environment", "ui_pending", "skipped"}


def test_tiers_are_exhaustive_and_disjoint():
    """total 恒等于四档之和，这是 gate/report/junit 共用一份计数的前提。"""
    rows = [_Row(False, c) for c in policy.TIER if c != "none"] + [_Row(True, "none")]
    t = policy.tally(rows)
    assert t.total == len(rows)
    assert t.passed + t.failed + t.blocked + t.pending == t.total
    assert (t.passed, t.failed, t.blocked, t.pending) == (1, 2, 1, 2)


@pytest.mark.parametrize("error_class,expected", [
    ("none", policy.TIER_PASS),
    ("assertion", policy.TIER_FAIL),
    ("config", policy.TIER_FAIL),
    ("environment", policy.TIER_BLOCKED),
    ("ui_pending", policy.TIER_PENDING),
    ("skipped", policy.TIER_PENDING),
])
def test_each_class_lands_where_designed(error_class, expected):
    assert policy.tier_of(error_class == "none", error_class) == expected


def test_unknown_class_is_blocked_never_passed():
    """兜底方向：没见过的归类算受阻（exit 2 "没测成"），绝不当通过。"""
    assert policy.tier_of(False, "who_knows") == policy.TIER_BLOCKED
    assert policy.tally([_Row(False, "who_knows")]).blocked == 1


def test_record_claiming_no_error_but_not_passed_is_blocked():
    """run.yaml 是人/AI 可编辑的 YAML，自相矛盾的条目不能被判成通过。"""
    assert policy.tier_of(False, "none") == policy.TIER_BLOCKED


@pytest.mark.parametrize("t,load_errors,want", [
    (policy.Tally(1, 1, 0, 0, 0), 0, 0),   # 全通过
    (policy.Tally(2, 1, 1, 0, 0), 0, 1),   # 用例失败
    (policy.Tally(2, 1, 0, 1, 0), 0, 2),   # 只剩受阻
    (policy.Tally(1, 1, 0, 0, 0), 1, 2),   # 库有坏文件：没测成，不是这次改动坏了
    (policy.Tally(2, 1, 0, 0, 1), 0, 0),   # 待实测不拖 exit 1
    (policy.Tally(2, 1, 0, 0, 1), 1, 2),   # 加载错误盖过待实测
    (policy.Tally(3, 1, 1, 1, 0), 0, 1),   # 失败优先于受阻
    (policy.Tally(3, 1, 0, 1, 1), 0, 2),   # 受阻优先于待实测
    (policy.Tally(3, 1, 1, 1, 0), 5, 1),   # 真失败时加载错误不再抬到 2
])
def test_projection_is_a_pure_function_of_tiers(t, load_errors, want):
    assert policy.project(t, load_errors=load_errors) == want


@pytest.mark.parametrize("t", [
    policy.Tally(0, 0, 0, 0, 0), policy.Tally(4, 1, 1, 1, 1), policy.Tally(3, 0, 2, 0, 1),
])
def test_projection_only_uses_the_three_documented_codes(t):
    assert policy.project(t) in (0, 1, 2)


def test_junit_unknown_class_is_skipped_not_failure():
    assert policy.junit_node("who_knows") == "skipped"
    assert policy.junit_node("environment") == "error"


def test_junit_counts_cannot_disagree_with_tally():
    t = policy.Tally(total=4, passed=1, failed=1, blocked=1, pending=1)
    assert policy.junit_counts(t) == {
        "tests": "4", "failures": "1", "errors": "1", "skipped": "1"}


def test_review_line_reports_all_three_states():
    def line(reviews):
        rec = type("R", (), {"reviews": reviews})()
        return policy.review_line(rec)

    assert "未经开发确认" in line([])
    assert "已获开发确认" in line([type("RV", (), {"verdict": "approve", "by": "li",
                                                   "note": ""})()])
    assert "已被开发驳回" in line([type("RV", (), {"verdict": "reject", "by": "li",
                                                   "note": "缺断言"})()])
