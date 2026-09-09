import httpx

from examples.spa_shop import make_server


def test_spa_shop_checkout_and_coupon_reuse():
    server = make_server()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with httpx.Client(base_url=base_url, trust_env=False) as client:
            login = client.post("/api/login", json={"username": "qa", "password": "secret123"})
            assert login.status_code == 200
            token = login.json()["data"]["token"]
            headers = {"Authorization": f"Bearer {token}"}

            first = client.post(
                "/api/checkout",
                headers=headers,
                json={"skuId": "SKU-ATK-1", "qty": 1, "coupon": "DUP10"},
            )
            assert first.status_code == 200
            assert first.json()["data"]["payable"] == 89

            second = client.post(
                "/api/checkout",
                headers=headers,
                json={"skuId": "SKU-ATK-1", "qty": 1, "coupon": "DUP10"},
            )
            assert second.status_code == 409
            assert second.json()["msg"] == "coupon already used"
    finally:
        server.shutdown()
