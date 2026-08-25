# atk — AI 原生双层自动化测试框架

即时层（变更驱动冒烟，不落库）+ 沉淀层（YAML 场景回归），设计详见
`docs/superpowers/specs/2026-08-24-atk-design.md`。

## M1 已有能力

- YAML 场景库：API 步骤确定性执行，UI 步骤占位（M2 接 ego-browser）
- 多环境配置 + `${var}` 变量替换（headers/body/URL）+ 步骤间变量捕获（场景间隔离）
- 断言：`status` / 点路径 `eq` / `not_null`；未显式断言 status 时隐式要求 status<400
- 环境错误与用例失败分类：非 JSON 响应、服务不可达等不会中断整批执行
- 坏场景文件逐个跳过并在报告/CLI 中提示，不连累其余场景
- CLI：`atk list` / `atk run`（`--env` 可省略，省略时按场景自身 `env` 字段路由）

**退出码**：`0`=全部通过；`1`=存在用例失败或场景加载错误（CI 门禁拦截）；
`2`=仅环境受阻（提示检查环境，不判用例失败）。

被测环境需经系统代理访问时，在 environments.yaml 对应环境下加 `trust_env: true`。
真实口令勿入库：vars 中的敏感值建议由 CI 注入临时配置文件（`${env:VAR}` 支持规划中）。

## 快速开始

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m examples.mock_server &   # 示例被测服务
.venv/bin/atk list
.venv/bin/atk run --env local                 # 打开 reports/report-latest.html 查看
```

## 场景编写

参考 `scenarios/demo/`。步骤分 `api:`（结构化，立即生效）与 `ui:`
（自然语言，M2 实现）。`expect` 是场景的灵魂，也是 QA 评审的核心对象。

```yaml
scenario: 下单后可查询到待支付订单
module: order
priority: P0            # P0=门禁必跑 / P1=夜间回归 / P2=周级全量
tags: [smoke, order]
steps:
  - api:
      call: "POST /api/login"
      body: { username: "${username}", password: "${password}" }
      expect: { status: 200, data.token: not_null }
      capture: { token: "data.token" }   # 捕获响应值供后续步骤使用
```

## M2 已有能力（即时层）

`atk diff` 影响面分析 → `atk plan` 生成运行计划 → `atk run --record-to <id>`
执行复用场景 → AI 用 ego-browser 实测新意图后 `atk record <id>` 回填 →
`atk report <id>` 输出统一 HTML 报告（含截图证据）。完整工作流见
`.claude/skills/atk-smoke/SKILL.md`；被测代码路径→测试模块的映射配置见
`config/modules.yaml`。

## 路线图

| 里程碑 | 内容 |
|---|---|
| M1 ✅ | 骨架 + API 执行器 + 场景库 + HTML 报告 |
| M2 ✅ | ego-browser UI 执行器（Agent 编排）+ 即时层全链路 |
| M3 | YAML→Playwright 固化流水线 + 自修复 + 夜间定时 + 门禁 bot |
| M4 | 角色规范文档定稿 + 团队试点 |
