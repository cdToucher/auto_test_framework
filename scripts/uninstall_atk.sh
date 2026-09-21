#!/usr/bin/env bash
# 卸载全局 atk 命令（uv tool uninstall dev2atf）。只动 uv 全局工具环境，
# 不删除任何项目里的 scenarios/config/reports。
#
# 用法：
#   ./scripts/uninstall_atk.sh          # 交互确认
#   ./scripts/uninstall_atk.sh --yes    # 免确认（脚本/CI 场景）
set -euo pipefail

command -v uv >/dev/null 2>&1 || { echo "未找到 uv" >&2; exit 1; }

# 包名以 pyproject [project] name 为准；命令名是 atk
if ! uv tool list 2>/dev/null | grep -q '^dev2atf'; then
  echo "当前未通过 uv tool 安装 dev2atf（atk），无需卸载"
  exit 0
fi

if [ "${1:-}" != "--yes" ]; then
  read -r -p "确认卸载全局工具 dev2atf（提供 atk 命令）？[y/N] " ans
  case "$ans" in
    [Yy]*) ;;
    *) echo "已取消"; exit 0 ;;
  esac
fi

uv tool uninstall dev2atf
echo "已卸载。残留检查："
command -v atk >/dev/null 2>&1 \
  && echo "  注意：PATH 中仍存在 atk（$(command -v atk)），可能来自 venv/pipx 等其他安装" \
  || echo "  atk 已从 PATH 移除 ✓"
