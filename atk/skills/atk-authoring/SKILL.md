---
name: atk-authoring
description: 根据 atk context/plan 的确定性上下文，为一次需求或提交起草 API 与 E2E 场景。用于 TUI/CLI Agent 编排，不是 atk CLI 命令；LLM 调用留在 Agent 层，atk 只负责上下文、校验、执行、记录和门禁。
---

# atk 场景起草协议（Agent/TUI 编排）

定位：你是 AI test author。你的职责是把一次需求或提交翻译成可执行的 atk 场景草稿，包括自动化 API 用例和 E2E/UI 用例。atk 负责确定性命令；你负责读代码、推导测试意图、写 YAML 草稿、运行校验，并把需要人判断的内容压缩成简短清单。

禁止把本协议理解为新的生成命令。仓库内不应新增由 atk 直接调用 LLM 的命令。

## 分工边界

| 环节 | 责任方 | 要求 |
|---|---|---|
| 收集事实 | atk | `atk context --format json`、`atk plan --format json` 输出事实，不做业务猜测。 |
| 用例起草 | AI | 只能基于 diff、代码、路由、schema、现有场景和需求文字生成草稿。 |
| expect 审定 | 人 | 只看精简 expect 清单，确认业务值是否正确。 |
| 实测与证据 | AI | API 用 `atk run`；UI/E2E 用浏览器工具实测后 `atk record` 回填。 |
| 放行判断 | CI/atk | `atk gate` 基于结构化记录判定，不接受口头结论。 |

## 总流程

1. 读取确定性上下文：
   ```bash
   atk context --base <base> --head HEAD --format json > /tmp/atk-context.json
   atk plan --base <base> --head HEAD --format json > /tmp/atk-plan.json
   ```
   若 context 中 `files` 为空，停止，不生成场景。

2. 建立代码事实表：
   - 后端：识别 controller/router、service 分支、DTO/schema、状态机、权限校验、错误码。
   - 前端：识别 route/page/component、表单字段、按钮动作、API 调用、可观察 UI 文案。
   - 测试资产：读取 affected_modules 的 existing_scenarios，避免重复。
   - 若缺少接口路径、字段名或页面入口，先查代码；仍无证据则不写该步骤。

3. 生成测试意图：
   - 每个变更模块至少列出正常路径、关键异常路径、权限/状态边界。
   - API 优先覆盖数据规则、状态码、关键业务字段。
   - E2E 优先覆盖用户可见闭环：入口、操作、结果、失败提示。
   - 没有可靠自动化入口的意图保留为 UI intent，等待浏览器实测后 `record`。

4. 写 YAML 草稿：
   - 新文件只写入场景库的 `<module>/gen-<short-head>-N.yaml`，只增不改旧文件。场景库位置由 atk 自动探测：init 新项目在 `.atk/scenarios/`，旧项目在 `scenarios/`（以 `atk validate` / `atk run --dry-run` 输出路径为准）。
   - `tags` 必须包含 `ai-generated`，再按需要加 `smoke`、`api`、`e2e`、业务标签。
   - `scenario` 不得与 existing_scenarios 重名。
   - `env` 沿用同模块现有场景；没有参考时用运行环境名称。
   - API 步骤必须结构化；UI 步骤必须表达业务语义，不写坐标。

5. 校验和实测：
   ```bash
   atk validate
   atk run --tags ai-generated
   ```
   API 草稿能跑则报告技术结果；跑不通不得伪装为业务失败，先判断是配置、环境还是断言。

6. 人审 expect：
   给人的输出必须简短，只列：
   - 草稿路径
   - 场景名
   - 请求/页面动作摘要
   - expect 清单
   - 需要确认的问题
   不要把完整 diff、完整 YAML 或长篇推理丢给人。

7. 评审落盘：
   ```bash
   atk review-draft <draft.yaml> --by <reviewer> --verdict approve|reject [--note ...]
   ```
   approve 后去掉 `ai-generated`；reject 必须写明业务原因。

## API 用例生成规则

API 用例必须来自代码事实，按以下顺序找证据：

1. 路由声明：HTTP method、path、path/query/body 参数。
2. request/response DTO、schema、OpenAPI 注解或序列化字段。
3. service/handler 分支：状态机、校验规则、权限、幂等、错误码。
4. 现有场景：登录、token、fixture、capture 风格。

每个 API 场景尽量一个业务意图，不要把多个独立功能塞进一条。

推荐覆盖：

| 类型 | 何时生成 | expect 要求 |
|---|---|---|
| 正向主链路 | 新接口、新状态、新核心分支 | `status` + 关键业务字段，不只断 200。 |
| 参数校验 | diff 出现 required、长度、枚举、空值判断 | 明确 400/422 和错误字段/错误码。 |
| 状态机 | diff 改了状态流转 | 先创建/查询，再断最终状态。 |
| 权限鉴权 | diff 涉及 token、role、org、owner | 断 401/403 或业务拒绝码。 |
| 幂等/冲突 | diff 涉及重复提交、唯一键、锁 | 断 409 或等价业务码。 |

API YAML 示例：

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

## E2E/UI 用例生成规则

E2E 用例也必须来自证据。按以下顺序找事实：

1. 前端 router/page/component 中的入口路径。
2. 表单字段、按钮、菜单、文案、data-testid、aria label。
3. 页面发起的 API 请求和响应字段。
4. 需求说明或截图中的业务流程。

UI 步骤要写用户意图和稳定定位线索，不写坐标，不写“看起来正常”。如果页面缺少稳定选择器，优先建议补 `data-testid`，再起草场景。

E2E YAML 示例：

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

## 防漂移规则

- 不得编造接口、字段、状态码、页面入口或业务文案。
- 不得只因常见 CRUD 习惯生成无证据接口。
- 不得把 `blocked` 写成 `pass`。
- 不得把环境/账号问题归为业务失败。
- 不得修改既有场景；草稿只增不改。
- 不得跳过 `atk validate` 和草稿实测。
- 不得让未审 `ai-generated` 草稿参与 gate 放行。
- 不能确定时，输出“缺证据，不生成”，并列出缺哪类证据。

## 给人的评审输出模板

```text
需要你确认 expect：
1. scenarios/order/gen-abc123-1.yaml
   场景：优惠券下单后金额扣减
   expect：
   - POST /api/orders -> status=200
   - data.discount == 10
   - data.payAmount == 原价 - 10
   问题：优惠券折扣是否固定为 10，还是应读取配置？
```

## UI 实测回填 JSON

浏览器实测后优先用 JSON 回填，避免 Agent 输出漂移：

```json
{
  "run_id": "smoke-20260909-153000",
  "title": "页面添加待办并完成",
  "status": "pass",
  "note": "登录后添加 AI E2E smoke 并完成，列表显示已完成",
  "evidence": ["/tmp/atk-ui-todo.png"]
}
```

回填命令：

```bash
atk record smoke-20260909-153000 --from-json /tmp/atk-ui-result.json
```
