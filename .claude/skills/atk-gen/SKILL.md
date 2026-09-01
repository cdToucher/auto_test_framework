---
name: atk-gen
description: 根据 git diff 与提交记录自动起草回归场景 YAML。当开发者要求"为这次改动补测试场景/根据提交生成用例/沉淀场景"时使用。编排 atk gen 产出变更上下文包，由 AI 编写场景并落库校验。
---

# atk AI 场景起草工作流（diff + 提交记录 → YAML）

原则：atk 负责确定性的上下文收集与校验；你（AI）负责把变更翻译成可执行的场景；
生成的场景一律是**草稿**——expect 断言必须人工评审后才算入库。

## 流程

1. **收集上下文**：在被测仓库根目录执行
   `atk gen --base <基线> --head HEAD [--context-out /tmp/atks-ctx.md]`
   输出即"变更上下文包"：提交记录（含说明正文）、分文件补丁、模块归属、
   受影响模块的现有场景清单（禁止重复 + 风格对齐）。

2. **编写场景**：基于上下文包，遵守以下规则编写 YAML：
   - 只针对 diff 涉及的行为：新接口/新参数/新分支各至少一条，
     正常链路 + diff 中有依据的异常分支（如 409 冲突、422 校验）；
   - `module` 必须取自上下文包的模块归属；`scenario` 名不得与现有场景重复；
   - 请求路径、字段名、示例值必须来自 diff 或提交说明，禁止臆造接口；
   - `expect` 必须具体（响应码 + 关键业务字段），多步骤用 `capture` 传递状态；
   - `env` 沿用该模块现有场景的 env；`tags` 加 `ai-generated`；
   - 文件写到 `scenarios/<module>/gen-<head短哈希>-N.yaml`。

   也可以直接 `atk gen --llm` 让框架调用 LLM 自动生成并写入草稿
   （需 `export ATK_LLM_API_KEY=...`，可选 `ATK_LLM_BASE_URL`/`ATK_LLM_MODEL`
   指向任意 OpenAI 兼容服务），你只负责评审产出。

3. **校验落库**：`atk validate` 通过后，向用户展示草稿路径与 expect 清单，
   请人确认；确认后可 `atk run --tags ai-generated` 实测。

## 禁止事项

- 不得修改既有场景文件（起草只增不改）。
- 不得跳过 validate 直接宣称"场景已入库"。
- 不得为 diff 中不存在的接口/字段编造步骤。
