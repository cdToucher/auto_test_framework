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
    with httpx.Client(base_url="http://127.0.0.1:1", timeout=1) as dead:
        ex = ApiExecutor(dead, {})
        r = ex.execute(ApiStep(call="GET /ping"))
    assert not r.passed and r.error_class == "environment"
