"""console.repo 测试：树扫描、加载、保存（门禁/锁/移动）。"""
import pytest

from atk.console.repo import ConflictError, YamlError, load_scenario, save_scenario, scan_tree

GOOD = """\
scenario: 下单
module: order
priority: P0
tags: [smoke]
env: local
steps:
  - api:
      call: "POST /orders"
      expect: { status: 200 }
"""
BAD_YAML = "scenario: [unclosed\n"


@pytest.fixture
def proj(tmp_path):
    d = tmp_path / "scenarios"
    (d / "order").mkdir(parents=True)
    (d / "order" / "create.yaml").write_text(GOOD, encoding="utf-8")
    (d / "broken.yaml").write_text(BAD_YAML, encoding="utf-8")
    return tmp_path


def test_scan_tree(proj):
    tree = scan_tree(proj)
    by_path = {s["path"]: s for s in tree["scenarios"]}
    assert by_path["order/create.yaml"]["name"] == "create.yaml"
    dirs = {d["name"]: d for d in tree["dirs"]}
    assert set(dirs) == {"order"}
    # 非法文件也列出，标记 error 供前端降级
    assert by_path["broken.yaml"].get("error")


def test_load_scenario_ok_and_error(proj):
    data, mtime = load_scenario(proj, "order/create.yaml")
    assert data["scenario"] == "下单"
    assert mtime > 0
    with pytest.raises(YamlError) as ei:
        load_scenario(proj, "broken.yaml")
    assert "line" in str(ei.value)


def test_save_validates_before_write(proj):
    bad = {"module": "x", "steps": []}
    with pytest.raises(ValueError):
        save_scenario(proj, "order/create.yaml", bad, move_to=None, if_mtime=None)
    assert load_scenario(proj, "order/create.yaml")[0]["scenario"] == "下单"


def test_save_optimistic_lock(proj):
    _, mtime = load_scenario(proj, "order/create.yaml")
    good = _yaml_load(GOOD)
    with pytest.raises(ConflictError):
        save_scenario(proj, "order/create.yaml", good,
                      move_to=None, if_mtime=mtime - 1, force=False)
    save_scenario(proj, "order/create.yaml", good,
                  move_to=None, if_mtime=mtime - 1, force=True)


def test_save_move(proj):
    good = _yaml_load(GOOD)
    save_scenario(proj, "order/create.yaml", good,
                  move_to="order/new-name.yaml", if_mtime=None)
    assert not (proj / "scenarios/order/create.yaml").exists()
    assert (proj / "scenarios/order/new-name.yaml").exists()


def _yaml_load(text):
    import yaml
    return yaml.safe_load(text)
