# atk 完整使用文档

> 适用版本：`pyproject.toml` 中 `version` 字段（当前 0.1.0）。命令以 `atk --help` 输出为准。

## 1. 这是什么

`atk`（AI 原生双层自动化测试框架）：即时层（变更驱动冒烟，不落库）+ 沉淀层（YAML 场景回归）。

核心分工：AI 干量产（生成/实测），人做判断（审断言、定性失败），机器守门（`gate`）。

## 2. 安装 / 升级 / 卸载（uv）

要求：Python ≥3.11，`uv --version` 可用。

### 2.1 给其他项目用

公共托管版（发布后，包名 `dev2atf`，命令仍是 `atk`）：

```bash
uv tool install dev2atf
uv tool install "dev2atf[console]"   # 带 Web 控制台
```

本地 editable 版（改框架代码即时生效，适合联调）：

```bash
# 基础版（API 冒烟全功能）
uv tool install --editable /path/to/auto_test_framework

# 带 Web 控制台
uv tool install --editable "/path/to/auto_test_framework[console]"
```

> 注意：`PACKAGE` 只能是一个参数，extra 必须写进路径引号里。
> 错误写法：`uv tool install --editable ".[console]" /path/...`（两个位置参数）。

装完即有全局 `atk`：

```bash
atk --help && which atk
```

editable 含义：指向源码目录，框架代码改了无需重装（已验证：`~/.local/share/uv/tools/atk/.../__editable__.atk-*.pth` 指向仓库 `atk/`）。

### 2.2 非 editable 安装（固定版本，适合 CI）

```bash
uv tool install /path/to/auto_test_framework
```

### 2.3 升级

```bash
uv tool upgrade dev2atf              # 公共托管版本（工具名跟包名走）
uv tool install --force --editable /path/to/auto_test_framework   # 本地版重装
uv tool list                        # 查看已装工具
```

### 2.4 卸载

```bash
uv tool uninstall dev2atf
```

### 2.5 故障排查

| 现象 | 处理 |
|---|---|
| `atk: command not found` | `uv tool list` 确认；检查 `~/.local/bin` 在 PATH |
| `atk console` 报缺 fastapi | 重装带 `[console]` 的版本 |
| editable 改代码不生效 | 确认装的是 `--editable` 版（看 `.pth` 文件）；`uv tool list` 对不上就 `--force` 重装 |

## 3. 新项目接入（3 步）

```bash
cd /path/to/你的项目
atk init        # 只建缺失文件，绝不覆盖：config/、scenarios/、fixtures/、reports/
```

1. 改 `config/environments.yaml`：环境地址 + 变量，敏感值走 `${env:VAR}`（不入库）：
   ```yaml
   staging:
     base_url: "https://staging.example.com"
     vars:
       username: testuser
       password: "${env:ATK_PASSWORD}"
   ```
2. 改 `config/modules.yaml`：被测代码路径 → 测试模块（fnmatch，首命中生效）：
   ```yaml
   modules:
     order:
       - "src/order/**"
   ```
3. 写场景到 `scenarios/<module>/xxx.yaml`，然后：
   ```bash
   atk validate && atk run --env staging
   ```

## 4. 命令详解（共 11 个）

| 命令 | 作用 | 常用示例 | 退出码 |
|---|---|---|---|
| `atk init` | 建骨架 | `atk init` | 0 |
| `atk validate` | 场景合法性+重名告警，提交前自查 | `atk validate` | 0 通过 / 1 有错误 |
| `atk context` | 输出变更上下文包（提交+补丁+模块+现有场景），供 Agent 起草 | `atk context --base main --context-out /tmp/ctx.md` | 0（含无变更）/ 2 git 错误 |
| `atk plan` | 建运行记录，输出复用清单 | `atk plan --base main --head HEAD` | 0 |
| `atk run` | 执行场景，HTML 报告，可选 JUnit，可并入记录 | `atk run --env staging --module order --tags smoke` | 0 通过 / 1 失败 / 2 受阻·配置 |
| `atk smoke` | 一键冒烟：plan→run→report→gate（函数复用），`--title` 整单命名 | `atk smoke --base main --env staging --title "下单链路"` | 0 放行 / 1 拦截 / 2 受阻 |
| `atk record` | 回填 UI 意图结论+截图证据 | `atk record <run_id> --title "..." --status pass --evidence a.png` | 0 / 2 记录不存在 |
| `atk review` | 开发整单确认（approve/reject，reject 必带 note，只告警不拦截） | `atk review <run_id> --by zhangsan --verdict approve` | 0 / 1 参数错误 / 2 记录不存在 |
| `atk report` | 渲染运行记录为 HTML | `atk report <run_id>` | 0 / 2 |
| `atk gate` | 合并门禁：变更一致+无失败+无未定性 | `atk gate <run_id>` | 0 放行 / 1 拦截 / 2 |
| `atk console` | Web 控制台（需 `[console]`） | `atk console` / `atk console -g` | — |

`run` 常用过滤：`--module`、`--tags a,b`（交集）、`--priority P1`（P0–P1 全跑）、`--junit out.xml`、`--record-to <id>`、`--record-new`。

`record` 的 `--status`：`pass`（断言成立）/ `fail`（复现问题，note 写重现步骤）/ `suspect`（疑似，需人定性，gate 拦截）/ `blocked`（环境原因，不拦截）。

## 5. 日常工作流

### 5.1 变更冒烟（每次 MR）

```bash
atk plan --base main --head HEAD        # 记 run_id，看 reuse 清单
atk run --record-to <run_id>            # 跑复用场景
# UI 意图 Agent 用 ego-browser 实测后：
atk record <run_id> --title "<意图>" --status pass --note "<证据>" --evidence <截图.png>
atk report <run_id>                     # 看报告
atk gate <run_id>                       # 门禁
```

Agent 编排版见 `.claude/skills/atk-smoke/SKILL.md`，CI 模板见 `.ci-examples/`。

### 5.1b AI 主导功能测试（推荐）

开发者只给功能描述或基线，AI 全程主导，开发只在两处介入确认：

```bash
atk smoke --base main --env staging --title "下单链路"   # 建单：拿 run_id，看复用结果
# 缺场景 → AI 按 atk-gen skill 补草稿（scenarios/<module>/gen-*.yaml）
# 介入点 1（停下）：开发审草稿 expect，通过再 atk run 实测
# AI 用 ego-browser 实测无覆盖意图并 atk record 回填
# 介入点 2（停下）：开发 atk review <run_id> --by xxx --verdict approve 整单确认（SLA 24h）
atk gate <run_id>                                        # 汇报门禁
```

规则：草稿只增不改；`review` 只告警不拦截（gate 判合并只看用例/定性/漂移）；
确认超时视为发布阻塞。详见 `docs/roles.md` 与 `.claude/skills/atk-smoke/SKILL.md`。

### 5.2 AI 补场景（context → YAML）

```bash
atk context --base main --context-out /tmp/ctx.md   # 落盘上下文包
# 对 Agent 说“为这次改动补测试场景”（走 atk-gen skill 起草到 scenarios/<module>/gen-*.yaml）
atk validate && atk run --tags ai-generated         # 校验+实测草稿
# 人工评审 expect 后入库
```

规则：只增不改旧文件；接口/字段必须来自 diff，禁臆造；`expect` 必须具体（码+业务字段）。

### 5.3 场景编写要点

```yaml
scenario: 下单后可查询订单
module: order
priority: P0            # P0 门禁必跑 / P1 夜间 / P2 周级
tags: [smoke]
env: staging            # 省略则 run 时用 --env 覆盖
data: fixtures/order.yaml
steps:
  - api:
      call: "POST /api/login"
      body: { username: "${username}", password: "${password}" }
      expect: { status: 200, data.token: not_null }
      capture: { token: "data.token" }
      retries: 2
```

变量优先级：`env < fixture < capture`；`api:` 立即生效，`ui:` 自然语言由 Agent 实测。详见 `docs/QUICKSTART_SCENARIOS.md`。

## 6. Web 控制台

```bash
atk console          # 当前目录项目，http://127.0.0.1:8900
atk console -g       # 全局模式，管理多工程
```

场景树浏览、表单/源码双模式编排（保存前强制校验）、SSE 实时执行、运行历史、定时任务（`config/schedules.yaml`，需控制台常驻，停机不补跑）。限制：表单保存丢注释。

## 7. 自带 Demo（练手）

```bash
.venv/bin/python -m examples.demo_app &   # :8766，alice/secret123
atk run --env demoapp --module todo       # 4/4 通过
```

`xinfei` 真实项目示例见 README“接入真实项目示例”节。
