---
name: atk-authoring
description: 需要为一次需求或提交新增/修改 atk 场景 YAML 时使用（写 API 用例与 E2E/UI 用例）。先按 existing_scenarios 判重复用、不重写，再动笔；只依据代码事实生成，expect 交人审定。LLM 调用留在 Agent 层，atk 只做上下文、校验、执行与门禁。
---

# atk 场景起草协议（Agent/TUI 编排）

前置：先完整读 `.atk/skills/atk-use/atk_use.md`（命令、envelope、硬规则）。本文只讲**怎么起草场景**——
这部分 atk 给不了，必须由你（AI test author）读代码推导。

不是新命令：仓库里不应出现"由 atk 直接调 LLM"的东西。你要写的 YAML 用
`atk context` 给的确定性事实喂，写完用 `atk agent --format json` 回到主循环。

## 分工边界

| 环节 | 责任方 | 要求 |
|---|---|---|
| 收集事实 | atk | `atk context --format json` / `atk plan --format json` 只输出事实，不做业务猜测 |
| 用例起草 | AI | 只能基于 diff、代码、路由、schema、现有场景、需求文字 |
| expect 审定 | 人 | 只看精简 expect 清单，确认业务值对不对 |
| 实测与证据 | AI | API 走 `atk run`；UI 用 {{UI_TOOL}} 实测后 `atk record` 回填 |
| 放行判断 | CI/atk | `atk gate` 只认结构化记录，不接受口头结论 |

## 先查重，再动笔（流程第 3 步的判据）

`atk context --format json` 的 `existing_scenarios[模块]` 已经把判重所需的材料给全了：
`calls`（`METHOD /path`，UI 步骤写作 `ui: <动作>`）、`expects`（逐步断言）、`file`（去哪核对）。
`atk plan --format json` 的 `reuse` 是本次**已经选中要跑**的老场景——它们不需要你重写。

判重看语义（calls + expect 断的是不是同一件业务事实），**不看场景名像不像**。

| 比对结果 | 怎么办 |
|---|---|
| calls 与 expect 等价 | **不写新的**。在评审输出里写明"已由 `<file>` 覆盖"；它本就在 `reuse` 里，`atk run` 会真跑 |
| calls 相同、老场景断得更弱（例如只断 200，没断关键字段） | 不要另起一条"补强版"平行副本。列成建议：`建议在 <file> 追加 expect: …`，**等人点头**再改老文件（人批准后的扩展不算 AI 静默改） |
| 同接口、不同业务意图 | 新建，一条场景一个意图（"正向可读"与"越权不可读"是两条） |
| calls 不同但意图重叠（API 与 E2E 各覆盖一遍） | 保留更靠近用户闭环的那条做主验证，另一条改 tags 降级，别两条都当主链路跑 |

同一批草稿内部也要查：写下一条前先 `ls <场景库>/<模块>/gen-*`，同一个意图不要因
多轮返工留下两份 `gen-abc123-1.yaml`、`gen-abc123-2.yaml` 各说一遍。

为什么这么严：重复场景不会让测试更强，只会让同一个断言**失败两遍**、`atk run` 变长、
gate 报告里出现两条指向同一根因的记录，排查成本翻倍。

写完自查：

```bash
atk validate                        # 重名会告警
atk run --dry-run --module <模块>    # 选中清单里不该出现两条等价场景
```

## 流程

1. 取确定性上下文：
   ```bash
   atk context --base <base> --head HEAD --format json > /tmp/atk-context.json
   atk plan --base <base> --head HEAD --format json > /tmp/atk-plan.json
   ```
   `files` 为空就停：没有变更就不要生成场景。
2. 建代码事实表（后端 controller/service/DTO/状态机/权限/错误码；前端
   route/form/button/API 调用/可观察文案；现有场景的 `env`/`capture`/fixture 写法照抄对齐）。
   缺接口路径、字段名或页面入口就先查代码；**查不到证据就不写那一步**。
3. 出测试意图：每个变更模块至少覆盖正常路径、关键异常路径、权限/状态边界。
   API 优先断数据规则与关键业务字段；E2E 优先断用户可见闭环（入口→操作→结果→失败提示）。
   没有可靠自动化入口的，留作 UI 意图，交给 `atk record` 回填。
   **每个意图先过一遍下一节的判重表**：等价的不要写，记一句"已由 `<file>` 覆盖"即可。
4. 写草稿：
   - 新文件只写 `<场景库>/<module>/gen-<short-head>-N.yaml`，**只增不改**旧场景
     （库位置以 `atk validate` / `atk run --dry-run` 输出为准：新布局 `.atk/scenarios/`，旧布局 `scenarios/`）。
     `gen-` 只是给人看的约定：代码判草稿认的是 `ai-generated` tag（`atk/drafts.py: is_draft`），
     改文件名不会转正，转正只有 `atk review-draft --verdict approve` 这一条路。
   - `tags` 必含 `ai-generated`；`env` 沿用同模块现有场景，无参考时用运行环境名。
     命名/重名只是最弱的一道防线（`atk validate` 会告警），语义重复要靠上面的判重表。
5. 回主循环：`atk agent --format json`。它会排好 `atk validate` → 草稿实测 →
   审 expect（`review-draft`）→ `run` → `record` 的顺序，并在该问人的地方停下来。

## API 用例：按序找证据

1. 路由声明：method、path、path/query/body 参数。
2. request/response DTO、schema、OpenAPI 注解、序列化字段。
3. service/handler 分支：状态机、校验规则、权限、幂等、错误码。
4. 现有场景的登录、token、fixture、capture 写法。

一条场景一个业务意图，不要把多个独立功能塞进一条。

| 类型 | 何时生成 | expect 要求 |
|---|---|---|
| 正向主链路 | 新接口、新状态、新核心分支 | `status` + 关键业务字段，不只断 200 |
| 参数校验 | diff 出现 required、长度、枚举、空值判断 | 明确 400/422 与错误字段/错误码 |
| 状态机 | diff 改了状态流转 | 先创建/查询，再断最终状态 |
| 权限鉴权 | 涉及 token、role、org、owner | 断 401/403 或业务拒绝码 |
| 幂等/冲突 | 涉及重复提交、唯一键、锁 | 断 409 或等价业务码 |

### expect 能写什么（`atk/store/models.py: parse_expect_value`）

`expect` 是「路径 → 表达式」的映射。裸写就是等值；其它形式必须**加引号**，
因为 YAML 会先吃掉 `<` 和 `关键字:`：

| 想断什么 | 写法 |
|---|---|
| 等值 | `status: 200`、`data.status: pending` |
| 比较 | `data.amount: "< 90"`、`data.count: ">= 1"`（另支持 `==` `!=` `>`） |
| 包含 | `message: "contains: 下单成功"`、`message: "not_contains: 失败"` |
| 正则 | `orderNo: "regex: ^SP-\\d{4}$"`（YAML 双引号里反斜杠要写两个） |
| 长度 | `data.items: "len: 2"` 或区间 `data.items: "len: 1..5"`（闭区间） |
| 类型 | `data.token: "type: str"`；可用 `str int float num bool list dict` |
| 枚举/前后缀 | `data.status: "in: pending, paid"`（**逗号分隔**）、`code: "startswith: ORD"` |
| 非空/空 | `data.orderNo: not_null`、`data.deletedAt: is_null` |

- 路径支持点号与下标：`data.list.0.status`。
- `status` 只能断具体状态码，写 `status: not_null` 会被直接拒（`ValueError`）。
- 要断的字面量本身长这样（以 `<` 或 `关键字:` 开头），用反斜杠转义：`msg: "\\<deprecated"`。
- 操作符写歪不用记错误信息：`atk validate` 会打印「未知断言操作符 X，可用：…」并拒绝加载该场景。
- `not_null` 只保证"非 None / 非空串"，`0`、`[]`、`{}` 都算非空；断"有内容"要配 `len:`。
- 场景按步骤顺序执行，**任一步失败即中断**（后续步骤依赖前序 `capture`），所以一条场景只装
  一个业务闭环，别把不相干的断言顺手塞进来。

```yaml
scenario: 创建待办后可查询到待办状态
module: todo
priority: P0
tags: [ai-generated, smoke, api]
env: demoapp
steps:
  - api:
      call: "POST /api/login"
      body: { username: "${username}", password: "${password}" }
      expect: { status: 200, data.token: not_null }
      capture: { token: "data.token" }
  - api:
      call: "POST /api/todos"
      headers: { Authorization: "Bearer ${token}" }
      body: { title: "AI authoring smoke" }
      expect: { status: 200, data.status: pending }
      capture: { todo_id: "data.id" }
  - api:
      call: "GET /api/todos/${todo_id}"
      headers: { Authorization: "Bearer ${token}" }
      expect: { status: 200, data.title: "AI authoring smoke", data.status: pending }
```

## E2E/UI 用例：同样只写有证据的

1. 前端 router/page/component 里的入口路径。
2. 表单字段、按钮、菜单、文案、`data-testid`、aria label。
3. 页面发起的 API 请求与响应字段。
4. 需求说明或截图里的业务流程。

写用户意图 + 稳定定位线索，不写坐标、不写"看起来正常"。页面缺稳定选择器，
先建议补 `data-testid` 再起草。

**`ui:` 步骤要放在场景末尾**：`atk run` 遇到第一个 `ui:` 步骤就标记 `ui_pending` 并中断
（`atk/executors/runner.py`），它后面的 `api:` 步骤本轮不会执行。所以下面示例里先做登录
拿 token、再收尾一条 `ui:`；如果 UI 之后还有接口要验，拆成两条场景，别串在一条里。
`--skip-ui` 是"这轮不做 UI"，会连待实测意图都不登记，别拿它凑绿。

```yaml
scenario: 页面添加待办并完成
module: ui
priority: P0
tags: [ai-generated, smoke, e2e]
env: demoapp
steps:
  - api:
      call: "POST /api/login"
      body: { username: "${username}", password: "${password}" }
      expect: { status: 200, data.token: not_null }
      capture: { token: "data.token" }
  - ui:
      action: 打开待办页面，使用已登录身份进入任务列表
      expect: 页面显示新任务输入框和任务列表区域
  - ui:
      action: 在新任务输入框填写"AI E2E smoke"并点击添加
      expect: 列表出现标题为"AI E2E smoke"且状态为待办的条目
  - ui:
      action: 点击该条目的完成按钮
      expect: 该条目状态变为已完成
```

## 防漂移

- 不得编造接口、字段、状态码、页面入口或业务文案；不得因"常见 CRUD 长这样"就生成。
- 不得改既有场景（只增不改）；要补强老场景的断言，先给人一句建议，**人点头才改**，
  不许另开一条"补强版"平行副本。
- 不得新写与现有场景语义等价（同 calls + 同 expect 事实）的新场景——等价就复用 `reuse`。
- 不得跳过 `atk validate` 与草稿实测。
- 不得让未审 `ai-generated` 草稿参与 gate 放行。
- 环境/账号问题归 `blocked`，不得记 `fail`；不确定时输出"缺证据，不生成"并列出缺哪类证据。

## 给人审 expect 的输出模板

只给这五项，别贴完整 YAML、完整 diff 或长篇推理：

```text
需要你确认 expect：
1. scenarios/order/gen-abc123-1.yaml
   场景：优惠券下单后金额扣减
   expect：
   - POST /api/orders -> status=200
   - data.discount == 10
   - data.payAmount == 原价 - 10
   问题：折扣是固定 10，还是应读取配置？

已覆盖，未新增（避免同一断言失败两遍）：
- 订单创建正向链路 → scenarios/demo/create_order.yaml 已断 data.status: 待支付
建议扩展现有场景（需你点头才改）：
- scenarios/demo/query_order.yaml 只断 status=200，建议追加 data.list.0.status: 待支付
```

开发给结论后落盘（AI 只转述，不代替 approve）：

```bash
atk review-draft scenarios/order/gen-abc123-1.yaml --by <评审人> --verdict approve
```

## UI 实测回填的 JSON

批量回填优先用 JSON，避免逐条拼参数时漂移：

```json
{
  "run_id": "smoke-20260909-153000",
  "title": "页面添加待办并完成",
  "status": "pass",
  "note": "登录后添加 AI E2E smoke 并完成，列表显示已完成",
  "evidence": ["/tmp/atk-ui-todo.png"]
}
```

```bash
atk record smoke-20260909-153000 --from-json /tmp/atk-ui-result.json
```
