"""demo 被测系统（待办任务）的业务规则测试。"""


def _token(client):
    r = client.post("/api/login", json={"username": "alice", "password": "secret123"})
    assert r.status_code == 200
    return r.json()["data"]["token"]


def test_login_bad_credentials(demo_client):
    bad = demo_client.post("/api/login", json={"username": "alice", "password": "x"})
    assert bad.status_code == 401


def test_create_requires_title(demo_client):
    h = {"Authorization": f"Bearer {_token(demo_client)}"}
    empty = demo_client.post("/api/todos", json={"title": "  "}, headers=h)
    missing = demo_client.post("/api/todos", json={}, headers=h)
    ok = demo_client.post("/api/todos", json={"title": "写周报"}, headers=h)
    assert empty.status_code == 422 and missing.status_code == 422
    assert ok.status_code == 200 and ok.json()["data"]["status"] == "pending"


def test_complete_state_machine(demo_client):
    h = {"Authorization": f"Bearer {_token(demo_client)}"}
    tid = demo_client.post("/api/todos", json={"title": "任务A"}, headers=h).json()["data"]["id"]
    done = demo_client.post(f"/api/todos/{tid}/complete", headers=h)
    again = demo_client.post(f"/api/todos/{tid}/complete", headers=h)
    missing = demo_client.post("/api/todos/T99999/complete", headers=h)
    assert done.status_code == 200 and done.json()["data"]["status"] == "done"
    assert again.status_code == 409
    assert missing.status_code == 404


def test_filter_by_status_and_delete(demo_client):
    h = {"Authorization": f"Bearer {_token(demo_client)}"}
    tid = demo_client.post("/api/todos", json={"title": "要删的"}, headers=h).json()["data"]["id"]
    demo_client.post(f"/api/todos/{tid}/complete", headers=h)
    filtered = demo_client.get("/api/todos?status=pending", headers=h)
    assert all(t["status"] == "pending" for t in filtered.json()["data"]["list"])
    deleted = demo_client.delete(f"/api/todos/{tid}", headers=h)
    gone = demo_client.delete(f"/api/todos/{tid}", headers=h)
    assert deleted.status_code == 200 and gone.status_code == 404


def test_unauthorized_rejected(demo_client):
    r = demo_client.get("/api/todos", headers={"Authorization": "Bearer forged"})
    assert r.status_code == 401


def test_pages_served(demo_client):
    login = demo_client.get("/")
    app = demo_client.get("/app")
    assert login.status_code == 200 and 'id="username"' in login.text
    assert app.status_code == 200 and 'id="list"' in app.text and 'id="filter"' in app.text
