import pytest
from atk.store.models import Scenario, ApiStep, UiStep, Priority, parse_step, parse_expect_map


def _raw_scenario():
    return {
        "scenario": "下单主流程",
        "module": "order",
        "priority": "P0",
        "tags": ["smoke", "order"],
        "env": "local",
        "steps": [
            {
                "api": {
                    "call": "POST /api/orders",
                    "headers": {"Authorization": "Bearer ${token}"},
                    "body": {"skuId": "${test_sku}", "qty": 1},
                    "expect": {"status": 200, "data.orderNo": "not_null"},
                    "capture": {"orderNo": "data.orderNo"},
                }
            },
            {
                "ui": {
                    "action": "用订单号搜索订单列表",
                    "expect": "出现一条待支付记录",
                }
            },
        ],
    }


def test_parse_scenario():
    s = Scenario.from_raw(_raw_scenario(), file="scenarios/order/create.yaml")
    assert s.scenario == "下单主流程"
    assert s.priority == Priority.P0
    assert isinstance(s.steps[0], ApiStep)
    assert s.steps[0].call == "POST /api/orders"
    assert s.steps[0].capture == {"orderNo": "data.orderNo"}
    assert isinstance(s.steps[1], UiStep)
    assert s.file == "scenarios/order/create.yaml"


def test_parse_expect_map():
    rules = parse_expect_map({"status": 200, "data.orderNo": "not_null", "data.code": 0})
    assert rules[0].status == 200
    assert rules[1].op == "not_null"
    assert rules[2].path == "data.code" and rules[2].value == 0


def test_parse_step_rejects_unknown():
    with pytest.raises(ValueError):
        parse_step({"grpc": {}})
