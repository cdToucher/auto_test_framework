# atk 设计思路

> 这份文档讲清"为什么这么设计"，比规格文档更平易、更带思考过程。
> 具体的字段、接口、命令格式以规格文档为准（见文末索引）。

## 1. 起点：要解决什么问题

我们在用 AI 写代码（Agent CLI / Claude Code / OpenCode 一类）。当时代码生产能力从"按周交付"变成"按小时交付"，测试环节迅速脱节：

1. **Dev/QA 角色分工失效。** QA 跟不上 AI 节奏；Dev 自测又无法替代业务判断。
2. **有 CI 无测试。** 流水线只会构建，跑不了任何业务断言。
3. **手工冒烟不可持续。** 每改一行代码都点一遍主流程，团队迅速疲劳。

目标很直接：**让"代码改了不必人点页面也能确认没回归"，同时保留"业务正确性由人定性"的能力边界**。交付两件事——

- **工具轨**：可运行的 CLI/Web 框架 `atk`，把回归集跑起来。
- **流程轨**：AI 协作规范，明确人机边界（详见 `docs/roles.md`）。

判断成功的两条硬标准：

- 合并前不再需要任何人手动点冒烟。
- QA 的工作从"点页面/写测试代码"变成"审中文场景 + 定性失败报告"。

---

## 2. 核心思想：双层 + 三态

### 2.1 双层而非单层

直觉方案是"一套场景库定时回归"，但单一层跑不通。原因是变化速度不匹配：

- 业务逻辑每天变，回归集是按月累积的资产。
- 同一份资产要同时回答两类问题：**"这次改动破没破东西？"** 和 **"当前完整功能还通吗？"**。

把它们压到一起会产生"每改一行就跑全量 P2"或"只跑 P0 漏掉 P1"的两难。解法：承认两种需求天然独立——

| 层 | 触发 | 输入 | 输出 | 是否沉淀 |
|---|---|---|---|---|
| **即时层（冒烟）** | 每次提交 / pre-MR | 变更 diff + 影响面分析 | 临时场景 → 当前环境实测 → 当次结论 | **不落库** |
| **沉淀层（回归）** | 夜间 / 周级 / 手动 | 固化 YAML 场景库 | 确定性执行 → 运行记录 → 报告 | 入库维护 |

两层共用**执行器**（API 走 httpx、UI 走 ego-browser）和**模型**（Scenario / Step / ApiExpect / IntentRecord），但输入输出与生命周期完全不同。强行复用一份场景反而麻烦。

### 2.2 退出码三态而非二态

`atk run` 退出码不是简单的 0/1：

| 码 | 语义 | gate 行为 |
|---|---|---|
| `0` | 全部通过 | 放行 |
| `1` | 存在用例失败或场景加载错误 | 拦截（人/AI 必须修） |
| `2` | 仅环境受阻（连接/权限/服务宕） | 记录但不判失败（环境事故） |

二态方案会让 CI 把"测试环境挂了"误判为"代码坏了"，半夜叫醒人来修服务。三态把环境类问题与代码问题解耦，门禁的判据更准。

### 2.3 Intents 四态

AI 实测 UI 步骤时结果比 API 更不可控：图片识别偏差、动态文案、偶发卡顿。强行三态会让 AI 撒谎（"不太确定"→"通过"）。解法——把不确定显性化：

| 状态 | 来源 | 含义 | gate |
|---|---|---|---|
| `pass` | 确定匹配 | 通过 | 放行 |
| `fail` | 断言不匹配 | 失败 | 拦截 |
| `suspect` | AI 把握不大 | 疑似问题 | **强制 QA 24h 内定性**（超时视为发布阻塞） |
| `blocked` | 环境/权限类 | 受阻 | 公告原因（不判失败） |

`suspect` 是这套规范最有意思的一招：把 AI 不会拍板的部分诚实地暴露出来，让人的判断力用在刀刃上。

---

## 3. 文件即事实源（YAML-first）

框架的第一个原则：**scenarios/*.yaml 永远是唯一事实源**。所有派生产物（HTML 报告、固化脚本、运行记录）都从它来；不存在"DB 是主、文件是导"的双写。

为什么这么挑：

- **AI 直接读写**。YAML 是 LLM 友好格式，AI 起场景、AI 改断言、AI 修脚本都不需要"导出"或"导入"中间步骤。
- **git 管版本**。所有变更可追溯、可回滚、可 Code Review。
- **CLI 确定性执行**。本地/CI 跑出来结果一致，不存在"我这边能过你那边不行"。
- **三方共写**（AI / CLI / 未来的 Web 界面）没有任何同步问题——因为它们在写同一份文件。

代价是放弃了一些能力：实时协作编辑、复杂的搜索索引、富文本注解。但这些对测试用例的边际收益极低，付出与回报完全不成比例。

副作用：YAML 写起来比 GUI 点起来慢。所以我们做了 **Web 控制台**——但控制台不是另一套存储，是 YAML 的视图和编辑器（详见第 7 节）。这是和 MeterSphere 等平台最大的差异：它们以数据库为中心，引入"导出 YAML"和"导入 YAML"两个易漂移的环节；我们没有这两个环节。

---

## 4. CLI 命令设计原则

11 个子命令，**全部围绕"YAML 在哪儿、跑哪些、跑完怎么办"三个问题**展开：

```
init         脚手架（新工程开箱）
validate     静态校验（不入库之前先看看合不合法）
list         浏览场景
diff         看改动了什么、影响哪些模块
plan         选场景（diff 驱动或手动）
run          执行（落 reports/runs/）
record       人为定性结果（pass/fail/suspect/blocked + 证据）
report       把运行记录渲染成 HTML
export       固化为 pytest 脚本（AI 难实时跑的 UI 场景用）
doctor       修复分类（automatic / repairable / suspect_bug / retired）
gate         CI 门禁判定（基于结构化记录，不基于 AI 自由发挥）
```

设计取舍：

- **命令之间不共享中间状态**。每个命令读 YAML / 写 YAML，不依赖前一步的内存。
- **人类友好**。命令名是英文单词而不是缩写（`gate` 而不是 `g`），可读性优先于节省三个字符。
- **CI 友好**。所有命令支持 `--json` 风格的输出、退出码规范一致。
- **AI 友好**。命令都能被 Agent 编排，不需要交互式输入。

`gate` 是唯一"强判定"的命令——MR 是否可合并由它决定。它只做结构化比对：变更文件 vs 运行记录 affected_files、意图状态表里有没有未定性的 fail/suspect、有没有用例失败。不允许 AI 给"通过"就通过。

---

## 5. 变量与配置：环境安全

`config/environments.yaml` 里能写 `${var}` 和 `${env:VAR}`：

```yaml
local:
  base_url: "http://127.0.0.1:8766"
  vars:
    username: alice
xinfei:
  base_url: "https://xinfei-test.anmiai.com"
  vars:
    xf_token: "${env:ATK_XF_TOKEN}"   # 从环境变量读，不进库
```

两个关键决策：

1. **vars 在加载期就解析**。如果 `atk` 在某个 CI 环境里跑，环境变量由 CI 注入；仓库里只留占位符。
2. **API 自动化示例验证过这一点**——实测信飞诉调系统时，token 必须从 `ATK_XF_TOKEN` 注入；放 yaml 里就是泄露。

`{env:VAR}` 这个能力是后期才加的。原因：M3 设计时标注"规划中"，直到真接信飞项目、会话凭证不能落库时才落地。这是 atk 的一种工作方式——**真实项目接入驱动功能补完**。

---

## 6. 场景模型：宁简勿花

一个 scenario 长这样：

```yaml
scenario: 下单后可查询到待支付订单
module: order
priority: P0
tags: [smoke, order]
env: local
steps:
  - api:
      call: "POST /api/login"
      body: { username: "${username}", password: "${password}" }
      expect: { status: 200 }
      capture: { token: "data.token" }     # 提的变量供后续步骤用
  - api:
      call: "POST /api/orders"
      headers: { Authorization: "Bearer ${token}" }
      body: { skuId: "${test_sku}", qty: 2 }
      expect: { status: 200, "data.orderNo": not_null }
      capture: { orderNo: "data.orderNo" }
  - api:
      call: "GET /api/orders?orderNo=${orderNo}"
      headers: { Authorization: "Bearer ${token}" }
      expect: { status: 200, "data.list.0.status": 待支付 }
```

设计要点：

- **断言显式可读**。`expect: { status: 200, "data.orderNo": not_null }` 是一目了然的"业务该满足什么"，而不是藏在代码里的 `assert r.json()["data"]["orderNo"]`。
- **op 只支持 eq 和 not_null**。故意克制。`>`, `<`, `contains` 等可以做，但八成需求是判等和判非空；扩展 op 的成本（学习/误用/重写）远大于收益。
- **capture 用点路径**。`data.token` 提取出来存 `${token}`，后面步骤可以直接引用——构成"轻量级的场景内变量"。
- **UI 步骤是平行的**。`{ ui: { action, target, expect } }` 和 `{ api: { call, ... } }` 是平级结构，不存在"先 API 再 UI"或"先 UI 再 API"的隐式顺序。一个场景可以混用两类步骤（demo_app/ui_todo_flow.yaml 就是典型）。

### 6.1 优先级只用三档

`P0 / P1 / P2`，不是无限分级。原因：

- 多了之后没人会认真区分 P3 / P4 / P5。
- `plan` 命令用 P0 ≤ 阈值来切子集——`--priority P1` 表示"包含 P0 和 P1"。

---

## 7. Web 控制台（原型已完成）

设计思路详见 `docs/superpowers/specs/2026-08-26-web-console-design.md`，这里只讲为什么。

调研过三类成熟参考：MeterSphere（DB 中心全功能平台）、HttpRunnerManager/扬帆（YAML 引擎的 Web 壳）、AirtestIDE/case_auto_hub（UI 步骤卡片化）。三家都很好，但都把**数据库**当事实源——这违反 atk 的第 3 节原则。

所以控制台的正确定位是 **"YAML 的视图和编辑器"**，不引入第二套存储：

```
项目模式：atk console --port 8900
├── 浏览：目录即分类（scenarios/ 目录结构 = 模块树）
├── 编排：表单卡片 ⇄ YAML 源码双模式
│   ├── API 卡：call / headers / body / 断言 / 提取 / 重试
│   └── UI 卡：action / target / value / expect
├── 执行：at- 走执行管线，SSE 实时回传
├── 历史：reports/runs/ 列表 + 截图证据
├── 设置：environments / modules / schedules
└── 定时任务：config/schedules.yaml 增量生效

全局模式：atk console -g
└── 项目列表（注册表 ~/.atk/registry.db）+ 一键拉起子项目控制台
```

几个有意思的设计选择：

### 7.1 模块树 = 目录

不引入"模块元数据"做二级分类。`scenarios/xinfei/seal/a.yaml` 的目录就是分类。AI 和 git 都天然理解目录操作；任何元数据方案都得多一份映射。

### 7.2 双模式（表单 ⇄ YAML）

AI 生成的复杂 YAML 可能超出表单能力（嵌套条件、特殊 hooks），所以必须有源码模式兜底。切换时调用后端 `/api/render` 和 `/api/parse` 做双向转换。代价：注释会丢（pyyaml dump 限制），规格里已声明。

### 7.3 乐观锁防外部覆盖

保存带 mtime 校验：被 AI/CLI 同时改了同一文件时返回 409，前端弹"刷新或强制覆盖"。三方共写的正确处理。

### 7.4 跨项目用 uv tool install

```bash
# 一次性（已实测）
uv tool install --editable ".[console]" /Users/dongchen/ClaudeCodeProjects/auto_test_framework

# 之后任意工程
cd 你的项目
atk init && atk console
```

`--editable` 让框架代码改了不用重装；`[console]` extra 把 fastapi/uvicorn/apscheduler 一起装。`atk init` 走 `Path.cwd()`——和 `git init` 一个套路，"在哪个目录跑就是给哪个项目 init"。

### 7.5 全局模式的设计取舍

`atk console -g` 不会试图在一个进程里同时给 N 个项目供服务（单根路径的 FastAPI app 不好动态切 root）。它做的是 **聚合仪表盘**——显示所有已注册工程，点"打开"则**为该项目子起一个独立端口的服务**（`atk console --port <free> --project-root <p>`），返回 URL 供跳转。

实现上的小坑：`python -m atk.cli` 没有 `__main__`，子进程找不到入口；补了一个 `atk/__main__.py` 调 `main()`。

---

## 8. 定时任务

控制台进程内嵌 APScheduler；定义文件 `config/schedules.yaml`：

```yaml
tasks:
  - name: 夜间回归
    env: xinfei
    module: seal
    daily_at: "02:00"          # 或 cron: "0 */6 * * *"
    enabled: true
```

设计取舍：

- **控制台停了就不触发，不补跑**。launchd / crontab 级别托管留到发布里程碑评估。
- **触发走 `atk run --record-new`**。每次定时触发创建独立运行记录，进历史页可查；不写记录等于没跑。
- **忙时跳过**（已有任务在执行）。本机单人足够，不需要排队。

---

## 9. 失败定性 SLA（流程轨核心）

`docs/roles.md` 已详尽，这里只点出为什么这么设计：

- **suspect → 24h 强制定性**：AI 说"看着不对但没把握"的状态必须由 QA 落地。不允许沉默通过。
- **fail → 当日认领**：AI 附重现步骤和截图，Dev 当日处理。
- **blocked → 公告原因**：环境/权限问题连续两晚升级为环境事故，发布阻塞。

SLA 是流程轨的"硬约束"——没有它，AI 的 suspect 永远不会被处理，最终被悄悄改回 pass，整个质量门禁就废了。

---

## 10. 发布路线（验证提效后立项）

原型当前形态：fork 仓库 `feature/web-console` 分支 14 个提交，113 测试全绿。个人使用确认提效后启动发布里程碑：

```
pip 发包（atk + console dist 内嵌）
  │
  └─→ npm 元包评估（采用"npm + 平台二进制可选依赖"模式，对标 esbuild/turbo）
      └─→ 验证成本过高则退化为 pipx install + 文档引导
```

不在原型期做是因为：发布决策依赖于"谁会用 / 怎么用"的真实数据，原型期没数据。验证提效后再立项更稳。

---

## 11. 已知限制（不做 YAGNI 列表）

设计上有意识放弃的能力：

- **用户体系 / 鉴权 / 多租户**。本机单人够用。
- **用例内容入库**。任何形式的 DB 双写都破坏"YAML 是唯一事实源"原则。
- **错过触发的补偿执行**。控制台停机期间定时任务不补跑，简化实现。
- **CodeMirror 高亮编辑**。原型期 `<textarea class="mono">` 够用，待提效验证后补。
- **移动端适配**。开发场景不需要。

每条都对应一个"为什么不做"——因为"它会让 YAML-first 变得更复杂、或带来第二个事实源、或只在边缘场景才有价值"。

---

## 12. 文档索引

| 文档 | 内容 |
|---|---|
| `README.md` | 上手指南、命令清单、demo 介绍 |
| `docs/DESIGN.md`（本文） | 设计思路与权衡 |
| `docs/roles.md` | 流程轨：AI 协作角色与质量门禁规范 |
| `docs/superpowers/specs/2026-08-24-atk-design.md` | 总体设计规格（字段/接口/退出码） |
| `docs/superpowers/specs/2026-08-26-web-console-design.md` | Web 控制台规格（API/页面/存储） |
| `docs/superpowers/plans/2026-08-24-atk-m1.md` | M1 即时层实施计划 |
| `docs/superpowers/plans/2026-08-25-atk-m2.md` | M2 沉淀层实施计划 |
| `docs/superpowers/plans/2026-08-25-atk-m3-m4.md` | M3-M4 实施计划 |
| `docs/superpowers/plans/2026-08-26-web-console.md` | Web 控制台实施计划 |
| `.claude/skills/atk-smoke/SKILL.md` | Agent 冒烟工作流（演练步骤） |
