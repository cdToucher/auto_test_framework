"""registry 测试：注册/列表/移除（用临时 db）。"""
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

