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
            rel_parts = f.relative_to(root).parts
            # 最近父目录即模块目录：scenarios/<...>/<module>/<file>.yaml
            dir_module = rel_parts[-2] if len(rel_parts) > 1 else ""
            # module 缺省时以最近父目录名兜底：目录即模块是控制台浏览口径，
            # 两者不一致会让 --module 静默选不中场景
            if dir_module and "module" not in raw:
                raw = {**raw, "module": dir_module}
            sc = Scenario.from_raw(raw, file=str(f))
            sc.dir_module = dir_module
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
    """按模块/标签/优先级过滤，三个条件同时成立才保留。

    - module：精确相等。模块名来自 modules.yaml 的映射，不是路径通配。
    - tags：**交集**语义——给定的每个标签都要命中（`--tags smoke,api` 是"且"不是"或"）。
    - priority：上限语义，选 P1 会连带 P0 一起跑，不是只跑 P1。

    这里不因选中为空而报错：由 CLI 按"选中 0 个场景 = 受阻 exit 2"处理，
    免得空库和过滤过严两种情况被混成同一种失败。
    """
    out = scenarios
    if module:
        out = [s for s in out if s.module == module]
    if tags:
        out = [s for s in out if set(tags) <= set(s.tags)]
    if priority:
        rank = {Priority.P0: 0, Priority.P1: 1, Priority.P2: 2}
        out = [s for s in out if rank[s.priority] <= rank[priority]]
    return out
