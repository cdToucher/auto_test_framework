import subprocess

import pytest
import yaml
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
    from atk.run_store import (
        ScenarioSummary,
        StepSummary,
        create_run,
        save_run,
    )

    rec = create_run(runs_dir=repo / "reports" / "runs")
    rec.base_ref = "HEAD~1"
    rec.head_ref = "HEAD"
    rec.affected_files = ["src/demo/logic.py"]
    # 新 gate 规则：无任何场景与意图的记录不放行，评审用例需带一条通过结果
    rec.scenarios.append(
        ScenarioSummary(
            name="s", file="scenarios/s.yaml", passed=True, error_class="none",
            steps=[StepSummary(title="step", passed=True, detail="200")],
        )
    )
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


def test_review_reject_persists_with_note(repo):
    """reject 落盘内容：by/verdict/note/at 全量持久化。"""
    from atk.run_store import load_run

    rec = _make_run(repo)
    r = CliRunner().invoke(
        app, ["review", rec.run_id, "--by", "dev2", "--verdict", "reject", "--note", "实现不对"]
    )
    assert r.exit_code == 0, r.output
    got = load_run(rec.run_id, runs_dir=repo / "reports" / "runs")
    assert len(got.reviews) == 1
    assert got.reviews[0].by == "dev2"
    assert got.reviews[0].verdict == "reject"
    assert got.reviews[0].note == "实现不对"
    assert got.reviews[0].at != ""
    raw = (repo / "reports" / "runs" / rec.run_id / "run.yaml").read_text(encoding="utf-8")
    assert "reject" in raw and "实现不对" in raw


def test_review_reject_requires_note(repo):
    rec = _make_run(repo)
    r = CliRunner().invoke(
        app, ["review", rec.run_id, "--by", "dev1", "--verdict", "reject"]
    )
    assert r.exit_code != 0


def test_review_invalid_verdict(repo):
    rec = _make_run(repo)
    r = CliRunner().invoke(
        app, ["review", rec.run_id, "--by", "dev1", "--verdict", "maybe"]
    )
    assert r.exit_code != 0
    assert "verdict 必须是" in r.output


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
    assert "已获开发确认" in r.output
    assert "未经开发确认" not in r.output
    assert "已被开发驳回" not in r.output


def test_gate_reject_warns_but_passes(repo):
    """gate 驳回告警分支：仅告警、exit 仍 0。"""
    rec = _make_run(repo)
    rr = CliRunner().invoke(
        app, ["review", rec.run_id, "--by", "dev2", "--verdict", "reject", "--note", "实现不对"]
    )
    assert rr.exit_code == 0, rr.output
    r = CliRunner().invoke(app, ["gate", rec.run_id])
    assert r.exit_code == 0, r.output
    assert "⚠ 已被开发驳回 by dev2" in r.output
    assert "仅告警，不拦截" in r.output
    assert "门禁结论：放行" in r.output


def test_gate_last_review_wins_approve(repo):
    """reject 后再 approve：按最后一条判定为已确认。"""
    rec = _make_run(repo)
    run = CliRunner().invoke
    assert run(app, ["review", rec.run_id, "--by", "d1", "--verdict", "reject", "--note", "no"]).exit_code == 0
    assert run(app, ["review", rec.run_id, "--by", "d2", "--verdict", "approve"]).exit_code == 0
    r = run(app, ["gate", rec.run_id])
    assert r.exit_code == 0, r.output
    assert "已获开发确认 by d2" in r.output
    assert "已被开发驳回" not in r.output
    assert "未经开发确认" not in r.output


def test_gate_last_review_wins_reject(repo):
    """approve 后再 reject：按最后一条判定为被驳回。"""
    rec = _make_run(repo)
    run = CliRunner().invoke
    assert run(app, ["review", rec.run_id, "--by", "d1", "--verdict", "approve"]).exit_code == 0
    assert run(app, ["review", rec.run_id, "--by", "d2", "--verdict", "reject", "--note", "回退"]).exit_code == 0
    r = run(app, ["gate", rec.run_id])
    assert r.exit_code == 0, r.output
    assert "已被开发驳回 by d2" in r.output
    assert "已获开发确认" not in r.output


def test_old_yaml_without_new_fields_loads(repo):
    """旧 run.yaml 无 title/commits/reviews 字段仍可加载，默认空值。"""
    from atk.run_store import load_run

    rec = _make_run(repo)
    f = repo / "reports" / "runs" / rec.run_id / "run.yaml"
    data = yaml.safe_load(f.read_text(encoding="utf-8"))
    for k in ("title", "commits", "reviews"):
        data.pop(k, None)
    f.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    got = load_run(rec.run_id, runs_dir=repo / "reports" / "runs")
    assert got.title == ""
    assert got.commits == []
    assert got.reviews == []


def _render_review_html(tmp_path, verdict=None):
    from atk.reporter.html_reporter import render_run_html
    from atk.run_store import CommitInfo, ReviewRecord, RunRecord

    reviews = (
        [ReviewRecord(by="dev1", verdict=verdict, note="n" if verdict == "reject" else "", at="2026-09-08T00:00:00")]
        if verdict
        else []
    )
    rec = RunRecord(
        run_id="smoke-x",
        created_at="2026-09-08T00:00:00",
        title="登录改造",
        base_ref="HEAD~1",
        head_ref="HEAD",
        affected_files=["a.py"],
        commits=[CommitInfo(short="abc123", subject="feat: login")],
        reviews=reviews,
    )
    out = render_run_html(rec, tmp_path / "r.html")
    return out.read_text(encoding="utf-8")


def test_report_renders_title_commits_and_approved(tmp_path):
    """report 渲染三段：功能标题 + 提交清单 + 确认状态（已获开发确认）。"""
    html = _render_review_html(tmp_path, verdict="approve")
    assert "功能：登录改造" in html
    assert "提交清单" in html and "abc123" in html and "feat: login" in html
    assert "确认状态" in html and "已获开发确认 by dev1" in html


def test_report_renders_rejected_and_unconfirmed(tmp_path):
    html_reject = _render_review_html(tmp_path, verdict="reject")
    assert "已被开发驳回 by dev1" in html_reject
    assert "已获开发确认" not in html_reject
    html_none = _render_review_html(tmp_path, verdict=None)
    assert "未经开发确认" in html_none


def test_report_last_review_wins(tmp_path):
    from atk.reporter.html_reporter import render_run_html
    from atk.run_store import ReviewRecord, RunRecord

    rec = RunRecord(
        run_id="smoke-x",
        created_at="2026-09-08T00:00:00",
        reviews=[
            ReviewRecord(by="d1", verdict="reject", note="no", at="2026-09-08T00:00:00"),
            ReviewRecord(by="d2", verdict="approve", note="", at="2026-09-08T00:00:01"),
        ],
    )
    html = render_run_html(rec, tmp_path / "r.html").read_text(encoding="utf-8")
    assert "已获开发确认 by d2" in html
    assert "已被开发驳回" not in html
