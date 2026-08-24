import httpx

from atk.executors.asserts import MISSING, evaluate, get_path

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


def test_eq_pass_fail_with_diff_message():
    assert evaluate(_resp(RESPONSE), E(path="data.orderNo", value="NO1"))[0] is True
    ok, why = evaluate(_resp(RESPONSE), E(path="data.orderNo", value="NO2"))
    assert ok is False and "NO2" in why and "NO1" in why


def test_not_null_and_missing_path_fails():
    assert evaluate(_resp(RESPONSE), E(path="data.list.0.status", op="not_null"))[0] is True
    assert evaluate(_resp(RESPONSE), E(path="data.empty", op="not_null"))[0] is False
    ok, _ = evaluate(_resp(RESPONSE), E(path="data.x.y", value=1))
    assert ok is False
