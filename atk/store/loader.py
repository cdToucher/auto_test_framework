"""场景库扫描与筛选。"""
from pathlib import Path

import yaml

from .models import Priority, Scenario


def load_scenarios(root: Path | str = "scenarios") -> list[Scenario]:
    root = Path(root)
    out: list[Scenario] = []
    for f in sorted(root.rglob("*.yaml")):
        raw = yaml.safe_load(f.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "scenario" not in raw:
            continue
        out.append(Scenario.from_raw(raw, file=str(f)))
    return out


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
