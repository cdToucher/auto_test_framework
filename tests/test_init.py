import importlib.resources as res

from typer.testing import CliRunner

from atk.cli import app

SKILL_DIR = ".atk/skills"  # 单一 skill 布局；AGENTS.md 负责让 AI 可见


def _skill_names() -> list[str]:
    return sorted(
        p.name for p in res.files("atk.skills").iterdir() if (p / "SKILL.md").is_file()
    )


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
        p = tmp_path / SKILL_DIR / name / "SKILL.md"
        assert p.exists(), p
        assert "atk" in p.read_text(encoding="utf-8")


def test_init_never_overwrites_skills(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mine: list = []
    for name in _skill_names():
        p = tmp_path / SKILL_DIR / name / "SKILL.md"
        p.parent.mkdir(parents=True)
        p.write_text("mine", encoding="utf-8")
        mine.append(p)
    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0
    for p in mine:
        assert p.read_text(encoding="utf-8") == "mine", p


def test_package_skill_source_is_self_consistent():
    """包内单一源：渲染后无模板残留、含核心命令词（仓库内旧布局副本由 atk purge 清理，不再比对）。"""
    from atk.skill_install import DEFAULT_UI_TOOL, render_skill

    for name in _skill_names():
        raw = (res.files("atk.skills") / name / "SKILL.md").read_text(encoding="utf-8")
        canon = render_skill(raw, DEFAULT_UI_TOOL)
        assert "{{UI_TOOL}}" not in canon
        assert "atk smoke" in canon or "atk context" in canon


def test_init_single_layout_and_agents_visibility(tmp_path, monkeypatch):
    """单一 .atk 布局：说明书在 .atk/atk_use.md，AGENTS.md 只留入口指针、不重复正文。"""
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0, r.output
    for name in _skill_names():
        assert (tmp_path / SKILL_DIR / name / "SKILL.md").exists()
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".cursor").exists()
    assert not (tmp_path / "skills").exists()

    body = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    use = (tmp_path / ".atk" / "atk_use.md").read_text(encoding="utf-8")
    assert ".atk/atk_use.md" in body          # AI 从 AGENTS.md 能找到说明书
    assert "atk agent" in body                # 不知道下一步跑什么的入口
    for name in _skill_names():
        assert f".atk/skills/{name}/SKILL.md" in body
    # 说明书正文已经移走：AGENTS.md 里不该再有命令清单和硬规则（两处会漂移）
    assert "atk gate --last --format json" not in body
    assert "选中 0 个场景" not in body
    # 命令清单与硬规则在 atk_use.md
    assert "atk gate --last --format json" in use
    assert "requires_human" in use
    assert "选中 0 个场景" in use


def test_init_rewrites_atk_use_from_package_source(tmp_path, monkeypatch):
    """atk_use.md 是生成物：手改过再 init 要重生成，否则说明书会悄悄过期。"""
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(app, ["init"])
    use = tmp_path / ".atk" / "atk_use.md"
    use.write_text("我手写的说明\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0, r.output
    assert "atk gate --last --format json" in use.read_text(encoding="utf-8")


def test_init_skill_bodies_are_not_overwritten(tmp_path, monkeypatch):
    """与说明书相反：skill 正文开发者会改，init 只建缺失。"""
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(app, ["init"])
    name = _skill_names()[0]
    skill = tmp_path / SKILL_DIR / name / "SKILL.md"
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


def test_init_preserves_existing_agents_md(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# 我的项目规范\n\n别乱改。\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0, r.output
    text = agents.read_text(encoding="utf-8")
    assert text.startswith("# 我的项目规范")
    assert "别乱改。" in text
    assert ".atk/atk_use.md" in text


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
