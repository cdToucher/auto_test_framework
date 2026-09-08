import subprocess

import pytest
from typer.testing import CliRunner

from atk.cli import app

SCEN = 'scenario: s\nmodule: m\nsteps:\n  - api:\n      call: "GET /ping"\n'


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios/s.yaml").write_text(SCEN, encoding="utf-8")
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=tmp_path)
    d = tmp_path / "src" / "demo"
    d.mkdir(parents=True)
    (d / "logic.py").write_text("x=1")
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "change demo", cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _make_run(repo):
    from atk.run_store import create_run, save_run

    rec = create_run(runs_dir=repo / "reports" / "runs")
    rec.base_ref = "HEAD~1"
    rec.head_ref = "HEAD"
    rec.affected_files = ["src/demo/logic.py"]
    save_run(rec, runs_dir=repo / "reports" / "runs")
    return rec


def test_review_approve_persists(repo):
    from atk.run_store import load_run

    rec = _make_run(repo)
    r = CliRunner().invoke(
        app, ["review", rec.run_id, "--by", "dev1", "--verdict", "approve", "--note", "ok"]
    )
    assert r.exit_code == 0, r.output
    got = load_run(rec.run_id, runs_dir=repo / "reports" / "runs")
    assert len(got.reviews) == 1
    assert got.reviews[0].by == "dev1"
    assert got.reviews[0].verdict == "approve"
    assert got.reviews[0].at != ""


def test_review_reject_requires_note(repo):
    rec = _make_run(repo)
    r = CliRunner().invoke(
        app, ["review", rec.run_id, "--by", "dev1", "--verdict", "reject"]
    )
    assert r.exit_code != 0


def test_gate_warns_without_review_but_passes(repo):
    rec = _make_run(repo)
    r = CliRunner().invoke(app, ["gate", rec.run_id])
    assert r.exit_code == 0, r.output
    assert "未经开发确认" in r.output


def test_gate_no_warn_with_approve(repo):
    from atk.run_store import load_run, save_run

    rec = _make_run(repo)
    rr = CliRunner().invoke(
        app, ["review", rec.run_id, "--by", "dev1", "--verdict", "approve"]
    )
    assert rr.exit_code == 0, rr.output
    r = CliRunner().invoke(app, ["gate", rec.run_id])
    assert r.exit_code == 0, r.output
    assert "未经开发确认" not in r.output
    assert "⚠" not in r.output
