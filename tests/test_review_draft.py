"""atk review-draft（草稿评审转正）+ gate 未评审草稿拦截测试。"""
from pathlib import Path

import yaml
from typer.testing import CliRunner

from atk.cli import app

runner = CliRunner()

DRAFT = """scenario: 草稿场景
module: demo
priority: P1
tags: [smoke, ai-generated]
steps:
  - api:
      call: "GET /ping"
      expect: {status: 200}
"""


def _write(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DRAFT, encoding="utf-8")
    return path


def test_approve_strips_tag_and_logs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = _write(tmp_path / "scenarios" / "demo" / "gen-abc-1.yaml")
    r = runner.invoke(app, ["review-draft", str(f), "--by", "qa", "--verdict", "approve"])
    assert r.exit_code == 0, r.output
    raw = yaml.safe_load(f.read_text(encoding="utf-8"))
    assert "ai-generated" not in raw["tags"] and "smoke" in raw["tags"]
    log = yaml.safe_load((tmp_path / "reports" / "draft-reviews.yaml").read_text(encoding="utf-8"))
    assert log[-1]["by"] == "qa" and log[-1]["verdict"] == "approve"


def test_approve_non_draft_exits_2(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "scenarios" / "demo" / "normal.yaml"
    f.parent.mkdir(parents=True)
    f.write_text(DRAFT.replace(", ai-generated", ""), encoding="utf-8")
    r = runner.invoke(app, ["review-draft", str(f), "--by", "qa", "--verdict", "approve"])
    assert r.exit_code == 2


def test_reject_requires_note_and_moves_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = _write(tmp_path / "scenarios" / "demo" / "gen-abc-1.yaml")
    bad = runner.invoke(app, ["review-draft", str(f), "--by", "qa", "--verdict", "reject"])
    assert bad.exit_code != 0 and f.exists()
    r = runner.invoke(
        app, ["review-draft", str(f), "--by", "qa", "--verdict", "reject", "--note", "断言不对"]
    )
    assert r.exit_code == 0, r.output
    assert not f.exists()
    assert (tmp_path / "reports" / "rejected" / "gen-abc-1.yaml").exists()


def test_gate_blocks_unreviewed_draft(tmp_path, monkeypatch):
    import subprocess

    from atk.run_store import ScenarioSummary, StepSummary, create_run, save_run

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-qm", "init"],
        cwd=tmp_path, check=True,
    )
    monkeypatch.chdir(tmp_path)
    f = _write(tmp_path / "scenarios" / "demo" / "gen-abc-1.yaml")
    rec = create_run(base_ref="HEAD", head_ref="HEAD", runs_dir=tmp_path / "reports" / "runs")
    rec.scenarios.append(ScenarioSummary(
        name="草稿场景", file=str(f), passed=True, error_class="none",
        steps=[StepSummary(title="s", passed=True, detail="200")],
    ))
    save_run(rec, runs_dir=tmp_path / "reports" / "runs")
    r = runner.invoke(app, ["gate", rec.run_id, "--repo", str(tmp_path)])
    assert r.exit_code == 1 and "未评审" in r.output
    # 转正后放行
    ok = runner.invoke(app, ["review-draft", str(f), "--by", "qa", "--verdict", "approve"])
    assert ok.exit_code == 0
    r2 = runner.invoke(app, ["gate", rec.run_id, "--repo", str(tmp_path)])
    assert r2.exit_code == 0, r2.output
