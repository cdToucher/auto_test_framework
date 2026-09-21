"""Agent skill 安装：包内单一源 -> `.atk/skills/`，经 AGENTS.md 让 AI 可见。

布局决策（收束版）：
- skill 正文只写 `.atk/skills/<name>/SKILL.md` 一份，不再铺 `.claude/`、
  `.cursor/`、根 `skills/` ——主流 TUI/CLI（Codex/OpenCode/Claude/Qoder 等）
  都会读项目根 AGENTS.md，由它指向正文文件即可加载。
- AGENTS.md 按标记块幂等维护，只动 atk 区块、绝不覆盖用户内容。
- 旧版散落的拷贝由 `atk purge` 清理。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import layout

DEFAULT_UI_TOOL = "ego-browser"

BEGIN = "<!-- atk:begin -->"
END = "<!-- atk:end -->"

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)

#: 旧版布局可能存在的 atk 生成拷贝（purge 依据命名+内容标记清理）
LEGACY_SKILL_RELS: tuple[str, ...] = (
    "skills/{name}/SKILL.md",
    ".claude/skills/{name}/SKILL.md",
    ".cursor/rules/atk-{name}.md",
)


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """拆出 YAML frontmatter 与正文；无 frontmatter 时返回空字典与全文。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text[m.end():]
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, text[m.end():]


def render_skill(text: str, ui_tool: str = DEFAULT_UI_TOOL) -> str:
    """替换 SKILL.md 里的 {{UI_TOOL}} 等模板变量。"""
    return text.replace("{{UI_TOOL}}", ui_tool)


def _agents_block(skills: dict[str, str], ui_tool: str, root_note: str) -> str:
    """AGENTS.md 区块：AI 入口。正文放 .atk/skills/，这里给索引+硬规则。"""
    index = "\n".join(
        f"- `.atk/skills/{name}/SKILL.md` —— {split_frontmatter(text)[0].get('description', name)}"
        for name, text in sorted(skills.items())
    )
    return (
        f"{BEGIN}\n"
        "## atk 自动化测试工作流（AI 必读）\n\n"
        f"本项目接入 atk（AI 原生双层自动化测试框架）。{root_note}\n"
        f"UI/E2E 实测工具：`{ui_tool}`。\n\n"
        "**执行任何 atk 工作流前，先完整阅读下列说明文件：**\n\n"
        f"{index}\n\n"
        "常用命令（`--last` 免拼接 run_id；`--format json` 输出含 `next` 的机器可读结果）：\n\n"
        "```bash\n"
        'atk smoke --title "<功能>" --base <基线> [--env <环境>] --format json\n'
        "atk context --base <基线> --format json     # 变更上下文包（起草场景用）\n"
        "atk validate                                # 场景库自查\n"
        "atk run --dry-run                           # 先看会跑哪些场景，不执行\n"
        "atk run [--env <env>] [--set k=v] [--record-new] [--skip-ui]\n"
        'atk record --last --title "<意图>" --status <pass|fail|suspect|blocked> --note "<证据>"\n'
        "atk review-draft <草稿.yaml> --by <人> --verdict approve|reject\n"
        "atk review --last --by <人> --verdict approve\n"
        "atk gate --last --format json\n"
        "```\n\n"
        "硬规则：\n"
        "- 断言不止 status，必须断关键业务字段（支持 `< 90`、`contains: x`、`regex:`、`len:`、`type:`）。\n"
        "- `ui:` 步骤 atk run 不执行，必须由 AI 实测后 `atk record` 回填；未回填 `atk gate` 拦截。\n"
        "- 回填 status 无默认值：实测是什么就记什么，不得把 blocked 写成 pass。\n"
        "- 不得编造接口/字段；AI 草稿（tags 含 ai-generated）未经人工评审转正，gate 拦截。\n"
        "- 选中 0 个场景按受阻处理（exit 2），空跑不算通过。\n"
        f"{END}\n"
    )


def agents_block_for(root: Path | str = ".", skills: dict[str, str] | None = None,
                     ui_tool: str = DEFAULT_UI_TOOL) -> str:
    """供 init 生成/更新 AGENTS.md 区块；legacy 与新布局措辞不同。"""
    skills = skills or {}
    if layout.is_legacy(root):
        root_note = (
            f"场景库 `{Path('scenarios')}/`、配置 `config/`、产物 `reports/`（旧布局）。"
        )
    else:
        root_note = "全部 atk 产物收束在 `.atk/` 目录（场景库 `.atk/scenarios/`）。"
    return _agents_block(skills, ui_tool, root_note)


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


def remove_agents_block(path: Path) -> bool:
    """purge 用：删除 atk 区块；若文件因此只剩空白则删除文件本身。返回是否改动。"""
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"\n*" + re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n*", re.S
    )
    new_text = pattern.sub("\n", text)
    if new_text == text:
        return False
    if not new_text.strip():
        path.unlink()
        return True
    path.write_text(new_text.rstrip() + "\n", encoding="utf-8")
    return True


def legacy_skill_paths(root: Path | str, names: list[str]) -> list[Path]:
    """旧版 init 可能铺下的散落拷贝路径（存在的才返回）。"""
    root = Path(root)
    out: list[Path] = []
    for name in names:
        for rel in LEGACY_SKILL_RELS:
            p = root / rel.format(name=name)
            if p.is_file():
                out.append(p)
    return out


def install_skills(
    root: Path | str,
    skills: dict[str, str],
    ui_tool: str = DEFAULT_UI_TOOL,
) -> tuple[list[str], list[str]]:
    """skill 单一布局：`.atk/skills/<name>/SKILL.md` + AGENTS.md 区块。

    已存在的 skill 文件跳过（只建缺失，绝不覆盖）；AGENTS.md 按标记块幂等
    替换，因此不算"跳过"。返回 (新建文件, 跳过的已存在文件)。
    """
    root = Path(root)
    created: list[str] = []
    skipped: list[str] = []

    rendered = {name: render_skill(text, ui_tool) for name, text in skills.items()}
    for name, text in rendered.items():
        dest = layout.skills_dir(root) / name / "SKILL.md"
        if dest.exists():
            skipped.append(str(dest))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        created.append(str(dest))

    agents = root / "AGENTS.md"
    if upsert_agents_md(agents, agents_block_for(root, rendered, ui_tool)):
        created.append(str(agents))
    return created, skipped
