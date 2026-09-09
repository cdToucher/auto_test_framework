---
name: atk-smoke
description: 功能级AI主导冒烟测试工作流。当开发者给出功能描述或基线要求验证功能时使用。AI全程主导：atk smoke建单→缺场景按atk-authoring协议补草稿→两处停下请开发确认→ego-browser实测回填→gate汇报。
---

# atk 功能级冒烟工作流（AI 主导）

入口：开发者只给功能描述或基线（base），AI 全程主导流程，开发只在两个介入点确认。

原则：atk 负责确定性环节（建单/执行/门禁）；你（AI）负责流程推进与 UI 实测；所有结论必须回填 atk 记录，禁止只口头汇报。

## 流程

1. **建单**：在被测仓库根目录执行
   `atk smoke --title "<功能描述>" --base <基线> [--env <环境>]`
   拿到 run_id 与复用场景执行结果（plan→run→report→gate 一次完成）。

2. **缺场景补草稿**：若 smoke 输出显示无复用覆盖或关键意图缺失，
   按 atk-authoring 协议调 `atk context` 补 API/E2E 草稿（`scenarios/<module>/gen-*.yaml`，只增不改）。

3. **介入点 1——审 expect（停下）**：向开发展示草稿路径与 expect 清单，
   请开发评审断言业务正确性；评审结论用命令落盘（approve 去 tag 转正，reject 移走留档）：
   `atk review-draft <草稿路径> --by <评审人> --verdict approve|reject [--note ...]`
   （reject 必须带 note）。未通过不得入库，未转正草稿参与执行会被 gate 拦截。

4. **UI 意图实测**（每条无覆盖意图）：
   - 用 ego-browser 打开目标环境页面，按意图操作；
   - 关键状态截图保存为 PNG 文件（如 `/tmp/opencode/atks-<slug>.png`）；
   - 回填：优先 `atk record <run_id> --from-json <result.json>`；也可用 `atk record <run_id> --title "<意图>" --status pass|fail|suspect|blocked --note "<证据与根因>" --evidence <截图路径>`
   - 状态语义：pass=断言成立；fail=复现问题（note 必须含重现步骤）；suspect=疑似问题需人定性；blocked=环境/权限原因无法执行。

5. **介入点 2——整单 review（停下）**：实测全部回填后，请开发整单确认
   `atk review <run_id> --by <确认人> --verdict approve|reject [--note ...]`（reject 必须带 note）；
   确认 SLA 24h，超时视为发布阻塞。

6. **汇报 gate**：`atk gate <run_id>`，向用户报告路径与结论摘要（重点讲 fail/suspect）。
   gate 只读开发确认状态（告警不拦截，见 roles.md）。

## 禁止事项

- 不得跳过 record 直接口头宣称"测试通过"。
- 不得把 blocked 标成 pass。
- UI 操作遇到登录态缺失先走页面登录流程，仍不行则记 blocked 并说明。
