"""增量选择必须有事实依据，且任何算不清都往"多跑"倒。

本仓库自己就是这条的反例（修之前）：config/modules.yaml 里没有
examples/shop_pro.py，而它是 scenarios/shop_pro/ 下 12 个场景的被测系统 ——
改它一个字符，plan 选中 0 个场景，gate 照样放行。这里把那条钉成回归测试。
"""
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from atk import deps
from atk.cli import app
from atk.diff_analyzer.modules import classify, load_module_map, module_map_status
from atk.store.loader import load_scenarios
from atk.store.models import ApiStep

cli = CliRunner()
REPO = Path(__file__).resolve().parents[1]
SCEN = REPO / "scenarios"
MODULES = REPO / "config" / "modules.yaml"


def _git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


# ------------------------------------------------------------ 端点归一化

@pytest.mark.parametrize("call,want", [
    ("GET /ping", "GET /ping"),
    ("get  /ping", "GET /ping"),
    ("POST /api/login", "POST /api/login"),
    ("GET /api/orders?orderNo=${orderNo}", "GET /api/orders?orderNo=*"),
    ("GET /api/orders?orderNo=SP-001", "GET /api/orders?orderNo=*"),
    ("GET /api/products/42", "GET /api/products/*"),
    ("GET /api/products/${sku}", "GET /api/products/*"),
    # 参数名排序，保证同一请求的两种书写方式归一到同一个键
    ("GET /a?b=1&a=2", "GET /a?a=*&b=*"),
])
def test_normalize_endpoint_collapses_values_keeps_shape(call, want):
    assert deps.normalize_endpoint(call) == want


def test_touchset_is_static_and_pure_ui_scenarios_have_none():
    scs, errors = load_scenarios(SCEN)
    assert not errors
    login = next(s for s in scs if "登录" in s.scenario)
    assert any(e.startswith("POST ") and e.endswith("/api/login") for e in deps.touchset(login))
    # 纯 ui: 场景不打任何接口：它的触达面是空的，只能靠 module 命中被选上。
    # 这条不是 bug，但决定了"端点命中"永远覆盖不了 UI 层。
    pure_ui = [s for s in scs if s.steps and not deps.touchset(s)]
    assert pure_ui, "预期存在纯 UI 场景"
    assert all(any(not isinstance(st, ApiStep) for st in s.steps) for s in pure_ui)


# ------------------------------------------------------------ 多命中分类

def test_classify_no_longer_stops_at_first_match():
    m = {"todo": ["examples/demo_app.py"], "ui": ["examples/demo_app.py"]}
    groups = classify(["examples/demo_app.py"], m)
    assert set(groups) == {"todo", "ui"}


def test_classify_collects_unmapped():
    groups = classify(["docs/x.md", "src/a.py"], {"demo": ["src/*"]})
    assert groups["demo"] == ["src/a.py"]
    assert groups["__unmapped__"] == ["docs/x.md"]


# ------------------------------------------------------------ 本仓库的回归

def test_repo_module_map_covers_every_scenario_module():
    """doctor 的 error 侧：有场景没映射 = 改这类代码永远选不到它。"""
    scs, _ = load_scenarios(SCEN)
    rep = deps.doctor(MODULES, scs)
    assert rep.errors == [], rep.errors


def test_repo_maps_the_shop_pro_server():
    """改 examples/shop_pro.py 必须能选到它那 12 个场景（修之前是 0 个）。"""
    scs, _ = load_scenarios(SCEN)
    groups = classify(["examples/shop_pro.py"], load_module_map(MODULES))
    affected = set(groups) - {deps.UNMAPPED}
    assert affected == {"order", "auth", "shop"}, groups
    sel = deps.select_for_changes(scenarios=scs, affected=affected,
                                  unmapped_files=[], ledger={})
    shop_pro = [s for s in scs if "shop_pro/" in str(s.file)]
    assert len(shop_pro) == 12
    assert {s.file for s in shop_pro} <= {s.file for s in sel.scenarios}


def test_changing_shop_pro_selects_its_checkout_scenarios():
    scs, _ = load_scenarios(SCEN)
    affected = set(classify(["examples/shop_pro.py"], load_module_map(MODULES))) - {deps.UNMAPPED}
    sel = deps.select_for_changes(scenarios=scs, affected=affected,
                                 unmapped_files=[], ledger={})
    names = {s.scenario for s in sel.scenarios}
    assert any("券" in n or "coupon" in n.lower() for n in names), names
    assert sel.mode == deps.MODE_MODULE


# ------------------------------------------------------------ 保守方向

def test_unmapped_code_file_widens_to_full_suite():
    scs, _ = load_scenarios(SCEN)
    sel = deps.select_for_changes(scenarios=scs, affected={"order"},
                                  unmapped_files=["src/untracked.py"], ledger={})
    assert sel.mode == deps.MODE_FULL
    assert len(sel.scenarios) == len(scs)
    assert sel.warnings


def test_docs_only_change_does_not_widen_to_full_suite():
    """只改 README 不该惊动全库，否则这条规则三周内就会被所有人忽略掉。"""
    scs, _ = load_scenarios(SCEN)
    sel = deps.select_for_changes(scenarios=scs, affected={"order"},
                                  unmapped_files=["README.md", "docs/DESIGN.md"], ledger={})
    assert sel.mode == deps.MODE_MODULE
    assert sel.unmapped_files == ["README.md", "docs/DESIGN.md"]


def test_unknown_extension_counts_as_relevant():
    assert deps.is_behavior_relevant("src thing.weirdext")
    assert not deps.is_behavior_relevant("docs/a.md")
    assert not deps.is_behavior_relevant("LICENSE")


def test_changed_scenario_is_pulled_in_even_when_out_of_scope(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    """内容变了的场景必须重跑，即使它的模块本次没被改到、端点也不重叠。"""
    a = _scenario("a", "touched", "GET /a")
    b = _scenario("b", "untouched", "GET /b", base=tmp_path)
    ledger = {deps._key(b): {"hash": "stale", "last_run": "old"}}
    sel = deps.select_for_changes(scenarios=[a, b], affected={"touched"},
                                  unmapped_files=[], ledger=ledger)
    names = {s.scenario for s in sel.scenarios}
    assert names == {"a", "b"}
    assert "场景内容已变" in sel.reasons[deps._key(b)]


def test_unchanged_out_of_scope_scenario_is_left_out(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    """没变、模块没被改、端点不重叠 —— 才允许不选，否则增量选择没有意义。"""
    b = _scenario("b", "untouched", "GET /b", base=tmp_path)
    ledger = {deps._key(b): {"hash": deps.file_hash(b.file), "last_run": "old"}}
    sel = deps.select_for_changes(scenarios=[b], affected={"touched"},
                                  unmapped_files=[], ledger=ledger)
    assert sel.scenarios == []


def test_endpoint_overlap_pulls_in_the_other_module():
    """两个模块打同一个端点时，改其中一个就把另一个也选上（共享依赖）。"""
    a = _scenario("a", "demo", "GET /ping")
    b = _scenario("b", "other", "GET /ping")
    sel = deps.select_for_changes(scenarios=[a, b], affected={"demo"},
                                  unmapped_files=[], ledger={})
    assert {s.scenario for s in sel.scenarios} == {"a", "b"}


def _scenario(name, module, call, base=None):
    """合成一条场景；给了 base 就真落盘，让内容指纹这条规则可测。"""
    from atk.store.models import Scenario

    path = f"scenarios/{module}/{name}.yaml"
    if base is not None:
        p = Path(base) / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"scenario: {name}\n", encoding="utf-8")
    return Scenario.from_raw(
        {"scenario": name, "module": module, "priority": "P0",
         "steps": [{"api": {"call": call, "expect": {"status": 200}}}]},
        file=path)


# ------------------------------------------------------------ 账本

def test_ledger_roundtrip_and_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert deps.load_ledger(tmp_path / "nope.yaml") == {}
    a = _scenario("a", "demo", "GET /ping", base=tmp_path)
    r = type("R", (), {"scenario": a, "passed": True, "error_class": "none", "env": "local"})()
    n = deps.record_execution([r], run_id="smoke-1", path=tmp_path / "deps.yaml")
    assert n == 1
    led = deps.load_ledger(tmp_path / "deps.yaml")
    entry = led[next(iter(led))]
    assert entry["last_run"] == "smoke-1" and entry["endpoints"] == ["GET /ping"]
    assert entry["hash"]


def test_ledger_is_atomic_and_not_left_half_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "deps.yaml"
    a = _scenario("a", "demo", "GET /ping", base=tmp_path)
    r = type("R", (), {"scenario": a, "passed": True, "error_class": "none", "env": ""})()
    deps.record_execution([r], run_id="smoke-1", path=p)
    assert not list(tmp_path.glob("*.tmp"))
    assert p.read_text(encoding="utf-8").startswith("scenarios/")


# ------------------------------------------------------------ CLI 层兜底

@pytest.fixture
def proj(tmp_path, monkeypatch, mock_base_url):
    (tmp_path / "scenarios" / "demo").mkdir(parents=True)
    (tmp_path / "scenarios" / "demo" / "a.yaml").write_text(
        "scenario: a\nmodule: demo\npriority: P0\ntags: [smoke]\nenv: local\n"
        'steps:\n  - api:\n      call: "GET /ping"\n      expect: { status: 200 }\n',
        encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "environments.yaml").write_text(
        f"local:\n  base_url: '{mock_base_url}'\n  vars: {{}}\n", encoding="utf-8")
    _git("init", "-q", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base",
         "--allow-empty", cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _commit(proj, rel, text):
    p = proj / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    _git("add", "-A", cwd=proj)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "change", cwd=proj)


def test_plan_without_module_map_never_selects_zero(proj):
    """没有 modules.yaml 时旧行为是"选 0 个然后当通过"，现在必须是全量。"""
    _commit(proj, "src/app.py", "x = 1\n")
    assert module_map_status(proj / "config/modules.yaml") == "missing"
    res = cli.invoke(app, ["plan", "--base", "HEAD~1", "--format", "json"], catch_exceptions=False)
    payload = json.loads(res.output)
    assert payload["selection"]["mode"] == deps.MODE_FULL
    assert len(payload["reuse"]) == 1


def test_plan_with_unmapped_code_reports_selection(proj):
    (proj / "config/modules.yaml").write_text(
        'modules:\n  demo:\n    files:\n      - "app/*"\n', encoding="utf-8")
    _commit(proj, "src/other.py", "y = 2\n")   # app/* 管不到 src/other.py
    res = cli.invoke(app, ["plan", "--base", "HEAD~1", "--format", "json"], catch_exceptions=False)
    payload = json.loads(res.output)
    assert payload["selection"]["mode"] == deps.MODE_FULL
    assert "src/other.py" in payload["selection"]["unmapped_files"]
    assert payload["selection"]["warnings"]


def test_plan_writes_the_ledger_only_for_executed_scenarios(proj):
    (proj / "config/modules.yaml").write_text(
        'modules:\n  demo:\n    files:\n      - "src/*"\n', encoding="utf-8")
    _commit(proj, "src/app.py", "x = 2\n")
    run = cli.invoke(app, ["run", "--record-new", "--format", "json"], catch_exceptions=False)
    rid = json.loads(run.output)["run_id"]
    led = deps.load_ledger(deps.ledger_path(proj))
    assert len(led) == 1
    assert next(iter(led.values()))["last_run"] == rid


def test_validate_fails_on_a_module_with_no_rule(proj):
    (proj / "config/modules.yaml").write_text(
        'modules:\n  unrelated:\n    files:\n      - "src/*"\n', encoding="utf-8")
    res = cli.invoke(app, ["validate"])
    assert res.exit_code == 1, res.output
    assert "模块 'demo'" in res.output
