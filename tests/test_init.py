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
