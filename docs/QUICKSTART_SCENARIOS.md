# 快速获得测试用例

> 这份文档讲清"测试用例从哪里来、怎么高效地产出"。
> 适用于 Dev（实现完功能想补场景）、QA（接手新项目搭场景库）、AI（按本指南自动起草）。

## 总览：三种途径

| 方式 | 触发 | 适用场景 | 速度 | 产出质量 |
|---|---|---|---|---|
| 1. **Agent 跟着改自动起草** | 每次 MR | 新功能 0→1 | 分钟级 | 依赖 Agent 业务理解 |
| 2. **批量 AI 补全** | 一次性 | 新接项目 / 大版本迁移 | 小时级 | 依赖上下文完整度 |
| 3. **录制 + 后整理** | 手动 | 复杂 UI 流程 / 易变文案 | 单流程 5-15 分钟 | 高（真人操作） |

三种方式不互斥，**实际项目通常组合使用**——批量补全打底，变更跟随 AI 起草增量补，复杂 UI 流程录制产出。

---

## 方式 1：变更驱动（推荐每次 MR）

这是最自然的工作流：Dev 改了代码 → CI/Agent 顺着 diff 生成对应场景。

### 操作步骤

1. **Dev 提交前**：
   ```bash
   atk context --base main --head HEAD
   ```
   看到模块归属中受影响模块 → 知道这次改动动了哪几个业务域。

2. **Agent 自动起草**（在 CI 或本地 Agent CLI，按 atk-gen skill 流程）：
   - 读上下文包中被改动的 controller / service / view 文件补丁
   - 结合 `config/modules.yaml` 找到这些代码对应的业务模块
   - 对每个改动的接口/页面生成 1-3 条场景草稿，存到 `scenarios/<module>/<slug>.yaml`
   - 草稿必须走 `atk validate` 通过

3. **Dev/QA 审定**：审 `expect` 字段是否符合业务，commit 进去。

### Agent 提示词模板

```
你是 atk 框架的测试场景起草助手。读下方 diff 摘要，按 YAML schema 写场景。

【schema 示例】
{从 scenarios/demo/ 选 1-2 个最相似的真实场景贴上}

【本次 diff】
{粘 git diff 或 atk context 上下文包}

【要求】
1. 每个改动的接口/页面至少 1 条场景
2. 优先级：核心链路 P0；分支路径 P1；边角 P2
3. expect 字段必须基于代码里的真实断言（不要编造）
4. tags 含 "smoke" 以便 plan 选
5. 输出 1-5 条草稿，路径建议 scenarios/<module>/<purpose>.yaml

【禁止】
- 不要发明不存在的接口
- 不要在 expect 里写模糊的"应该成功"
```

### 产出物

```yaml
scenario: 新增订单带优惠券校验
module: order
priority: P0
tags: [smoke, order]
env: demoapp
steps:
  - api:
      call: "POST /api/orders"
      body: { skuId: "${test_sku}", qty: 2, coupon: "WELCOME10" }
      expect: { status: 200, "data.discount": 10.0 }
```

---

## 方式 2：批量 AI 补全（一次性铺底）

新接项目或大版本迁移时，手工补全全部功能不现实。让 AI 一次性产出几十条。

### 操作步骤

1. **准备上下文包**（一次性）：
   ```
   atk-project-context/
   ├── api/                    # OpenAPI/Swagger JSON 或 .proto
   ├── schemas/                # GraphQL schema 或数据库 DDL
   ├── pages/                  # UI 截图（按业务域分目录）
   ├── user-stories.md         # 用户故事（QA 写）
   └── code-modules.md         # 模块清单 + 一句话职责
   ```

2. **批量提示 Agent**（按业务域分批，每批 5-10 条）：
   ```
   你是 atk 框架的测试场景起草助手。
   
   业务域：订单管理
   上下文：./atk-project-context/
   
   请：
   1. 读 OpenAPI 的 order 标签下所有接口
   2. 读 user-stories.md 中"订单"部分
   3. 起草 8-12 条场景覆盖：创建/查询/取消/退款/状态机
   4. 场景写进 scenarios/order/<purpose>.yaml
   5. expect 字段用真实业务值（如"待支付"而不是"success"）
   ```

3. **批量校验**：
   ```bash
   atk validate             # 静态校验
   atk run --env <env>      # 跑一遍看哪些能直接通过
   ```

4. **QA 批量 review**：开控制台 `/edit?path=scenarios/...` 一条条看 expect 对不对。

### 关键技巧

- **分批而不是一次 100 条**——LLM 上下文有限，5-10 条/批质量远高于 50 条/批
- **用 demo_app 现有 5 个场景做范本**——AI 看真实例子比看 schema 描述好
- **提供反例**："不要在 expect 里只断 status=200"——强制它写点路径断言
- **review 时先用 `atk run` 跑**——能直接通过的 expect 通常是写得对的；失败的优先 review

---

## 方式 3：录制 + 后整理（UI 复杂流程）

UI 流程长、按钮位置固定但文案易变时，录制比手写快。

### 操作步骤

1. **打开 ego-browser**（用户登录态已存在）：
   ```bash
   ego-browser nodejs <<'EOF'
   await useOrCreateTaskSpace('录制 <流程名>')
   await openOrReuseTab('https://your-app/...', { wait: true })
   EOF
   ```

2. **手动操作**（用户执行，Agent 观察同步记录）：
   - 每一步告诉 Agent："打开订单页 / 点查询 / 选第一条 / 点详情 / 截图"
   - Agent 维护一份操作记录表
   - 关键节点截图存到 `scenarios/.../evidence/<step>.png`

3. **后整理成 YAML**：
   ```yaml
   scenario: 用印文件查询流程
   module: seal
   priority: P1
   tags: [ui, e2e]
   env: xinfei
   steps:
     - ui:
         action: open
         target: "https://xinfei-test.anmiai.com/seals/seal"
     - ui:
         action: fill
         target: "文件名称输入框"
         value: "测试文件"
     - ui:
         action: click
         target: "查询按钮"
     - ui:
         action: assert_text
         expect: "结果区域出现含'测试文件'的行"
   ```

4. **AI 实测**：`atk run` 触发，Agent 用 ego-browser 跑过并对 expect 字符串做断言。

### 注意

- 这种方式**必须有 AI Agent 配合**——纯录制会产生"坐标点击"那种脆性脚本
- expect 字段用**业务语义字符串**（"结果区域出现含'测试文件'的行"）而不是"按钮在 (240, 320)"——前者抗重构
- 截图证据是核心——既给 QA 看、也给失败时定位用

---

## 提高 AI 产出质量的十条

1. **提供真实场景作范本**——demo_app 5 个场景是金标准
2. **一次给一批业务域而不是一个接口**——5-10 条/批是 LLM 甜区
3. **API 场景必须看 OpenAPI**——不靠猜字段名
4. **UI 场景必须有截图**——AI 看图比看文字更准
5. **expect 要具体到业务值**——"待支付"而不是"success"；点路径而不是"返回值有东西"
6. **tags 必含 smoke**——否则 `atk plan` 不会自动选
7. **变量用 `${name}` 占位**——敏感值用 `${env:NAME}`，不要硬编码
8. **capture 用点路径**——`data.orderNo` 提出来供后续引用
9. **一个场景一个意图**——不要把"下单 + 支付 + 退款"塞一个文件
10. **review 时用 `atk run` 跑一遍**——能跑通的 expect 大概率是对的

## 哪些测试不该让 AI 写

- **业务断言很主观的**（"页面看起来没问题"）—— AI 写出来一定 `suspect`，不如人工
- **异常路径稀少但关键**（如并发竞态、幂等保证）——AI 起场景覆盖率低，QA 手工补
- **合规 / 安全**（越权、注入）——这些场景独立于功能，不该混在功能用例里

---

## 与 atk 命令的搭配

```bash
# 起草时
atk validate                         # 语法门禁（每条草稿先过）

# 验收时
atk run --env demoapp --module order # 跑新加的场景

# 发布前
atk plan --base main --head HEAD    # 改了什么
atk run --record-to <run_id>        # 跑 + 落记录
atk record <run_id> --status pass --note "QA 审定 OK"  # 落定性
atk gate <run_id>                   # 门禁判定
```

---

## 工具化的钩子（未来可做）

- `atk scenarios suggest` —— 读 diff 自动调 AI 起草场景草稿
- `atk scenarios import-openapi` —— 从 OpenAPI 一键生成所有接口的正向用例骨架
- `atk scenarios coverage` —— 报告每个接口被覆盖的用例数（基于路径/方法匹配）

这三个都是 CLI 一行命令就能串的事，先记录在这里，等真正瓶颈出现再做。
