---
name: atk-smoke
description: 开发者给一句功能描述或基线、要求验证功能时使用（"帮我测下这次改动"也算）。AI 主导整轮冒烟：建单→按 atk agent 的 next 推进→UI 实测回填→人工确认→报门禁。
---

# atk 功能级冒烟（AI 主导）

前置：先完整读 `.atk/atk_use.md`。命令、envelope 字段、硬规则都在那份里，本文不重复。

## 循环

1. 建单并跑完 API 层：
   `atk smoke --title "<功能描述>" --base <基线> [--env <环境>] --format json`
2. 之后不要自己排流程：`atk agent --format json`，照 `next` 逐条执行，跑完再问一次。
   它会把待实测意图、未评审草稿、该谁确认按序排好。
3. `requires_human: true` 就停下，把 `human_prompt` 原样转给开发，拿到答复再继续。
   这两处（审 expect、整单确认）是人的判断，AI 不得代答。
4. `blockers` 非空先解阻塞（多半是环境/凭据/场景库为空），别硬往下跑。

## UI 意图实测（next 里每条 atk record 就是一条意图）

- 用 {{UI_TOOL}} 打开目标环境，按**意图标题**操作；意图是业务语言，不是坐标脚本。
- 关键状态截图存成 PNG（如 `/tmp/atk-<slug>.png`），回填时 `--evidence` 指向它。
- 状态语义，实测是什么记什么：
  - `pass` 断言成立 · `fail` 复现了问题（note 必须含重现步骤）
  - `suspect` 疑似问题，需人定性（atk agent 会因此 requires_human）
  - `blocked` 环境/权限原因没能执行——不得写成 pass，也不得记成 fail
- 多条意图用批量回填，省得逐条拼参数：
  `atk record --last --from-json /tmp/atk-ui-result.json`（JSON 形状见 atk-authoring）

## 收尾

两条 next 走完（`review` → `gate`）后，把 gate 的 `blocking`/`warnings` 原文摘要给人，
重点讲 fail 与 suspect。合不合并由人和 CI 判，AI 不替人下"可以合并"的结论。

## 禁止

- 跳过 `atk record` 口头宣称"测试通过"。
- 登录态缺失就硬猜结论：先走页面登录流程，仍不行记 `blocked` 并说明缺什么。
- 为了跑绿去改场景断言或把 `blocked` 调成 `pass`。
