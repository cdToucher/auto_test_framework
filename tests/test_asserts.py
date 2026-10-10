import httpx

from atk.executors.asserts import MISSING, compare, evaluate, get_path

RESPONSE = {
    "code": 0,
    "data": {"orderNo": "NO1", "list": [{"status": "待支付"}]},
}


class E:
    def __init__(self, status=None, path=None, op="eq", value=None):
        self.status = status
        self.path = path
        self.op = op
        self.value = value


def _resp(body) -> httpx.Response:
    return httpx.Response(200, json=body)


def test_get_path_dotted_and_index():
    assert get_path(RESPONSE, "data.orderNo") == "NO1"
    assert get_path(RESPONSE, "data.list.0.status") == "待支付"


def test_get_path_missing_returns_sentinel():
    assert get_path(RESPONSE, "data.nope") is MISSING
    assert get_path(RESPONSE, "data.list.5.x") is MISSING


def test_status_eq_pass_fail():
    assert evaluate(_resp(RESPONSE), E(status=200))[0] is True
    ok, why = evaluate(httpx.Response(500), E(status=200))
    assert ok is False and "500" in why


def test_is_null_accepts_empty_string():
    """空串是"没有值"的第三种形态：既不满足 not_null，就必须满足 is_null。"""
    assert compare("is_null", "", None)[0] is True
    assert compare("not_null", "", None)[0] is False


def test_bool_never_equals_number():
    """True == 1 是 Python 的巧合；业务断言里布尔标志与数值 1 是两回事。"""
    assert compare("eq", True, 1)[0] is False
    assert compare("eq", 1, True)[0] is False
    assert compare("eq", True, True)[0] is True
    assert compare("eq", 1, 1)[0] is True


def test_eq_pass_fail_with_diff_message():
    assert evaluate(_resp(RESPONSE), E(path="data.orderNo", value="NO1"))[0] is True
    ok, why = evaluate(_resp(RESPONSE), E(path="data.orderNo", value="NO2"))
    assert ok is False and "NO2" in why and "NO1" in why


def test_not_null_and_missing_path_fails():
    assert evaluate(_resp(RESPONSE), E(path="data.list.0.status", op="not_null"))[0] is True
    assert evaluate(_resp(RESPONSE), E(path="data.empty", op="not_null"))[0] is False
    ok, _ = evaluate(_resp(RESPONSE), E(path="data.x.y", value=1))
    assert ok is False
