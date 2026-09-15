"""Agent skill 安装：包内单一源 -> 多种 Agent 布局。

不同 Agent 读 skill 的位置不同：
- Claude / CodeBuddy：`.claude/skills/<name>/SKILL.md`
- Cursor：`.cursor/rules/*.md`（需 YAML frontmatter）
- Codex / OpenCode 等：`AGENTS.md`

只装一种布局会导致换 Agent 即失效。这里一次性铺开，并对已存在的
AGENTS.md 做标记块幂等替换，绝不覆盖用户自己的内容。
"""
from __future__ import annotations

import re
from pathlib import Path

DEFAULT_UI_TOOL = "ego-browser"

BEGIN = "<!-- atk:begin -->"
END = "<!-- atk:end -->"

#: 布局：(目录, 文件名生成函数, 是否需要 frontmatter 转换)
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """拆出 YAML frontmatter 与正文；无 frontmatter 时返回空字典与全文。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, text[m.end() :]


def render_skill(text: str, ui_tool: str = DEFAULT_UI_TOOL) -> str:
    """替换 SKILL.md 里的 {{UI_TOOL}} 等模板变量。"""
    return text.replace("{{UI_TOOL}}", ui_tool)


def _cursor_doc(name: str, body_md: str, description: str) -> str:
    """Cursor 规则文档：需要 description 与 alwaysApply 两个字段。"""
    desc = description or f"atk {name} 工作流"
    return (
        "---\n"
        f"description: {desc}\n"
        "alwaysApply: false\n"
        "---\n\n"
        f"# atk · {name}\n\n"
        f"{body_md.strip()}\n"
    )


def _agents_block(skills: dict[str, str], ui_tool: str) -> str:
    """生成 AGENTS.md 的公共区块：命令速查 + 工作流要点。"""
    names = "、".join(sorted(skills))
    return (
        f"{BEGIN}\n"
        "## atk 自动化测试工作流\n\n"
        f"本项目接入 atk（AI 原生双层自动化测试框架），已安装 skill：{names}。\n"
        f"UI/E2E 实测工具：`{ui_tool}`。\n\n"
        "常用命令（用 `--last` 可免手工拼接 run_id）：\n\n"
        "```bash\n"
        'atk smoke --title "<功能>" --base <基线> [--env <环境>] [--format json]\n'
        "atk context --base <基线> --format json     # 变更上下文包\n"
        "atk validate                                # 场景库自查\n"
        "atk run --env <env> [--record-new] [--skip-ui]\n"
        "atk record --last --title \"<意图>\" --status pass|fail|suspect|blocked\n"
        "atk review-draft <草稿.yaml> --by <人> --verdict approve|reject\n"
        "atk review --last --by <人> --verdict approve\n"
        "atk gate --last --format json\n"
        "```\n\n"
        "要点：\n"
        "- 断言不止 status，必须断关键业务字段（支持 `< 90`、`contains: x`、`regex:`、`len:`、`type:`）。\n"
        "- `ui:` 步骤 atk run 不执行，必须由 AI 实测后 `atk record` 回填，未回填 gate 会拦截。\n"
        "- 不得编造接口/字段；不得把 blocked 写成 pass；草稿只增不改。\n"
        f"{END}\n"
    )


def upsert_agents_md(path: Path, block: str) -> bool:
    """把区块写入 AGENTS.md：有标记就替换，无标记就追加。返回是否新增文件。"""
    existed = path.is_file()
    if existed:
        text = path.read_text(encoding="utf-8")
        pattern = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
        if pattern.search(text):
            new_text = pattern.sub(lambda _m: block.strip("\n"), text)
        else:
            sep = "" if text.endswith("\n") else "\n"
            new_text = f"{text}{sep}\n{block}"
        path.write_text(new_text, encoding="utf-8")
        return False
    path.write_text(block, encoding="utf-8")
    return True


def install_skills(
    root: Path | str,
    skills: dict[str, str],
    ui_tool: str = DEFAULT_UI_TOOL,
) -> tuple[list[str], list[str]]:
    """把 skill 铺到各 Agent 布局。返回 (新建文件, 跳过的已存在文件)。

    已存在的文件一律跳过（只建缺失，绝不覆盖），AGENTS.md 例外——
    它按标记块幂等替换，因此不算"跳过"。
    """
    root = Path(root)
    created: list[str] = []
    skipped: list[str] = []

    rendered = {name: render_skill(text, ui_tool) for name, text in skills.items()}

    for name, text in rendered.items():
        fm, body = split_frontmatter(text)
        targets: list[tuple[Path, str]] = [
            (root / "skills" / name / "SKILL.md", text),
            (root / ".claude" / "skills" / name / "SKILL.md", text),
            (root / ".cursor" / "rules" / f"atk-{name}.md",
             _cursor_doc(name, body, fm.get("description", ""))),
        ]
        for dest, content in targets:
            if dest.exists():
                skipped.append(str(dest))
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            created.append(str(dest))

    agents = root / "AGENTS.md"
    upsert_agents_md(agents, _agents_block(skills, ui_tool))
    return created, skipped
