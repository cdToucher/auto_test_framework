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
