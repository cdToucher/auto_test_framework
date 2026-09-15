"""断言 DSL 测试：操作符矩阵、转义、列表式、向后兼容。"""
import httpx
import pytest

from atk.executors.asserts import MISSING, compare, describe, evaluate
from atk.store.models import ApiExpect, parse_expect, parse_expect_value


def _resp(body, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


BODY = {
    "code": 0,
    "data": {
        "orderNo": "NO001",
        "amount": 89.5,
        "discount": 10,
        "status": "pending",
        "msg": "下单成功",
        "items": [{"sku": "A"}, {"sku": "B"}, {"sku": "C"}],
        "mobile": "13800138000",
        "err": None,
    },
}


def _eval(path: str, op: str, value=None, body=None):
    return evaluate(_resp(BODY if body is None else body), ApiExpect(path=path, op=op, value=value))


# ------------------------------------------------------------------ 值解析

@pytest.mark.parametrize(
    "raw,op,value",
    [
        ("done", "eq", "done"),                      # 字面量
        (200, "eq", 200),                            # 非字符串
        ("not_null", "not_null", None),
        ("is_null", "is_null", None),
        ("< 90", "lt", "90"),
        (">= 1", "gte", "1"),
        ("!= cancelled", "ne", "cancelled"),
        ("contains: 成功", "contains", "成功"),
        ("regex: ^1[3-9]\\d{9}$", "regex", "^1[3-9]\\d{9}$"),
        ("len: 3", "len", 3),
        ("len: 1..5", "len", {"min": 1, "max": 5}),
        ("type: str", "type", "str"),
        ("in: [pending, paid]", "in", ["pending", "paid"]),
        ("not_in: [a, b]", "not_in", ["a", "b"]),
        ("startswith: NO", "startswith", "NO"),
        ("\\<not-an-op", "eq", "<not-an-op"),        # 转义
        ("https://example.com", "eq", "https://example.com"),  # 冒号但不匹配关键字
        ("12:30", "eq", "12:30"),                    # 数字开头的冒号
    ],
)
def test_parse_expect_value(raw, op, value):
    assert parse_expect_value(raw) == (op, value)


def test_parse_expect_rejects_bad_shape():
    with pytest.raises(ValueError):
        parse_expect("not-a-map")


def test_unknown_op_rejected_by_model():
    with pytest.raises(Exception):
        ApiExpect(path="data.x", op="greater_than")


# ------------------------------------------------------------------ 比较操作符

@pytest.mark.parametrize(
    "op,value,expected",
    [
        ("eq", 89.5, True),
        ("ne", 100, True),
        ("lt", 90, True),
        ("lt", 80, False),
        ("lte", 89.5, True),
        ("gt", 50, True),
        ("gt", 100, False),
        ("gte", 89.5, True),
    ],
)
def test_numeric_ops(op, value, expected):
    assert _eval("data.amount", op, value)[0] is expected


def test_numeric_op_on_non_numeric_fails_clearly():
    ok, why = _eval("data.status", "lt", 90)
    assert ok is False and "无法做数值比较" in why


@pytest.mark.parametrize(
    "path,op,value,expected",
    [
        ("data.msg", "contains", "成功", True),
        ("data.msg", "contains", "失败", False),
        ("data.msg", "not_contains", "失败", True),
        ("data.msg", "not_contains", "成功", False),
        ("data.orderNo", "startswith", "NO", True),
        ("data.orderNo", "startswith", "XX", False),
        ("data.orderNo", "endswith", "001", True),
        ("data.orderNo", "regex", r"^NO\d+$", True),
        ("data.orderNo", "regex", r"^[A-Z]+$", False),
        ("data.status", "in", ["pending", "paid"], True),
        ("data.status", "in", ["cancelled"], False),
        ("data.status", "not_in", ["cancelled"], True),
        ("data.mobile", "regex", r"^1[3-9]\d{9}$", True),
    ],
)
def test_string_ops(path, op, value, expected):
    assert _eval(path, op, value)[0] is expected


def test_regex_invalid_reports_error():
    ok, why = _eval("data.orderNo", "regex", "[unclosed")
    assert ok is False and "正则表达式非法" in why


@pytest.mark.parametrize(
    "value,expected",
    [(3, True), (2, False), ({"min": 1, "max": 5}, True), ({"min": 5, "max": 9}, False)],
)
def test_len_op(value, expected):
    assert _eval("data.items", "len", value)[0] is expected


def test_len_on_unsized_value():
    assert _eval("data.amount", "len", 3)[0] is False


@pytest.mark.parametrize(
    "value",
    ["not-a-number", {"min": "x"}, {"min": 3, "max": 1}, {}, {"foo": 1}],
)
def test_len_rejects_invalid_ranges_during_validation(value):
    with pytest.raises(Exception, match="len"):
        ApiExpect(path="data.items", op="len", value=value)


@pytest.mark.parametrize(
    "path,value,expected",
    [
        ("data.amount", "float", True),
        ("data.amount", "num", True),
        ("data.amount", "str", False),
        ("data.discount", "int", True),
        ("data.status", "str", True),
        ("data.items", "list", True),
        ("data.items", "dict", False),
        ("data", "dict", True),
        ("data", "object", True),
        ("data.status", "bogus", False),
    ],
)
def test_type_op(path, value, expected):
    assert _eval(path, "type", value)[0] is expected


def test_null_ops():
    assert _eval("data.err", "is_null")[0] is True
    assert _eval("data.orderNo", "is_null")[0] is False
    assert _eval("data.orderNo", "not_null")[0] is True
    assert _eval("data.nope", "not_null")[0] is False


def test_missing_field_fails_for_all_ops():
    for op in ("eq", "lt", "contains", "regex", "len", "type", "in"):
        ok, why = _eval("data.nope", op, 1)
        assert ok is False and "缺失" in why


def test_contains_on_list():
    assert _eval("data.items", "contains", {"sku": "A"})[0] is True


# ------------------------------------------------------------------ status 断言

def test_status_symbol_ops():
    r = _resp(BODY)
    assert evaluate(r, ApiExpect(status=200))[0] is True
    # status: "< 400" 表示 实际状态码 < 400
    assert evaluate(r, ApiExpect(status=400, status_op="lt"))[0] is True
    ok, why = evaluate(r, ApiExpect(status=200, status_op="lt"))
    assert ok is False and "status lt 200" in why
    ok, why = evaluate(r, ApiExpect(status=500, status_op="ne"))
    assert ok is True
    ok, why = evaluate(r, ApiExpect(status=200, status_op="ne"))
    assert ok is False


def test_status_string_coerced_to_int():
    e = parse_expect({"status": "200"})[0]
    assert e.status == 200 and e.status_op == "eq"


# ------------------------------------------------------------------ YAML 端到端

def test_map_form_backward_compatible():
    rules = parse_expect({"status": 200, "data.status": "pending", "data.orderNo": "not_null"})
    assert [(r.status, r.path, r.op, r.value) for r in rules] == [
        (200, None, "eq", None),
        (None, "data.status", "eq", "pending"),
        (None, "data.orderNo", "not_null", None),
    ]
    assert all(evaluate(_resp(BODY), r)[0] for r in rules)


def test_map_form_with_operators():
    rules = parse_expect(
        {"status": 200, "data.amount": "< 90", "data.msg": "contains: 成功"}
    )
    assert all(evaluate(_resp(BODY), r)[0] for r in rules)


def test_list_form():
    rules = parse_expect(
        [
            {"path": "data.amount", "op": "lte", "value": 90},
            {"path": "data.items", "op": "len", "value": {"min": 1, "max": 10}},
            {"status": 200},
        ]
    )
    assert all(evaluate(_resp(BODY), r)[0] for r in rules)


def test_list_form_rejects_non_mapping():
    with pytest.raises(ValueError):
        parse_expect(["data.amount"])


def test_list_form_accepts_symbol_alias_op():
    """op: "==" 这类符号别名在列表式里必须合法（否则 AI 写出来的场景直接加载失败）。"""
    rules = parse_expect([{"path": "data.discount", "op": "==", "value": 10}])
    assert rules[0].op == "eq"
    assert evaluate(_resp(BODY), rules[0])[0] is True


def test_list_form_parses_operator_inside_value():
    """value: "< 400" 形式（未显式给 op）在列表式里也要识别。"""
    rules = parse_expect([{"path": "data.amount", "value": "< 90"}])
    assert rules[0].op == "lt"
    assert evaluate(_resp(BODY), rules[0])[0] is True


def test_list_form_status_operator_inside_value():
    rules = parse_expect([{"status": "< 400"}])
    assert rules[0].status_op == "lt" and rules[0].status == 400
    assert evaluate(_resp(BODY), rules[0])[0] is True


def test_list_form_status_explicit_op_still_works():
    rules = parse_expect([{"status": 400, "op": "lt"}])
    assert rules[0].status_op == "lt" and rules[0].status == 400


def test_list_form_allows_multiple_assertions_on_same_path():
    """映射式受 YAML 键唯一限制做不到；列表式必须支持。"""
    rules = parse_expect(
        [
            {"path": "data.orderNo", "op": "not_null"},
            {"path": "data.orderNo", "op": "regex", "value": "^NO\\d+$"},
        ]
    )
    assert len(rules) == 2
    assert all(evaluate(_resp(BODY), r)[0] for r in rules)


def test_list_form_rejects_nullary_status():
    with pytest.raises(ValueError):
        parse_expect([{"status": "not_null"}])


def test_raw_preserved_for_reporting():
    rules = parse_expect({"data.amount": "< 90"})
    assert describe(rules[0]) == "data.amount: < 90"


def test_describe_falls_back_without_raw():
    e = ApiExpect(path="data.x", op="lt", value=5)
    assert describe(e) == "data.x lt 5"


def test_coupon_scenario_from_docs():
    """文档 5.2 节的"优惠券下单后金额扣减"示例，以前只能断 status:200。"""
    body = {"data": {"discount": 10, "payAmount": 89.5, "orderNo": "NO001"}}
    rules = parse_expect(
        {
            "status": 200,
            "data.discount": 10,
            "data.payAmount": "< 100",
            "data.orderNo": "not_null",
        }
    )
    assert all(evaluate(_resp(body), r)[0] for r in rules)


def test_compare_rejects_unknown_op():
    ok, why = compare("nope", 1, 1)
    assert ok is False and "未知操作符" in why


# ------------------------------------------------------------------ 执行器贯通

def _execute(expect_raw, body=None):
    """走完整 ApiExecutor 链路，确保 status_op / raw 不在重建断言时丢失。"""
    from atk.executors.api_executor import ApiExecutor
    from atk.store.models import ApiStep

    payload = BODY if body is None else body
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    with httpx.Client(transport=transport, base_url="http://test") as client:
        step = ApiStep(call="GET /x", expect=parse_expect(expect_raw))
        return ApiExecutor(client, {}).execute(step)


def test_executor_applies_status_operator():
    assert _execute({"status": "< 400"}).passed is True
    failed = _execute({"status": "< 200"})
    assert failed.passed is False and "status lt 200" in failed.detail


def test_executor_applies_path_operators():
    assert _execute({"data.amount": "< 90"}).passed is True
    failed = _execute({"data.amount": "> 100"})
    assert failed.passed is False and "data.amount" in failed.detail


def test_executor_substitutes_variables_in_operators():
    from atk.executors.api_executor import ApiExecutor
    from atk.store.models import ApiStep

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=BODY))
    with httpx.Client(transport=transport, base_url="http://test") as client:
        step = ApiStep(call="GET /x", expect=parse_expect({"data.amount": "< ${max}"}))
        r = ApiExecutor(client, {"max": 100}).execute(step)
    assert r.passed is True


def test_missing_sentinel_never_equals_value():
    assert compare("eq", MISSING, "x")[0] is False
