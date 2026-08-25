import re
import subprocess

import pytest
from typer.testing import CliRunner

from atk.cli import app

SCENARIO = (
    "scenario: 登录冒烟\nmodule: demo\npriority: P0\ntags: [smoke]\nenv: local\n"
    "steps:\n  - api:\n      call: \"GET /ping\"\n      expect: {status: 200}\n"
)


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def m2_repo(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "modules.yaml").write_text('modules:\n  demo: ["src/demo/**"]\n', encoding="utf-8")
    sc = tmp_path / "scenarios" / "demo"
    sc.mkdir(parents=True)
    (sc / "login.yaml").write_text(SCENARIO, encoding="utf-8")
    (tmp_path / "reports").mkdir()
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=tmp_path)
    d = tmp_path / "src" / "demo"
    d.mkdir(parents=True)
    (d / "logic.py").write_text("x=1")
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "change demo", cwd=tmp_path)
    return tmp_path


def test_diff_outputs_groups(m2_repo, monkeypatch):
    monkeypatch.chdir(m2_repo)
    r = CliRunner().invoke(app, ["diff", "--base", "HEAD~1", "--head", "HEAD"])
    assert r.exit_code == 0, r.output
    assert '"demo"' in r.output and "src/demo/logic.py" in r.output


def test_plan_creates_run_record(m2_repo, mock_base_url, monkeypatch):
    monkeypatch.chdir(m2_repo)
    (m2_repo / "config" / "environments.yaml").write_text(
        f"local:\n  base_url: {mock_base_url}\n", encoding="utf-8"
    )
    r = CliRunner().invoke(app, ["plan", "--base", "HEAD~1", "--head", "HEAD"])
    assert r.exit_code == 0, r.output
    m = re.search(r"run_id: (\S+)", r.output)
    assert m, r.output
    from atk.run_store import load_run

    rec = load_run(m.group(1), runs_dir=m2_repo / "reports" / "runs")
    assert rec.affected_modules == ["demo"]
    assert rec.planned_scenarios == ["scenarios/demo/login.yaml"]


def test_record_appends_intent(m2_repo, tmp_path, monkeypatch):
    monkeypatch.chdir(m2_repo)
    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG")
    from atk.run_store import create_run, load_run

    rec = create_run(runs_dir=m2_repo / "reports" / "runs")
    r = CliRunner().invoke(
        app,
        [
            "record",
            rec.run_id,
            "--title",
            "登录页 UI 冒烟",
            "--status",
            "pass",
            "--note",
            "快照断言通过",
            "--evidence",
            str(shot),
        ],
    )
    assert r.exit_code == 0, r.output
    got = load_run(rec.run_id, runs_dir=m2_repo / "reports" / "runs")
    assert got.intents[0].title == "登录页 UI 冒烟"
    assert got.intents[0].evidence == ["evidence/s.png"]
    assert (m2_repo / "reports" / "runs" / rec.run_id / "evidence" / "s.png").exists()


def test_record_rejects_bad_status_and_missing_run(m2_repo, monkeypatch):
    monkeypatch.chdir(m2_repo)
    from atk.run_store import create_run

    rec = create_run(runs_dir=m2_repo / "reports" / "runs")
    bad = CliRunner().invoke(app, ["record", rec.run_id, "--title", "x", "--status", "ok"])
    assert bad.exit_code != 0
    gone = CliRunner().invoke(app, ["record", "smoke-none", "--title", "x"])
    assert gone.exit_code == 2


def test_run_record_to_merges_summaries(m2_repo, mock_base_url, monkeypatch):
    monkeypatch.chdir(m2_repo)
    (m2_repo / "config" / "environments.yaml").write_text(
        f"local:\n  base_url: {mock_base_url}\n", encoding="utf-8"
    )
    from atk.run_store import create_run, load_run

    rec = create_run(runs_dir=m2_repo / "reports" / "runs")
    r = CliRunner().invoke(app, ["run", "--record-to", rec.run_id])
    assert r.exit_code == 0, r.output
    got = load_run(rec.run_id, runs_dir=m2_repo / "reports" / "runs")
    assert len(got.scenarios) == 1
    assert got.scenarios[0].name == "登录冒烟"
    assert got.scenarios[0].passed is True
