"""演示被测系统：待办任务管理（API + Web）。

业务规则（为测试提供正/负断言素材）：
- 登录：alice/secret123 颁发 token；错误凭据 401
- 创建：title 必填非空，否则 422；初始状态 pending
- 完成：pending -> done；重复完成 409；不存在 404
- 删除：不存在 404
启动：python -m examples.demo_app  （http://127.0.0.1:8766）
"""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_USERS = {"alice": "secret123"}
_TODOS: dict[str, dict] = {}
_SEQ = iter(range(1, 10000))
_LOCK = threading.Lock()

_LOGIN_HTML = """<!doctype html><html lang=zh><head><meta charset=utf-8><title>登录</title></head>
<body><h1>待办任务系统</h1>
<form onsubmit="return doLogin()">
  <input id="username" placeholder="用户名"><br>
  <input id="password" type="password" placeholder="密码"><br>
  <button type="submit">登录</button>
</form><pre id="msg"></pre>
<script>
async function doLogin(){
  const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:document.getElementById('username').value,
                         password:document.getElementById('password').value})});
  if(r.ok){ localStorage.setItem('token',(await r.json()).data.token); location='/app'; }
  else document.getElementById('msg').textContent='用户名或密码错误';
  return false;
}
</script></body></html>"""

_APP_HTML = """<!doctype html><html lang=zh><head><meta charset=utf-8><title>我的待办</title></head>
<body><h1>我的待办</h1>
<input id="title" placeholder="新任务标题">
<button onclick="addTodo()">添加</button>
<select id="filter" onchange="load()">
  <option value="">全部</option><option value="pending">待办</option><option value="done">已完成</option>
</select>
<ul id="list"></ul><pre id="err"></pre>
<script>
function h(){return {Authorization:'Bearer '+localStorage.getItem('token'),
                     'Content-Type':'application/json'}}
async function load(){
  const st=document.getElementById('filter').value;
  const r=await fetch('/api/todos'+(st?('?status='+st):''),{headers:h()});
  if(r.status===401){location='/';return;}
  const list=(await r.json()).data.list;
  document.getElementById('list').innerHTML=list.map(t=>
    `<li data-id="${t.id}" data-status="${t.status}">${t.title}`+
    (t.status==='pending'?` <button onclick="done('${t.id}')">完成</button>`:' [已完成]')+`</li>`
  ).join('');
}
async function addTodo(){
  const r=await fetch('/api/todos',{method:'POST',headers:h(),
    body:JSON.stringify({title:document.getElementById('title').value})});
  if(!r.ok){document.getElementById('err').textContent=(await r.json()).msg;}
  else{document.getElementById('title').value='';document.getElementById('err').textContent='';}
  load();
}
async function done(id){
  await fetch(`/api/todos/${id}/complete`,{method:'POST',headers:h()});
  load();
}
load();
</script></body></html>"""


class DemoHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, payload: dict | None = None, html: str | None = None):
        body = (json.dumps(payload, ensure_ascii=False) if payload is not None else html or "").encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def _authed(self) -> bool:
        return bool(re.fullmatch(r"Bearer tok_[A-Za-z0-9_]+", self.headers.get("Authorization", "")))

    def do_POST(self):
        if self.path == "/api/login":
            b = self._body()
            if _USERS.get(b.get("username")) == b.get("password"):
                user = b["username"]
                self._send(200, {"code": 0, "data": {"token": f"tok_{user}_demo"}})
            else:
                self._send(401, {"code": 401, "msg": "bad credentials"})
            return
        if not self._authed():
            self._send(401, {"code": 401, "msg": "unauthorized"})
            return
        m = re.fullmatch(r"/api/todos/([^/]+)/complete", self.path)
        if self.path == "/api/todos":
            b = self._body()
            title = str(b.get("title") or "").strip()
            if not title:
                self._send(422, {"code": 422, "msg": "title 必填"})
                return
            with _LOCK:
                tid = f"T{next(_SEQ)}"
                _TODOS[tid] = {"id": tid, "title": title, "status": "pending"}
            self._send(200, {"code": 0, "data": _TODOS[tid]})
        elif m:
            tid = m.group(1)
            with _LOCK:
                t = _TODOS.get(tid)
                if t is None:
                    self._send(404, {"code": 404, "msg": "todo 不存在"})
                elif t["status"] == "done":
                    self._send(409, {"code": 409, "msg": "already done"})
                else:
                    t["status"] = "done"
                    self._send(200, {"code": 0, "data": t})
        else:
            self._send(404, {"code": 404, "msg": "not found"})

    def do_GET(self):
        if self.path == "/":
            self._send(200, html=_LOGIN_HTML)
        elif self.path == "/app":
            self._send(200, html=_APP_HTML)
        elif self.path.startswith("/api/todos"):
            if not self._authed():
                self._send(401, {"code": 401, "msg": "unauthorized"})
                return
            status = self.path.split("status=")[-1].split("&")[0] if "status=" in self.path else ""
            items = [t for t in _TODOS.values() if not status or t["status"] == status]
            self._send(200, {"code": 0, "data": {"list": items}})
        else:
            self._send(404, {"code": 404, "msg": "not found"})

    def do_DELETE(self):
        if not self._authed():
            self._send(401, {"code": 401, "msg": "unauthorized"})
            return
        m = re.fullmatch(r"/api/todos/([^/]+)", self.path)
        if m:
            with _LOCK:
                if _TODOS.pop(m.group(1), None) is None:
                    self._send(404, {"code": 404, "msg": "todo 不存在"})
                else:
                    self._send(200, {"code": 0, "data": "deleted"})
        else:
            self._send(404, {"code": 404, "msg": "not found"})


def make_server(port: int = 0) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), DemoHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    server = make_server(8766)
    print("demo app on http://127.0.0.1:8766 (Ctrl-C 退出)")
    server.serve_forever()
