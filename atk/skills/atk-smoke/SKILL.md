---
name: atk-smoke
description: 开发者给一句功能描述或基线、要求验证功能时使用（"帮我测下这次改动"也算）。AI 主导整轮冒烟：建单→按 atk agent 的 next 推进→UI 实测回填→人工确认→报门禁。
---

# atk 功能级冒烟（AI 主导）

前置：先完整读 `.atk/skills/atk-use/atk_use.md`（命令清单、envelope 字段、硬规则都在那份里，
本文不重复）。这里只讲一轮冒烟里 AI 怎么推进，以及 UI 实测的判据。

## 循环

1. 建单：`atk smoke --title "<功能描述>" --base <基线> [--env <环境>] --format json`
   它一次做完 plan→run→report→gate。
   **`gate_skipped: true` / `gate: null` 表示"没测成"**（run 异常或选中 0 个场景，exit 2），
   不是"通过"——按受阻处理，别往下报结论。
2. 之后不要自己排流程：`atk agent --format json`，照 `next` 逐条执行，跑完再问一次。
   它会把待实测意图、未评审草稿、该谁确认按序排好（状态会变，命令会变）。
3. `requires_human: true` 就**停下**，把 `human_prompt` 原样转给开发，拿到答复再继续。
   审 expect 与整单确认是人的判断，AI 不得代答。
4. `blockers` 非空先解阻塞（多半是环境/凭据不可达、场景库为空、未接入），别硬往下跑。

## UI 意图实测

- 清单来源：smoke 返回的 `intents_pending`，即 `atk run` 为含 `ui:` 步骤的场景自动登记的
  pending 意图；**意图标题就是场景的 `scenario` 名**，回填时 `--title` 要一字不差对上，
  否则等于新开一条意图、原来那条仍挂 pending。
- 用 {{UI_TOOL}} 打开**同一个 env** 的 `base_url`（见 `config/environments.yaml` 或
  `.atk/environments.yaml`），按意图标题操作；意图是业务语言，不是坐标脚本。
- 关键状态截图必须真落到磁盘（如 `/tmp/atk-<slug>.png`）：`atk record` 会跳过不存在的证据
  文件，并在返回里给 `evidence_missing`。这个字段非空说明你贴错了路径，补好再回填一次。
- 状态语义，实测是什么记什么：
  - `pass` 断言成立
  - `fail` 复现了问题（note 必须含重现步骤，否则 record 直接 exit 1）
  - `suspect` 疑似问题、需人定性（record 会返回 `requires_human`，gate 拦到人说）
  - `blocked` 环境/权限原因没能执行——不得写成 pass，也不得记成 fail
- 多条意图用批量回填，省得逐条拼参数：
  `atk record --last --from-json /tmp/atk-ui-result.json`（JSON 形状见 atk-authoring）。
- 本轮确实不做 UI 验证，才用 `atk run --skip-ui`：它**连待实测意图都不登记**，
  等于对外声明这次没测 UI，别拿它绕 gate。

## 收尾

两条 next 走完（`review` → `gate`）后，把 gate 的 `blocking` / `warnings` 原文摘要给人，
重点讲 fail 与 suspect。合不合并由人和 CI 判，AI 不替人下"可以合并"的结论。

## 禁止

- 跳过 `atk record` 口头宣称"测试通过"。
- 登录态缺失就硬猜结论：先走页面登录流程，仍不行记 `blocked` 并说明缺什么。
- 为了跑绿去改场景断言、把 `blocked` 调成 `pass`。
- 靠"删掉 `ui:` 步骤"或把草稿文件移走来让 gate 放行：门禁同时看运行记录里落盘的 `draft`
  标记与磁盘现状，记录时点是草稿、文件又不见了，**照样算未评审**，只会多一条拦截。
- 手工把 YAML 里的 `ai-generated` 摘掉来"转正"：转正只认 `atk review-draft --verdict approve`
  这一条路（它会去掉 tag 并留下评审记录）。摘标目前骗得过门禁，但那是在伪造评审结论，
  人查记录时发现没有对应 `review-draft`，整单结论一起作废。
