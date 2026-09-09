# atk AI 场景起草协议

本协议给 TUI/CLI Agent 使用，不是 atk CLI command。仓库不应新增由 atk 直接调用 LLM 的内置生成命令。

## 分工

| 环节 | 责任方 | 说明 |
|---|---|---|
| 上下文收集 | atk | `atk context --format json`、`atk plan --format json` 输出确定性事实。 |
| 用例起草 | AI | 基于代码、diff、schema、路由、现有场景生成 API/E2E YAML 草稿。 |
| expect 审定 | 人 | 只确认业务断言是否正确，输出必须简短。 |
| UI 实测 | AI | 用浏览器工具执行，截图取证后 `atk record --from-json` 回填。 |
| 门禁 | atk/CI | `atk gate --format json` 基于结构化记录判定。 |

## AI 起草流程

1. 获取事实：
   ```bash
   atk context --base <base> --head HEAD --format json > /tmp/atk-context.json
   atk plan --base <base> --head HEAD --format json > /tmp/atk-plan.json
   ```
2. 读代码建立事实表：
   - 后端：controller/router、service 分支、DTO/schema、状态机、权限、错误码。
   - 前端：route/page/component、表单字段、按钮、API 调用、可观察文案、data-testid。
   - 测试资产：existing_scenarios、fixture、env、capture 风格。
3. 只基于证据生成测试意图：
   - API：正向主链路、参数校验、权限、状态机、幂等/冲突。
   - E2E：用户入口、核心操作、结果确认、错误提示。
4. 写草稿：
   - 路径：`scenarios/<module>/gen-<short-head>-N.yaml`
   - 标签：必须包含 `ai-generated`，按需加 `smoke`、`api`、`e2e`。
   - 原则：只增不改，不能与现有场景重名。
5. 校验：
   ```bash
   atk validate
   atk run --tags ai-generated
   ```
6. 给人审 expect：只列路径、场景名、请求/页面动作摘要、expect、疑问。
7. 审核落盘：
   ```bash
   atk review-draft <draft.yaml> --by <name> --verdict approve|reject [--note ...]
   ```

## API 用例规则

API 用例按证据优先级生成：

1. 路由声明：method、path、query/path/body 参数。
2. DTO/schema/OpenAPI 注解：字段名、类型、必填、枚举。
3. service/handler：业务分支、状态流转、权限、错误码。
4. 现有场景：登录、token、fixture、capture。

expect 必须包含 status 和关键业务字段，不允许只断 `status: 200`。

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
```

## E2E 用例规则

E2E 用例按证据优先级生成：

1. 前端路由和页面入口。
2. 表单字段、按钮、菜单、文案、data-testid、aria label。
3. 页面调用的 API 和响应字段。
4. 需求说明或截图。

UI 步骤表达业务语义和稳定定位线索，不写坐标，不写“看起来正常”。缺少稳定选择器时，先建议补 `data-testid`。

```yaml
scenario: 页面添加待办并完成
module: ui
priority: P0
tags: [ai-generated, smoke, e2e]
env: demoapp
steps:
  - ui:
      action: 打开待办页面，进入任务列表
      expect: 页面显示新任务输入框和任务列表区域
  - ui:
      action: 在新任务输入框填写"AI E2E smoke"并点击添加
      expect: 列表出现标题为"AI E2E smoke"且状态为待办的条目
  - ui:
      action: 点击该条目的完成按钮
      expect: 该条目状态变为已完成
```

## 防漂移规则

- 不编造接口、字段、状态码、页面入口、业务文案。
- 不把环境问题写成业务失败。
- 不把 blocked 写成 pass。
- 不跳过 `atk validate`、`atk run --tags ai-generated`。
- 不让未审 `ai-generated` 草稿参与 gate 放行。
- 证据不足时输出“缺证据，不生成”，并列明缺什么。

## UI 实测回填 JSON

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
