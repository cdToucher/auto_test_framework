---
name: atk-smoke
description: 变更驱动的自动化冒烟测试工作流。当开发者要求"跑冒烟/验证这次改动/发布前检查"，或提交涉及业务代码时使用。编排 atk 确定性命令完成影响面分析、回归复用与 UI 探索实测。
---

# atk 即时层冒烟工作流

原则：atk 负责确定性环节；你（AI）负责语义分析与 UI 实测；所有结论必须回填 atk 记录，禁止只口头汇报。

## 流程

1. **生成计划**：在被测仓库根目录执行
   `atk plan --base <基线> --head HEAD`
   一步拿到 run_id、reuse 场景清单与 unmapped 文件清单。若无 reuse 且 unmapped 有业务文件，
   基于补丁内容提出 1-5 条最关键的测试意图。对 unmapped 文件，用
   `git diff <基线>...HEAD -- <文件>` 阅读补丁内容自行判断可能影响的业务模块，
   并在后续 record 的 note 中注明推断依据。

2. **执行复用场景**：
   `atk run --record-to <run_id>`
   失败场景阅读输出中的断言详情定位。

3. **UI 意图实测**（每条意图）：
   - 用 ego-browser 打开目标环境页面，按意图操作；
   - 关键状态截图保存为 PNG 文件（如 `/tmp/opencode/atks-<slug>.png`）；
   - 回填：`atk record <run_id> --title "<意图>" --status pass|fail|suspect|blocked --note "<证据与根因>" --evidence <截图路径>`
   - 状态语义：pass=断言成立；fail=复现问题（note 必须含重现步骤）；suspect=疑似问题需人定性；blocked=环境/权限原因无法执行。

4. **出报告**：`atk report <run_id>`，向用户报告路径与结论摘要（重点讲 fail/suspect）。

## 禁止事项

- 不得跳过 record 直接口头宣称"测试通过"。
- 不得把 blocked 标成 pass。
- UI 操作遇到登录态缺失先走页面登录流程，仍不行则记 blocked 并说明。
