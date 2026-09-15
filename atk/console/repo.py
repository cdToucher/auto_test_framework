"""场景文件仓库：树扫描 / 加载 / 带门禁的保存。文件系统是唯一事实源。"""
import os
from pathlib import Path

import yaml

SCENARIO_EXTS = {".yaml", ".yml"}


class YamlError(Exception):
    pass


class ConflictError(Exception):
    pass


def _safe_rel(root: Path, rel: str) -> Path:
    base = root / "scenarios"
    p = (base / rel).resolve()
    if not p.is_relative_to(base.resolve()):
        raise ValueError(f"路径越界: {rel}")
    return p


def scan_tree(root: Path) -> dict:
    """返回 {"dirs":[{name,path,children}], "scenarios":[{path,name,priority,module,tags,error?}]}。"""
    root = root / "scenarios"
    scenarios: list[dict] = []
    dirs_by_path: dict[str, dict] = {}
    top = {"dirs": [], "name": "", "path": ""}
    dirs_by_path[""] = top
    if not root.exists():
        return {"dirs": [], "scenarios": []}
    for f in sorted({*root.rglob("*.yaml"), *root.rglob("*.yml")}):
        try:
            # 收敛工程根内：跳过指向根外的 symlink，避免读取任意文件
            if not f.resolve().is_relative_to(root.resolve()):
                continue
        except Exception:
            continue
        rel = str(f.relative_to(root))
        item = {"path": rel, "name": f.name}
        # 确保父目录链存在
        parts = Path(rel).parts[:-1]
        for i in range(len(parts)):
            sub = "/".join(parts[: i + 1])
            if sub not in dirs_by_path:
                node = {"name": parts[i], "path": sub, "children": []}
                dirs_by_path[sub] = node
                holder = dirs_by_path["/".join(parts[:i])] if i else top
                holder.setdefault("children", []).append(node)
        try:
            raw = yaml.safe_load(f.read_text(encoding="utf-8"))
            assert isinstance(raw, dict) and "scenario" in raw
            item.update(
                scenario=raw.get("scenario"),
                module=raw.get("module", "default"),
                priority=raw.get("priority", "P1"),
                tags=raw.get("tags", []),
            )
        except Exception as e:
            item["error"] = str(e).splitlines()[0]
        scenarios.append(item)
    children = top.pop("children", [])
    return {"dirs": children, "scenarios": scenarios}


def load_scenario(root: Path, rel: str) -> tuple[dict, int]:
    """返回 (原始 dict, mtime_ns)。解析失败抛 YamlError（含行号）。"""
    p = _safe_rel(root, rel)
    try:
        text = p.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        line = getattr(getattr(e, "problem_mark", None), "line", None)
        raise YamlError(f"{rel} 解析失败 line={line + 1 if line is not None else '?'}: {e}") from e
    if not isinstance(data, dict):
        raise YamlError(f"{rel}: 根节点不是映射")
    st = p.stat()
    return data, st.st_mtime_ns


def validate_scenario(data: dict) -> list[str]:
    """用与 CLI 相同的模型校验场景。返回错误列表，空即合法。"""
    errs = []
    if not isinstance(data, dict) or not data.get("scenario"):
        errs.append("缺少 scenario 字段")
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        errs.append("steps 为空，至少需要一个步骤")
    else:
        for i, s in enumerate(steps):
            if not isinstance(s, dict) or not ({"api", "ui"} & set(s)):
                errs.append(f"steps[{i}] 缺少 api/ui 键")
    if errs:
        return errs
    try:
        from ..store.models import Scenario

        Scenario.from_raw(data)
    except Exception as e:
        errs.append(str(e))
    return errs


def render_yaml(data: dict) -> str:
    return yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def save_scenario(
    root: Path,
    rel: str,
    data: dict,
    *,
    move_to: str | None,
    if_mtime: int | None,
    force: bool = False,
) -> Path:
    errs = validate_scenario(data)
    if errs:
        raise ValueError("; ".join(errs))
    src = _safe_rel(root, rel)
    if if_mtime is not None and not force:
        cur = src.stat().st_mtime_ns if src.exists() else -1
        if cur != if_mtime:
            raise ConflictError(f"{rel} 已被外部修改（当前 mtime 与请求不符），请刷新或强制覆盖")

    dst = _safe_rel(root, move_to) if move_to else src
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    tmp.write_text(render_yaml(data), encoding="utf-8")
    os.replace(tmp, dst)
    if move_to and src.exists() and src != dst:
        src.unlink()
        # 清理空父目录（保留 scenarios 根）
        for parent in src.parents:
            if parent == (root / "scenarios").resolve() or not parent.is_relative_to((root / "scenarios").resolve()):
                break
            try:
                parent.rmdir()
            except OSError:
                break
    return dst
