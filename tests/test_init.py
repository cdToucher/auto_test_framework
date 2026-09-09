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
    """包内 canonical 与仓库两处副本内容一致（单源多投，改一处必须同步）。"""
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    for name in _skill_names():
        canon = (res.files("atk.skills") / name / "SKILL.md").read_text(encoding="utf-8")
        for copy in (
            repo / "skills" / name / "SKILL.md",
            repo / ".claude" / "skills" / name / "SKILL.md",
        ):
            assert copy.exists(), f"副本缺失: {copy}"
            assert copy.read_text(encoding="utf-8") == canon, copy
        assert "atk smoke" in canon or "atk context" in canon


def test_console_static_package_data_present():
    static = res.files("atk.console") / "static"
    assert (static / "index.html").is_file()
    assets = static / "assets"
    assert assets.is_dir()
    assert any(p.name.endswith(".js") for p in assets.iterdir())
