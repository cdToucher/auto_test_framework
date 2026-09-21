#!/usr/bin/env bash
# 安装全局 atk 命令（uv tool）。默认 editable 安装：框架代码改动即时生效，
# 并带上 [console] extra（fastapi/uvicorn/apscheduler，atk console 可用）。
#
# 用法：
#   ./scripts/install_atk.sh                # editable 安装本仓库
#   ATK_INSTALL_MODE=plain ./scripts/install_atk.sh   # 非 editable（复制式）安装
#   ./scripts/install_atk.sh --force        # 追加任意 uv 参数
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

command -v uv >/dev/null 2>&1 || {
  echo "未找到 uv，请先安装：https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
}

MODE="${ATK_INSTALL_MODE:-editable}"
PKG="$REPO_ROOT[console]"

if [ "$MODE" = "editable" ]; then
  echo "==> uv tool install --editable \"$PKG\" $*"
  uv tool install --editable "$PKG" "$@"
else
  echo "==> uv tool install \"$PKG\" $*"
  uv tool install "$PKG" "$@"
fi

echo
echo "==> 安装结果："
uv tool list | grep -i -A2 dev2atf || true

if ! command -v atk >/dev/null 2>&1; then
  echo "警告：atk 不在 PATH 中，请执行 uv tool update-shell 后重开终端" >&2
  exit 1
fi
echo
echo "==> 验证：$(atk --help 2>/dev/null | grep -v '^[[:space:]]*$' | head -1)"
echo "跨项目使用：cd <被测项目> && atk init --url <base_url> --modules <模块名>"
