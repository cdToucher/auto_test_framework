"""atk review-draft（草稿评审）+ gate 未评审草稿拦截测试。

转正判据是审批账本里以断言面指纹为键、带有效签名的一条记录；`ai-generated`
tag 只是溯源标记——approve 不再改写场景文件。
"""
from pathlib import Path

import yaml
from typer.testing import CliRunner

from atk.cli import app
from atk.drafts import DRAFT_TAG

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


def test_approve_keeps_the_file_untouched(tmp_path, monkeypatch):
    """approve 不再改写场景文件：转正只发生在审批账本里。

    曾经的"去 tag"是 50 行逐行文本手术 + YAML 重写兜底，只为保留注释排版；
    而 tag 本就是 AI 能写的字符串，删掉它从不等于转正。现在 tag 是溯源标记，
    文件一个字节都不动——之后谁改一条断言，指纹变化，旧审批自动失效。
    """
    monkeypatch.chdir(tmp_path)
    f = _write(tmp_path / "scenarios" / "demo" / "gen-abc-1.yaml")
    before = f.read_bytes()
    r = runner.invoke(app, ["review-draft", str(f), "--by", "qa", "--verdict", "approve"])
    assert r.exit_code == 0, r.output
    assert f.read_bytes() == before, "approve 不应改动场景文件"
    assert DRAFT_TAG in yaml.safe_load(f.read_text(encoding="utf-8"))["tags"]
    log = yaml.safe_load((tmp_path / "reports" / "draft-reviews.yaml").read_text(encoding="utf-8"))
    assert log[-1]["by"] == "qa" and log[-1]["verdict"] == "approve"


def test_approve_never_touches_nested_business_data(tmp_path, monkeypatch):
    """嵌套 tags / body 业务数据：不改文件，就没有"改错字段"这一类风险。

    这条曾专门盯 _strip_draft_tag_linewise 的逐行手术别误伤 body 里的同名 tags
    字段；手术删除后，风险类别整体消失，用例留下钉住这个不变量。
    """
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "scenarios" / "demo" / "gen-nested-tags.yaml"
    f.parent.mkdir(parents=True)
    original = (
        "scenario: 嵌套标签\n"
        "tags: [ai-generated]\n"
        "steps:\n"
        "  - api:\n"
        "      call: 'POST /events'\n"
        "      body:\n"
        "        tags: [ai-generated]\n"
    )
    f.write_text(original, encoding="utf-8")
    r = runner.invoke(app, ["review-draft", str(f), "--by", "qa", "--verdict", "approve"])
    assert r.exit_code == 0, r.output
    assert f.read_text(encoding="utf-8") == original


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


def test_gate_blocks_unreviewed_draft(tmp_path, monkeypatch, human_approval):
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
    # 评审了但拿不出终端凭证：动作记录下来了，审批记录却没有，gate 依然拦
    no_token = runner.invoke(app, ["review-draft", str(f), "--by", "qa",
                                   "--verdict", "approve"])
    assert no_token.exit_code == 0
    r2 = runner.invoke(app, ["gate", rec.run_id, "--repo", str(tmp_path)])
    assert r2.exit_code == 1, r2.output
    assert "未经人工审批" in r2.output
    # 真人铸凭证后放行
    token = human_approval(str(f))
    ok = runner.invoke(app, ["review-draft", str(f), "--by", "qa", "--verdict", "approve",
                             "--approval", token])
    assert ok.exit_code == 0, ok.output
    r3 = runner.invoke(app, ["gate", rec.run_id, "--repo", str(tmp_path)])
    assert r3.exit_code == 0, r3.output


def test_agent_ladder_stops_asking_once_approved(tmp_path, monkeypatch, human_approval):
    """批准之后，atk agent 不能再把这条草稿当未评审介入点。

    tag 永久保留（只作溯源），若 ladder 还按"文件带 tag"判未评审，批准一次
    只会永远卡在 await_expect_review——人工介入点变成死循环。
    """
    import json
    import subprocess

    from atk.last_run import write_last_run
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
    write_last_run(tmp_path, rec.run_id)
    token = human_approval(str(f))
    ok = runner.invoke(app, ["review-draft", str(f), "--by", "qa", "--verdict", "approve",
                             "--approval", token])
    assert ok.exit_code == 0, ok.output
    r = runner.invoke(app, ["agent", "--format", "json"])
    payload = json.loads(r.stdout)
    assert payload["state"] != "await_expect_review", payload
