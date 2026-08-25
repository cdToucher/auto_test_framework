import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from atk.cli import app
from atk.run_store import (
    IntentRecord,
    RunRecord,
    ScenarioSummary,
    StepSummary,
    save_run,
)

SCEN = 'scenario: s\nmodule: m\nsteps:\n  - api:\n      call: "GET /ping"\n'


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
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
    return tmp_path


def _save(rec: RunRecord, repo: Path):
    rec.base_ref = "HEAD~1"
    rec.head_ref = "HEAD"
    rec.affected_files = ["src/demo/logic.py"]
    rec.affected_modules = ["demo"]
    save_run(rec, runs_dir=repo / "reports" / "runs")


def test_gate_missing_run(repo, monkeypatch):
    monkeypatch.chdir(repo)
    r = CliRunner().invoke(app, ["gate", "smoke-none"])
    assert r.exit_code == 2


def test_gate_pass(repo, monkeypatch):
    from atk.run_store import create_run

    monkeypatch.chdir(repo)
    rec = create_run(runs_dir=repo / "reports" / "runs")
    rec.scenarios.append(ScenarioSummary(
        name="s", passed=True, error_class="none",
        steps=[StepSummary(title="step", passed=True, detail="200")],
    ))
    _save(rec, repo)
    r = CliRunner().invoke(app, ["gate", rec.run_id])
    assert r.exit_code == 0, r.output


def test_gate_blocks_on_case_failure(repo, monkeypatch):
    from atk.run_store import create_run

    monkeypatch.chdir(repo)
    rec = create_run(runs_dir=repo / "reports" / "runs")
    rec.scenarios.append(ScenarioSummary(
        name="s", passed=False, error_class="assertion",
        steps=[StepSummary(title="step", passed=False, detail="status 500")],
    ))
    _save(rec, repo)
    r = CliRunner().invoke(app, ["gate", rec.run_id])
    assert r.exit_code == 1
    assert "用例失败" in r.output


def test_gate_blocks_on_unresolved_suspect(repo, monkeypatch):
    from atk.run_store import create_run

    monkeypatch.chdir(repo)
    rec = create_run(runs_dir=repo / "reports" / "runs")
    rec.intents.append(IntentRecord(title="探索", status="suspect", note="疑似"))
    _save(rec, repo)
    r = CliRunner().invoke(app, ["gate", rec.run_id])
    assert r.exit_code == 1
    assert "未定性" in r.output


def test_gate_blocked_intent_warns_but_passes(repo, monkeypatch):
    from atk.run_store import create_run

    monkeypatch.chdir(repo)
    rec = create_run(runs_dir=repo / "reports" / "runs")
    rec.intents.append(IntentRecord(title="环境不可用", status="blocked", note="服务宕机"))
    _save(rec, repo)
    r = CliRunner().invoke(app, ["gate", rec.run_id])
    assert r.exit_code == 0, r.output
    assert "受阻" in r.output


def test_gate_drift_detection(repo, monkeypatch):
    from atk.run_store import create_run

    monkeypatch.chdir(repo)
    rec = create_run(runs_dir=repo / "reports" / "runs")
    _save(rec, repo)
    rec.affected_files = ["ghost.py"]  # 篡改声称的变更集，模拟漂移
    save_run(rec, runs_dir=repo / "reports" / "runs")
    r = CliRunner().invoke(app, ["gate", rec.run_id])
    assert r.exit_code == 1
    assert "漂移" in r.output
