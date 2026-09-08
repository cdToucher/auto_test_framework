# 发布 atk 到公共托管（PyPI）操作文档

> 本文档与 `docs/USAGE.md` 独立：只讲“如何把本项目推到公共托管供下载”，不讲使用。
> 全程在发布者本机操作，约 15 分钟。

## 0. 包名：已定为 `dev2atf`

PyPI 上的 `atk` 已被占用（2019 年的 docker/k8s 工具），本项目以 `dev2atf` 发布
（已查名，404 可用）。`pyproject.toml` 中 `name = "dev2atf"`，但 `[project.scripts]`
的命令仍叫 `atk`、`import` 包名仍是 `atk`——用户 `uv tool install dev2atf` 后用的还是
`atk` 命令，现有文档/skill/CI 无需改动。

## 1. 准备事项

1. Python ≥3.11，`uv --version` 可用（构建发布都用 uv）。
2. 注册 PyPI 账号 + 开 2FA：https://pypi.org/account/register/
3. 生成 API Token（Account settings → API tokens，scope 选 Entire account，
   或先用 TestPyPI 的 token）：形如 `pypi-...`，只显示一次，存好。
4. 本机登录（任选其一）：
   ```bash
   uv publish --help >/dev/null && echo ok
   export UV_PUBLISH_TOKEN="pypi-..."   # 或按 uv 提示用 keyring
   ```

## 2. 发布前检查（本地）

```bash
cd /path/to/auto_test_framework

# 版本号：改 pyproject.toml 的 version（如 0.1.0 → 0.2.0），提交并打 tag
# 规则：X.Y.Z，修 bug 升 Z，加功能升 Y，不兼容升 X；tag 与 version 一致
git tag v0.2.0 && git push origin v0.2.0

.venv/bin/python -m pytest tests/ -q     # 全绿
.venv/bin/atk validate                   # 场景库合法
uv build                                 # 产物进 dist/（.tar.gz + .whl）
ls dist/
```

`uv build` 失败多为 `[build-system]` 缺声明——本项目已配 `setuptools>=68`，一般直接过。

## 3. 先发 TestPyPI 验证（强烈建议）

TestPyPI 是公共测试托管，与正式 PyPI 账号体系独立，需单独注册：
https://test.pypi.org/account/register/（同样开 2FA、建 token）。

```bash
export UV_PUBLISH_TOKEN="<testpypi-token>"
uv publish --index https://test.pypi.org/legacy/ dist/*
```

另起干净环境验证可装可用：

```bash
uv tool install --index https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ dev2atf
atk --help && atk validate   # 在任意含 scenarios/ 的目录跑
uv tool uninstall dev2atf
```

> `--extra-index-url` 是为了同时拉取依赖（typer/httpx 等只在正式源有）。

## 4. 正式发布

```bash
export UV_PUBLISH_TOKEN="<pypi-token>"
uv publish dist/*
# 成功后约 1 分钟可在 https://pypi.org/project/dev2atf/ 看到
```

发布后换机器验证（别人视角）：

```bash
uv tool install dev2atf            # 基础版
uv tool install "dev2atf[console]" # 带 Web 控制台
atk --help
```

## 5. 日常更新流程

```bash
# 改代码 → 升 version → tag → pytest → uv build → TestPyPI（可选）→ uv publish
# 用户侧升级：
uv tool upgrade dev2atf
```

## 6. 常见失败

| 现象 | 原因 / 处理 |
|---|---|
| `403 Forbidden` | token 错scope/过期，或包名被占；重建 token，确认改名已生效 |
| `400 File already exists` | 同一 version 发过——PyPI 不允许覆盖，必须升版本号重发 |
| `TestPyPI 装完缺依赖` | 忘加 `--extra-index-url https://pypi.org/simple/` |
| `atk` 命令冲突 | 用户装过旧 `atk` 包（docker 那个）：`uv tool uninstall atk` 后再装 dev2atf |
| 想撤回已发布版本 | PyPI 不支持删除文件后重传同名版本；只能 `yank`（后台管理页操作）后发新版 |

## 7. 一句话清单

查名改名 → 定 version 打 tag → pytest 全绿 → `uv build` → TestPyPI 验证 →
`uv publish` → 异机 `uv tool install` 验证 → 写 README 安装段。
