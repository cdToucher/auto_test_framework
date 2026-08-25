from typer.testing import CliRunner

from atk.cli import app

BAD = "module: x\n"
GOOD = 'scenario: s1\nsteps:\n  - api:\n      call: "GET /ping"\n'
DUP = 'scenario: dup\nsteps:\n  - api:\n      call: "GET /ping"\n'


def test_validate_reports_errors_and_dups(tmp_path, monkeypatch):
    sc = tmp_path / "scenarios"
    sc.mkdir()
    (sc / "bad.yaml").write_text(BAD, encoding="utf-8")
    (sc / "ok.yaml").write_text(GOOD, encoding="utf-8")
    d = sc / "d"
    d.mkdir()
    (d / "a.yaml").write_text(DUP, encoding="utf-8")
    (d / "b.yaml").write_text(DUP, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["validate", "--root", "scenarios"])
    assert r.exit_code == 1
    assert "bad.yaml" in r.output
    assert "重复" in r.output
    assert "1 个场景通过校验" in r.output


def test_validate_clean(tmp_path, monkeypatch):
    sc = tmp_path / "scenarios"
    sc.mkdir()
    (sc / "ok.yaml").write_text(GOOD, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["validate", "--root", "scenarios"])
    assert r.exit_code == 0, r.output


def test_validate_empty_dir(tmp_path, monkeypatch):
    (tmp_path / "scenarios").mkdir()
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["validate", "--root", "scenarios"])
    assert r.exit_code == 0
