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


def test_non_json_response_capture_warns_not_crashes(mock_client):
    ex = ApiExecutor(mock_client, {"token": "tok_demo123"})
    r = ex.execute(
        ApiStep(
            call="GET /api/text",
            expect=[],
            capture={"x": "data.y"},
        )
    )
    assert r.passed and "响应非 JSON" in r.detail and "capture" in r.detail


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
