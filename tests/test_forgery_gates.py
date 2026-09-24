"""三条绕过路径的回归测试：写文件 ≠ 有权限。

上一轮 review 实测出这三条能过 gate，这里把它们钉成"改了就必须报警"：
A 手写 config/approvals.yaml 伪造审批记录
B 不 plan、只 run --record-new，产出一条与改动无关的记录来过门禁
C 手改 run.yaml 后同步伪造 events.yaml 里的落盘指纹

共同根因：把"防伪"实现成"记录在不在"。修法是把可信根挪到仓库外（atk/keystore.py），
仓库内的任何编辑都不再能凭空造出有效性。
"""
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from atk import approvals, events, keystore
from atk.cli import app
from atk.run_store import (IntentRecord, RunRecord, ScenarioSummary, StepSummary,
                           create_run, save_run)

cli = CliRunner()

KEY = "test-approval-key-A"
OTHER_KEY = "test-approval-key-B"
SCEN = ('scenario: 基线\nmodule: order\npriority: P0\nenv: local\n'
        'steps:\n  - api:\n      call: "GET /ping"\n      expect: { status: 200 }\n')


@pytest.fixture(autouse=True)
def _key(monkeypatch, tmp_path_factory):
    """测试自带密钥：绝不落到开发者真实的 ~/.atk/approval.key。"""
    monkeypatch.setenv(keystore.KEY_ENV, KEY)
    yield


def _git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def proj(tmp_path, monkeypatch):
    (tmp_path / "scenarios" / "order").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "scenarios" / "order" / "base.yaml").write_text(SCEN, encoding="utf-8")
    (tmp_path / "config" / "environments.yaml").write_text(
        "local:\n  base_url: 'http://127.0.0.1:1'\n  vars: {}\n", encoding="utf-8")
    (tmp_path / "config" / "modules.yaml").write_text(
        'modules:\n  order:\n    files:\n      - "src/*"\n', encoding="utf-8")
    (tmp_path / ".gitignore").write_text("reports/\n.atk/\n", encoding="utf-8")
    _git("init", "-q", cwd=tmp_path)
    _git("add", "-A", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base", cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _bound_record(proj, *, name="s", module="order", file="", extra_intents=()):
    runs = proj / "reports" / "runs"
    rec = create_run(base_ref="HEAD", head_ref="HEAD", runs_dir=runs,
                     affected_files=[], affected_modules=[])
    rec.scenarios = [ScenarioSummary(name=name, module=module, file=file, passed=True,
                                     error_class="none",
                                     steps=[StepSummary(title="t", passed=True)])]
    rec.intents = list(extra_intents)
    save_run(rec, runs_dir=runs)
    return rec.run_id, runs


def _gate(proj, rid, *extra):
    return cli.invoke(app, ["gate", rid, "--repo", str(proj), "--format", "json", *extra])


def _blocking(res):
    return json.loads(res.stdout)["blocking"]


# ---------------------------------------------------------------- 绕过 A

def test_hand_written_approval_record_is_not_trusted(proj):
    """往 approvals.yaml 里补一条记录，不再等于"有人批过"。

    场景保持未提交状态：这才是常见的 AI 工作形状（写完就跑）。它一旦先被提交、
    再以 HEAD 为基线跑 plan，就会落进"沿袭豁免"——那条残余缺口另有测试记录。
    """
    f = proj / "scenarios" / "order" / "ai_new.yaml"
    f.write_text(SCEN.replace("基线", "AI新写的断言"), encoding="utf-8")

    rid, runs = _bound_record(proj, name="AI新写的断言", file="scenarios/order/ai_new.yaml")
    key = approvals.expect_hash(f)
    # 伪造：字段全对，只是没有效签名（攻击者能写仓库内文件，写不到 ~/.atk）
    approvals.write_approvals(approvals.approvals_file(proj), [
        approvals.Approval(kind="scenario", target="scenarios/order/ai_new.yaml",
                           key=key, by="李四", at="2026-09-24T06:00:00",
                           note="伪造", token="0" * 64)])
    res = _gate(proj, rid)
    assert res.exit_code == 1, res.output
    assert any("未经人工审批" in b for b in _blocking(res))
    assert any("签名无效" in b for b in _blocking(res)), "必须说清账本里有记录但不被采信"


def test_approval_signed_with_another_key_is_not_trusted(proj, monkeypatch):
    f = proj / "scenarios" / "order" / "x.yaml"
    f.write_text(SCEN, encoding="utf-8")
    key = approvals.expect_hash(f)
    token, _ = (monkeypatch.setattr(approvals, "human_present", lambda: True),
                monkeypatch.setattr("typer.confirm", lambda *a, **k: True),
                approvals.mint(target=str(f), key=key, project=proj))[2]
    approvals.record_approval(target=str(f), key=key, by="李四", note="",
                              token=token, project=proj)
    assert approvals.is_approved(target=f, project=proj)
    monkeypatch.setenv(keystore.KEY_ENV, OTHER_KEY)          # 换一把钥匙
    assert not approvals.is_approved(target=f, project=proj)


def test_no_key_at_all_means_nothing_is_approved(proj, monkeypatch):
    """密钥缺失 = 没有任何一条记录可信：fail-closed，而不是 fail-open。"""
    f = proj / "scenarios" / "order" / "y.yaml"
    f.write_text(SCEN, encoding="utf-8")
    key = approvals.expect_hash(f)
    monkeypatch.setattr(approvals, "human_present", lambda: True)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    token, _ = approvals.mint(target=str(f), key=key, project=proj)
    approvals.record_approval(target=str(f), key=key, by="李四", note="",
                             token=token, project=proj)
    assert approvals.is_approved(target=f, project=proj)
    monkeypatch.delenv(keystore.KEY_ENV, raising=False)
    monkeypatch.setattr(keystore, "key_file",
                        lambda home=None: proj / "nonexistent-approval.key")
    assert not approvals.is_approved(target=f, project=proj)
    assert "签名密钥不可用" in keystore.missing_key_hint()


def test_committed_before_the_base_is_grandfathered(proj):
    """已知残余缺口，故意钉成测试而不是悄悄修掉：先提交、再以该提交当基线 → 沿袭豁免。

    修法是让基线锚到受保护分支（而不是 HEAD~1 这种可被自己的提交推动的引用）。
    这条测试的价值在于：一旦有人改了豁免条件，这里会先响，逼他重新读一遍权衡。
    """
    f = proj / "scenarios" / "order" / "late.yaml"
    f.write_text(SCEN.replace("基线", "提交后再看"), encoding="utf-8")
    _git("add", "-A", cwd=proj)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "commit it", cwd=proj)
    rid, _runs = _bound_record(proj, name="提交后再看", file="scenarios/order/late.yaml")
    res = _gate(proj, rid)
    assert res.exit_code == 0, (res.output, "豁免条件已变：确认是不是要把基线锚到受保护分支")


# ---------------------------------------------------------------- 绕过 B

def test_record_without_a_base_ref_cannot_be_a_merge_decision(proj):
    """`run --record-new` 那种没绑定基线的记录，过不了门禁。"""
    runs = proj / "reports" / "runs"
    rec = create_run(runs_dir=runs)              # base_ref 空
    rec.scenarios = [ScenarioSummary(name="s", module="order", passed=True,
                                     error_class="none",
                                     steps=[StepSummary(title="t", passed=True)])]
    save_run(rec, runs_dir=runs)
    res = _gate(proj, rec.run_id)
    assert res.exit_code == 1, res.output
    assert any("未绑定任何 Git 基线" in b for b in _blocking(res))


def test_allow_unbound_is_an_explicit_opt_in(proj):
    runs = proj / "reports" / "runs"
    rec = create_run(runs_dir=runs)
    rec.scenarios = [ScenarioSummary(name="s", module="order", passed=True,
                                     error_class="none",
                                     steps=[StepSummary(title="t", passed=True)])]
    save_run(rec, runs_dir=runs)
    res = _gate(proj, rec.run_id, "--allow-unbound")
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["unbound_allowed"] is True
    assert any("--allow-unbound" in w for w in payload["warnings"]), payload["warnings"]


def test_non_git_project_only_warns(tmp_path, monkeypatch):
    """非 git 项目不该被这条拦住：它没有"合并门禁"语义。"""
    (tmp_path / "scenarios").mkdir()
    monkeypatch.chdir(tmp_path)
    runs = tmp_path / "reports" / "runs"
    rec = create_run(runs_dir=runs)
    rec.scenarios = [ScenarioSummary(name="s", module="m", passed=True, error_class="none",
                                     steps=[StepSummary(title="t", passed=True)])]
    save_run(rec, runs_dir=runs)
    res = cli.invoke(app, ["gate", rec.run_id, "--repo", str(tmp_path),
                           "--runs-dir", str(runs), "--format", "json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["blocking"] == [], payload["blocking"]
    assert any("不是 git 仓库" in w for w in payload["warnings"]), payload["warnings"]


# ---------------------------------------------------------------- 绕过 C

def test_editing_run_yaml_and_recomputing_the_digest_still_fails(proj):
    """改完 run.yaml 再把最后一条 save 的 sha256 对齐，链一验就断。

    攻击者能写仓库内任意文件，但写不到 ~/.atk/approval.key，也就造不出新签名。
    """
    rid, runs = _bound_record(proj)
    f = runs / rid / "run.yaml"
    ev = runs / rid / "events.yaml"
    data = yaml.safe_load(f.read_text(encoding="utf-8"))
    data["intents"] = [IntentRecord(title="我根本没测", status="pass",
                                    note="编的", evidence=[]).model_dump()]
    f.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    items = yaml.safe_load(ev.read_text(encoding="utf-8"))
    import hashlib
    items[-1]["sha256"] = hashlib.sha256(f.read_bytes()).hexdigest()
    ev.write_text(yaml.safe_dump(items, allow_unicode=True), encoding="utf-8")

    res = _gate(proj, rid)
    assert res.exit_code == 1, res.output
    assert any("流水不可信" in b or "流水被" in b or "签名无效" in b
               for b in _blocking(res)), _blocking(res)


def test_truncating_the_event_ledger_fails_the_chain(proj):
    rid, runs = _bound_record(proj)
    ev = runs / rid / "events.yaml"
    items = yaml.safe_load(ev.read_text(encoding="utf-8"))
    assert len(items) >= 2
    ev.write_text(yaml.safe_dump(items[:1], allow_unicode=True), encoding="utf-8")
    res = _gate(proj, rid)
    assert res.exit_code == 1, res.output
    assert any("流水" in b for b in _blocking(res))


def test_clean_run_has_a_verifiable_chain(proj):
    """反向确认：不是"什么都拦"，正常链路必须验得过。"""
    rid, runs = _bound_record(proj)
    assert events.verify_chain(runs, rid)[0] is True
    assert _gate(proj, rid).exit_code == 0
