import os

from typer.testing import CliRunner

from atk.cli import app


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
    for layout in ("skills", ".claude/skills"):
        for name in ("atk-smoke", "atk-gen"):
            p = tmp_path / layout / name / "SKILL.md"
            assert p.exists(), p
            assert "atk" in p.read_text(encoding="utf-8")


def test_init_never_overwrites_skills(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mine = tmp_path / "skills" / "atk-gen" / "SKILL.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("mine", encoding="utf-8")
    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0
    assert mine.read_text(encoding="utf-8") == "mine"


def test_skill_copies_in_sync():
    """包内 canonical 与仓库两处副本内容一致（单源多投，改一处必须同步）。"""
    import importlib.resources as res
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    for name in ("atk-smoke", "atk-gen"):
        canon = (res.files("atk.skills") / name / "SKILL.md").read_text(encoding="utf-8")
        for copy in (
            repo / "skills" / name / "SKILL.md",
            repo / ".claude" / "skills" / name / "SKILL.md",
        ):
            if copy.exists():
                assert copy.read_text(encoding="utf-8") == canon, copy
        assert "atk smoke" in canon or "atk context" in canon
