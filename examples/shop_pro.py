"""复杂演示被测系统：优惠券商城 Pro（API + Web），供 atk 全流程 dogfood。

业务规则（为场景库提供正/负断言素材，覆盖鉴权/参数校验/状态机/库存/优惠券核销）：
- 登录：qa|alice / secret123 颁发 token；错误凭据 401；无 token 访问 API 401
- 货架：SKU-ATK-1(50元,库存充足)、SKU-TEA(30元,库存2)；未知 SKU 404
- 购物车：qty 必须正整数否则 422；超库存 409
- 优惠券：SAVE10 满减10、PCT20 八折取整、FULL100 满100减30、MIN200 满200减50、
  READYN 面额1元（仅供核销演示）、OLD5 已过期(410)；每张券每人限用一次
  （下单时核销，重复使用 409）；满减未达门槛 422
- 下单：购物车为空 422；金额 = Σ单价×数量 - 折扣（不低于 0）；成功扣库存、清购物车
- 订单状态机：pending --pay--> paid；pending --cancel--> cancelled；
  对 paid 取消 409；对已完成订单再操作 409；未知订单 404
- 演示辅助：POST /api/dev/reset 重置全部业务状态（重启即等价），回归重跑前调用

启动：python -m examples.shop_pro   （http://127.0.0.1:8788）
场景库：scenarios/shop_pro/（auth/ shop/ order/ 三个模块目录）
"""
import json
import math
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_USERS = {"qa": "secret123", "alice": "secret123"}
_COUPONS = {
    "SAVE10": {"discount": 10},
    "PCT20": {"percent": 20},
    "FULL100": {"discount": 30, "min": 100},
    "MIN200": {"discount": 50, "min": 200},
    "READYN": {"discount": 1},  # 仅供核销/查询路径演示，下单场景不消耗它
    "OLD5": {"discount": 5, "expired": True},
}


class _Store:
    """可整体重置的业务状态（reset 支撑回归重跑）。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with getattr(self, "lock", threading.Lock()):
            self.tokens: dict[str, str] = {}          # token -> user
            self.products = [
                {"sku": "SKU-ATK-1", "name": "自动化测试课", "price": 50, "stock": 50},
                {"sku": "SKU-TEA", "name": "茶叶礼盒", "price": 30, "stock": 2},
            ]
            self.carts: dict[str, list] = {}          # user -> [{sku, qty}]
            self.used_coupons: dict[str, set] = {}    # user -> {code}
            self.orders: dict[str, dict] = {}
            self.seq = iter(range(1, 10000))

    def find_product(self, sku: str):
        return next((p for p in self.products if p["sku"] == sku), None)

    def in_cart(self, user: str, sku: str) -> int:
        return sum(i["qty"] for i in self.carts.get(user, []) if i["sku"] == sku)


_STORE = _Store()

_INDEX_HTML = """<!doctype html><html lang=zh><head><meta charset=utf-8><title>券商城 Pro</title>
<style>body{font-family:-apple-system,'PingFang SC',sans-serif;margin:24px}button{cursor:pointer}
li{margin:4px 0}.muted{color:#888}</style></head><body>
<h1>优惠券商城 Pro</h1>
<div id="login">
  <input id="username" placeholder="用户名"><input id="password" type="password" placeholder="密码">
  <button id="btn-login" onclick="doLogin()">登录</button><pre id="login-msg"></pre>
</div>
<div id="main" hidden>
  <p id="who"></p><h3>货架</h3><ul id="goods"></ul>
  <h3>购物车</h3><ul id="cart"></ul>
  券码 <input id="coupon" placeholder="SAVE10">
  <button id="btn-checkout" onclick="checkout()">下单</button>
  <h3>订单</h3><ul id="orders"></ul>
</div>
<script>
let tok='';
const H=()=>({'Authorization':'Bearer '+tok,'Content-Type':'application/json'});
async function doLogin(){
  const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:username.value,password:password.value})});
  const j=await r.json();
  if(!r.ok){login_msg.textContent=j.msg;return;}
  tok=j.data.token;localStorage.setItem('sp-token',tok);enter();
}
const login_msg=document.getElementById('login-msg');
async function enter(){
  document.getElementById('login').hidden=true;
  document.getElementById('main').hidden=false;
  who.textContent='已登录：'+tok.slice(0,8)+'…';
  const p=(await (await fetch('/api/products',{headers:H()})).json()).data.items;
  goods.innerHTML=p.map(x=>`<li data-sku="${x.sku}">${x.name} ${x.price}元
    <button onclick="add('${x.sku}')">加入购物车</button></li>`).join('');
  await refresh();
}
async function add(sku){
  await fetch('/api/cart/add',{method:'POST',headers:H(),body:JSON.stringify({sku,qty:1})});
  await refresh();
}
async function checkout(){
  const body=coupon.value?{coupon:coupon.value}:{};
  const r=await fetch('/api/checkout',{method:'POST',headers:H(),body:JSON.stringify(body)});
  const j=await r.json();
  if(!r.ok){alert(j.msg);return;}
  alert(`下单成功 ${j.data.orderNo}，应付 ${j.data.payable} 元`);
  await refresh();
}
async function refresh(){
  const c=(await (await fetch('/api/cart',{headers:H()})).json()).data;
  cart.innerHTML=c.items.map(i=>`<li data-sku="${i.sku}">${i.sku} ×${i.qty}
    （合计 ${c.total} 元）</li>`).join('')||'<li class=muted>空</li>';
  const o=(await (await fetch('/api/orders',{headers:H()})).json()).data.list;
  orders.innerHTML=o.map(x=>`<li data-order="${x.orderNo}" data-status="${x.status}">
    ${x.orderNo} ${x.payable}元 [${x.status}]${x.status==='pending'
      ?` <button onclick="pay('${x.orderNo}')">支付</button>`:''}</li>`).join('')||'<li class=muted>暂无</li>';
}
async function pay(no){
  await fetch(`/api/orders/${no}/pay`,{method:'POST',headers:H()});await refresh();
}
if(localStorage.getItem('sp-token')){tok=localStorage.getItem('sp-token');enter();}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默访问日志
        pass

    # ---------- 基础 ----------
    def _send(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return None  # type: ignore[return-value]
        return data if isinstance(data, dict) else None

    def _user(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        m = re.match(r"^Bearer (\S+)$", auth)
        if not m:
            return None
        with _STORE.lock:
            return _STORE.tokens.get(m.group(1))

    # ---------- 路由 ----------
    def do_GET(self):
        if self.path == "/" or self.path == "/app":
            return self._html(_INDEX_HTML)
        if self.path == "/health":
            return self._send(200, {"code": 0, "data": {"status": "ok"}})
        if self.path.startswith("/api/"):
            return self._api("GET")
        self._send(404, {"code": 404, "msg": "not found"})

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._api("POST")
        self._send(404, {"code": 404, "msg": "not found"})

    def _api(self, method: str):
        path = self.path.split("?")[0]
        if (method, path) == ("POST", "/api/dev/reset"):
            _STORE.reset()
            return self._send(200, {"code": 0, "data": "reset"})
        if (method, path) == ("POST", "/api/login"):
            return self._login()
        user = self._user()
        if user is None:
            return self._send(401, {"code": 401, "msg": "未登录或 token 无效"})
        if (method, path) == ("GET", "/api/products"):
            with _STORE.lock:
                return self._send(200, {"code": 0, "data": {"items": _STORE.products}})
        m = re.fullmatch(r"/api/products/(\S+)", path)
        if m and method == "GET":
            p = _STORE.find_product(m.group(1))
            if not p:
                return self._send(404, {"code": 404, "msg": "SKU 不存在"})
            return self._send(200, {"code": 0, "data": p})
        if (method, path) == ("POST", "/api/cart/add"):
            return self._cart_add(user)
        if (method, path) == ("GET", "/api/cart"):
            with _STORE.lock:
                items = [
                    {**i, **{k: p[k] for k in ("name", "price")}}
                    for i in _STORE.carts.get(user, [])
                    if (p := _STORE.find_product(i["sku"]))
                ]
                total = sum(i["price"] * i["qty"] for i in items)
            return self._send(200, {"code": 0, "data": {"items": items, "total": total}})
        if (method, path) == ("POST", "/api/cart/clear"):
            with _STORE.lock:
                _STORE.carts[user] = []
            return self._send(200, {"code": 0, "data": {"count": 0}})
        if (method, path) == ("POST", "/api/coupons/redeem"):
            return self._coupon_redeem(user)
        if (method, path) == ("POST", "/api/checkout"):
            return self._checkout(user)
        if (method, path) == ("GET", "/api/orders"):
            with _STORE.lock:
                lst = [
                    {k: o[k] for k in ("orderNo", "status", "payable", "total", "discount")}
                    for o in _STORE.orders.values() if o["user"] == user
                ]
            return self._send(200, {"code": 0, "data": {"list": lst}})
        m = re.fullmatch(r"/api/orders/(\S+)", path)
        if m and method == "GET":
            with _STORE.lock:
                o = _STORE.orders.get(m.group(1))
            if not o or o["user"] != user:
                return self._send(404, {"code": 404, "msg": "订单不存在"})
            return self._send(200, {"code": 0, "data": o})
        m = re.fullmatch(r"/api/orders/(\S+)/(pay|cancel)", path)
        if m and method == "POST":
            return self._transition(m.group(1), m.group(2))
        self._send(404, {"code": 404, "msg": "not found"})

    # ---------- 业务 ----------
    def _login(self):
        body = self._json_body() or {}
        u, p = str(body.get("username", "")), str(body.get("password", ""))
        if _USERS.get(u) != p or not p:
            return self._send(401, {"code": 401, "msg": "用户名或密码错误"})
        token = uuid.uuid4().hex
        with _STORE.lock:
            _STORE.tokens[token] = u
            _STORE.carts.setdefault(u, [])
            _STORE.used_coupons.setdefault(u, set())
        self._send(200, {"code": 0, "data": {"token": token, "user": u}})

    def _cart_add(self, user: str):
        body = self._json_body() or {}
        sku = str(body.get("sku", ""))
        qty = body.get("qty")
        if not sku or not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
            return self._send(422, {"code": 422, "msg": "sku 必填且 qty 为正整数"})
        with _STORE.lock:
            prod = _STORE.find_product(sku)
            if not prod:
                return self._send(404, {"code": 404, "msg": "SKU 不存在"})
            if _STORE.in_cart(user, sku) + qty > prod["stock"]:
                return self._send(409, {"code": 409, "msg": "库存不足"})
            _STORE.carts.setdefault(user, []).append({"sku": sku, "qty": qty})
        self._send(200, {"code": 0, "data": {"added": sku, "qty": qty}})

    def _coupon_view(self, code: str):
        """返回 (http_code, payload)。redeem/checkout 共用的券合法性判定。"""
        c = _COUPONS.get(code)
        if not c:
            return 404, {"code": 404, "msg": "券不存在"}
        if c.get("expired"):
            return 410, {"code": 410, "msg": "券已过期"}
        return 200, {"code": 0, "data": {"coupon": code, "usable": True}}

    def _coupon_redeem(self, user: str):
        body = self._json_body() or {}
        code = str(body.get("code", ""))
        http, payload = self._coupon_view(code)
        if http != 200:
            return self._send(http, payload)
        with _STORE.lock:
            if code in _STORE.used_coupons.get(user, set()):
                return self._send(409, {"code": 409, "msg": "该券已被你使用过"})
        self._send(200, payload)

    def _discount_of(self, c: dict, total: int) -> int:
        if "percent" in c:
            return int(math.floor(total * c["percent"] / 100))
        return int(c.get("discount", 0))

    def _checkout(self, user: str):
        body = self._json_body() or {}
        code = str(body.get("coupon", "")).strip()
        with _STORE.lock:
            cart = _STORE.carts.get(user, [])
            if not cart:
                return self._send(422, {"code": 422, "msg": "购物车为空"})
            items = []
            for i in cart:
                p = _STORE.find_product(i["sku"])
                if not p:
                    return self._send(404, {"code": 404, "msg": "SKU 不存在"})
                if i["qty"] > p["stock"]:
                    return self._send(409, {"code": 409, "msg": f"{i['sku']} 库存不足"})
                items.append({"sku": p["sku"], "price": p["price"], "qty": i["qty"]})
            total = sum(x["price"] * x["qty"] for x in items)
            discount = 0
            if code:
                c = _COUPONS.get(code)
                http, payload = self._coupon_view(code)
                if http != 200:
                    return self._send(http, payload)
                if code in _STORE.used_coupons.setdefault(user, set()):
                    return self._send(409, {"code": 409, "msg": "该券已被你使用过"})
                if c.get("min") and total < c["min"]:
                    return self._send(422, {"code": 422, "msg": f"未达门槛，满 {c['min']} 可用"})
                discount = self._discount_of(c, total)
            payable = max(0, total - discount)
            for x in items:
                _STORE.find_product(x["sku"])["stock"] -= x["qty"]
            no = f"SP-{next(_STORE.seq):04d}"
            _STORE.orders[no] = {
                "orderNo": no, "user": user, "items": items, "total": total,
                "discount": discount, "coupon": code or None,
                "payable": payable, "status": "pending",
            }
            if code:
                _STORE.used_coupons[user].add(code)
            _STORE.carts[user] = []
        self._send(200, {"code": 0, "data": _STORE.orders[no]})

    def _transition(self, no: str, action: str):
        with _STORE.lock:
            o = _STORE.orders.get(no)
            if not o:
                return self._send(404, {"code": 404, "msg": "订单不存在"})
            if o["status"] != "pending":
                return self._send(409, {"code": 409, "msg": f"状态 {o['status']} 不允许{action}"})
            o["status"] = "paid" if action == "pay" else "cancelled"
        self._send(200, {"code": 0, "data": {"orderNo": no, "status": o["status"]}})


def make_server(port: int = 0) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    server = make_server(8788)
    print("shop_pro on http://127.0.0.1:8788 (Ctrl-C 退出；回归重跑前 POST /api/dev/reset)")
    server.serve_forever()
