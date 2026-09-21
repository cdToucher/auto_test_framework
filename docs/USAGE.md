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

## 4. 命令详解（共 15 个）

| 命令 | 作用 | 常用示例 | 退出码 |
|---|---|---|---|
| `atk init` | 建骨架（产物收束 `.atk/`：说明书 `atk_use.md` + skill 正文；AGENTS.md 只写入口指针） | `atk init --url <base_url> --modules order` | 0 |
| `atk agent` | 状态机入口：现在该跑哪条命令、卡在哪、要不要问人（AI 用 `--format json`） | `atk agent --format json` | 0 有下一步 / 2 阻塞 |
| `atk validate` | 场景合法性+重名告警，提交前自查 | `atk validate` | 0 通过 / 1 有错误 |
| `atk context` | 输出变更上下文包（提交+补丁+模块+现有场景），供 Agent 起草 | `atk context --base main --context-out /tmp/ctx.md` | 0（含无变更）/ 2 git 错误 |
| `atk plan` | 建运行记录，输出复用清单；Agent 可用 `--format json` | `atk plan --base main --head HEAD --format json` | 0 |
| `atk run` | 执行场景，HTML 报告，可选 JUnit，可并入记录 | `atk run --env staging --module order --tags smoke` | 0 通过 / 1 失败 / 2 受阻·配置 |
| `atk smoke` | 一键冒烟：plan→run→report→gate（函数复用），`--title` 整单命名 | `atk smoke --base main --env staging --title "下单链路"` | 0 放行 / 1 拦截 / 2 受阻 |
| `atk record` | 回填 UI 意图结论+截图证据；Agent 可用 `--from-json` | `atk record <run_id> --from-json result.json` | 0 / 1 fail/suspect 缺 note / 2 记录不存在 |
| `atk review` | 开发整单确认（approve/reject，reject 必带 note，只告警不拦截） | `atk review <run_id> --by zhangsan --verdict approve` | 0 确认成功 / 1 reject 缺 --note / 2 verdict 非法·记录不存在 |
| `atk review-draft` | 评审AI草稿（approve 去 tag 转正，reject 移走留档，未转正进 gate 拦截） | `atk review-draft scenarios/demo/gen-x-1.yaml --by qa --verdict approve` | 0 评审落盘 / 1 reject 缺 --note / 2 verdict 非法·非草稿 |
| `atk last` | 看最近一次运行记录上下文（`.atk/last-run.json`），Agent 用它确认操作对象 | `atk last --format json` | 0 / 2 暂无记录 |
| `atk report` | 渲染运行记录为 HTML | `atk report <run_id>` | 0 / 2 |
| `atk gate` | 合并门禁：变更一致+无失败+无未定性+无未评审草稿；支持 `--format json` | `atk gate <run_id> --format json` | 0 放行 / 1 拦截 / 2 |
| `atk purge` | 删除本项目 atk 生成物料（手改过的保留并提示；`--with-reports` 才删历史） | `atk purge --yes` | 0 |
| `atk console` | Web 控制台（需 `[console]`）；`-d` 后台常驻，`--status/--logs/--stop` 管理 | `atk console -d` / `atk console --stop` | 0 运行中·已停 / 1 未在跑 / 2 端口占用 |

### 4.1 AI 怎么触发（envelope）

`--format json` 的输出统一平铺一层契约（只增键、不改老键）：

| 字段 | 含义 |
|---|---|
| `state` | `agent` 独有，当前卡在哪一步：`not_wired`（未接入/说明书缺失）· `library_broken` · `no_scenarios` · `no_run` · `run_missing`（--last 指针失效）· `await_expect_review` · `ui_pending` · `run_blocked` · `cases_failing` · `gate_blocking` · `gate_unavailable` · `await_run_review` · `ready_to_merge` |
| `ok` / `exit_code` | 与进程退出码一致：0 通过 · 1 失败或拦截 · 2 受阻/配置错 |
| `next` | **可直接执行**的 atk 命令列表（散文一律放 `hint`），按序跑完再问一次 `atk agent` |
| `blockers` | 阻塞原因，非空先处理它 |
| `requires_human` / `human_prompt` | 两个人工介入点（审 expect、整单确认）的机器可读形式；AI 见此必须停下转述，不得代答 |

`run` 常用过滤：`--module`、`--tags a,b`（交集）、`--priority P1`（P0–P1 全跑）、`--junit out.xml`、`--record-to <id>`、`--record-new`。

`record` 的 `--status`：`pass`（断言成立）/ `fail`（复现问题，note 写重现步骤）/ `suspect`（疑似，需人定性，gate 拦截）/ `blocked`（环境原因，不拦截）。`fail/suspect` 必须带 note；AI 回填推荐 `--from-json`。

## 5. 日常工作流

### 5.1 变更冒烟（每次 MR）

```bash
atk plan --base main --head HEAD --format json  # 记 run_id，看 reuse 清单；Agent 推荐 JSON
atk run --record-to <run_id>            # 跑复用场景
# UI 意图 Agent 用 ego-browser 实测后：
atk record <run_id> --title "<意图>" --status pass --note "<证据>" --evidence <截图.png>
atk report <run_id>                     # 看报告
atk gate <run_id>                       # 门禁
```

Agent 编排版见 `.atk/skills/atk-smoke/SKILL.md`，CI 模板见 `.ci-examples/`。

### 5.1b AI 主导功能测试（推荐）

开发者只给功能描述或基线，AI 全程主导，开发只在两处介入确认：

```bash
atk smoke --base main --env staging --title "下单链路"   # 建单：拿 run_id，看复用结果
# 缺场景 → AI 按 atk-authoring 协议补草稿（scenarios/<module>/gen-*.yaml）
# 介入点 1（停下）：先 atk run --tags ai-generated 实测草稿，再向开发展示 expect 清单评审，
# 评审结论用 atk review-draft 落盘（approve 去 tag 转正），未转正不得入库
# AI 用 ego-browser 实测无覆盖意图并 atk record --from-json 回填
# 介入点 2（停下）：开发 atk review <run_id> --by xxx --verdict approve 整单确认（SLA 24h）
atk gate <run_id>                                        # 汇报门禁
```

规则：草稿只增不改；`review` 只告警不拦截（gate 判合并只看用例/定性/漂移）；
确认超时视为发布阻塞。详见 `docs/roles.md` 与 `.atk/skills/atk-smoke/SKILL.md`。

### 5.2 AI 补场景（context → YAML）

```bash
atk context --base main --context-out /tmp/ctx.md   # 落盘上下文包
# 对 Agent 说“为这次改动补测试场景”（按 atk-authoring 协议起草到 scenarios/<module>/gen-*.yaml）
atk validate                                        # 校验草稿
atk run --tags ai-generated                         # 先实测草稿（技术正确性）
atk review-draft <草稿> --by qa --verdict approve   # 再评审转正（去 ai-generated tag，需人定性 expect）
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
atk console            # 项目模式（当前目录），前台运行并打印地址，Ctrl-C 停止
atk console -d         # 后台常驻，立即返回地址；定时任务要靠它才真能触发
atk console -g         # 全局模式，管理多工程
atk console --status   # 列出在跑的；没有在跑的 exit 1
atk console --logs     # 后台日志尾部（--port 指定其一）
atk console --stop     # 停止；不带 --port 则停掉该模式下全部
atk console --port 0 -d  # 自动挑空闲端口（8900 被占时用这个）
```

场景树浏览、表单/源码双模式编排（保存前强制校验）、SSE 实时执行、运行历史、定时任务（`config/schedules.yaml`，需控制台常驻，停机不补跑）。限制：表单保存丢注释。

### 6.1 控制台运行边界

- 仅监听回环：`atk console` 固定绑定 `127.0.0.1`，不对外暴露；需远程访问请自行经 SSH 端口转发。
- 本机控制台不做登录鉴权；被测服务的 Cookie、token 等凭据不应作为控制台口令使用。
- 被测服务凭据在 `config/environments.yaml` 以 `${env:VAR}` 引用，启动控制台前注入环境变量；控制台后续启动的 `atk run` 会继承这些变量，例如：`ATK_XF_COOKIE="..." ATK_XF_TOKEN="..." atk console`。
- 路径收敛：场景读写、运行报告、证据、fixture 解析均 `resolve()` 后校验落在工程根内，越界返回 404/配置错误。

### 6.2 后台进程与残留

- 状态与日志：项目模式落在 `<工程根>/.atk/console-<端口>.{json,log}`，全局模式落在 `~/.atk/`；
  按端口分文件，所以可以同时跑多个控制台。
- 停止走进程组：`-d` 起的控制台自成会话（`start_new_session`），全局模式点"打开"为某项目拉起的
  子控制台继承同一进程组，`--stop` 一次全带走。原来这些子进程 stdout 进 DEVNULL、无人回收，
  父进程一退就被 launchd 收养，端口能占到重启（实测留过一个跑了 12 天的孤儿进程）。
- 前台起的、以及被"打开"拉起的子控制台也各自登记一条状态（`pgid=0`，只许单进程 kill，
  绝不 killpg——它和用户的终端同组），因此 `--status` 看得见它们，`--stop --port <端口>` 收得掉。
- 被 `kill -9` 留下的失效状态文件不必手工清：下次 `--status/--stop` 探到进程已不在就顺手删掉。
- 卸载 atk 前先 `atk console --stop`：`scripts/uninstall_atk.sh` 会停进程、清 `~/.atk`，
  但只 `uv tool uninstall` 是清不干净的。

## 7. 自带 Demo（练手）

```bash
.venv/bin/python -m examples.demo_app &   # :8766，alice/secret123
atk run --env demoapp --module todo       # 4/4 通过
```

`xinfei` 真实项目示例见 README“接入真实项目示例”节。
