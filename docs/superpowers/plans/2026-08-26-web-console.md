# Web 控制台原型实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 atk 构建 Web 控制台原型：项目模式浏览/表单编排/执行/历史，全局聚合与定时任务。

**Architecture:** 单 FastAPI 应用两种挂载（项目/-g 全局）；后端复用 atk 现有库直接读写 YAML；Vue3 SPA 预构建 dist 由包内静态目录托管。registry.db 只存索引不存用例。

**Tech Stack:** FastAPI + uvicorn + APScheduler；Vue3 + Vite + Element Plus + vuedraggable（原型期源码/YAML 编辑用等宽 textarea，CodeMirror 后置）。

规格：`docs/superpowers/specs/2026-08-26-web-console-design.md`
执行方式：本会话内联逐任务执行（用户要求立即体验）。

---

## 文件结构

```
atk/console/                  # 新增子包
  __init__.py                 # create_app(project_root=None, global_mode=False)
  repo.py                     # 场景文件仓库：tree/load/save(move_to)/锁
  render.py                   # 结构化JSON⇄YAML 双向渲染
  jobs.py                     # 执行任务管理：subprocess+SSE队列+单槽
  registry.py                 # ~/.atk/registry.db（projects/runs_index）
  schedules.py                # schedules.yaml 读写 + APScheduler 装配
  routes.py                   # 全部路由（原型期单文件，超300行再拆）
  static/                     # 前端构建产物（提交入库）
atk/cli.py                    # 增加 console 子命令
console-ui/                   # Vue 工程
  src/views/{Browse,Editor,RunView,History,Settings,GlobalHome}.vue
  src/components/{KvTable,StepCard}.vue
  src/api.js  src/router.js  src/main.js
tests/test_console_{repo,render,jobs,registry,routes,schedules}.py
```

## 关键接口定义（后续任务引用）

```python
# repo.py
def scan_tree(root: Path) -> dict          # {"dirs":[{"name","path","children"}],"scenarios":[{path,name,priority,module,tags}]}
def load_scenario(root: Path, rel: str) -> tuple[dict, int]   # (data, mtime_ns)；解析失败 raise YamlError(带行号)
def save_scenario(root: Path, rel: str, data: dict, *, move_to: str|None, if_mtime: int|None, force: bool=False) -> Path
# 锁语义：if_mtime 与当前不符且 not force → ConflictError

# jobs.py
class JobManager:
    def start(self, project_root: Path, args: list[str]) -> str    # 占用单槽否则 BusyError；返回 job_id
    def stream(self, job_id: str) -> Iterator[dict]                # {"type":"log","line"}|{"type":"done","exit_code"}
# 渲染为 SSE：event: log/done, data: json

# registry.py  表结构
CREATE TABLE projects(id INTEGER PRIMARY KEY, path TEXT UNIQUE, name TEXT, last_seen_at TEXT)
CREATE TABLE runs_index(run_id TEXT, project_path TEXT, started_at TEXT,
                        status TEXT, pass_n INT, fail_n INT, blocked_n INT, skipped_n INT,
                        PRIMARY KEY(run_id, project_path))

# schedules.yaml 项目级格式
tasks:
  - name: 夜间回归
    env: xinfei
    module: seal        # 可空=全部
    cron: "0 2 * * *"   # 或 daily_at: "02:00"
    enabled: true
```

---

### Task 1: 依赖与 FastAPI 骨架

**Files:** Modify `pyproject.toml`、Create `atk/console/__init__.py` `atk/console/app.py`、Test `tests/test_console_routes.py`

- [ ] 加依赖：`uv pip install fastapi uvicorn apscheduler httpx --python .venv` 并更新 pyproject（fastapi/uvicorn[standard]/apscheduler）
- [ ] 写失败测试：

```python
# tests/test_console_routes.py
from pathlib import Path
from fastapi.testclient import TestClient
from atk.console import create_app

def make_client(tmp_path):
    return TestClient(create_app(project_root=tmp_path))

def test_health(tmp_path):
    c = make_client(tmp_path)
    assert c.get("/api/health").json() == {"ok": True}
```

- [ ] 实现 `create_app`：FastAPI() + `/api/health`；static 挂载延后到 Task 14
- [ ] 测试过 → commit `feat(console): FastAPI 骨架`

### Task 2: repo.scan_tree + load_scenario（TDD）

**Files:** Create `atk/console/repo.py`、Test `tests/test_console_repo.py`

- [ ] 失败测试：tmp 工程造 `scenarios/a/x.yaml`(P0,tags)、`scenarios/b.yml`（非法 YAML）；断言 scan_tree 目录嵌套正确、scenario 元信息齐全、load_scenario 对非法文件抛 YamlError 且 message 含行号
- [ ] 实现：os.walk 收集 `.yaml/.yml`；load 复用 `atk.scenarios.loader.load_scenario_file`（现有函数，若签名不同以现名为准），异常包装行号
- [ ] 过 → commit `feat(console): 场景树扫描与加载`

### Task 3: render.py JSON⇄YAML（TDD）

**Files:** Create `atk/console/render.py`、Test `tests/test_console_render.py`

- [ ] 失败测试：对 scenarios/demo_app/*.yaml 全部 round-trip 断言 `render(yaml_text) -> data -> dump == 原解析对象相等`；断言 dump 输出 allow_unicode=True、sort_keys=False
- [ ] 实现：`yaml.safe_load` + 自定义 Dumper(default_flow_style=False)
- [ ] 过 → commit `feat(console): YAML 双向渲染`

### Task 4: save_scenario 校验门禁+乐观锁（TDD）

**Files:** Modify `atk/console/repo.py`、Test `tests/test_console_repo.py`

- [ ] 失败测试三例：
  1. 合法数据保存成功且落盘文本可被 loader 读回
  2. 数据缺 `scenario:` 字段 → ValidationError 不落盘
  3. `if_mtime` 过期且 force=False → ConflictError；force=True 覆盖成功
  4. `move_to="c/y.yaml"` 时旧路径删除新路径存在
- [ ] 实现：校验复用 validator（内存态调用其校验函数）；写盘用 tmp 文件 + os.replace 原子替换
- [ ] 过 → commit `feat(console): 场景保存门禁与乐观锁`

### Task 5: 路由接线（tree/detail/save/validate）+ 静态挂载

**Files:** Create `atk/console/routes.py`、Modify `app.py`、Test `tests/test_console_routes.py`

- [ ] TestClient 用例：GET /api/tree 返回 demo 结构；GET /api/scenarios/{rel} 返回 {data,mtime}；PUT 带 if_mtime 正常保存、过期得 409；POST /api/validate {data} 返回 {ok,errors}
- [ ] 实现 routes.py 四端点，path 参数防目录穿越（resolve 后必须 root 内）
- [ ] 过 → commit `feat(console): 场景路由`

### Task 6: jobs.py 执行管理（TDD）

**Files:** Create `atk/console/jobs.py`、Test `tests/test_console_jobs.py`

- [ ] 失败测试：JobManager 以 `[sys.executable,"-c","print('hi')"]` 启动；stream 收到含 'hi' 的 log 与 done(exit_code=0)；第二个 job 未结束时启动抛 BusyError；结束后可再启
- [ ] 实现：subprocess.Popen + 后台线程读 stdout 行入队列；done 记录 exit_code；单槽 = 活跃计数
- [ ] 过 → commit `feat(console): 执行任务管理器`

### Task 7: 执行与历史路由 + SSE

**Files:** Modify `routes.py`、Test `tests/test_console_routes.py`

- [ ] POST /api/run {env,module?} → 组装 `["run","--env",env,(--module)]` 交 JobManager，返回 job_id；忙时 409
- [ ] GET /api/jobs/{id}/stream → StreamingResponse(media_type="text/event-stream")
- [ ] GET /api/runs?limit=20 倒序列出 reports/runs/*/run.yaml 摘要；GET /api/runs/{id} 详情；GET /api/runs/{id}/evidence/{name} FileResponse（白名单校验路径）
- [ ] TestClient 测试 runs 列表用预置 run.yaml fixture
- [ ] 过 → commit `feat(console): 执行与历史路由`

### Task 8: 配置路由（env/modules 掩码）

**Files:** Modify `routes.py`、Test 补用例

- [ ] GET /api/environments 返回结构但值中 `${env:X}` 保持原样、其余 vars 原样（本就不回显系统环境变量——因为 load 时不注入 os.environ 到响应）
- [ ] PUT 整体写回 environments.yaml/modules.yaml
- [ ] 过 → commit `feat(console): 配置查看编辑`

### Task 9: CLI 子命令 `atk console`

**Files:** Modify `atk/cli.py`、Test `tests/test_cli.py` 补一条

- [ ] `atk console [--port] [-g]`：非 -g 自动 upsert 当前项目进 registry；uvicorn.run(app, host="127.0.0.1", port)
- [ ] 测试：capsys 或直接调命令回调断言 create_app 被以正确参数调用（mock uvicorn.run）
- [ ] commit `feat(cli): console 子命令`

### Task 10: 前端脚手架 + api.js + Browse 视图

**Files:** Create `console-ui/**`

- [ ] `npm create vite@latest console-ui -- --template vue`；装 element-plus vue-router vuedraggable@next
- [ ] vite.config：server.proxy `/api → http://127.0.0.1:8900`；build.outDir 指向 `../atk/console/static`（emptyOutDir）
- [ ] api.js：fetch 封装（json、409 冲突特殊标记）
- [ ] Browse.vue：el-tree 左目录 + 右 el-table（名称/P0-P3/tag/最近结果占位）；点击行 → /edit?path=
- [ ] router.js：/ → Browse
- [ ] 手动验证：uvicorn 起后端 + vite dev，浏览器可见真实场景树
- [ ] commit `feat(ui): 脚手架与场景浏览`

### Task 11: Editor 视图（表单卡片 ⇄ 源码）

**Files:** Create `src/views/Editor.vue`、`src/components/KvTable.vue`、`src/components/StepCard.vue`

- [ ] 加载 /api/scenarios/{path} → 基本信息 el-form（scenario/module/priority select/tags/el-select env 多自 data fixtures KvTable）
- [ ] 步骤区 draggable 列表：按步骤键（api/ui）渲染 StepCard；API 卡字段=method+call、headers KvTable、params/body textarea(JSON)、expect 规则行(key/op 下拉/value)、capture 行、retries；UI 卡=action 下拉/target/value/expect
- [ ] 「源码模式」切换：整页变 textarea 显示 YAML；两模式互转都经后端 render
- [ ] 保存：组包 POST /api/validate → ok 则 PUT（带 if_mtime）；409 弹窗「刷新/强制覆盖」
- [ ] 手动验证：改 demo_app 场景保存，git diff 确认只动预期字段
- [ ] commit `feat(ui): 场景表单编排与双模式`

### Task 12: Run 视图 + History 视图

**Files:** Create `src/views/RunView.vue`、`src/views/History.vue`

- [ ] RunView：env/module 选择 + 运行按钮 → POST /api/run → EventSource 订阅 stream 滚日志黑框 → done 展示退出码与「查看报告」链接(/api/runs/{latest}/report 跳 HTML)
- [ ] History：列表（时间/env/pass-fail-blocked/状态徽标）→ 详情抽屉展示各 intent + 截图 img(evidence URL)
- [ ] 手动验证：跑 demo_app 全量，看实时日志与截图
- [ ] commit `feat(ui): 执行与历史`

### Task 13: Settings 视图（env/modules 表格编辑）

**Files:** Create `src/views/Settings.vue`

- [ ] 两个 tab：environments 树形表格编辑 vars；modules 表格；保存走 PUT
- [ ] 手动验证 + commit `feat(ui): 设置页`

### Task 14: dist 构建入库 + 静态托管 + 全局模式

**Files:** Modify `app.py` `routes.py`、Create `src/views/GlobalHome.vue`

- [ ] npm run build → atk/console/static；app.py StaticFiles(html=True) 兜底路由（/api 之外全部回 index.html 支持前端路由）
- [ ] GlobalHome：GET /api/projects 卡片列表 + 注册/移除；点击项目开 `/p/?root=<encoded>`（同应用以 query 指定 project_root 重挂载——实现：create_app 支持 root 参数由中间件从请求 query 解析，仅限已注册项目白名单）
- [ ] registry.py 在此任务前已完成则跳过；否则先补 registry TDD（projects upsert/list/remove + rebuild）
- [ ] 手动验证 -g 模式跨本项目+临时第二工程
- [ ] commit `feat: 全局模式与静态产物`

### Task 15: schedules 定时任务（M5）

**Files:** Create `atk/console/schedules.py`、Modify `routes.py`、Create `src/views/Schedules.vue`（并入 Settings 页第三 tab 亦可）

- [ ] 失败测试：read/write schedules.yaml 往返；next_run(cron 解析用 croniter 或 APScheduler CronTrigger.get_next_fire_time)；enabled=false 不装配
- [ ] 实现：create_app 时装配 AsyncIOScheduler；触发=JobManager.start 同管线；console 关闭即停（限制已在规格声明）
- [ ] 路由 CRUD + POST trigger；UI 列表（名称/cron/enabled 开关/下次时间/立即运行）
- [ ] 手动验证：建每分钟任务观察 reports 出新 run
- [ ] commit `feat(console): 定时任务`

### Task 16: dogfood 收尾

- [ ] 对本仓库全流程实测清单走查：浏览→编辑保存→执行→历史→定时→(-g 若实现)聚合
- [ ] README 增「Web 控制台」一节（启动命令/截图位/已知限制：注释丢失、停机不补跑）
- [ ] 全量 pytest 回归 + commit `docs: 控制台使用说明`

## Self-Review 结论

- 规格覆盖：§1-§7 各节均映射到 Task 1-16（§4 页面↔Task 10-15；§5 限制在 Task 15/16 落文档；§7 发布不在本计划）
- 类型一致：接口定义块中签名与各 Task 引用一致（scan_tree/save_scenario/JobManager.start/stream）
- 无占位符：关键算法（锁、SSE、调度）给出行为契约与测试断言，模板类代码在执行时按 Element Plus 惯例落地
