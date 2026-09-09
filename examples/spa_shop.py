"""Single-page shop demo used to dogfood ATK's AI test workflow.

Routes:
GET  /                              -> SPA
GET  /health                        -> 200 {code:0,data:"ok"}
POST /api/login                     -> token
POST /api/checkout                  -> create order
GET  /api/orders?orderNo=...         -> query order
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import count
from urllib.parse import parse_qs, urlparse


_USERS = {"qa": "secret123"}
_PRODUCTS = {
    "SKU-ATK-1": {"name": "ATK Starter Kit", "price": 99},
    "SKU-ATK-2": {"name": "ATK Pro Kit", "price": 149},
}
_ORDERS: dict[str, dict] = {}
_USED_COUPONS: set[tuple[str, str]] = set()
_ORDER_SEQ = count(7001)
_TOKEN_SEQ = count(1)
_LOCK = threading.Lock()


_SPA_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ATK Demo Shop</title>
  <style>
    :root { color-scheme: light; font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; background: #f7f8fa; color: #202733; }
    main { width: min(1040px, calc(100vw - 32px)); margin: 28px auto; }
    header { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
    h1 { margin: 0; font-size: 28px; line-height: 1.15; }
    .subtitle { margin: 6px 0 0; color: #697386; font-size: 14px; }
    .grid { display: grid; grid-template-columns: 320px 1fr; gap: 16px; align-items: start; }
    section { background: #fff; border: 1px solid #dfe3ea; border-radius: 8px; padding: 18px; box-shadow: 0 1px 2px rgba(16,24,40,.05); }
    h2 { margin: 0 0 14px; font-size: 18px; }
    label { display: grid; gap: 6px; color: #3b4452; font-size: 13px; margin-bottom: 12px; }
    input, select { min-height: 38px; border: 1px solid #c8d0dc; border-radius: 6px; padding: 8px 10px; font-size: 14px; }
    button { min-height: 38px; border: 0; border-radius: 6px; padding: 0 14px; background: #1b6ac9; color: #fff; font-weight: 650; cursor: pointer; }
    button.secondary { background: #374151; }
    button:disabled { background: #a5adba; cursor: not-allowed; }
    .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .status { min-height: 22px; margin: 12px 0 0; color: #1f5f32; font-size: 14px; }
    .error { color: #b42318; }
    .result { margin-top: 14px; background: #f4f6f9; border: 1px solid #e3e7ee; border-radius: 6px; padding: 12px; min-height: 70px; white-space: pre-wrap; }
    .product { display: grid; grid-template-columns: 1fr auto; gap: 6px; padding: 12px; border: 1px solid #e3e7ee; border-radius: 8px; margin-bottom: 12px; }
    .product strong { display: block; }
    .price { font-weight: 750; }
    @media (max-width: 760px) { .grid { grid-template-columns: 1fr; } header { display: block; } }
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>ATK Demo Shop</h1>
      <p class="subtitle">登录、优惠券、下单、订单查询的单页面测试目标。</p>
    </div>
    <div id="sessionLabel" class="subtitle">未登录</div>
  </header>

  <div class="grid">
    <section aria-label="登录">
      <h2>登录</h2>
      <label>用户名 <input id="username" autocomplete="username" value="qa"></label>
      <label>密码 <input id="password" type="password" autocomplete="current-password" value="secret123"></label>
      <button id="loginBtn" type="button">登录</button>
      <div id="loginStatus" class="status"></div>
    </section>

    <section aria-label="结算">
      <h2>结算</h2>
      <div class="product">
        <div><strong>ATK Starter Kit</strong><span class="subtitle">适合研发自测的示例商品</span></div>
        <div class="price">¥99</div>
      </div>
      <div class="row">
        <label>商品
          <select id="sku">
            <option value="SKU-ATK-1">ATK Starter Kit</option>
            <option value="SKU-ATK-2">ATK Pro Kit</option>
          </select>
        </label>
        <label>数量 <input id="qty" type="number" min="1" value="1"></label>
        <label>优惠券 <input id="coupon" value="SAVE10"></label>
      </div>
      <button id="checkoutBtn" type="button">提交订单</button>
      <div id="checkoutStatus" class="status"></div>
      <div class="row" style="margin-top:14px">
        <label style="flex:1">订单号 <input id="orderNo" placeholder="下单后自动填入"></label>
        <button id="queryBtn" class="secondary" type="button">查询订单</button>
      </div>
      <div id="orderResult" class="result">暂无订单</div>
    </section>
  </div>
</main>
<script>
const state = { token: localStorage.getItem('atk_demo_token') || '', user: '' };
const $ = (id) => document.getElementById(id);

function setSessionLabel() {
  $('sessionLabel').textContent = state.token ? `已登录：${state.user || 'qa'}` : '未登录';
}

async function api(path, options = {}) {
  const headers = Object.assign({'Content-Type': 'application/json'}, options.headers || {});
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, Object.assign({}, options, { headers }));
  const payload = await response.json();
  if (!response.ok) throw Object.assign(new Error(payload.msg || '请求失败'), { response, payload });
  return payload;
}

$('loginBtn').addEventListener('click', async () => {
  $('loginStatus').className = 'status';
  $('loginStatus').textContent = '登录中...';
  try {
    const payload = await api('/api/login', {
      method: 'POST',
      body: JSON.stringify({ username: $('username').value, password: $('password').value })
    });
    state.token = payload.data.token;
    state.user = payload.data.user;
    localStorage.setItem('atk_demo_token', state.token);
    setSessionLabel();
    $('loginStatus').textContent = '登录成功';
  } catch (err) {
    $('loginStatus').className = 'status error';
    $('loginStatus').textContent = '登录失败：' + err.message;
  }
});

$('checkoutBtn').addEventListener('click', async () => {
  $('checkoutStatus').className = 'status';
  $('checkoutStatus').textContent = '提交中...';
  try {
    const payload = await api('/api/checkout', {
      method: 'POST',
      body: JSON.stringify({ skuId: $('sku').value, qty: Number($('qty').value), coupon: $('coupon').value })
    });
    const order = payload.data;
    $('orderNo').value = order.orderNo;
    $('checkoutStatus').textContent = `下单成功 ${order.orderNo}，应付 ¥${order.payable}`;
    $('orderResult').textContent = `订单号：${order.orderNo}\\n状态：${order.status}\\n商品：${order.productName}\\n应付：¥${order.payable}`;
  } catch (err) {
    $('checkoutStatus').className = 'status error';
    $('checkoutStatus').textContent = '下单失败：' + err.message;
  }
});

$('queryBtn').addEventListener('click', async () => {
  try {
    const payload = await api('/api/orders?orderNo=' + encodeURIComponent($('orderNo').value), { method: 'GET' });
    const order = payload.data.list[0];
    $('orderResult').textContent = order
      ? `订单号：${order.orderNo}\\n状态：${order.status}\\n商品：${order.productName}\\n应付：¥${order.payable}`
      : '未找到订单';
  } catch (err) {
    $('orderResult').textContent = '查询失败：' + err.message;
  }
});

setSessionLabel();
</script>
</body>
</html>"""


class SpaShopHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send_html(self, markup: str) -> None:
        body = markup.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw or b"{}")

    def _token(self) -> str:
        header = self.headers.get("Authorization", "")
        return header.removeprefix("Bearer ").strip()

    def _require_auth(self) -> str | None:
        token = self._token()
        if not token.startswith("tok_spashop_"):
            self._json(401, {"code": 401, "msg": "unauthorized"})
            return None
        return token

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(_SPA_HTML)
            return
        if parsed.path == "/health":
            self._json(200, {"code": 0, "data": "ok"})
            return
        if parsed.path == "/api/orders":
            if self._require_auth() is None:
                return
            order_no = parse_qs(parsed.query).get("orderNo", [""])[0]
            order = _ORDERS.get(order_no)
            self._json(200, {"code": 0, "data": {"list": [order] if order else []}})
            return
        self._json(404, {"code": 404, "msg": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/login":
            body = self._body()
            username = str(body.get("username", ""))
            if _USERS.get(username) != body.get("password"):
                self._json(401, {"code": 401, "msg": "bad credentials"})
                return
            with _LOCK:
                token = f"tok_spashop_{username}_{next(_TOKEN_SEQ)}"
            self._json(200, {"code": 0, "data": {"token": token, "user": username}})
            return

        if parsed.path == "/api/checkout":
            token = self._require_auth()
            if token is None:
                return
            body = self._body()
            sku_id = str(body.get("skuId", ""))
            product = _PRODUCTS.get(sku_id)
            if product is None:
                self._json(422, {"code": 422, "msg": "unknown sku"})
                return
            qty = int(body.get("qty") or 0)
            if qty < 1:
                self._json(422, {"code": 422, "msg": "qty must be positive"})
                return
            coupon = str(body.get("coupon") or "").strip()
            coupon_key = (token, coupon)
            discount = 0
            if coupon:
                if not coupon.startswith("SAVE") and not coupon.startswith("DUP"):
                    self._json(422, {"code": 422, "msg": "invalid coupon"})
                    return
                if coupon_key in _USED_COUPONS:
                    self._json(409, {"code": 409, "msg": "coupon already used"})
                    return
                discount = 10

            with _LOCK:
                if coupon:
                    _USED_COUPONS.add(coupon_key)
                order_no = f"SO{next(_ORDER_SEQ)}"
                subtotal = product["price"] * qty
                order = {
                    "orderNo": order_no,
                    "skuId": sku_id,
                    "productName": product["name"],
                    "qty": qty,
                    "subtotal": subtotal,
                    "discount": discount,
                    "payable": subtotal - discount,
                    "status": "待支付",
                }
                _ORDERS[order_no] = order
            self._json(200, {"code": 0, "data": order})
            return

        self._json(404, {"code": 404, "msg": "not found"})


def make_server(port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), SpaShopHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    server = make_server(8777)
    print("spa shop on http://127.0.0.1:8777 (Ctrl-C 退出)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
