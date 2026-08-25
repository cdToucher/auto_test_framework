def test_login_ok_and_bad(mock_client):
    r = mock_client.post("/api/login", json={"username": "alice", "password": "secret123"})
    assert r.status_code == 200 and r.json()["data"]["token"].startswith("tok_")
    bad = mock_client.post("/api/login", json={"username": "alice", "password": "x"})
    assert bad.status_code == 401


def test_create_and_query_order(mock_client):
    h = {"Authorization": "Bearer tok_demo123"}
    r = mock_client.post("/api/orders", json={"skuId": "SKU-001", "qty": 2}, headers=h)
    no = r.json()["data"]["orderNo"]
    assert r.json()["data"]["status"] == "待支付"
    q = mock_client.get(f"/api/orders?orderNo={no}")
    assert q.json()["data"]["list"][0]["orderNo"] == no


def test_web_pages_served(mock_client):
    login = mock_client.get("/")
    assert login.status_code == 200 and "text/html" in login.headers["content-type"]
    assert 'id="username"' in login.text and 'id="password"' in login.text
    app = mock_client.get("/app")
    assert app.status_code == 200 and 'id="orderNo"' in app.text
