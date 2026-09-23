# atk — AI 原生双层自动化测试框架

【此项目还是在沉淀和实践中，目的是使用本地AI或者非常便宜的API 自动化生成冒烟用例和做接口测试，结合AI 浏览器再做e2e 测试，生成测试报告，扩展开发角色责任边界 】

即时层（变更驱动冒烟，不落库）+ 沉淀层（YAML 场景回归）。设计思路见 `docs/DESIGN.md`，
原始规格见 `docs/superpowers/specs/`，角色规范见 `docs/roles.md`，
人机协作全流程见 `docs/human-ai-workflow.html`。

核心思想：**AI 干量产的活（生成/实测/固化填充），人做判断的事（审断言、定性失败），机器守确定的门（gate）。**

## 三条命令起步

接入一个新项目，人只需要回答两个问题：**被测服务在哪**、**先建哪些模块**。

```bash
cd /path/to/被测项目

# ① 一步生成：环境 + 模块映射 + 首批健康检查场景 + AI 工作流说明
atk init --url http://127.0.0.1:8080 --modules order,coupon \
  --var 'token=${env:ATK_TOKEN}'      # 凭据形态自定：token/cookie/用户名密码，全部非必填

# ② 验证链路（先看清会跑什么，再真跑）
atk validate
atk run --dry-run                     # 只列选中场景，不执行
atk run                               # 健康场景应已通过——流程已经转起来

# ③ 之后交给 AI / CI
atk smoke --base main --title "优惠券下单" --format json
```

`modules.yaml` 是可选项：不配代码→模块映射时 `smoke` 自动全量执行（慢但绝不漏测）；
配好后才享受"按变更增量选场景"。凭据永远不入库：`${env:VAR}` 加载期解析、缺失显式告警；
临时参数 `atk run --set k=v`（优先级 env < fixture < `--set` < capture）。

## 目录布局：一切产物收束 `.atk/`

```text
<被测项目>/
├── AGENTS.md                  # AI 入口，只留指向说明书的窄指针（根级仅此与 .gitignore）
├── .gitignore                 # init 幂等维护：只忽略可再生产物，场景库照常入库
└── .atk/
    ├── environments.yaml      # 环境（vars 默认为空，示例以注释给出）
    ├── modules.yaml           # 代码→模块映射（可选）
    ├── scenarios/<模块>/      # 场景库——YAML 是唯一事实源，照常提交 git
    ├── fixtures/              # 场景 data: 引用的测试数据
    ├── reports/runs/          # 运行记录 + 报告 + 截图证据
    ├── skills/                # 三个 skill：说明书 + 两份工作流正文
    │   ├── atk-use/atk_use.md     # 说明书（触发循环/命令/envelope/硬规则）
    │   ├── atk-smoke/SKILL.md     # 功能级冒烟：AI 主导
    │   └── atk-authoring/SKILL.md # 场景起草协议（先判重复用再动笔）
    ├── last-run.json          # --last 指针
    └── manifest.json          # init 生成物清单+内容指纹（purge 的安全依据）
```

- **旧项目零迁移**：存在 `scenarios/`、`config/`、`reports/` 任一即自动识别为旧布局，
  全部命令原样工作；显式传 `--root/--env-file/--runs-dir` 等参数永远优先。
- **说明书本身就是一个 skill**：`.atk/skills/atk-use/atk_use.md`（带 frontmatter，说明
  什么时候该加载它）。入口文件特意不叫 `SKILL.md`——`.atk/skills/` 下三份同名文件用
  `@` 唤出时分不清是谁，独特的 basename 才能 `@atk_use` 一次命中。它每次 init 由包内源
  重生成（写歪了会误导下一个 AI）；两份工作流正文则只建不覆盖，因为开发者会改。
- **不创建 `.claude/`、`.cursor/`**：主流 TUI/CLI（Codex、OpenCode、Claude Code 等）都会
  自动加载项目根 `AGENTS.md`——所以那里必须留指针，但只留指针：命令清单与硬规则集中在
  说明书里，两处各写一份必然漂移。`atk init --ui-tool playwright` 可替换正文里的实测
  工具名（默认 `ego-browser`）。
- **AI 不必背流程**：`atk agent --format json` 返回当前 `state`、`blockers`、可直接执行的
  `next[]`，以及该问人时的 `requires_human` + `human_prompt`。
- **反悔**：`atk purge` 按 manifest 指纹只删 init 生成且未手工改动的物料，
  你写的场景、改过的配置一律保留；还能清掉旧版散落的 `.claude/.cursor/skills` 拷贝。

## 功能总览

| 命令 | 作用 |
|---|---|
| `atk init` | 一步初始化（`--url/--env-name/--modules/--var`；只建缺失，绝不覆盖）；`--ui-tool` 指定 AI 实测浏览器 |
| `atk validate` | 场景库校验：错误 + 重名告警 + module 与目录不一致告警 |
| `atk context` | 确定性变更上下文包（提交+补丁+模块归属+现有场景），供 AI 按 atk-authoring 起草 |
| `atk plan` | 建运行记录，输出复用清单与待补全意图；`--format json` |
| `atk run` | 执行场景；`--dry-run` 只列选中；空选中按受阻（exit 2，`--allow-empty` 豁免）；`--set k=v` 临时变量；`--skip-ui`；JUnit `--junit` |
| `atk smoke` | 一键冒烟 plan→run→report→gate；`--format json` 的 manifest 含 `next` 建议命令 |
| `atk record` | 回填实测结论（pass/fail/suspect/blocked + 截图）；**status 必须显式给出**；同名意图就地更新；`--from-json` |
| `atk report` / `atk last` | 渲染运行记录 HTML（含截图缩略）/ 查看最近操作对象 |
| `atk review` | 开发整单确认（approve/reject），仅告警不拦截 |
| `atk review-draft` | AI 草稿评审：approve 去 `ai-generated` tag 转正，reject 移走留档 |
| `atk gate` | 合并门禁（见下节）；`--format json` 输出结构化 verdicts/blocking/warnings |
| `atk purge` | 删除 init 生成物料（manifest 指纹保护手工内容；`--yes` 免确认；`--with-reports` 连运行记录一起清） |
| `atk console` | Web 控制台（需 `.[console]` extra） |

## gate 到底拦什么

全部基于结构化记录判定，不接受口头结论：

| 检查项 | 结论 |
|---|---|
| 变更文件清单与记录不一致 | **拦截**（测的不是要合的代码，重新 plan） |
| head commit SHA 与记录时点不同 | **拦截**（文件清单没变但内容变了也拦，防 amend/rebase） |
| 用例失败（断言/配置错误） | **拦截** |
| 意图 `fail` / `suspect` | **拦截**（suspect 必须人定性，不允许沉默） |
| UI 意图仍是 `pending`（未实测回填） | **拦截**（没测过不能说通过） |
| 用例在运行时是 AI 草稿且未评审转正 | **拦截**（跑完草稿再 reject 移走也绕不过） |
| 记录里无场景结果也无意图 | **拦截**（空跑不构成通过的证据） |
| 工作区未提交文件 / 环境受阻 / 未经整单确认 | 仅告警 |

与之配套：`run`/`smoke` **选中 0 场景按受阻处理（exit 2）**——模块名打错、场景库为空
都不可能"静默全绿"；未解析的 `${var}` 发请求前拦截，`${env:VAR}` 缺失显式告警。

## 断言语法

`expect` 支持映射式（简洁）与列表式（无歧义）。除 `status` 与相等外，
还支持比较、包含、正则、长度、类型、枚举——别再只断 `status: 200`。

```yaml
steps:
  - api:
      call: "POST /api/orders"
      expect:
        status: 200
        data.orderNo: not_null
        data.payAmount: "< 90"            # 比较：< <= > >= == !=
        data.msg: "contains: 成功"         # 包含 / not_contains
        data.mobile: "regex: ^1[3-9]\\d{9}$"
        data.items: "len: 3"              # 或 "len: 1..5"
        data.id: "type: str"
        data.state: "in: [pending, paid]"
        data.err: is_null
```

列表式（值里带冒号等易歧义时用）：

```yaml
      expect:
        - { path: data.payAmount, op: lte, value: 90 }
        - { path: data.items, op: len, value: { min: 1, max: 10 } }
```

字面量以反斜杠开头可转义（`"\\<not-an-op"`）。`status` 同样支持比较：`status: "< 400"`。

## UI 步骤与 AI 实测（人机接力）

`atk run` 不执行 `ui:` 步骤，而是标记为**待实测**（`ui_pending`）并自动登记 `pending`
意图；AI 用浏览器实测后回填，未回填前 `atk gate` 拦截——门从 run 阶段移到 gate 阶段，
既消除"必然受阻"的误报噪音，也保证"没测过不会说通过"。`--skip-ui` 可显式忽略。

即时层工作流（AI 主导，人只有两个介入点：**审 expect**、**整单确认**）：

```bash
atk smoke --title "优惠券下单" --base main --format json   # manifest 含 next 命令
# (Agent 按 intents_pending 逐条浏览器实测，截图存档)
atk record --last --from-json /tmp/ui-result.json
atk review --last --by dev --verdict approve
atk gate --last --format json
```

`plan/smoke/run --record-new` 会把 run_id 写入 `.atk/last-run.json`，后续命令一律
`--last`，AI 不必解析拼接；`--format json` 只输出机器可读结果（含 `next` 建议命令，
status 刻意留占位符——回填必须显式表态）。完整编排规则见
`.atk/skills/atk-smoke/SKILL.md`，起草协议见 `docs/AI_AUTHORING_PROTOCOL.md` /
`.atk/skills/atk-authoring/SKILL.md`；CI 模板见 `.ci-examples/`。

## 场景编写

场景所在目录名即模块名（`module` 缺省按最近父目录兜底；字段与目录不一致 validate 会告警——
`--module` 与影响面分析按字段匹配，目录名匹配不到）。

```yaml
scenario: 下单后可查询订单
module: order
priority: P0            # P0=门禁必跑 / P1=夜间回归 / P2=周级全量
tags: [smoke]
data: fixtures/order.yaml   # 场景级测试数据，优先级 env < fixture < --set < capture
steps:
  - api:
      call: "POST /api/login"
      body: { username: "${username}", password: "${password}" }
      expect: { status: 200 }
      capture: { token: "data.token" }   # 捕获响应值供后续步骤引用
      retries: 2                          # 仅环境类错误重试，断言失败不重试
```

执行质量：退出码三态（0 通过 / 1 失败 / 2 受阻·空选中·配置问题）；非 JSON 响应保护；
变量捕获场景间隔离；`run --record-to` 幂等（重跑覆盖旧结果，不重复累积）。

## Demo 被测系统

| 系统 | 端口 | 说明 |
|---|---|---|
| `examples/mock_server.py` | 8765 | 快速上手最小服务 |
| `examples/demo_app.py` | 8766 | 待办任务：状态机/参数校验/页面，配套 `scenarios/demo_app/` |
| `examples/spa_shop.py` | 8777 | 单页商城：API+UI 混合 dogfood，见 `docs/spa-shop-dogfood.html` |
| `examples/shop_pro.py` | 8788 | **复杂演示**：鉴权、库存、购物车校验、五种优惠券规则（满减/折扣/门槛/过期/每人限用）、订单状态机、401/404/409/410/422 全错误面 + 页面 |

`scenarios/shop_pro/` 按 `auth/ shop/ order/` 目录组织 13 张场景，覆盖 fixture 数据、
capture 链、整数变量注入、UI 闭环与全部拦截路径；重跑回归前需重置演示状态：

```bash
python -m examples.shop_pro &
atk run --tags shoppro                            # 11 API 全过 + 1 UI 登记待实测
curl -X POST http://127.0.0.1:8788/api/dev/reset  # 回归重跑前
```

端到端自动化验证（smoke→拦截→回填→放行全链、init 一键、--set）见
`tests/test_shop_pro_dogfood.py`。

## Web 控制台（需安装 `.[console]` extra）

```bash
atk console          # 项目模式（当前目录），http://127.0.0.1:8900，路径跟随布局探测
atk console -g       # 全局模式：注册/管理多个 atk 工程，一键拉起各项目控制台
atk console -d       # 后台常驻（启动即打印地址），--status / --logs / --stop 管理
```

功能：场景树浏览、表单 ⇄ YAML 源码双模式编排（保存前强制校验、mtime 乐观锁防外部覆盖）、
触发执行（SSE 实时日志 + 断流轮询兜底）、运行历史（受阻/待实测分色、截图证据）、
配置编辑、定时任务（`.atk/schedules.yaml` 或旧布局 `config/schedules.yaml`，cron 或
"每天 HH:MM"，控制台常驻、停机不补跑）。只监听 `127.0.0.1`；被测服务凭据经
`${env:VAR}` 在启动时注入，由子进程 `atk run` 继承。前端构建产物已入库，无需 Node。

## 接入真实项目示例：信飞诉调系统

`scenarios/xinfei/` + 环境配置演示对已部署测试环境的接入（旧布局）：Cookie+token 双凭证
经 `${env:VAR}` 注入，只读接口冒烟先行。

```bash
export ATK_XF_COOKIE="token=...; orgId=..."   # 登录后从 DevTools 导出
export ATK_XF_TOKEN="dac581dc..."
atk run --env xinfei --module seal
```

## 安装

```bash
# 仓库内开发
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m examples.mock_server &
.venv/bin/atk validate && .venv/bin/atk run --env local

# 全局 atk 命令（跨项目使用；editable，框架改动即时生效）
./scripts/install_atk.sh        # = uv tool install --editable "<repo>[console]"
./scripts/uninstall_atk.sh      # 卸载全局工具，不碰项目内文件
```

## 已知限制（YAGNI 清单）

不做用户体系/多租户（本机单人）；不做用例入库（任何形式的 DB 双写都会破坏
YAML 唯一事实源）；停机期间定时任务不补跑；`ui:` 步骤由 AI 实测回填而非内置执行
（刻意保留人机接力点）；表单保存重写 YAML 会丢注释。

## 路线图与状态

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1 | 骨架 + API 执行器 + 场景库 + HTML 报告 | ✅ |
| M2 | Agent 编排 UI 执行器 + 即时层全链路 | ✅ |
| M3 | 门禁(gate) + CI 样例（export/doctor 已下线） | ✅ |
| M4 | 角色规范文档（docs/roles.md） | ✅ |
| M5 | 强化门禁与低门槛起步：空选中拦截、内容一致性、草稿时点判定、`.atk/` 布局收束、init 一键、`--dry-run/--set/--var`、`purge`、复杂 demo | ✅ |
| — | 发布：pip 发包 → npm 元包评估（对标 esbuild/turbo 模式） | 待提效数据立项，见 `docs/PUBLISH.md` |

343 个自动化测试全绿（`pytest`），含复杂 demo 全流程 dogfood。

## 文档索引

| 文档 | 内容 |
|---|---|
| `docs/human-ai-workflow.html` | **人机协作使用说明**：三条命令起步、gate 判定表、AI 操作细则 |
| `docs/DESIGN.md` | 设计思路与权衡（为什么双层、为什么 YAML-first、为什么不做的清单） |
| `docs/roles.md` | 流程轨：角色与质量门禁规范（suspect 24h SLA 等） |
| `docs/AI_AUTHORING_PROTOCOL.md` | AI 起草场景协议（skill 正文的文档版） |
| `docs/USAGE.md` / `docs/QUICKSTART_SCENARIOS.md` | 命令手册 / 场景快速上手 |
| `docs/superpowers/` | 原始规格（specs）与各里程碑实施计划（plans） |
