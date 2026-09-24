"""envelope 契约：字段稳定 + 向后兼容，AI 与 CI 都靠它。"""
import json

from atk import envelope


def test_wrap_preserves_existing_payload_keys():
    """平铺合并：老键一个都不能少（skill 与 CI 在读 run_id / verdict / intents_pending）。"""
    payload = {"run_id": "r1", "verdict": "approve", "intents_pending": ["a"]}
    out = envelope.wrap("review", payload, next_steps=["atk gate --last --format json"])
    assert out["run_id"] == "r1"
    assert out["verdict"] == "approve"
    assert out["intents_pending"] == ["a"]
    assert out["cmd"] == "review"
    assert out["schema_version"] == envelope.SCHEMA_VERSION


def test_wrap_respects_payloads_own_next_unless_overridden():
    """record/smoke 按分支自己算 next，接入契约时不该被覆盖成空的。"""
    out = envelope.wrap("record", {"next": ["atk gate --last --format json"]})
    assert out["next"] == ["atk gate --last --format json"]
    assert envelope.wrap("record", {"next": ["a"]}, next_steps=["b"])["next"] == ["b"]


def test_blockers_make_ok_false_and_exit_2():
    out = envelope.wrap("agent", {"state": "no_scenarios"}, blockers=["场景库为空"])
    assert out["ok"] is False and out["exit_code"] == 2


def test_failure_without_blockers_is_exit_1():
    out = envelope.wrap("gate", {"passed": False}, ok=False)
    assert out["ok"] is False and out["exit_code"] == 1


def test_explicit_exit_code_wins():
    """契约允许命令显式给退出码，不许 wrap 按 ok 反推——run/gate/smoke 都要覆盖。

    举例命令刻意不用 plan：plan 的加载错误过去被特判成 exit 0，现在与 run 一样
    走 policy.project（配置错=2），拿它当例子会把一个已修掉的口径写成契约。
    """
    out = envelope.wrap("run", {}, blockers=["x"], ok=False, exit_code=2)
    assert out["exit_code"] == 2 and out["ok"] is False


def test_requires_human_and_prompt_are_always_present():
    """机器读的字段恒定存在：不该为"这个键今天有没有"写分支。"""
    plain = envelope.wrap("gate", {})
    assert plain["requires_human"] is False and plain["human_prompt"] is None
    human = envelope.wrap("gate", {}, requires_human=True, human_prompt="请确认")
    assert human["requires_human"] is True and human["human_prompt"] == "请确认"


def test_dumps_is_json_parseable_and_keeps_chinese():
    body = envelope.dumps("agent", {"state": "就绪"}, next_steps=["atk agent --format json"])
    assert json.loads(body)["state"] == "就绪"


def test_contract_fields_are_listed_once():
    """文档/断言引用这份字段名，别在各命令里散着写。"""
    out = envelope.wrap("x", {})
    assert set(envelope.FIELDS) <= set(out)
