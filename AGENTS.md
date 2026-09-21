<!-- atk:begin -->
## atk 自动化测试工作流（AI 必读）

本项目接入 atk（AI 原生双层自动化测试框架）。场景库 `scenarios/`、配置 `config/`、产物 `reports/`（旧布局）。
UI/E2E 实测工具：`ego-browser`。

**执行任何 atk 工作流前，先完整阅读下列说明文件：**

- `.atk/skills/atk-authoring/SKILL.md` —— 根据 atk context/plan 的确定性上下文，为一次需求或提交起草 API 与 E2E 场景。用于 TUI/CLI Agent 编排，不是 atk CLI 命令；LLM 调用留在 Agent 层，atk 只负责上下文、校验、执行、记录和门禁。
- `.atk/skills/atk-smoke/SKILL.md` —— 功能级AI主导冒烟测试工作流。当开发者给出功能描述或基线要求验证功能时使用。AI全程主导：atk smoke建单→缺场景按atk-authoring协议补草稿→两处停下请开发确认→浏览器实测回填→gate汇报。

常用命令（`--last` 免拼接 run_id；`--format json` 输出含 `next` 的机器可读结果）：

```bash
atk smoke --title "<功能>" --base <基线> [--env <环境>] --format json
atk context --base <基线> --format json     # 变更上下文包（起草场景用）
atk validate                                # 场景库自查
atk run --dry-run                           # 先看会跑哪些场景，不执行
atk run [--env <env>] [--set k=v] [--record-new] [--skip-ui]
atk record --last --title "<意图>" --status <pass|fail|suspect|blocked> --note "<证据>"
atk review-draft <草稿.yaml> --by <人> --verdict approve|reject
atk review --last --by <人> --verdict approve
atk gate --last --format json
```

硬规则：
- 断言不止 status，必须断关键业务字段（支持 `< 90`、`contains: x`、`regex:`、`len:`、`type:`）。
- `ui:` 步骤 atk run 不执行，必须由 AI 实测后 `atk record` 回填；未回填 `atk gate` 拦截。
- 回填 status 无默认值：实测是什么就记什么，不得把 blocked 写成 pass。
- 不得编造接口/字段；AI 草稿（tags 含 ai-generated）未经人工评审转正，gate 拦截。
- 选中 0 个场景按受阻处理（exit 2），空跑不算通过。
<!-- atk:end -->
