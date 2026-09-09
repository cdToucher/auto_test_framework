# AI 协作角色与质量门禁规范

> 本规范是 atk 框架"流程轨"的正式定义，与工具轨（atk CLI）配套生效。
> 核心命题：AI 打破了原有角色分工，但业务判断与质量定性仍有门槛——
> 因此让 AI 干量产的活，让人做判断的事，让机器守确定的门。

## 1. 角色分工

| 角色 | 传统模式 | atk 模式 | 日常动作 |
|---|---|---|---|
| **Dev** | 写代码+凭感觉自测 | 审 expect + 整单确认 | 审草稿场景的 `expect`（介入点1）；`atk review` 整单确认（介入点2，SLA 24h） |
| **QA** | 手工点测+追着版本跑 | 业务断言的评审者 | `atk validate` 自查场景；评审 YAML 中 `expect` 的业务正确性；对 suspect/fail 做定性（SLA 24h） |
| **AI Agent** | 无定位 | 流程主导者 | 给定功能描述或基线即全程主导：`atk smoke` 建单→缺场景调 atk-gen 补草稿→ego-browser 实测回填→提请两处确认→汇报 gate（一切产出均为草稿） |
| **CI/bot** | 只做构建 | 确定性门禁 | 执行 `gate`；拦截用例失败/未定性/变更漂移；归档 JUnit 与 HTML 报告 |

## 2. 能力边界三原则

1. **AI 产出一律是草稿。** 场景由 AI 按 atk-gen skill 根据 `atk context` 上下文包起草、QA 审定。
2. **确定性判断交给编译产物。** 合并与否由 `atk gate` 基于结构化记录判定，AI 不直接决定合并；断言是显式 `expect`，不是 AI 的自由发挥。
3. **不确定就标"疑似"，强制人工定性。** Agent 实测结论只允许 pass / fail / suspect / blocked 四态；suspect 与 fail 进入 gate 拦截清单，禁止静默通过。

## 3. 质量门禁规则

| 卡点 | 判定命令 | 通过条件 |
|---|---|---|
| MR 合并 | `atk gate <run_id>` | 变更未漂移 + 无用例失败 + 无 fail/suspect 未定性 |
| 发布前 | 夜间流水线全绿 | P0/P1 全过 + 无未定性 suspect |
| 场景入库 | `atk validate` + `atk review-draft` | 文件合法 + 草稿已转正（去 `ai-generated` tag，未转正参与执行 gate 直接拦截） |

退出码语义：`0` 放行；`1` 拦截（用例失败 / 未定性 / 变更漂移）；`2` 配置或记录缺失（不判人失败）。

## 4. 日常节奏

- **开发日**：Dev 提交 → CI 自动 plan→run→gate（MR 流水线）；Agent 按 SKILL.md 补 UI 意图实测并 record。
- **夜间（22:00）**：定时任务跑当日变更冒烟 + P1 回归；suspect 出报告交 QA（SLA 24h 定性）。
- **每周**：P2 全量回归；QA 抽查 AI 自修复 diff；场景库去重（validate 重名告警清理）。

## 5. 失败定性与确认 SLA

- `suspect`：QA 24 小时内定性为 bug 或误报；超时未处理视为发布阻塞。
- `fail`：Agent 已附重现步骤与证据截图，Dev 当日认领。
- `blocked`：不计失败但要公示原因（环境/权限），连续两晚 blocked 升级为环境事故。
- 整单确认：开发收到 AI 提请后 24h 内完成 `atk review`（approve/reject）；超时视为发布阻塞，gate 只读告警。

## 6. 禁止事项

- 不得跳过 record 直接口头宣称"测试通过"。
- 不得把 blocked 改标 pass。
- 不得在场景 vars 中提交真实口令（走 CI 注入）。

## 7. 控制台安全边界（最小可用）

- 控制台只绑 `127.0.0.1`，默认无鉴权仅回环可用；对外暴露必须配 `--token`/`ATK_CONSOLE_TOKEN` 并经 `X-Auth-Token` 校验。
- 口令只存在于启动环境与调用方请求头，不入库、不进前端包；未设口令时启动警告“仅回环无鉴权”。
- 越界读取一律拒绝：报告/证据/scenario/fixture 均收敛工程根内，违例记 404 或配置错误（单场景失败不杀整个 run）。
