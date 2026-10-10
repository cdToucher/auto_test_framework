import httpx

from atk.executors.api_executor import ApiExecutor
from atk.store.models import ApiExpect, ApiStep


def test_execute_pass_captures_vars(mock_client):
    ex = ApiExecutor(mock_client, {})
    login = ApiStep(
        call="POST /api/login",
        body={"username": "alice", "password": "secret123"},
        expect=[ApiExpect(status=200)],
        capture={"token": "data.token"},
    )
    r = ex.execute(login)
    assert r.passed and ex.variables["token"].startswith("tok_")


def test_execute_failure_reports_diff(mock_client):
    ex = ApiExecutor(mock_client, {})
    bad = ApiStep(
        call="POST /api/login",
        body={"username": "a", "password": "b"},
        expect=[ApiExpect(status=200)],
    )
    r = ex.execute(bad)
    assert not r.passed and "401" in r.detail


def test_variable_substitution_and_auth_header(mock_client):
    ex = ApiExecutor(mock_client, {"token": "tok_demo123", "test_sku": "SKU-001"})
    step = ApiStep(
        call="POST /api/orders",
        headers={"Authorization": "Bearer ${token}"},
        body={"skuId": "${test_sku}", "qty": 1},
        expect=[ApiExpect(status=200)],
        capture={"orderNo": "data.orderNo"},
    )
    r = ex.execute(step)
    assert r.passed and ex.variables["orderNo"].startswith("NO")


def test_environment_error_classification():
    with httpx.Client(base_url="http://127.0.0.1:1", timeout=1, trust_env=False) as dead:
        ex = ApiExecutor(dead, {})
        r = ex.execute(ApiStep(call="GET /ping"))
    assert not r.passed and r.error_class == "environment"


def test_implicit_status_guard_when_no_expect(mock_client):
    ex = ApiExecutor(mock_client, {})
    r = ex.execute(ApiStep(call="POST /api/orders"))  # 无 token -> 401，且无显式断言
    assert not r.passed
    assert "<400" in r.detail or "401" in r.detail


def test_implicit_guard_fails_path_only_expect_on_error_status(mock_client):
    ex = ApiExecutor(mock_client, {})
    step = ApiStep(
        call="POST /api/login",
        body={"username": "a", "password": "b"},
        expect=[],  # 只断言 body 字段、漏 status
    )
    from atk.store.models import ApiExpect as E2

    step.expect = [E2(path="code", op="eq", value=401)]  # body 巧合匹配
    r = ex.execute(step)
    assert not r.passed and "隐式" in r.detail


def test_invalid_call_format_is_config_error(mock_client):
    ex = ApiExecutor(mock_client, {})
    for bad_call in ("/ping", "FETCH /ping", "GET"):
        r = ex.execute(ApiStep(call=bad_call))
        assert not r.passed and r.error_class == "config"


def test_capture_miss_fails_the_step_not_the_next_one(mock_client):
    """capture 没命中必须当步失败。

    旧实现只把"未命中路径"写进 detail 的警告里、步骤照过：真正的报错要到下一步
    "变量未解析"才出现——失败点与原因点隔了一步，报告上看起来像下一步的错。
    """
    ex = ApiExecutor(mock_client, {})
    r = ex.execute(
        ApiStep(
            call="GET /ping",
            expect=[],
            capture={"x": "nope.deep"},
        )
    )
    assert not r.passed
    assert "capture" in r.detail and "nope.deep" in r.detail


def test_non_json_response_with_capture_fails_the_step(mock_client):
    """声明了 capture 却拿到非 JSON：同样当步失败，不再只是警告。"""
    ex = ApiExecutor(mock_client, {"token": "tok_demo123"})
    r = ex.execute(
        ApiStep(
            call="GET /api/text",
            expect=[],
            capture={"x": "data.y"},
        )
    )
    assert not r.passed
    assert "capture" in r.detail


def test_non_json_response_without_capture_is_fine(mock_client):
    """没声明 capture 时，响应是不是 JSON 与断言无关，不警告也不失败。"""
    ex = ApiExecutor(mock_client, {})
    r = ex.execute(ApiStep(call="GET /api/text", expect=[]))
    assert r.passed and r.detail == "200"


def test_environment_error_retries_then_passes():
    calls = {"n": 0}

    class Flaky:
        def request(self, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("boom")
            return httpx.Response(200, json={"code": 0, "data": "pong"})

    ex = ApiExecutor(Flaky(), {})
    r = ex.execute(ApiStep(call="GET /ping"))
    assert r.passed and calls["n"] == 2


def test_environment_error_exhausts_retries():
    calls = {"n": 0}

    class Dead:
        def request(self, *a, **kw):
            calls["n"] += 1
            raise httpx.ConnectError("boom")

    ex = ApiExecutor(Dead(), {})
    r = ex.execute(ApiStep(call="GET /ping", retries=2))
    assert not r.passed and r.error_class == "environment"
    assert calls["n"] == 3 and "重试2次" in r.detail


def test_call_path_variable_substitution(mock_client):
    ex = ApiExecutor(mock_client, {"token": "tok_demo123", "test_sku": "SKU-001"})
    create = ApiStep(
        call="POST /api/orders",
        headers={"Authorization": "Bearer ${token}"},
        body={"skuId": "${test_sku}", "qty": 1},
        expect=[ApiExpect(status=200)],
        capture={"orderNo": "data.orderNo"},
    )
    assert ex.execute(create).passed
    query = ApiStep(
        call="GET /api/orders?orderNo=${orderNo}",
        headers={"Authorization": "Bearer ${token}"},
        expect=[
            ApiExpect(status=200),
            ApiExpect(path="data.list.0.orderNo", op="eq", value=ex.variables["orderNo"]),
        ],
    )
    assert ex.execute(query).passed


def test_expect_value_variable_substitution(mock_client):
    ex = ApiExecutor(
        mock_client,
        {"username": "alice", "password": "secret123", "test_sku": "SKU-001"},
    )
    login = ApiStep(
        call="POST /api/login",
        body={"username": "${username}", "password": "${password}"},
        expect=[
            ApiExpect(status=200),
            ApiExpect(path="data.token", op="not_null"),
        ],
        capture={"token": "data.token"},
    )
    assert ex.execute(login).passed

    create = ApiStep(
        call="POST /api/orders",
        headers={"Authorization": "Bearer ${token}"},
        body={"skuId": "${test_sku}", "qty": 1},
        expect=[
            ApiExpect(status=200),
            ApiExpect(path="data.skuId", op="eq", value="${test_sku}"),
        ],
    )
    assert ex.execute(create).passed
