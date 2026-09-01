# atk — AI 原生双层自动化测试框架

即时层（变更驱动冒烟，不落库）+ 沉淀层（YAML 场景回归），设计详见
`docs/superpowers/specs/2026-08-24-atk-design.md`，角色规范见 `docs/roles.md`。

核心思想：**AI 干量产的活（生成/实测/固化填充），人做判断的事（审断言、定性失败），机器守确定的门（gate）。**

## 功能总览

| 命令 | 作用 |
|---|---|
| `atk init` | 初始化项目结构（只建缺失文件，绝不覆盖） |
| `atk validate` | 场景库合法性校验（错误+重名告警），QA 提交前自查 |
| `atk diff` | git 变更 → 模块影响面 JSON |
| `atk gen` | diff + 提交记录 → AI 起草场景 YAML（`--llm` 直连大模型，或输出上下文包交给 Agent） |
| `atk plan` | 创建运行记录，输出复用场景清单与待补全意图 |
| `atk run` | 执行场景：HTML 报告 + 可选 JUnit XML + 可并入运行记录 |
| `atk record` | Agent 回填 UI 探索意图结论（pass/fail/suspect/blocked + 截图证据） |
| `atk report` | 渲染运行记录为统一 HTML 报告（含截图缩略） |
| `atk export` | 场景固化为自包含 pytest 脚本（API 确定性；UI 步骤留占位由 Agent 填充） |
| `atk doctor` | 固化脚本诊断分类：healthy/pending/repairable/suspect_bug/broken |
| `atk gate` | 合并门禁：变更一致性 + 用例失败 + 未定性三查 |

执行质量特性：环境错误自动重试（`retries` 默认 1）、非 JSON 响应保护、
场景级 fixtures 数据（`data:` 字段）、变量捕获场景间隔离、退出码三态
（0 通过 / 1 失败 / 2 受阻或配置问题）、`${env:VAR}` 敏感值注入
（口令/会话凭证不入库，加载期解析）。

## Web 控制台

```bash
atk console          # 项目模式（当前目录），浏览器打开 http://127.0.0.1:8900
atk console -g       # 全局模式：注册/管理多个 atk 工程，一键拉起各项目控制台
```

功能：场景树浏览、表单化编排（API/UI 步骤卡片 ⇄ YAML 源码双模式，保存前强制校验，
mtime 乐观锁防外部覆盖）、触发执行（SSE 实时日志）、运行历史与截图证据、
环境/模块配置编辑、**定时任务**（`config/schedules.yaml` 定义 cron 或"每天 HH:MM"，
APScheduler 到点自动执行并写入运行记录；控制台需常驻，停机不补跑）。
界面/CLI 触发的执行均带 `--record-new` 自动入历史。前端构建产物已入库，无需 Node 环境。

其他项目使用：`uv tool install --editable ".[console]" /path/to/auto_test_framework`
后即获得全局 `atk` 命令；目标项目内 `atk init && atk console`。

已知限制：表单保存重写 YAML 会丢失注释；定时任务需控制台常驻。

## Demo 被测系统

`examples/demo_app.py`（端口 8766）：待办任务管理，含登录鉴权、状态机
（pending→done，重复完成 409）、参数校验（空标题 422）与 Web 页面，
配套场景库在 `scenarios/demo_app/`——覆盖正向链路、负向断言与 UI 探索，
是框架各能力的端到端示例。

## 接入真实项目示例：信飞诉调系统

`scenarios/xinfei/` + `config/environments.yaml[xinfei]` 演示了对已部署
测试环境（https://xinfei-test.anmiai.com）的接入方式：Cookie+token 双凭证
经 `${env:VAR}` 注入，只读接口冒烟先行。日常用法：

```bash
export ATK_XF_COOKIE="token=...; orgId=..."   # 登录后从 DevTools 导出
export ATK_XF_TOKEN="dac581dc..."
atk run --env xinfei --module seal
```

## 快速开始

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/atk init                            # 生成配置与示例场景骨架
.venv/bin/python -m examples.mock_server &    # 示例被测服务
.venv/bin/atk validate && .venv/bin/atk run --env local
```

## 即时层工作流（变更驱动冒烟）

```
atk diff → atk plan → (Agent) atk run --record-to <id> + ego-browser 实测
        → atk record <id> → atk report <id> → atk gate <id>
```

完整编排规则见 `.claude/skills/atk-smoke/SKILL.md`；代码→模块映射见
`config/modules.yaml`。CI 接入模板见 `.ci-examples/`（GitLab / GitHub Actions，
含 MR 门禁 job 与夜间定时回归）。

## AI 起草场景（diff + 提交记录 → YAML）

```bash
# 方式一：框架直连 LLM（任意 OpenAI 兼容服务），生成后自动校验写入草稿
export ATK_LLM_API_KEY="..."                    # 可选 ATK_LLM_BASE_URL / ATK_LLM_MODEL
atk gen --llm --base <基线>                     # 草稿写入 scenarios/<module>/gen-*.yaml

# 方式二：输出变更上下文包，交给 AI Agent（如 ZCode，配合 .claude/skills/atk-gen）编写
atk gen --base <基线> --context-out /tmp/ctx.md
```

上下文包含：提交记录（含说明正文）、分文件补丁、模块归属、受影响模块的现有场景
（去重 + 风格对齐）。草稿自动加 `ai-generated` 标签与溯源头注释，绝不覆盖已有文件；
expect 断言需人工评审后再参与门禁。

```bash
atk validate && atk run --tags ai-generated     # 校验并实测草稿
```

## 场景编写

参考 `scenarios/demo/`。步骤分 `api:`（结构化，立即生效）与 `ui:`
（自然语言，即时层由 Agent 实测；沉淀层 export 后由 Agent 填充选择器）。
`expect` 是场景的灵魂，也是 QA 评审的核心对象。

```yaml
scenario: 下单后可查询订单
module: order
priority: P0            # P0=门禁必跑 / P1=夜间回归 / P2=周级全量
tags: [smoke]
data: fixtures/order.yaml   # 场景级测试数据，优先级 env < fixture < capture
steps:
  - api:
      call: "POST /api/login"
      body: { username: "${username}", password: "${password}" }
      expect: { status: 200 }
      capture: { token: "data.token" }   # 捕获响应值供后续步骤
      retries: 2                          # 仅环境类错误重试
```

## 路线图

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1 | 骨架 + API 执行器 + 场景库 + HTML 报告 | ✅ |
| M2 | Agent 编排 UI 执行器 + 即时层全链路 | ✅ |
| M3 | 固化流水线(export/doctor) + 门禁(gate) + CI 样例 | ✅ |
| M4 | 角色规范文档（docs/roles.md） | ✅ |
