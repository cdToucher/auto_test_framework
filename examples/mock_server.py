"""Dogfood 用 mock 后端。路由：
POST /api/login  {username,password}          -> 200 {code:0,data:{token}} / 401
POST /api/orders {skuId,qty} (需Bearer tok_*) -> 200 {code:0,data:{orderNo,amount,status}}
GET  /api/orders?orderNo=X                      -> 200 {code:0,data:{list:[...]}}
GET  /ping                                      -> 200 {code:0,data:"pong"}
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_USERS = {"alice": "secret123"}
_ORDERS: dict[str, dict] = {}
_SEQ = iter(range(1001, 9999))
_LOCK = threading.Lock()

_LOGIN_HTML = """<!doctype html><html lang=zh><head><meta charset=utf-8><title>登录</title></head>
<body><h1>演示商城</h1>
<form onsubmit="return doLogin()">
  <input id="username" placeholder="用户名"><br>
  <input id="password" type="password" placeholder="密码"><br>
  <button type="submit">登录</button>
</form><pre id="msg"></pre>
<script>
async function doLogin(){
  const r = await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:document.getElementById('username').value,
                         password:document.getElementById('password').value})});
  const j = await r.json();
  if(r.ok){ localStorage.setItem('token', j.data.token); location='/app'; }
  else document.getElementById('msg').textContent='登录失败';
  return false;
}
</script></body></html>"""

_APP_HTML = """<!doctype html><html lang=zh><head><meta charset=utf-8><title>订单</title></head>
<body><h1>订单查询</h1>
<input id="orderNo" placeholder="订单号">
<button onclick="query()">查询</button>
<pre id="out"></pre>
<script>
async function query(){
  const no=document.getElementById('orderNo').value;
  const r=await fetch('/api/orders?orderNo='+encodeURIComponent(no),
    {headers:{Authorization:'Bearer '+localStorage.getItem('token')}});
  const j=await r.json();
  const it=(j.data&&j.data.list&&j.data.list[0])||null;
  document.getElementById('out').textContent= it? ('状态:'+it.status+' 金额:'+it.amount) : '未找到';
}
</script></body></html>"""


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默访问日志
        pass

    def _send_html(self, markup: str):
        body = markup.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def _authed(self) -> bool:
        return self.headers.get("Authorization", "").startswith("Bearer tok_")

    def do_POST(self):
        if self.path == "/api/login":
            b = self._body()
            if _USERS.get(b.get("username")) == b.get("password"):
                self._json(200, {"code": 0, "data": {"token": "tok_demo123"}})
            else:
                self._json(401, {"code": 401, "msg": "bad credentials"})
        elif self.path == "/api/orders":
            if not self._authed():
                self._json(401, {"code": 401, "msg": "unauthorized"})
                return
            b = self._body()
            qty = int(b.get("qty", 1))
            with _LOCK:
                no = f"NO{next(_SEQ)}"
                order = {
                    "orderNo": no,
                    "amount": round(qty * 9.9, 2),
                    "status": "待支付",
                    "skuId": b.get("skuId"),
                }
                _ORDERS[no] = order
            self._json(200, {"code": 0, "data": order})
        else:
            self._json(404, {"code": 404})

    def do_GET(self):
        if self.path == "/":
            self._send_html(_LOGIN_HTML)
        elif self.path == "/app":
            self._send_html(_APP_HTML)
        elif self.path.startswith("/api/orders"):
            no = self.path.split("orderNo=")[-1].split("&")[0]
            order = _ORDERS.get(no)
            lst = [order] if order else []
            self._json(200, {"code": 0, "data": {"list": lst}})
        elif self.path == "/ping":
            self._json(200, {"code": 0, "data": "pong"})
        elif self.path == "/api/text":
            body = b"plain text not json"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json(404, {"code": 404})


def make_server(port: int = 0) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), MockHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    server = make_server(8765)
    print("mock api on http://127.0.0.1:8765 (Ctrl-C 退出)")
    server.serve_forever()
