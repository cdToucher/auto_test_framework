import importlib.resources as res

from typer.testing import CliRunner

from atk.cli import app

LAYOUTS = ("skills", ".claude/skills")


def _skill_names() -> list[str]:
    return sorted(
        p.name for p in res.files("atk.skills").iterdir() if (p / "SKILL.md").is_file()
    )


def test_init_creates_scaffold(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "config/environments.yaml").exists()
    assert (tmp_path / "config/modules.yaml").exists()
    assert (tmp_path / "scenarios/demo/example.yaml").exists()
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
    for layout in LAYOUTS:
        for name in _skill_names():
            p = tmp_path / layout / name / "SKILL.md"
            assert p.exists(), p
            assert "atk" in p.read_text(encoding="utf-8")


def test_init_never_overwrites_skills(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mine: list = []
    for layout in LAYOUTS:
        for name in _skill_names():
            p = tmp_path / layout / name / "SKILL.md"
            p.parent.mkdir(parents=True)
            p.write_text("mine", encoding="utf-8")
            mine.append(p)
    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0
    for p in mine:
        assert p.read_text(encoding="utf-8") == "mine", p


def test_skill_copies_in_sync():
    """包内 canonical（模板）渲染后与仓库两处副本一致（单源多投，改一处必须同步）。"""
    from pathlib import Path

    from atk.skill_install import DEFAULT_UI_TOOL, render_skill

    repo = Path(__file__).resolve().parent.parent
    for name in _skill_names():
        raw = (res.files("atk.skills") / name / "SKILL.md").read_text(encoding="utf-8")
        canon = render_skill(raw, DEFAULT_UI_TOOL)
        for copy in (
            repo / "skills" / name / "SKILL.md",
            repo / ".claude" / "skills" / name / "SKILL.md",
        ):
            assert copy.exists(), f"副本缺失: {copy}"
            assert copy.read_text(encoding="utf-8") == canon, copy
        assert "atk smoke" in canon or "atk context" in canon
        # 副本里不得残留未渲染的模板变量
        assert "{{UI_TOOL}}" not in canon


def test_init_installs_to_all_agent_layouts(tmp_path, monkeypatch):
    """换 Agent 不失效：Claude / Cursor / AGENTS.md 三处都要有。"""
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0, r.output
    for name in _skill_names():
        assert (tmp_path / ".claude" / "skills" / name / "SKILL.md").exists()
        cursor = tmp_path / ".cursor" / "rules" / f"atk-{name}.md"
        assert cursor.exists(), cursor
        head = cursor.read_text(encoding="utf-8").splitlines()[:5]
        assert head[0] == "---" and "alwaysApply: false" in head
    agents = tmp_path / "AGENTS.md"
    assert agents.exists()
    body = agents.read_text(encoding="utf-8")
    assert "atk smoke" in body and "atk record --last" in body


def test_init_renders_ui_tool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["init", "--ui-tool", "playwright"])
    assert r.exit_code == 0, r.output
    smoke = tmp_path / ".claude" / "skills" / "atk-smoke" / "SKILL.md"
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
    assert "atk smoke" in text


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
