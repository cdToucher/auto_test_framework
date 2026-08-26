# atk Web 控制台设计（原型优先）

日期：2026-08-26
状态：已确认（方案 A）
前置调研：MeterSphere（DB 为中心的全功能平台）、HttpRunnerManager/扬帆（YAML 引擎的 Web 壳）、AirtestIDE/case_auto_hub（UI 步骤卡片+截图证据）

## 背景与定位

atk 的命脉是"YAML 文件 = 唯一事实源"（git 管版本、AI 直接读写、CLI 确定性执行）。
成熟测试平台均以数据库为中心，与 AI 协作模式冲突。因此本控制台的定位：

> **文件的视图与编辑器，而非另一套存储。** AI、CLI、Web 三方共写同一份资产。

使用模型（对齐 codegraph）：
- 项目内 `atk init` 已有配置即被识别；`atk console` 启动单项目视图
- `atk console -g` 启动全局聚合视图（所有注册项目的用例与历史）

**交付策略：原型优先。** 先跑通全链路可用，视觉与交互细节后续迭代；
个人使用确认提效后，再进入发布里程碑（见 §7）。

## §1 总体架构

```
atk console --port 8900        # 项目模式：根路径 = 当前项目
atk console -g --port 8900     # 全局模式：聚合 ~/.atk/registry.db 中全部项目
```

- 同一个 FastAPI 应用两种挂载，共用一套 Vue3 SPA（dist 预构建随包分发，用户侧零 Node 依赖）
- 后端直接复用现有 atk 库：scenario loader / validator / runner / run_store / env loader

## §2 存储

| 层 | 位置 | 内容 |
|---|---|---|
| 项目内 | `scenarios/**/*.yaml` `config/*.yaml` `reports/runs/<run_id>/` | 不变，唯一事实源 |
| 全局 | `~/.atk/registry.db`（SQLite） | projects(path,name,last_seen_at)、runs_index(run_id,project_path,status,pass,fail,blocked,started_at)、schema_version |

- registry 只存路径清单与运行记录索引，不存用例内容；删库可由扫描重建
- 首次 `console`（非 -g）自动注册当前项目
- **模块树 = 目录结构**（`scenarios/xinfei/seal/a.yaml` 目录即分类）；现有 `module:` 字段降级为检索标签，向后兼容 validator

## §3 REST API

| 组 | 端点 | 说明 |
|---|---|---|
| 浏览 | GET `/api/tree` `/api/scenarios/{path}` | 目录树 + 结构化场景详情（解析失败返回 errors 降级源码模式） |
| 编辑 | PUT `/api/scenarios/{path}` | 结构化 JSON→渲染 YAML；带 `move_to` 即移动；保存前强制校验失败不落盘 |
| 校验 | POST `/api/validate` | 复用现有 validator |
| 执行 | POST `/api/run` {env,module} → GET `/api/jobs/{id}/stream` (SSE) | 子进程跑 `atk run`，stdout 行转 SSE；全局同时仅 1 个任务，占用时 409 |
| 历史 | GET `/api/runs` `/api/runs/{id}` `/api/runs/{id}/evidence/{file}` | 复用 run_store；截图等证据可内联预览 |
| 配置 | GET/PUT `/api/environments` `/api/modules` | `${env:X}` 引用只显示占位符，不回显真实值 |
| 全局 | GET `/api/projects`，POST/DELETE `/api/projects/{id}` | 注册表管理 + 各项目 runs 聚合查询 |

并发保护：PUT 带 mtime 乐观锁，文件被外部修改返回 409（前端提示刷新或强制覆盖）。
安全：绑定 127.0.0.1，无鉴权（本机单人）。

## §4 前端页面（Vue3 + Vite + Element Plus）

技术栈：Element Plus、vuedraggable（步骤排序）、CodeMirror（JSON/YAML 高亮编辑）。

1. **场景浏览页**（默认）：左模块目录树 + 右场景列表（名称/P0-P3/标签/最近结果）；点击进详情
2. **场景详情/编排页**：
   - 基本信息：scenario/module/priority/tags/env(下拉取自 environments.yaml)/data fixtures k-v 表
   - 步骤卡片列表（拖拽排序），两类卡：
     - **API 卡**：method+call 行、headers k-v 表、params/body JSON 编辑器、断言规则表(key + op 下拉 eq/ne/not_null/gt/gte/lt/lte/contains/regex + value)、capture 提取表、retries
     - **UI 卡**：action 下拉(open/click/fill/snapshot/wait/assert_text…) + target + value + expect
   - 表单 ⇄ YAML 源码双模式切换（复杂 YAML 兜底）
   - 保存流：表单 JSON → POST /api/validate → 渲染 YAML 落盘
   - 已知限制：pyyaml 渲染丢注释，注释请放 description 类字段（文档明示）
3. **运行页**：选环境/module 触发；SSE 实时滚日志；结束后渲染结果摘要 + 各 intent 状态 + 截图证据内嵌
4. **历史页**：运行记录列表 → 详情（场景级 pass/fail/blocked、报告 HTML 跳转、证据查看）
5. **设置页**：environments/modules 表格化查看与编辑
6. **全局模式首页**：项目卡片列表（场景数/最近运行状态）→ 点击新开对应项目视图；聚合最近运行时间线

## §5 执行流与错误处理

- 运行 = subprocess `atk run ...`，超时上限默认 600s 可配，kill 后标记 timeout
- YAML 解析失败：详情页降级源码模式并标注错误行
- SSE 断线自动重连（EventSource 原生行为），job 结束事件幂等
- 敏感值永不出后端（响应只含占位符文本）

## §6 测试策略（原型期从简）

- 后端 pytest + TestClient：CRUD、乐观锁冲突、validate 拦截、registry 注册/重建、SSE 基本流（复用 tmp 工程夹具）
- 前端：不做全量组件测试；一条 Playwright E2E 冒烟（建场景→保存→运行→看结果）在 M4 收尾补
- Dogfood：对本仓库 scenarios/demo_app 走全流程实测验收

## §7 分期与发布路线

**原型（本设计范围）**
- M1 骨架+只读浏览：FastAPI 骨架、Vue 工程、树/详情/历史/证据
- M2 触发执行+SSE 实时反馈
- M3 API 用例表单化编辑（CRUD/断言/提取/双模式/乐观锁）
- M4 UI 卡片编辑 + `-g` 全局聚合 + E2E 冒烟

**发布里程碑（原型验证提效后另行立项，本设计不实施）**
- pip wheel 正式发包（引擎+控制台 dist 内嵌）
- npm 分发评估：采用业界通行"npm 元包 + 平台二进制可选依赖"模式（esbuild/turbo 同款），npm 包负责安装引导，实际引擎仍为 Python 产物；若评估成本过高则退化为 `pipx install atk` + 文档引导
- 版本化安装器：`curl -fsSL ... | sh` 单命令装好 atk 与控制台

## 明确不做（YAGNI）

- 用户体系/鉴权、多租户
- 用例内容入库（任何形式的 DB 双写）
- 性能测试、定时任务调度（CI 侧已有 gate/run 机制）
- 移动端适配
