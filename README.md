# atk — AI 原生双层自动化测试框架

即时层（变更驱动冒烟，不落库）+ 沉淀层（YAML 场景回归），设计详见
`docs/superpowers/specs/2026-08-24-atk-design.md`。

## M1 已有能力

- YAML 场景库：API 步骤确定性执行，UI 步骤占位（M2 接 ego-browser）
- 多环境配置 + `${var}` 变量替换（headers/body/URL）+ 步骤间变量捕获
- 断言：`status` / 点路径 `eq` / `not_null`；无显式断言时隐式要求 status<400
- 环境错误与业务失败分类，HTML 报告输出
- CLI：`atk list` / `atk run`

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

## 路线图

| 里程碑 | 内容 |
|---|---|
| M1 ✅ | 骨架 + API 执行器 + 场景库 + HTML 报告 |
| M2 | ego-browser UI 执行器 + 即时层全链路（diff→plan→run→report） |
| M3 | YAML→Playwright 固化流水线 + 自修复 + 夜间定时 + 门禁 bot |
| M4 | 角色规范文档定稿 + 团队试点 |
