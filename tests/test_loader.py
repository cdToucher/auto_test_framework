from pathlib import Path

from atk.store.loader import load_scenarios, select
from atk.store.models import Priority


def _write(root: Path, rel: str, text: str):
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")


def _make_tree(tmp_path: Path) -> Path:
    _write(
        tmp_path,
        "order/create_order.yaml",
        "scenario: 下单\nmodule: order\npriority: P0\ntags: [smoke, order]\n"
        'steps:\n  - api:\n      call: "GET /ping"\n',
    )
    _write(
        tmp_path,
        "user/login.yml",
        'scenario: 登录\nmodule: user\npriority: P1\ntags: [smoke]\n'
        'steps:\n  - api:\n      call: "GET /ping"\n',
    )
    return tmp_path


def test_load_scenarios_includes_yml_and_fills_file(tmp_path):
    scs, errors = load_scenarios(_make_tree(tmp_path))
    assert errors == []
    assert len(scs) == 2
    assert all(s.file for s in scs)
    assert {s.module for s in scs} == {"order", "user"}


def test_select_filters(tmp_path):
    scs, _ = load_scenarios(_make_tree(tmp_path))
    assert len(select(scs, module="order")) == 1
    assert len(select(scs, tags=["smoke"])) == 2
    assert len(select(scs, priority=Priority.P0)) == 1
    assert len(select(scs, module="user", priority=Priority.P0)) == 0


def test_bad_files_collected_as_errors_not_fatal(tmp_path):
    _write(tmp_path, "a_missing_key.yaml", "just_a_string")
    _write(tmp_path, "b_empty.yaml", "")
    _write(tmp_path, "c_no_scenario.yaml", "module: x\nsteps: []\n")
    _write(
        tmp_path,
        "d_empty_steps.yaml",
        'scenario: 空\nsteps: []\n',
    )
    _write(
        tmp_path,
        "ok.yaml",
        'scenario: 正常\nsteps:\n  - api:\n      call: "GET /ping"\n',
    )
    scs, errors = load_scenarios(tmp_path)
    assert len(scs) == 1 and scs[0].scenario == "正常"
    assert len(errors) == 4
    assert any("b_empty" in e or "c_no_scenario" in e for e in errors)
