"""`atk record` 的取证契约与运行记录的落盘原子性。

`record` 是 AI 唯一能自己写入"结论"的入口，所以它的校验必须是**入口级**的：
一个没有凭据的 pass、一份填错记录的 payload、一条凭空发明的意图，都不该进得了账。
"""
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from atk.cli import app
from atk.run_store import IntentRecord, ScenarioSummary, StepSummary, create_run, load_run, save_run

cli = CliRunner()


@pytest.fixture
def runs(tmp_path):
    return tmp_path / "runs"


def _rec(runs, *intents, with_pass=True):
    rec = create_run(runs_dir=runs)
    rec.intents = list(intents)
    if with_pass and not intents:
        rec.intents.append(IntentRecord(title="页面下单", status="pending"))
    save_run(rec, runs_dir=runs)
    return rec


def _shot(tmp_path, name="s.png"):
    f = tmp_path / name
    f.write_bytes(b"\x89PNG evidence")
    return f


def _args(rec, runs, *extra):
    return ["record", rec.run_id, "--runs-dir", str(runs), *extra]


# ---------------------------------------------------------------- 取证要求

def test_pass_without_evidence_is_rejected(runs, tmp_path):
    rec = _rec(runs)
    r = cli.invoke(app, _args(rec, runs, "--title", "页面下单", "--status", "pass",
                              "--note", "看着没问题"))
    assert r.exit_code == 1, r.output
    assert "--evidence" in r.output


def test_pass_without_note_is_rejected(runs, tmp_path):
    rec = _rec(runs)
    r = cli.invoke(app, _args(rec, runs, "--title", "页面下单", "--status", "pass",
                              "--evidence", str(_shot(tmp_path))))
    assert r.exit_code == 1, r.output


def test_missing_evidence_file_exits_two_instead_of_silently_skipping(runs, tmp_path):
    """过去这条会 exit 0 并留下一条看着完整的证据列表，缺的那份只在终端提一句。"""
    rec = _rec(runs)
    good = _shot(tmp_path)
    r = cli.invoke(app, _args(rec, runs, "--title", "页面下单", "--status", "pass",
                              "--note", "订单可见", "--evidence",
                              f"{good},{tmp_path / 'ghost.png'}"))
    assert r.exit_code == 2, r.output
    assert "ghost.png" in r.output
    after = load_run(rec.run_id, runs)
    assert after.intents[0].status == "pending", "被拒的回填不许改动当前态"


def test_blocked_requires_note_but_no_artifact(runs):
    rec = _rec(runs)
    assert cli.invoke(app, _args(rec, runs, "--title", "页面下单", "--status", "blocked",
                                "--note", "服务 502")).exit_code == 0
    rec2 = _rec(runs)
    assert cli.invoke(app, _args(rec2, runs, "--title", "页面下单", "--status", "blocked",
                                "--note", " ")).exit_code == 1


def test_inventing_an_unregistered_intent_needs_an_explicit_flag(runs, tmp_path):
    """凭空补一条本次运行没覆盖的意图 = 发明覆盖面，默认拒绝。"""
    rec = _rec(runs)
    r = cli.invoke(app, _args(rec, runs, "--title", "我从没测过的功能", "--status", "pass",
                              "--note", "顺手记一条", "--evidence", str(_shot(tmp_path))))
    assert r.exit_code == 2, r.output
    assert "--allow-new-intent" in r.output
    ok = cli.invoke(app, _args(rec, runs, "--title", "我从没测过的功能", "--status", "pass",
                               "--note", "确实新增", "--evidence", str(_shot(tmp_path)),
                               "--allow-new-intent"))
    assert ok.exit_code == 0, ok.output
    assert any(i.title == "我从没测过的功能" for i in load_run(rec.run_id, runs).intents)


# ---------------------------------------------------------------- --from-json

def _payload(tmp_path, rec, **over):
    body = {"run_id": rec.run_id, "title": "页面下单", "status": "pass",
            "note": "订单在列表可见", "evidence": [str(_shot(tmp_path, "ui.png"))]}
    body.update(over)
    p = tmp_path / "ui.json"
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return p


def test_from_json_must_declare_which_run_it_fills(runs, tmp_path):
    """不带 run_id 的 payload 可以套在任意一条记录上——这是最容易串台的一条。"""
    rec = _rec(runs)
    p = tmp_path / "loose.json"
    p.write_text(json.dumps({"title": "页面下单", "status": "pass", "note": "ok",
                             "evidence": [str(_shot(tmp_path))]}, ensure_ascii=False),
                 encoding="utf-8")
    r = cli.invoke(app, ["record", rec.run_id, "--runs-dir", str(runs), "--from-json", str(p)])
    assert r.exit_code == 2, r.output
    assert "run_id" in r.output


def test_from_json_with_wrong_run_id_is_rejected(runs, tmp_path):
    rec = _rec(runs)
    other = _rec(runs)
    p = _payload(tmp_path, other)
    r = cli.invoke(app, ["record", rec.run_id, "--runs-dir", str(runs), "--from-json", str(p)])
    assert r.exit_code == 2, r.output


def test_status_is_a_literal_enum_not_a_free_string(runs, tmp_path):
    """`PASSED` 过去会被原样收下，落进 run.yaml 变成 gate 谁都不认的第四十六种状态。"""
    rec = _rec(runs)
    p = _payload(tmp_path, rec, status="PASSED")
    r = cli.invoke(app, ["record", rec.run_id, "--runs-dir", str(runs), "--from-json", str(p)])
    assert r.exit_code == 2, r.output
    assert load_run(rec.run_id, runs).intents[0].status == "pending"


def test_unknown_payload_key_is_rejected(runs, tmp_path):
    """多余键说明两边对不上，静默忽略会让 AI 以为自己的字段生效了。"""
    rec = _rec(runs)
    p = tmp_path / "extra.json"
    p.write_text(json.dumps({"run_id": rec.run_id, "title": "页面下单", "status": "pass",
                             "note": "ok", "evidence": [str(_shot(tmp_path))],
                             "confidence": 0.9}), encoding="utf-8")
    r = cli.invoke(app, ["record", rec.run_id, "--runs-dir", str(runs), "--from-json", str(p)])
    assert r.exit_code == 2, r.output


def test_pass_records_the_approver_and_timestamps(runs, tmp_path):
    rec = _rec(runs)
    assert cli.invoke(app, _args(rec, runs, "--title", "页面下单", "--status", "pass",
                                 "--note", "订单可见", "--evidence", str(_shot(tmp_path)),
                                 "--by", "lisi")).exit_code == 0
    after = load_run(rec.run_id, runs)
    it = after.intents[0]
    assert it.status == "pass" and it.at and it.by == "lisi"
    # 证据要留下内容指纹，不只是文件名：文件名可以被换成任意一个空文件
    assert after.evidence_sha and list(after.evidence_sha.values())[0]


# ---------------------------------------------------------------- 落盘原子性

def test_save_run_leaves_no_temp_file_and_bumps_revision(runs):
    rec = create_run(runs_dir=runs)
    first = load_run(rec.run_id, runs).revision
    for i in range(3):
        rec.scenarios = [ScenarioSummary(name=f"s{i}", passed=True, error_class="none",
                                         steps=[StepSummary(title="t", passed=True)])]
        save_run(rec, runs_dir=runs)
    d = runs / rec.run_id
    assert not list(d.glob("*.tmp")), "原子写不该留下残渣"
    assert load_run(rec.run_id, runs).revision == first + 3
    assert load_run(rec.run_id, runs).updated_at


def test_no_torn_read_is_possible(runs):
    """读到的要么是旧的一份、要么是新的一份：内容必须始终能解析。"""
    rec = create_run(runs_dir=runs)
    save_run(rec, runs_dir=runs)
    f = runs / rec.run_id / "run.yaml"
    for i in range(20):
        rec.title = f"第 {i} 次改写"
        save_run(rec, runs_dir=runs)
        assert yaml.safe_load(f.read_text(encoding="utf-8"))["title"] == f"第 {i} 次改写"
    assert load_run(rec.run_id, runs).title == "第 19 次改写"
