"""registry 测试：注册/列表/移除/索引重建（用临时 db）。"""
from pathlib import Path

from atk.console import registry as reg


def test_upsert_list_remove(tmp_path: Path):
    db = tmp_path / "reg.db"
    p1 = tmp_path / "projA"
    p1.mkdir()
    reg.upsert_project(p1, db=db)
    reg.upsert_project(p1, db=db)  # 幂等
    items = reg.list_projects(db=db)
    assert len(items) == 1 and items[0]["name"] == "projA"

    reg.remove_project(str(p1), db=db)
    assert reg.list_projects(db=db) == []


def test_rebuild_index(tmp_path: Path):
    db = tmp_path / "reg.db"
    proj = tmp_path / "projB"
    rd = proj / "reports" / "runs" / "smoke-1"
    rd.mkdir(parents=True)
    (rd / "run.yaml").write_text(
        "run_id: smoke-1\ncreated_at: '2026-08-26T10:00:00'\n"
        "scenarios:\n- name: a\n  passed: true\n"
        "- name: b\n  passed: false\n", encoding="utf-8")
    reg.upsert_project(proj, db=db)
    n = reg.rebuild_index(db=db)
    assert n == 1
