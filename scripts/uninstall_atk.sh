#!/usr/bin/env bash
# 彻底卸载 atk。旧版只做了 `uv tool uninstall`，实测漏掉三类残留：
#
#   1) 常驻进程：atk console 给每个注册项目 Popen 一个独立子进程
#      （console/routes.py:129，stdout/stderr 进 DEVNULL、无人回收）。父进程一退
#      它就被 launchd 收养（PPID=1），靠 cwd 直接 import 仓库源码跑，卸掉任何包
#      都动不了它 —— 端口一直占着。
#   2) 其它安装渠道：项目 .venv 里的 editable 安装、pipx，和 uv tool 互不相干。
#      而且仓库改名过一次，.venv 里 dev2atf / atk 两份 dist-info 并存，按一个名字
#      卸会留下另一份。所以这里按"哪个发行包提供了 atk 命令"来枚举，不认死名字。
#   3) 全局状态：~/.atk/registry.db 是 console 的项目注册表（console/registry.py:6），
#      存在用户目录，不随包卸载。
#
# 另有一个假信号：仓库根的 dev2atf.egg-info/ 让"以仓库为 cwd 的任意 python"都报告
# dev2atf 已安装。所以本脚本所有探测一律切到中立目录执行，否则核验永远不干净。
#
# 只清全局。项目内 scenarios/config/reports/.atk 是用例资产，不碰——要清用 atk purge。
#
# 用法：
#   ./scripts/uninstall_atk.sh                  # 交互确认后全清
#   ./scripts/uninstall_atk.sh --yes            # 免确认（CI / 脚本）
#   ./scripts/uninstall_atk.sh --dry-run        # 只报告残留，什么都不改
#   ./scripts/uninstall_atk.sh --keep-registry  # 保留 ~/.atk 注册表
set -euo pipefail

ASSUME_YES=0
ASSUME_NO=0
DRY_RUN=0
KEEP_REGISTRY=0

for arg in "$@"; do
  case "$arg" in
    --yes|-y) ASSUME_YES=1 ;;
    --no) ASSUME_NO=1 ;;
    --dry-run|-n) DRY_RUN=1 ;;
    --keep-registry) KEEP_REGISTRY=1 ;;
    -h|--help)
      awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "${BASH_SOURCE[0]}"
      exit 0 ;;
    *) echo "未知参数：${arg}（--help 看用法）" >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ask() {  # ask "提示" → 0=同意
  [ "$ASSUME_YES" = 1 ] && return 0
  [ "$ASSUME_NO" = 1 ] && return 1
  if [ ! -t 0 ]; then
    echo "  非交互且未加 --yes，跳过：$1" >&2
    return 1
  fi
  local ans
  read -r -p "$1 [y/N] " ans
  case "$ans" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

doit() {  # doit 命令... —— dry-run 只打印
  if [ "$DRY_RUN" = 1 ]; then
    echo "  [dry-run] $*"
  else
    "$@"
  fi
}

# console 进程特征 " atk console "：前置空格或路径分隔符，避免误伤同名文本参数
console_pids() { pgrep -f '[ /]atk console( |$)' 2>/dev/null || true; }

uv_has_atk() {
  command -v uv >/dev/null 2>&1 || return 1
  uv tool list 2>/dev/null | grep -qE '^(dev2atf|atk)( |$)'
}

# 枚举某虚拟环境里"装了 atk 命令"的发行包名（不依赖 cwd，兼容历史改名残留）
env_atk_names() {  # env_atk_names <venv根> → 每行一个发行包名
  local v="$1"
  [ -x "$v/bin/python" ] || return 0
  ( cd / && "$v/bin/python" - "$v" <<'PY'
import pathlib, re, sys
venv = pathlib.Path(sys.argv[1])
for sp in sorted(venv.glob("lib/python*/site-packages")):
    for di in sorted(sp.glob("*.dist-info")):
        try:
            if not re.search(r"^\s*atk\s*=", (di / "entry_points.txt").read_text(), re.M):
                continue
        except OSError:
            continue
        # dev2atf-0.1.0.dist-info -> dev2atf（先剥后缀，名字里也有 '-'）
        stem = di.name.split(".dist-info")[0]
        print(stem.rsplit("-", 1)[0])
PY
  )
}

# ------------------------------------------------------------ 残留探测
PIDS="$(console_pids)"
FOUND=0
note() { FOUND=1; echo "  $*"; }

echo "==> atk 残留探测"
if [ -n "$PIDS" ]; then
  note "常驻 console 进程："
  # shellcheck disable=SC2086
  ps -o pid,ppid,etime,command -p $(echo "$PIDS" | tr '\n' ',' | sed 's/,$//') 2>/dev/null | tail -n +2 | sed 's/^/      /'
else
  echo "  常驻 console 进程：无"
fi
if uv_has_atk; then note "uv tool：$(uv tool list 2>/dev/null | grep -E '^(dev2atf|atk)' | tr '\n' ' ')"; else echo "  uv tool：未安装"; fi
if command -v atk >/dev/null 2>&1; then note "PATH 中 atk：$(command -v atk)"; else echo "  PATH 中 atk：无"; fi

VENV_HITS=""   # 每行 "<venv>|<发行包名>"（bash 3.2 下空数组 + set -u 有坑，用字符串）
for v in "$REPO_ROOT/.venv" "$HOME/.venv"; do
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    VENV_HITS+="$v|$name"$'\n'
  done <<< "$(env_atk_names "$v")"
done
if [ -n "$VENV_HITS" ]; then
  note "venv 内的 editable 安装："
  echo "$VENV_HITS" | sed '/^$/d; s/^/      /'
fi
if [ -d "$HOME/.local/share/pipx/venvs/dev2atf" ]; then note "pipx：dev2atf"; fi
if [ -e "$HOME/.atk" ]; then note "全局注册表：$HOME/.atk/registry.db"; else echo "  全局注册表：无"; fi
if [ -d "$REPO_ROOT/dev2atf.egg-info" ]; then
  echo "  ℹ 仓库根有 dev2atf.egg-info/（editable 构建产物，非残留，删后 setup 会重建）"
fi

if [ "$FOUND" = 0 ]; then
  echo
  echo "已经干净了，无需卸载。"
  exit 0
fi
if [ "$DRY_RUN" = 1 ]; then
  echo
  echo "dry-run：以上均未改动。"
  exit 0
fi

# ------------------------------------------------------------ 1/3 停进程
echo
if [ -n "$PIDS" ]; then
  echo "==> 1/3 停止 console 进程"
  if ask "结束上述进程？未跑完的 job 会中断"; then
    # shellcheck disable=SC2086
    doit kill $PIDS 2>/dev/null || true
    if [ "$DRY_RUN" = 0 ]; then
      for _ in $(seq 1 20); do
        [ -z "$(console_pids)" ] && break
        sleep 0.3
      done
      alive="$(console_pids)"
      if [ -n "$alive" ]; then
        echo "  优雅退出超时，强制结束：$(echo "$alive" | tr '\n' ' ')"
        # shellcheck disable=SC2086
        doit kill -9 $alive 2>/dev/null || true
      fi
    fi
  else
    echo "  已跳过（进程继续占用端口）"
  fi
else
  echo "==> 1/3 无进程需要停止"
fi

# ------------------------------------------------------------ 2/3 卸本体
echo
echo "==> 2/3 卸载工具本体"
if uv_has_atk; then
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    uv tool uninstall "$name" && echo "  uv tool：$name 已卸载 ✓" \
      || echo "  uv tool 卸载 $name 失败" >&2
  done < <(uv tool list 2>/dev/null | grep -E '^(dev2atf|atk)( |$)' | awk '{print $1}')
else
  echo "  uv tool：无需卸载"
fi

# 用 FD 3 喂列表，把 stdin 留给终端 —— 否则 ask 的 read 会读到列表上
while IFS= read -r entry <&3; do
  [ -n "$entry" ] || continue
  v="${entry%%|*}"
  name="${entry#*|}"
  # 变量后紧跟中文标点必须写 ${var}：bash 3.2 会把多字节字符吞进变量名
  if ask "卸载 ${v} 里的 ${name}？（只删该环境的安装记录，不动仓库源码）"; then
    if command -v uv >/dev/null 2>&1; then
      VIRTUAL_ENV="$v" uv pip uninstall "$name" || echo "  uv pip 卸 $name 失败" >&2
    else
      "$v/bin/python" -m pip uninstall -y "$name" || echo "  pip 卸 $name 失败" >&2
    fi
  fi
done 3<<< "$VENV_HITS"

if [ -d "$HOME/.local/share/pipx/venvs/dev2atf" ] && ask "卸载 pipx 里的 dev2atf？"; then
  if command -v pipx >/dev/null 2>&1; then
    pipx uninstall dev2atf
  else
    echo "  找不到 pipx 命令，请手工处理 ~/.local/share/pipx/venvs/dev2atf" >&2
  fi
fi

# pipx/手工安装不会自动收软链；只删指向已失效环境的，活着的可能属于别的工具
for stray in "$HOME/.local/bin/atk" /opt/homebrew/bin/atk /usr/local/bin/atk; do
  [ -L "$stray" ] || continue
  target="$(readlink "$stray" 2>/dev/null || true)"
  case "$target" in *dev2atf*|*atk*) ;; *) continue ;; esac
  if [ ! -e "$target" ]; then
    doit rm -f "$stray" && echo "  已清理失效软链：$stray -> $target"
  fi
done

# ------------------------------------------------------------ 3/3 清状态
echo
echo "==> 3/3 全局状态"
if [ ! -e "$HOME/.atk" ]; then
  echo "  无全局状态"
elif [ "$KEEP_REGISTRY" = 1 ]; then
  echo "  按要求保留 ~/.atk（下次 atk console -g 仍列得出项目）"
elif ask "删除全局注册表 ~/.atk/（只有项目路径索引，不含用例）？"; then
  doit rm -rf "$HOME/.atk" && echo "  ~/.atk 已删除 ✓"
else
  echo "  已保留 ~/.atk"
fi

# ------------------------------------------------------------ 收尾核验
echo
echo "==> 残留核验"
fail=0
if command -v atk >/dev/null 2>&1; then
  echo "  ✗ PATH 仍有 atk：$(command -v atk)"; fail=1
else
  echo "  ✓ atk 已从 PATH 移除"
fi
if uv_has_atk; then echo "  ✗ uv tool 仍装有 dev2atf/atk"; fail=1; else echo "  ✓ uv tool 干净"; fi
for v in "$REPO_ROOT/.venv" "$HOME/.venv"; do
  if [ -n "$(env_atk_names "$v")" ]; then
    echo "  ✗ $v 仍提供 atk 命令（激活该 venv 就能跑 atk）"; fail=1
  fi
done
if [ -n "$(console_pids)" ]; then
  echo "  ✗ 仍有 console 进程占用端口：$(console_pids | tr '\n' ' ')"; fail=1
else
  echo "  ✓ 无 console 进程"
fi
if [ -e "$HOME/.atk" ]; then echo "  ℹ 保留着 $HOME/.atk"; else echo "  ✓ 无全局状态"; fi

if [ "$fail" = 1 ]; then
  echo
  echo "未清理干净，按上面 ✗ 项处理。" >&2
  exit 1
fi
echo
echo "卸载完成。项目内 scenarios/config/reports/.atk 未改动；要清用 atk purge。"
