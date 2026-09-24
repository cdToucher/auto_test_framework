---
name: atk-use
description: atk 使用说明（AI 必读）。任何时候需要知道 atk 怎么用、下一步该跑哪条命令、命令清单与退出码含义、envelope 字段、硬规则与人工介入点时，加载本文件。项目接入 atk 后由 atk init 生成。
---

# atk 使用说明（AI 必读）

> 本文件由 `atk init` 生成，是 atk 工作流的**单一说明书**：AGENTS.md 只留指向这里的
> 窄指针，命令清单与硬规则不在别处重复（两处各写一份必然漂移）。
> 项目私有的补充说明请写进 AGENTS.md 的 atk 区块之外——写在这里会在下次 init 被覆盖。

本项目接入 atk（AI 原生双层自动化测试框架）。{{LAYOUT}}
UI/E2E 实测工具：`{{UI_TOOL}}`。

## 怎么触发：不要背流程，问 atk

不知道下一步做什么，就执行 `atk agent --format json`，然后：

1. 读返回里的 `next` 数组，**按序执行第一条**；跑完再问一次（状态会变，命令会变）。
2. `requires_human: true` 时**停下**：把 `human_prompt` 原样转给开发者，拿到答复再继续。
   这是两个人工介入点（审 expect、整单确认）的机器可读形式，AI 不得自行代答。
3. `blockers` 非空先解阻塞（多半是没接入、场景库空、环境/凭据不可达），别硬往下跑。
4. `state` 是当前所处环节的机器可读标签，见下。
5. 全流程结论必须落进 atk 记录（`atk record` / `atk review`），禁止只口头汇报。

`state` 取值：`not_wired`（未接入或说明书/技能文件缺失）· `library_broken` ·
`no_scenarios` · `no_run` · `run_missing`（`--last` 指针失效）· `await_expect_review` ·
`ui_pending` · `run_blocked` · `cases_failing` · `gate_blocking` · `gate_unavailable` ·
`await_run_review` · `ready_to_merge`。

## 一键入口

```bash
atk agent --format json      # 我现在该跑什么？（AI 的主循环）
atk last --format json       # 最近一次运行记录是谁（--last 免拼 run_id）
```

## 常用命令

流程里的命令都支持 `--format json`（`init`/`purge`/`console` 例外），输出统一 envelope：

```bash
atk agent --format json                    # 不知道下一步跑什么就问它
atk smoke --title "<功能>" --base <基线> [--env <环境>] --format json
atk context --base <基线> --format json     # 变更上下文包（起草场景用）
atk validate                                # 场景库自查
atk run --dry-run                           # 先看会跑哪些场景，不执行
atk run [--env <env>] [--set k=v] [--record-new] [--skip-ui]
atk record --last --title "<意图>" --status <pass|fail|suspect|blocked> --note "<证据>"
atk record --last --from-json <result.json> # UI 实测批量回填（推荐）
atk review-draft <草稿.yaml> --by <人> --verdict approve|reject
atk review --last --by <人> --verdict approve
atk gate --last --format json
atk approvals mint <草稿.yaml|--run <run_id>>   # 只能由人在交互式终端执行
atk gate <run_id> --allow-unbound              # 明知故犯：判定一条未绑定基线的记录
atk approvals list                              # 看审批账本与待核销凭证
atk report --last                           # 渲染 HTML 报告
```

### envelope 字段

| 字段 | 含义 |
|---|---|
| `ok` / `exit_code` | 与进程退出码一致：0 通过 · 1 失败或拦截 · 2 受阻/配置错 |
| `next` | **可直接执行**的 atk 命令列表（散文在 `hint`），按序跑完再问 `atk agent`。已定位到某条 run 时命令里带的是**真实 run_id**，不是 `--last` |
| `blockers` | 阻塞原因列表，非空先处理它 |
| `requires_human` / `human_prompt` | 需要人判断时同时给出问题；AI 见此必须停下转述 |
| `failed_steps`（`run`/`smoke` 失败时） | 每条含 `scenario` / `step` / `detail`（哪条断言、实际值）/ `response`（整份实际返回，已脱敏截断） |

**报告失败必须带实际返回**：向人复述时引用 `failed_steps[].detail` 与 `response`，
不要只说"某场景失败"——那样没人能定位，只能重跑猜。

### 退出码约定

`0` 通过或放行 · `1` 失败/拦截（含 reject 缺 note）· `2` 受阻或配置错（环境不可达、
记录不存在、选中 0 个场景、verdict 非法、**场景库里有文件加载不了**）。
CI 只需 `test $? -le 1` 就能区分"业务不通过"与"没测成"。

坏 YAML 归 2 不归 1：那不是本次改动的产品回归，报成 1 会让流水线把库问题算到
被评审的提交头上。`plan` 与 `run` 走同一张投影表，两种 `--format` 同一个码。

**照 `next` 执行时不要把它换回 `--last`**：`.atk/last-run.json` 是全局单槽，
每次 plan/run/smoke 都覆盖它，两条 run 同时在飞（本地改一版、CI 跑同一版）时
`--last` 会指到最新那条，于是结论回填进别人的记录。`--last` 只给人手工用。

## 技能正文（按 atk agent 指示再读，不必一次读完）

{{SKILL_INDEX}}

## 硬规则

- 断言不止 status，必须断关键业务字段（`"< 90"`、`"contains: x"`、`"regex: ^SO"`、
  `"len: 1..5"`、`"type: str"`、`"in: a, b"`、`not_null`；全量语法与转义规则见 atk-authoring）。
- `ui:` 步骤 atk 不执行，必须由 AI 用 {{UI_TOOL}} 实测后 `atk record` 回填；未回填 `atk gate` 拦截。
  本次不测 UI 就显式 `atk run --skip-ui`（不登记待实测意图）。
- 环境/账号问题记 `blocked`，不得记 `fail`。
- 不得编造接口/字段；AI 草稿（tags 含 `ai-generated`）未经人工评审转正，gate 拦截。
- **转正的判据是内容，不是标签**：`config/approvals.yaml` 里以断言面 sha256 为键、
  并带**有效签名**的一条记录才算已批准。签名密钥在仓库外（`~/.atk/approval.key`，
  CI 用 `ATK_APPROVAL_KEY` 注入同一把），条目只能由 `atk approvals mint <文件>` 在
  **交互式终端**里核销凭证后写入。所以：删掉 `ai-generated` tag 不会转正；
  **手写一条 approvals.yaml 记录也不会转正**（没有效签名 = 不采信，gate 会明说
  "账本里有 N 条签名无效的条目"）；改一个断言就得重审；改注释不会撤销审批；
  没密钥时一律判未批。你在非终端 shell 里跑 `mint` 会 exit 2 —— 这是设计，不是故障。
  撞到 `未经人工审批` 时**停下来把命令原样转给开发者**去终端执行，不要试图绕过。
- **记录必须绑定基线才能当合并依据**：`base_ref` 为空的记录（如 `run --record-new`
  的产物）会被 `unbound_record` 拦下——它没描述"哪次变更被测过"。明知故犯用
  `atk gate --allow-unbound`（放行会在 warnings 里留痕）。
- 回填 status 无默认值，且 `pass` 必须带 `--note` 与**真实存在**的 `--evidence` 文件
  （缺文件 exit 2，无证据的 pass 不予登记）；实测是什么就记什么，不得把 blocked 写成 pass。
  `--from-json` 的 payload 必须自带 `run_id`，title 必须是本次运行登记过的意图
  （凭空补覆盖面要显式 `--allow-new-intent`）。
- 不要手改 `reports/runs/<id>/run.yaml`：当前态与 `events.yaml` 会被 gate 交叉校验
  （流水是 HMAC 链，逐条签名并链住上一条），改记录或改流水都会被判"结论不可信"。
- 已知的残余缺口（诚实声明，未修）：新场景若**先被提交**、再以那次提交或其后的引用当
  基线跑 plan，会被当成"早于基线存在"而享受沿袭豁免。锚定到受保护分支才能封掉，
  见 tests/test_forgery_gates.py::test_committed_before_the_base_is_grandfathered。
- 选中 0 个场景按受阻处理（exit 2），空跑不算通过。
- **影响面算不清就多跑**：有未映射的代码变更、`modules.yaml` 缺失或不可解析、拿不到
  git 基线时，`plan` 自动改成 `selection.mode = full_suite`（跑全库），绝不返回空选择。
  只改文档类文件不会触发这条。gate 会拦"变更落在已知模块却没有任何场景覆盖它"，
  所以补映射（`config/modules.yaml`，一个文件可命中多个模块）或补场景是必须的，
  `atk validate` 会双向对账并 exit 1。
- 已有等价场景时**复用不重写**：判重看 `calls` + `expects` 语义，不看场景名（细则见
  atk-authoring）。

## 后台与残留

`atk console -d` 起的是常驻进程，状态与日志在 `.atk/console-<端口>.{json,log}`；
用 `atk console --status` / `--logs` / `--stop` 管理，`--stop` 走进程组，会连带
全局模式下为各项目拉起的子控制台一起停。卸载前先 `--stop`。
