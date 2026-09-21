<!-- atk:begin -->
## atk 自动化测试（AI 入口）

本项目接入 atk（AI 原生双层自动化测试框架）。

- **做任何 atk 相关工作前，先完整阅读 `.atk/atk_use.md`**（说明书：触发循环、命令清单、envelope 字段、硬规则都在那里）。
- 技能正文：`.atk/skills/atk-authoring/SKILL.md`、`.atk/skills/atk-smoke/SKILL.md`。
- UI/E2E 实测工具：`ego-browser`。
- 不知道下一步跑什么：`atk agent --format json`，按 `next` 走；返回 `requires_human: true` 必须停下问人。
<!-- atk:end -->
