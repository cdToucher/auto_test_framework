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
        "user/login.yaml",
        'scenario: 登录\nmodule: user\npriority: P1\ntags: [smoke]\n'
        'steps:\n  - api:\n      call: "GET /ping"\n',
    )
    return tmp_path


def test_load_scenarios(tmp_path):
    scs = load_scenarios(_make_tree(tmp_path))
    assert len(scs) == 2
    assert all(s.file for s in scs)
    assert {s.module for s in scs} == {"order", "user"}


def test_select_filters(tmp_path):
    scs = load_scenarios(_make_tree(tmp_path))
    assert len(select(scs, module="order")) == 1
    assert len(select(scs, tags=["smoke"])) == 2
    assert len(select(scs, priority=Priority.P0)) == 1
    assert len(select(scs, module="user", priority=Priority.P0)) == 0


def test_skips_invalid_yaml_files(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("just_a_string", encoding="utf-8")
    (tmp_path / "empty.yaml").write_text("", encoding="utf-8")
    assert load_scenarios(tmp_path) == []
