# atk — AI 原生双层自动化测试框架

【此项目还是在沉淀和实践中，目的是使用本地AI或者非常便宜的API 自动化生成冒烟用例和做接口测试，结合AI 浏览器再做e2e 测试，生成测试报告，扩展开发角色责任边界 】

即时层（变更驱动冒烟，不落库）+ 沉淀层（YAML 场景回归），设计详见
`docs/superpowers/specs/2026-08-24-atk-design.md`，角色规范见 `docs/roles.md`。

核心思想：**AI 干量产的活（生成/实测/固化填充），人做判断的事（审断言、定性失败），机器守确定的门（gate）。**

## 功能总览

| 命令 | 作用 |
|---|---|
| `atk init` | 初始化项目结构（只建缺失文件，绝不覆盖）；`--ui-tool` 指定 AI 实测浏览器 |
| `atk validate` | 场景库合法性校验（错误+重名告警），QA 提交前自查 |
| `atk context` | 输出确定性变更上下文包（提交记录 + 补丁 + 模块归属），供 Agent 按 atk-authoring 协议起草场景 |
| `atk plan` | 创建运行记录，输出复用场景清单与待补全意图；支持 `--format json` |
| `atk run` | 执行场景：HTML 报告 + 可选 JUnit XML + 可并入运行记录；`--skip-ui` 跳过 UI 步骤 |
| `atk smoke` | 一键冒烟：plan→run→report→gate；`--format json` 输出含 `next` 建议的 manifest |
| `atk record` | Agent 回填 UI 探索意图结论（pass/fail/suspect/blocked + 截图证据），支持 `--from-json` |
| `atk report` | 渲染运行记录为统一 HTML 报告（含截图缩略） |
| `atk review` | 开发整单确认（approve/reject），仅告警不拦截 |
| `atk review-draft` | 草稿评审：approve 去 `ai-generated` tag 转正，reject 移走留档 |
| `atk gate` | 合并门禁：变更一致性 + 用例失败 + 未定性 + UI 未回填四查；支持 `--format json` |
| `atk last` | 显示最近一次运行记录上下文；供 `--last` 系列确认操作对象 |
| `atk console` | 启动 Web 控制台（需 `.[console]` extra） |

## run_id 不用手工搬运

`plan` / `smoke` / `run --record-new` 会把 run_id 写入 `.atk/last-run.json`，
后续命令一律用 `--last`，Agent 不必从上一条输出里解析再拼接：

```bash
atk smoke --title "优惠券下单" --base main --format json   # 输出 manifest，含 next
atk record --last --title "页面下单后列表显示待支付" --status pass
atk review --last --by dev --verdict approve
atk gate --last --format json
```

`atk smoke --format json` 只输出一个 JSON 对象（中间过程静默），
其中 `next` 字段直接给出下一步该执行的命令，AI 可据此跑完整条链路。

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

## UI 步骤与门禁

`atk run` 不执行 `ui:` 步骤，而是标记为**待实测**（`ui_pending`）：
既不计失败也不计受阻（不再返回 exit 2），但会自动登记成 `pending` 意图。
AI 浏览器实测后 `atk record --last` 回填，未回填前 `atk gate` 拦截——
门从 run 阶段移到 gate 阶段，避免"没测过就说通过"，也消除"必然受阻"的误报噪音。

`atk run --skip-ui` 可显式跳过 UI 步骤（不计入结论，也不登记意图）。

执行质量特性：环境错误自动重试（`retries` 默认 1）、非 JSON 响应保护、
场景级 fixtures 数据（`data:` 字段）、变量捕获场景间隔离、退出码三态
（0 通过 / 1 失败 / 2 受阻或配置问题）、`${env:VAR}` 敏感值注入
（口令/会话凭证不入库，加载期解析）。

## Web 控制台（需安装 `.[console]` extra，否则 `atk console` 不可用）

```bash
atk console          # 项目模式（当前目录），浏览器打开 http://127.0.0.1:8900
atk console -g       # 全局模式：注册/管理多个 atk 工程，一键拉起各项目控制台
```

功能：场景树浏览、表单化编排（API/UI 步骤卡片 ⇄ YAML 源码双模式，保存前强制校验，
mtime 乐观锁防外部覆盖）、触发执行（SSE 实时日志）、运行历史与截图证据、
环境/模块配置编辑、**定时任务**（`config/schedules.yaml` 定义 cron 或"每天 HH:MM"，
APScheduler 到点自动执行并写入运行记录；控制台需常驻，停机不补跑）。
界面/CLI 触发的执行均带 `--record-new` 自动入历史。前端构建产物已入库，无需 Node 环境。

控制台只监听本机 `127.0.0.1`，不需要登录口令。被测服务的 Cookie、token 等凭据通过
`config/environments.yaml` 中的 `${env:VAR}` 在启动时注入；控制台启动的 `atk run` 会继承这些环境变量。

其他项目使用：`uv tool install --editable "/path/to/auto_test_framework[console]"`
后即获得全局 `atk` 命令；目标项目内 `atk init && atk console`。

已知限制：表单保存重写 YAML 会丢失注释；定时任务需控制台常驻。

## Demo 被测系统

`examples/demo_app.py`（端口 8766）：待办任务管理，含登录鉴权、状态机
（pending→done，重复完成 409）、参数校验（空标题 422）与 Web 页面，
配套场景库在 `scenarios/demo_app/`——覆盖正向链路、负向断言与 UI 探索，
是框架各能力的端到端示例。

`examples/spa_shop.py`（端口 8777）：单页面商城，覆盖登录、优惠券、下单、
订单查询。配套场景库在 `scenarios/spashop/`，其中 `api` 标签场景由
`atk run` 自动执行，`ui/e2e` 场景由 AI 浏览器实测后通过
`atk record --from-json` 归档。完整 dogfood 流程见
`docs/spa-shop-dogfood.html`。

## 接入真实项目示例：信飞诉调系统

`scenarios/xinfei/` + `config/environments.yaml[xinfei]` 演示了对已部署
测试环境（https://xinfei-test.anmiai.com）的接入方式：Cookie+token 双凭证
经 `${env:VAR}` 注入，只读接口冒烟先行。日常用法：

```bash
export ATK_XF_COOKIE="token=...; orgId=..."   # 登录后从 DevTools 导出
export ATK_XF_TOKEN="dac581dc..."
atk run --env xinfei --module seal
```

## 快速开始

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/atk init                            # 生成配置与示例场景骨架
.venv/bin/python -m examples.mock_server &    # 示例被测服务
.venv/bin/atk validate && .venv/bin/atk run --env local
```

## 即时层工作流（变更驱动冒烟）

```
atk smoke（plan→run→report→gate）→ (Agent) 浏览器实测 → atk record --last
        → atk review --last → atk gate --last
```

完整编排规则见 `.claude/skills/atk-smoke/SKILL.md`，API/E2E 场景起草协议见
`docs/AI_AUTHORING_PROTOCOL.md` 或 `.claude/skills/atk-authoring/SKILL.md`；代码→模块映射见
`config/modules.yaml`。CI 接入模板见 `.ci-examples/`（GitLab / GitHub Actions，
含 MR 门禁 job 与夜间定时回归）。

### Skill 安装布局（换 Agent 不失效）

`atk init` 把包内单一源同时铺到三种布局，并按标记块幂等维护 `AGENTS.md`：

| 布局 | 面向 |
|---|---|
| `.claude/skills/<name>/SKILL.md` | Claude / CodeBuddy |
| `.cursor/rules/atk-<name>.md` | Cursor（含 frontmatter） |
| `AGENTS.md`（`<!-- atk:begin/end -->` 区块） | Codex / OpenCode / 其他 |
| `skills/<name>/SKILL.md` | 通用兜底 |

UI 实测工具不再写死：`atk init --ui-tool playwright`（默认 `ego-browser`），
SKILL.md 里的 `{{UI_TOOL}}` 会被替换。已存在的 skill 文件一律跳过，绝不覆盖。

## AI 起草场景（context 上下文包 → YAML）

`atk context` 只产出确定性上下文包，场景由 AI Agent 按 atk-authoring 协议编写：

```bash
# 方式一：Agent 会话内触发 skill（自动调 atk context 拿包并起草）
# 对 Agent 说：为这次改动补测试场景

# 方式二：先落盘上下文包，再交给 Agent（配合 .claude/skills/atk-authoring 使用）
atk context --base <基线> --context-out /tmp/ctx.md
```

上下文包含：提交记录（含说明正文）、分文件补丁、模块归属、受影响模块的现有场景
（去重 + 风格对齐）。草稿自动加 `ai-generated` 标签与溯源头注释，绝不覆盖已有文件；
expect 断言需人工评审后再参与门禁。

```bash
atk validate && atk run --tags ai-generated     # 校验并实测草稿
```

## 场景编写

参考 `scenarios/demo/`。步骤分 `api:`（结构化，立即生效）与 `ui:`
（自然语言，即时层由 Agent 实测）。
`expect` 是场景的灵魂，也是 QA 评审的核心对象。

```yaml
scenario: 下单后可查询订单
module: order
priority: P0            # P0=门禁必跑 / P1=夜间回归 / P2=周级全量
tags: [smoke]
data: fixtures/order.yaml   # 场景级测试数据，优先级 env < fixture < capture
steps:
  - api:
      call: "POST /api/login"
      body: { username: "${username}", password: "${password}" }
      expect: { status: 200 }
      capture: { token: "data.token" }   # 捕获响应值供后续步骤
      retries: 2                          # 仅环境类错误重试
```

## 路线图

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1 | 骨架 + API 执行器 + 场景库 + HTML 报告 | ✅ |
| M2 | Agent 编排 UI 执行器 + 即时层全链路 | ✅ |
| M3 | 门禁(gate) + CI 样例（export/doctor 已下线） | ✅ |
| M4 | 角色规范文档（docs/roles.md） | ✅ |
