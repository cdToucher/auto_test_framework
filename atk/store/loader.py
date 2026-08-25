"""场景库扫描与筛选。坏文件逐个容错：收集错误继续加载其余。"""
from pathlib import Path

import yaml

from .models import Priority, Scenario


def load_scenarios(root: Path | str = "scenarios") -> tuple[list[Scenario], list[str]]:
    """返回 (场景列表, 错误列表)。错误形如 '路径: 原因'，由调用方决定如何呈现。"""
    root = Path(root)
    files = sorted({*root.rglob("*.yaml"), *root.rglob("*.yml")})
    out: list[Scenario] = []
    errors: list[str] = []
    for f in files:
        try:
            raw = yaml.safe_load(f.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or "scenario" not in raw:
                raise ValueError("根节点不是映射或缺少 scenario 字段")
            sc = Scenario.from_raw(raw, file=str(f))
            if not sc.steps:
                raise ValueError("steps 为空，至少需要一个步骤")
            out.append(sc)
        except Exception as e:
            errors.append(f"{f}: {e}")
    return out, errors


def select(
    scenarios: list[Scenario],
    module: str | None = None,
    tags: list[str] | None = None,
    priority: Priority | None = None,
) -> list[Scenario]:
    out = scenarios
    if module:
        out = [s for s in out if s.module == module]
    if tags:
        out = [s for s in out if set(tags) <= set(s.tags)]
    if priority:
        rank = {Priority.P0: 0, Priority.P1: 1, Priority.P2: 2}
        out = [s for s in out if rank[s.priority] <= rank[priority]]
    return out
