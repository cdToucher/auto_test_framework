"""git 变更上下文包：提交记录 + 分文件补丁 + 模块归属 + 现有场景摘要。

供 LLM 提示词或 Agent 编排使用，让 AI 只依赖本包即可理解"这次改了什么、
属于哪些模块、库里已有什么场景"，避免凭空编造或重复起场景。
"""
import subprocess
from pathlib import Path

from ..diff_analyzer.git_diff import changed_files
from ..diff_analyzer.modules import classify, load_module_map
from ..store.loader import load_scenarios
from ..store.models import ApiStep, Step

# 单文件补丁与总量上限，防止大 diff 撑爆提示词
MAX_PATCH_LINES_PER_FILE = 400
MAX_PATCH_LINES_TOTAL = 1200


def _git(*args: str, repo: str = ".") -> str:
    proc = subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {proc.stderr.strip()}")
    return proc.stdout


def commit_log(base: str, head: str, repo: str = ".") -> list[dict]:
    """返回 base...head 三点区间的提交（merge-base(base,head)..head）。

    与 changed_files/file_patches 统一用三点口径：线性历史下等价于两点
    base..head，有分叉时以 merge-base 为准，避免提交与补丁口径对不上。

    字段/说明/文件段分别用 \\x1f、\\x1d、\\x1e 分隔，避免与正文文本冲突；
    注意不能对分块做 strip()——Python 会把 \\x1f 当空白剥掉。
    """
    out = _git(
        "log", f"{base}...{head}",
        "--date=short",
        # \x1e 放在格式串开头作记录分隔：--name-only 的文件列表输出在
        # pretty 正文之后，若 \x1e 收尾会把文件列表并入下一分块
        "--pretty=format:\x1e%h\x1f%an\x1f%ad\x1f%s\x1f%b\x1d",
        "--name-only",
        repo=repo,
    )
    commits: list[dict] = []
    for chunk in out.split("\x1e"):
        chunk = chunk.lstrip("\n")
        if not chunk:
            continue
        fields_body, _, files_part = chunk.partition("\x1d")
        parts = fields_body.split("\x1f", 4)
        if len(parts) != 5:
            continue
        files = [ln.strip() for ln in files_part.splitlines() if ln.strip()]
        commits.append(
            {
                "short": parts[0],
                "author": parts[1],
                "date": parts[2],
                "subject": parts[3],
                "body": parts[4].strip(),
                "files": files,
            }
        )
    return commits


def file_patches(
    base: str,
    head: str,
    repo: str = ".",
    max_lines_per_file: int = MAX_PATCH_LINES_PER_FILE,
    max_lines_total: int = MAX_PATCH_LINES_TOTAL,
) -> list[dict]:
    """返回 base...head 的分文件补丁 [{file, patch, truncated}]，超限截断。"""
    full = _git("diff", "--no-color", f"{base}...{head}", repo=repo)
    patches: list[dict] = []
    used = 0
    for block in full.split("diff --git ")[1:]:
        lines = block.splitlines()
        # 首行形如 a/path b/path
        name_line = lines[0] if lines else ""
        file = name_line.split(" b/")[-1] if " b/" in name_line else name_line
        body = ["diff --git " + ln if i == 0 else ln for i, ln in enumerate(lines)]
        truncated = False
        if len(body) > max_lines_per_file:
            body = body[:max_lines_per_file]
            truncated = True
        remaining = max_lines_total - used
        if remaining <= 0:
            truncated = True
            body = []
        elif len(body) > remaining:
            body = body[:remaining]
            truncated = True
        used += len(body)
        patches.append({"file": file, "patch": "\n".join(body), "truncated": truncated})
    return patches


def _step_expect(st: Step) -> str:
    """一步的断言摘要：查重时"同接口"不够，得看清断的是不是同一件业务事实。"""
    if isinstance(st, ApiStep):
        parts = [
            e.raw or (f"{e.path} {e.op} {e.value}" if e.path else f"status {e.status_op} {e.status}")
            for e in st.expect
        ]
        return ", ".join(p for p in parts if p)
    return st.expect or ""


def existing_scenarios(
    modules: list[str], scenarios_root: Path | str = "scenarios"
) -> dict[str, list[dict]]:
    """汇总受影响模块的现有场景摘要，供去重与风格对齐。

    带上 file 与逐步 expects：Agent 光看 calls 只能猜"大概重复"，要判"等价"必须能
    直接看到断言了什么、以及去哪核对，否则它会另写一条平行的重复场景。
    """
    scs, _ = load_scenarios(scenarios_root)
    out: dict[str, list[dict]] = {}
    for s in scs:
        if s.module not in modules:
            continue
        calls = [
            st.call if isinstance(st, ApiStep) else f"ui: {st.action}" for st in s.steps
        ]
        out.setdefault(s.module, []).append(
            {
                "name": s.scenario,
                "priority": s.priority.value,
                "tags": s.tags,
                "file": s.file,
                "calls": calls,
                "expects": [_step_expect(st) for st in s.steps],
            }
        )
    return out


def build_context(
    base: str,
    head: str,
    repo: str = ".",
    module_map_path: Path | str = "config/modules.yaml",
    scenarios_root: Path | str = "scenarios",
    max_patch_lines: int = MAX_PATCH_LINES_PER_FILE,
) -> dict:
    """构建完整变更上下文包（dict 形态）。"""
    files = changed_files(base, head, repo)
    module_map = load_module_map(module_map_path)
    groups = classify(files, module_map)
    affected = sorted(m for m in groups if m != "__unmapped__")
    return {
        "base": base,
        "head": head,
        "repo": str(repo),
        "head_short": _git("rev-parse", "--short", head, repo=repo).strip(),
        "commits": commit_log(base, head, repo),
        "files": files,
        "groups": groups,
        "affected_modules": affected,
        "patches": file_patches(base, head, repo, max_lines_per_file=max_patch_lines),
        "existing_scenarios": existing_scenarios(affected, scenarios_root),
    }


def render_markdown(ctx: dict) -> str:
    """把上下文包渲染为 Markdown（LLM 提示词 / Agent 阅读用）。"""
    lines: list[str] = []
    lines.append(f"# 变更上下文：{ctx['base']} → {ctx['head']}（{ctx['head_short']}）\n")

    lines.append("## 提交记录")
    if ctx["commits"]:
        for c in ctx["commits"]:
            lines.append(f"- `{c['short']}` {c['date']} {c['author']}：{c['subject']}")
            if c["body"]:
                for ln in c["body"].splitlines():
                    lines.append(f"  > {ln}")
    else:
        lines.append("（区间内无提交，仅工作区差异）")
    lines.append("")

    lines.append("## 变更文件与模块归属")
    for mod, fs in ctx["groups"].items():
        if mod == "__unmapped__":
            continue
        lines.append(f"- 模块 `{mod}`：{', '.join(fs)}")
    if "__unmapped__" in ctx["groups"]:
        lines.append(f"- 未映射文件：{', '.join(ctx['groups']['__unmapped__'])}")
    lines.append("")

    lines.append("## 代码补丁")
    for p in ctx["patches"]:
        note = "（已截断）" if p["truncated"] else ""
        lines.append(f"### {p['file']} {note}\n```diff\n{p['patch']}\n```")
    if not ctx["patches"]:
        lines.append("（无补丁内容）")
    lines.append("")

    lines.append("## 受影响模块的现有场景（禁止重复，风格对齐参考）")
    if ctx["existing_scenarios"]:
        for mod, scs in ctx["existing_scenarios"].items():
            lines.append(f"### 模块 `{mod}`")
            for s in scs:
                steps = "；".join(
                    f"{c} -> {e}" if e else c
                    for c, e in zip(s["calls"], s["expects"])
                ) or "（无步骤）"
                lines.append(
                    f"- {s['name']}（{s['priority']}，tags: {s['tags']}）`{s['file']}`：{steps}"
                )
    else:
        lines.append("（这些模块尚无场景）")
    return "\n".join(lines)
