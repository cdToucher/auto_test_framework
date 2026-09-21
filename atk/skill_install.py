"""Agent skill 安装：包内单一源 -> `.atk/skills/` + `.atk/atk_use.md`，AGENTS.md 只留入口指针。

布局决策（收束版）：
- skill 正文只写 `.atk/skills/<name>/SKILL.md` 一份，说明书只写 `.atk/atk_use.md` 一份，
  不再铺 `.claude/`、`.cursor/`、根 `skills/`。
- 主流 TUI/CLI（Codex/OpenCode/Claude/Qoder 等）只自动加载项目根 AGENTS.md，
  所以那里必须留一段窄指针把 AI 引到 `.atk/atk_use.md`；命令清单与硬规则一律不重复，
  避免"AGENTS.md 里那份"和"init 新生成的那份"两处漂移。
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


def _command_index() -> str:
    """命令清单：说明书正文用，AGENTS.md 不再重复。"""
    return (
        "```bash\n"
        "atk agent --format json                    # 不知道下一步跑什么就问它\n"
        'atk smoke --title "<功能>" --base <基线> [--env <环境>] --format json\n'
        "atk context --base <基线> --format json     # 变更上下文包（起草场景用）\n"
        "atk validate                                # 场景库自查\n"
        "atk run --dry-run                           # 先看会跑哪些场景，不执行\n"
        "atk run [--env <env>] [--set k=v] [--record-new] [--skip-ui]\n"
        'atk record --last --title "<意图>" --status <pass|fail|suspect|blocked> --note "<证据>"\n'
        "atk record --last --from-json <result.json> # UI 实测批量回填（推荐）\n"
        "atk review-draft <草稿.yaml> --by <人> --verdict approve|reject\n"
        "atk review --last --by <人> --verdict approve\n"
        "atk gate --last --format json\n"
        "```"
    )


def atk_use_text(skills: dict[str, str], ui_tool: str, root_note: str) -> str:
    """AI 说明书正文（`.atk/atk_use.md`）。

    这里是原 AGENTS.md 区块的全部内容——正文与技能索引放这份文件，AGENTS.md 只留
    指向它的窄指针：说明书每次 init 由包内源重生成，不会和版本漂移，也不占 AGENTS.md。
    """
    index = "\n".join(
        f"- `.atk/skills/{name}/SKILL.md` —— {split_frontmatter(text)[0].get('description', name)}"
        for name, text in sorted(skills.items())
    )
    return (
        "# atk 使用说明（AI 必读）\n\n"
        "> 本文件由 `atk init` 生成，是 atk 工作流的单一说明书。\n"
        "> AGENTS.md 里的 atk 区块只保留指向此处的指针，请勿在本文件里写项目私有说明——"
        "重新 init 会覆盖它。\n\n"
        f"本项目接入 atk（AI 原生双层自动化测试框架）。{root_note}\n"
        f"UI/E2E 实测工具：`{ui_tool}`。\n\n"
        "## 怎么触发：不要背流程，问 atk\n\n"
        "1. 执行 `atk agent --format json`，读返回的 `next` 数组。\n"
        "2. `next` 非空就照顺序执行下一条命令，跑完再回第 1 步（状态会变，命令会变）。\n"
        f"3. 返回里 `requires_human: true` 时**停下**，把 `human_prompt` 原样转给开发者，"
        "拿到答复再回第 1 步。这是两个人工介入点（审 expect、整单确认）的机器可读形式，"
        "不得自行代答。\n"
        "4. 返回里 `blockers` 非空时先解阻塞，不要硬跑。\n"
        "5. 全流程结论必须落进 atk 记录（`atk record` / `atk review`），禁止只口头汇报。\n\n"
        "## 技能正文（按 atk agent 指示再读，不必一次读完）\n\n"
        f"{index}\n\n"
        "## 常用命令\n\n"
        "流程里的命令都支持 `--format json`（`init`/`purge`/`console` 例外），"
        "输出统一 envelope：\n"
        "`--last` 免去拼 run_id（指针在 `.atk/last-run.json`）。\n\n"
        f"{_command_index()}\n\n"
        "### envelope 字段\n\n"
        "| 字段 | 含义 |\n|---|---|\n"
        "| `ok` / `exit_code` | 命令结论；0 通过 · 1 拦截或失败 · 2 受阻/配置错 |\n"
        "| `data` | 该命令的结构化结果（原有键保持不变） |\n"
        "| `next` | 可直接执行的 atk 命令列表，按序走 |\n"
        "| `requires_human` | 该步需要人判断，AI 必须停下 |\n"
        "| `human_prompt` | 给人看的问题（配合 requires_human） |\n"
        "| `blockers` | 阻塞原因列表，非空时先处理它 |\n\n"
        "## 硬规则\n\n"
        "- 断言不止 status，必须断关键业务字段（支持 `< 90`、`contains: x`、`regex:`、`len:`、`type:`）。\n"
        f"- `ui:` 步骤 atk run 不执行，必须由 AI 用 `{ui_tool}` 实测后 `atk record` 回填；未回填 `atk gate` 拦截。\n"
        "- 回填 status 无默认值：实测是什么就记什么，不得把 blocked 写成 pass。\n"
        "- 不得编造接口/字段；AI 草稿（tags 含 ai-generated）未经人工评审转正，gate 拦截。\n"
        "- 选中 0 个场景按受阻处理（exit 2），空跑不算通过。\n"
        "- 环境/账号问题记 blocked，不得归为业务失败（fail）。\n"
    )


def _agents_block(skills: dict[str, str], ui_tool: str) -> str:
    """AGENTS.md 区块：只留入口指针。

    必须保留这一小段：TUI/CLI agent 只会自动加载 AGENTS.md（或等价文件），
    完全清空就没有任何东西把 AI 引到 .atk/atk_use.md 了。
    """
    names = "、".join(f"`.atk/skills/{n}/SKILL.md`" for n in sorted(skills))
    return (
        f"{BEGIN}\n"
        "## atk 自动化测试（AI 入口）\n\n"
        "本项目接入 atk（AI 原生双层自动化测试框架）。\n\n"
        "- **做任何 atk 相关工作前，先完整阅读 `.atk/atk_use.md`**"
        "（说明书：触发循环、命令清单、envelope 字段、硬规则都在那里）。\n"
        f"- 技能正文：{names or '（未安装）'}。\n"
        f"- UI/E2E 实测工具：`{ui_tool}`。\n"
        "- 不知道下一步跑什么：`atk agent --format json`，按 `next` 走；"
        "返回 `requires_human: true` 必须停下问人。\n"
        f"{END}\n"
    )


def agents_block_for(root: Path | str = ".", skills: dict[str, str] | None = None,
                     ui_tool: str = DEFAULT_UI_TOOL) -> str:
    """供 init 生成/更新 AGENTS.md 区块；legacy 与新布局措辞不同。"""
    return _agents_block(skills or {}, ui_tool)


def atk_use_text_for(root: Path | str = ".", skills: dict[str, str] | None = None,
                     ui_tool: str = DEFAULT_UI_TOOL) -> str:
    """供 init 生成 `.atk/atk_use.md`；legacy 与新布局措辞不同。"""
    skills = skills or {}
    if layout.is_legacy(root):
        root_note = "场景库 `scenarios/`、配置 `config/`、产物 `reports/`（旧布局）。"
    else:
        root_note = "全部 atk 产物收束在 `.atk/` 目录（场景库 `.atk/scenarios/`）。"
    return atk_use_text(skills, ui_tool, root_note)


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
    """单一布局：`.atk/skills/` 正文 + `.atk/atk_use.md` 说明书 + AGENTS.md 窄指针。

    skill 正文只建缺失、绝不覆盖（开发者会改它）；`atk_use.md` 每次都按包内源重生成
    （它是说明书，被手改会在下次 init 悄悄漂移，项目私有说明请写进 AGENTS.md 区块外）。
    AGENTS.md 按标记块幂等替换。返回 (新建或更新的文件, 跳过的已存在文件)。
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

    use = layout.atk_use_file(root)
    use_text = atk_use_text_for(root, rendered, ui_tool)
    if use.is_file() and use.read_text(encoding="utf-8") == use_text:
        skipped.append(str(use))
    else:
        use.parent.mkdir(parents=True, exist_ok=True)
        use.write_text(use_text, encoding="utf-8")
        created.append(str(use))

    agents = root / "AGENTS.md"
    if upsert_agents_md(agents, agents_block_for(root, rendered, ui_tool)):
        created.append(str(agents))
    return created, skipped
