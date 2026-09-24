"""模块映射：被测代码路径通配 → 测试模块（场景的 module 字段）。

这里是**手维护**的一张表，所以它一定会漏、会过期。两条兜底写死在下面：
`load_module_map` 只在真的没文件时返回空表，并把状态回报给调用方；
调用方（plan / context）拿到 missing/empty/unparsable 任一状态都必须**改为跑全量**，
不许把空映射当成"这次改动没有受影响模块"。
"""
import fnmatch
from pathlib import Path
from typing import Any

import yaml

UNMAPPED = "__unmapped__"

#: 映射表状态。ok 以外的一切都由调用方折算成"跑全量"。
MAP_OK = "ok"
MAP_MISSING = "missing"
MAP_EMPTY = "empty"
MAP_UNPARSABLE = "unparsable"


def _coerce(data: Any) -> dict[str, list[str]]:
    """两种写法都收：裸列表（旧）与 {files: [...]}（v2，便于以后加 owner/x-scope）。"""
    out: dict[str, list[str]] = {}
    modules = data.get("modules") or {}
    if not isinstance(modules, dict):
        raise ValueError("modules 必须是映射")
    for mod, spec in modules.items():
        if isinstance(spec, dict):
            pats = spec.get("files") or []
        else:
            pats = spec or []
        if isinstance(pats, str):
            pats = [pats]
        if not isinstance(pats, list):
            raise ValueError(f"模块 {mod} 的 files 必须是列表")
        out[str(mod)] = [str(p) for p in pats]
    return out


def module_map_status(path: Path | str) -> str:
    """映射表可不可用。和 load_module_map 分开，调用方才能区分"没有表"与"表是空的"。"""
    p = Path(path)
    if not p.is_file():
        return MAP_MISSING
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:
        return MAP_UNPARSABLE
    if not isinstance(data, dict):
        return MAP_UNPARSABLE
    try:
        return MAP_OK if _coerce(data) else MAP_EMPTY
    except ValueError:
        return MAP_UNPARSABLE


def load_module_map(path: Path | str = "config/modules.yaml") -> dict[str, list[str]]:
    p = Path(path)
    if not p.is_file():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return _coerce(data)


def classify(files: list[str], module_map: dict[str, list[str]]) -> dict[str, list[str]]:
    """把变更文件按命中的模块归组；未命中进 __unmapped__。

    一个文件可以落进**多个**模块：本仓库的 modules.yaml 里 todo 和 ui 都指
    examples/demo_app.py，旧实现"首个命中即 break"会让 ui 永远选不中。
    宁可多跑一个模块，也不能因为表里两条规则打架就漏掉一侧。
    """
    result: dict[str, list[str]] = {}
    for f in files:
        norm = f.replace("\\", "/")
        hit = [mod for mod, pats in module_map.items()
               if any(fnmatch.fnmatch(norm, pat) for pat in pats)]
        for mod in (hit or [UNMAPPED]):
            result.setdefault(mod, []).append(norm)
    return result


def unmapped_files(groups: dict[str, list[str]]) -> list[str]:
    return list(groups.get(UNMAPPED) or [])


def affected_modules(groups: dict[str, list[str]]) -> list[str]:
    return sorted(m for m in groups if m != UNMAPPED)
