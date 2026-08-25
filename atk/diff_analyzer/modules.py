"""模块映射：被测代码路径通配 -> 测试模块目录(scenarios/<module>)。"""
import fnmatch
from pathlib import Path

import yaml


def load_module_map(path: Path | str = "config/modules.yaml") -> dict[str, list[str]]:
    p = Path(path)
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {str(m): [str(x) for x in pats] for m, pats in data.get("modules", {}).items()}


def classify(files: list[str], module_map: dict[str, list[str]]) -> dict[str, list[str]]:
    """把变更文件按首个命中的模块归组；未命中进 __unmapped__。"""
    result: dict[str, list[str]] = {}
    for f in files:
        norm = f.replace("\\", "/")
        matched = None
        for mod, pats in module_map.items():
            if any(fnmatch.fnmatch(norm, pat) for pat in pats):
                matched = mod
                break
        result.setdefault(matched or "__unmapped__", []).append(norm)
    return result
