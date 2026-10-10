"""next 里必须是可直接执行的命令，且寻址用真实 run_id 而不是 --last。

`.atk/last-run.json` 是**全局单槽**：每次 plan/run/smoke 写记录都会覆盖它。所以只要
有两条 run 在飞（本地改一版、CI 同时在跑同一版），照 `atk record --last` 执行就会
把结论回填到别人的记录上。next 由机器消费，不能依赖一个会被抢写的指针。
"""
import json
import subprocess

import pytest
from typer.testing import CliRunner

from atk.cli import app

cli = CliRunner()

API_OK = """
scenario: ping 通
module: demo
priority: P0
env: local
steps:
  - api:
      call: "GET /ping"
      expect: { status: 200 }
"""

UI_PENDING = """
scenario: 页面能下单
module: demo
priority: P0
env: local
steps:
  - ui:
      action: 打开下单页并提交
      expect: 列表出现待支付订单
"""

# 加载期就会被 loader 拒掉（steps 为空），属于"库坏了"而不是"用例失败了"
BROKEN = "scenario: 坏文件\nmodule: demo\npriority: P0\nsteps: []\n"


def _git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def proj(tmp_path, monkeypatch, mock_base_url):
    """一个带 git 基线、指向 mock_server 的最小接入项目。"""
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "a.yaml").write_text(API_OK, encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "environments.yaml").write_text(
        f"local:\n  base_url: '{mock_base_url}'\n  vars: {{}}\n", encoding="utf-8"
    )
    (tmp_path / "config" / "modules.yaml").write_text(
        "modules:\n  demo:\n    - \"src/*\"\n", encoding="utf-8"
    )
    _git("init", "-q", cwd=tmp_path)
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base", cwd=tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "logic.py").write_text("x=1", encoding="utf-8")
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "change", cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _json(*args):
    res = cli.invoke(app, list(args))
    return res.exit_code, json.loads(res.output)


# ------------------------------------------------------------- plan 的退出码

def test_plan_with_healthy_library_exits_zero(proj):
    code, payload = _json("plan", "--base", "HEAD~1", "--format", "json")
    assert code == 0, payload
    assert payload["load_errors"] == [] and payload["blockers"] == []


def test_plan_with_broken_scenario_exits_two(proj):
    """加载不了的 YAML 是"没测成"（2），不是"这次改动坏了"（1）。"""
    (proj / "scenarios" / "bad.yaml").write_text(BROKEN, encoding="utf-8")
    code, payload = _json("plan", "--base", "HEAD~1", "--format", "json")
    assert code == 2, payload
    assert payload["blockers"] and payload["load_errors"]
    assert payload["next"] == ["atk validate"]


def test_plan_text_and_json_agree_on_exit_code(proj):
    (proj / "scenarios" / "bad.yaml").write_text(BROKEN, encoding="utf-8")
    txt = cli.invoke(app, ["plan", "--base", "HEAD~1"])
    js = cli.invoke(app, ["plan", "--base", "HEAD~1", "--format", "json"])
    assert txt.exit_code == js.exit_code == 2


def test_run_and_plan_share_one_projection(proj):
    """同一个坏库，两个命令必须给同一个码——此前 runner 判 1、plan 判 0。"""
    (proj / "scenarios" / "bad.yaml").write_text(BROKEN, encoding="utf-8")
    plan = cli.invoke(app, ["plan", "--base", "HEAD~1", "--format", "json"])
    run = cli.invoke(app, ["run", "--format", "json"])
    assert plan.exit_code == run.exit_code == 2, (plan.output, run.output)


# ---------------------------------------------------------------- next 的寻址

def test_run_without_a_record_does_not_point_at_last(proj):
    """没落记录时不许指回 --last：那时它还指着上一条 run。"""
    code, payload = _json("run", "--format", "json")
    assert payload["run_id"] is None
    assert payload["next"] == ["atk run --record-new"]
    assert "--last" not in json.dumps(payload["next"], ensure_ascii=False)


def test_run_next_names_the_record_it_just_created(proj):
    code, payload = _json("run", "--record-new", "--format", "json")
    rid = payload["run_id"]
    assert rid
    assert payload["scenarios"]["failed"] == 0
    assert payload["next"] == [f"atk gate {rid} --format json"]


def test_ui_backfill_command_carries_the_run_id(proj):
    (proj / "scenarios" / "a.yaml").write_text(UI_PENDING, encoding="utf-8")
    code, payload = _json("run", "--record-new", "--format", "json")
    rid = payload["run_id"]
    assert payload["scenarios"]["pending_ui"] == 1
    [cmd] = payload["next"]
    assert cmd.startswith(f"atk record {rid} ")
    assert "--last" not in cmd


def test_report_and_gate_next_carry_the_run_id(proj, approve_library):
    # 用 plan→run --record-to 而不是 run --record-new：后者产出的记录没绑定基线，
    # 会被 gate 的 unbound_record 拦下（那正是它该被拦的情形）
    # 沿袭豁免已删除：记录里每个场景都要有签名审批。存量一次性批掉，
    # 这组用例测的是 next 命令带不带真实 run_id，不是审批。
    approve_library(proj)
    rid = _json("plan", "--base", "HEAD~1", "--format", "json")[1]["run_id"]
    assert _json("run", "--record-to", rid, "--format", "json")[0] == 0
    assert _json("report", rid, "--format", "json")[1]["next"] == [f"atk gate {rid} --format json"]
    assert _json("last", "--format", "json")[1]["next"] == [f"atk gate {rid} --format json"]
    _code, gate = _json("gate", rid, "--format", "json")
    assert all(rid in c for c in gate["next"]), gate["next"]
    assert all("--last" not in c for c in gate["next"])
    # 未整单确认时，介入点 2 的提示里也要能直接执行
    assert gate["requires_human"] is True
    assert rid in (gate["human_prompt"] or "")
