"""Agent skill 安装：包内单一源 -> `.atk/skills/`，AGENTS.md 只留入口指针。

布局决策（收束版）：
- 每个 skill 一个目录，入口文件按通用约定叫 `SKILL.md`；只有说明书（atk-use）例外，
  叫 `atk_use.md` —— `.atk/skills/` 下三份同名 `SKILL.md` 用 `@` 唤出时分不清是谁，
  独特的 basename 才能 `@atk_use` 一次命中。
- 说明书每次 init 由包内源重生成（当前版本怎么用，写歪了会误导下一个 AI）；
  工作流正文开发者会改，只建不覆盖。两者都不铺 `.claude/`、`.cursor/`、根 `skills/`。
- 主流 TUI/CLI（Codex、OpenCode、Claude Code、Qoder 等）只自动加载项目根 AGENTS.md，
  所以那里必须留一小段指针；命令清单与硬规则不在别处重复，两处各写一份必然漂移。
- AGENTS.md 按标记块幂等维护，只动 atk 区块、绝不覆盖用户内容。
- 旧版散落的拷贝与旧落位的 `.atk/atk_use.md` 由 `atk purge` / init 升级清理。
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


#: 说明书这个 skill 的文件名是特意的：`.atk/skills/` 下三份 `SKILL.md` 同名，
#: 用 @ 唤出时分不清是谁；说明书改叫 atk_use.md，@atk_use 一眼唯一命中。
USE_SKILL = "atk-use"
USE_ENTRY = "atk_use.md"
#: 说明书由包内源生成，每次 init 重生成（手改会被覆盖）；工作流正文开发者会改，只建不覆盖
REGENERATED = frozenset({USE_SKILL})

#: 历版说明书的落位，purge 负责收掉遗留
LEGACY_USE_RELS: tuple[str, ...] = (".atk/atk_use.md",)


def entry_name(name: str) -> str:
    return USE_ENTRY if name == USE_SKILL else "SKILL.md"


#: skill 目录的入口文件候选：SKILL.md 是通用约定，说明书走可被 @ 唯一命中的特例名
ENTRY_CANDIDATES = ("SKILL.md", USE_ENTRY)


def package_skill_texts() -> dict[str, str]:
    """包内 skill 单一源（init / purge / 测试共用）；读不到抛 RuntimeError。"""
    import importlib.resources as res

    root = res.files("atk.skills")
    out: dict[str, str] = {}
    try:
        dirs = sorted(root.iterdir(), key=lambda p: getattr(p, "name", ""))
        for d in dirs:
            entry = next((d / n for n in ENTRY_CANDIDATES if (d / n).is_file()), None)
            if entry is not None:
                out[d.name] = entry.read_text(encoding="utf-8")
    except Exception as e:  # 资源缺失/读不动：init 契约统一按 RuntimeError 报
        raise RuntimeError(str(e)) from e
    return out


def installed_path(root: Path | str, name: str) -> Path:
    """某个 skill 装到项目里的路径（文件名可能是特例名）。"""
    return layout.skills_dir(root) / name / entry_name(name)


def use_file(root: Path | str = ".") -> Path:
    """说明书落位：AGENTS.md 指针、断链检查、purge 都走这一处。"""
    return layout.skills_dir(root) / USE_SKILL / USE_ENTRY


def legacy_use_files(root: Path | str = ".") -> list[Path]:
    return [Path(root) / rel for rel in LEGACY_USE_RELS]


def render_skill(text: str, *, ui_tool: str = DEFAULT_UI_TOOL,
                 layout_note: str = "", skill_index: str = "") -> str:
    """替换包内源里的模板变量（{{UI_TOOL}} 等）。"""
    return (text.replace("{{UI_TOOL}}", ui_tool)
                .replace("{{LAYOUT}}", layout_note)
                .replace("{{SKILL_INDEX}}", skill_index))


def layout_note(root: Path | str = ".") -> str:
    if layout.is_legacy(root):
        return "场景库 `scenarios/`、配置 `config/`、产物 `reports/`（旧布局）。"
    return "全部 atk 产物收束在 `.atk/` 目录（场景库 `.atk/scenarios/`）。"


def skill_index(skills: dict[str, str]) -> str:
    """说明书里的技能索引：不含它自己，路径按安装后的真实文件名写。"""
    return "\n".join(
        f"- `.atk/skills/{name}/{entry_name(name)}` —— "
        f"{split_frontmatter(text)[0].get('description', name)}"
        for name, text in sorted(skills.items()) if name != USE_SKILL
    ) or "（本项目未安装其它 atk 技能正文）"


def _agents_block(skills: dict[str, str], ui_tool: str) -> str:
    """AGENTS.md 区块：只留入口指针。

    必须保留这一小段：TUI/CLI agent 只自动加载 AGENTS.md（或等价文件），
    完全清空就没有任何东西把 AI 引到说明书了。
    """
    names = "、".join(f"`.atk/skills/{n}/{entry_name(n)}`"
                      for n in sorted(skills) if n != USE_SKILL)
    return (
        f"{BEGIN}\n"
        "## atk 自动化测试（AI 入口）\n\n"
        "本项目接入 atk（AI 原生双层自动化测试框架）。\n\n"
        f"- **做任何 atk 相关工作前，先完整阅读 `{use_file().as_posix()}`**"
        "（说明书：触发循环、命令清单、退出码、envelope 字段、硬规则都在那里；"
        "TUI/CLI 里可用 `@atk_use` 唤出）。\n"
        f"- 技能正文：{names or '（未安装）'}。\n"
        f"- UI/E2E 实测工具：`{ui_tool}`。\n"
        "- 不知道下一步跑什么：`atk agent --format json`，按 `next` 走；"
        "返回 `requires_human: true` 必须停下问人。\n"
        f"{END}\n"
    )


def agents_block_for(root: Path | str = ".", skills: dict[str, str] | None = None,
                     ui_tool: str = DEFAULT_UI_TOOL) -> str:
    """供 init 生成/更新 AGENTS.md 区块。"""
    return _agents_block(skills or {}, ui_tool)


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
    """单一布局：`.atk/skills/<名称>/<入口文件>` + AGENTS.md 窄指针。

    工作流正文只建缺失、绝不覆盖（开发者会改它）；说明书（REGENERATED）每次按包内源
    重生成，手改同样被覆盖——它是"这个版本的 atk 怎么用"的唯一真源，写歪了会误导下一个人
    或下一个 AI，项目私有说明请写在 AGENTS.md 区块之外。
    AGENTS.md 按标记块幂等替换。返回 (新建或更新的文件, 跳过的已存在文件)。
    """
    root = Path(root)
    created: list[str] = []
    skipped: list[str] = []
    index = skill_index(skills)

    rendered = {
        name: render_skill(text, ui_tool=ui_tool, layout_note=layout_note(root),
                           skill_index=index)
        for name, text in skills.items()
    }
    for name, text in rendered.items():
        dest = layout.skills_dir(root) / name / entry_name(name)
        if dest.is_file() and name not in REGENERATED:
            skipped.append(str(dest))          # 可能被开发者改过，只建不覆盖
            continue
        if dest.is_file() and dest.read_text(encoding="utf-8") == text:
            skipped.append(str(dest))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        created.append(str(dest))

    # 升级残留：老落位 .atk/atk_use.md 与新位置内容同源，留着会有两份互相矛盾的说明书
    new_use = use_file(root)
    if new_use.is_file():
        for old in legacy_use_files(root):
            if old != new_use and old.is_file():
                try:
                    old.unlink()
                except OSError:
                    pass

    agents = root / "AGENTS.md"
    if upsert_agents_md(agents, agents_block_for(root, rendered, ui_tool)):
        created.append(str(agents))
    return created, skipped
