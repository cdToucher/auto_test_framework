import importlib.resources as res

from typer.testing import CliRunner

from atk import skill_install
from atk.cli import app

SKILL_DIR = ".atk/skills"  # 单一 skill 布局；AGENTS.md 负责让 AI 可见


def _skill_names() -> list[str]:
    """包内 skill 名单（入口文件名可能是特例名，枚举规则只在 skill_install 一处）。"""
    return sorted(skill_install.package_skill_texts())


def _installed(tmp_path, name):
    return tmp_path / SKILL_DIR / name / skill_install.entry_name(name)


def test_init_creates_scaffold(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / ".atk/environments.yaml").exists()
    assert (tmp_path / ".atk/modules.yaml").exists()
    assert (tmp_path / ".atk/scenarios/demo/example.yaml").exists()
    assert "下一步" in r.output
    # 场景库应可直接通过校验
    v = CliRunner().invoke(app, ["validate"])
    assert v.exit_code == 0, v.output


def test_init_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "environments.yaml").write_text("myenv:\n  base_url: http://keep\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0
    content = (cfg / "environments.yaml").read_text(encoding="utf-8")
    assert content.startswith("myenv:")  # 未被覆盖


def test_init_installs_skills(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0, r.output
    assert _skill_names(), "包内 skill 为空"
    for name in _skill_names():
        p = skill_install.installed_path(tmp_path, name)
        assert p.exists(), p
        assert "atk" in p.read_text(encoding="utf-8")


def test_use_skill_filename_is_at_able():
    """说明书的文件名必须独特：@ 唤出时按 basename 找，三份 SKILL.md 分不出谁是谁。"""
    names = _skill_names()
    assert skill_install.USE_SKILL in names, "包内缺 atk-use 说明书 skill"
    basenames = [skill_install.entry_name(n) for n in names]
    assert basenames.count(skill_install.USE_ENTRY) == 1
    assert skill_install.entry_name(skill_install.USE_SKILL) == "atk_use.md"
    # 其它工作流正文保持通用约定，便于将来接客户端的 skill 目录
    for n in names:
        if n != skill_install.USE_SKILL:
            assert skill_install.entry_name(n) == "SKILL.md"


def test_manual_commands_all_exist():
    """防漂移：说明书里出现的 `atk <命令>` 必须真在命令注册表里。

    说明书是包内手写文本，命令改名后它不会自己跟着变，靠这条测试兜住。
    """
    import re

    from atk.cli import app as typer_app

    registered = {c.name or c.callback.__name__ for c in typer_app.registered_commands}
    text = skill_install.package_skill_texts()[skill_install.USE_SKILL]
    used = set(re.findall(r"`atk ([a-z][a-z-]*)", text)) | set(
        re.findall(r"^atk ([a-z][a-z-]*)", text, re.M))
    assert used, "说明书里一个命令都没提，测试就白写了"
    unknown = used - registered
    assert not unknown, f"说明书提到不存在的命令：{sorted(unknown)}"


def test_init_never_overwrites_workflow_skills(tmp_path, monkeypatch):
    """工作流正文开发者会改：init 只建不覆盖。说明书是例外，见下一条。"""
    monkeypatch.chdir(tmp_path)
    mine: list = []
    for name in _skill_names():
        if name in skill_install.REGENERATED:
            continue
        p = skill_install.installed_path(tmp_path, name)
        p.parent.mkdir(parents=True)
        p.write_text("mine", encoding="utf-8")
        mine.append(p)
    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0
    for p in mine:
        assert p.read_text(encoding="utf-8") == "mine", p


def test_package_skill_source_is_self_consistent():
    """包内单一源：渲染后无模板残留、含核心命令词（仓库内旧布局副本由 atk purge 清理，不再比对）。"""
    for name, raw in skill_install.package_skill_texts().items():
        canon = skill_install.render_skill(
            raw, ui_tool=skill_install.DEFAULT_UI_TOOL,
            layout_note="（测试）", skill_index="- 索引占位")
        assert "{{UI_TOOL}}" not in canon
        assert "{{LAYOUT}}" not in canon
        assert "{{SKILL_INDEX}}" not in canon
        assert "atk smoke" in canon or "atk context" in canon


def test_init_single_layout_and_agents_visibility(tmp_path, monkeypatch):
    """单一 .atk 布局：说明书是 atk-use skill，AGENTS.md 只留入口指针、不重复正文。"""
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0, r.output
    use = skill_install.use_file(tmp_path)
    assert use.is_file() and use.name == "atk_use.md"
    for name in _skill_names():
        assert skill_install.installed_path(tmp_path, name).exists()
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".cursor").exists()
    assert not (tmp_path / "skills").exists()

    body = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    text = use.read_text(encoding="utf-8")
    assert ".atk/skills/atk-use/atk_use.md" in body   # AI 从 AGENTS.md 能找到说明书
    assert "@atk_use" in body                        # 且告诉人/Agent 怎么唤出
    assert "atk agent" in body                        # 不知道下一步跑什么的入口
    for name in _skill_names():
        if name != skill_install.USE_SKILL:
            assert f".atk/skills/{name}/{skill_install.entry_name(name)}" in body
    # 说明书正文已经移走：AGENTS.md 里不该再有命令清单和硬规则（两处会漂移）
    assert "atk gate --last --format json" not in body
    assert "选中 0 个场景" not in body
    # 命令清单、硬规则、技能索引在说明书里，且索引已注入
    assert "atk gate --last --format json" in text
    assert "requires_human" in text
    assert "选中 0 个场景" in text
    assert "{{SKILL_INDEX}}" not in text and ".atk/skills/atk-smoke/SKILL.md" in text
    # 它是带 frontmatter 的 skill
    assert skill_install.split_frontmatter(text)[0].get("name") == "atk-use"


def test_init_rewrites_manual_from_package_source(tmp_path, monkeypatch):
    """说明书是生成物：手改过再 init 要重生成，否则它会悄悄过期误导下一个 AI。"""
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(app, ["init"])
    use = skill_install.use_file(tmp_path)
    use.write_text("我手写的说明\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0, r.output
    assert "atk gate --last --format json" in use.read_text(encoding="utf-8")


def test_init_upgrades_legacy_manual_location(tmp_path, monkeypatch):
    """老落位 .atk/atk_use.md 不能和新位置并存，否则两份说明书互相矛盾。"""
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(app, ["init"])
    legacy = tmp_path / ".atk" / "atk_use.md"
    legacy.write_text("旧版说明书\n", encoding="utf-8")
    CliRunner().invoke(app, ["init"])
    assert not legacy.exists()
    assert skill_install.use_file(tmp_path).is_file()


def test_init_skill_bodies_are_not_overwritten(tmp_path, monkeypatch):
    """与说明书相反：工作流正文开发者会改，init 只建缺失。"""
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(app, ["init"])
    name = next(n for n in _skill_names() if n not in skill_install.REGENERATED)
    skill = skill_install.installed_path(tmp_path, name)
    skill.write_text("---\nname: x\ndescription: y\n---\n我的私有流程\n", encoding="utf-8")
    CliRunner().invoke(app, ["init"])
    assert "我的私有流程" in skill.read_text(encoding="utf-8")


def test_init_renders_ui_tool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["init", "--ui-tool", "playwright"])
    assert r.exit_code == 0, r.output
    smoke = tmp_path / SKILL_DIR / "atk-smoke" / "SKILL.md"
    text = smoke.read_text(encoding="utf-8")
    assert "playwright" in text
    assert "{{UI_TOOL}}" not in text
    assert "ego-browser" not in text
    assert "playwright" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    # 说明书里注入的是客户端指定的工具，不是模板占位
    manual = skill_install.use_file(tmp_path).read_text(encoding="utf-8")
    assert "{{UI_TOOL}}" not in manual and "playwright" in manual


def test_init_preserves_existing_agents_md(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# 我的项目规范\n\n别乱改。\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0, r.output
    text = agents.read_text(encoding="utf-8")
    assert text.startswith("# 我的项目规范")
    assert "别乱改。" in text
    assert ".atk/skills/atk-use/atk_use.md" in text


def test_init_agents_md_block_is_idempotent(tmp_path, monkeypatch):
    """重复 init 不应产生第二个 atk 区块。"""
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(app, ["init"])
    CliRunner().invoke(app, ["init"])
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert text.count("<!-- atk:begin -->") == 1


def test_console_static_package_data_present():
    static = res.files("atk.console") / "static"
    assert (static / "index.html").is_file()
    assets = static / "assets"
    assert assets.is_dir()
    assert any(p.name.endswith(".js") for p in assets.iterdir())
