"""场景 → 端点的依赖账本，让"这次改动该跑哪些场景"有事实依据。

为什么要有这个模块：`config/modules.yaml` 是一张**人手维护**的"代码路径 → 模块"表，
它错了、漏了、或者干脆不存在时，`plan` 会安静地选出 0 个场景（未映射文件被直接丢弃），
而 gate 照样基于这份空选择放行。本模块补两件事实源之外的事：

1. **场景静态触达了哪些端点**（`touchset`）：不必执行，plan 期就能用。
2. **这条场景上次真跑过没有、跑的时候文件内容是什么**（`.atk/deps.yaml` 账本）——
   借 pytest-testmon 的思路：没有观测数据的条目一律**包含进来**，而不是跳过。

账本与 modules.yaml 都是**派生件**，可以随时删除；删掉的后果是退化成"跑全量"，
绝不是"跑 0 个"。这是本模块唯一的方向约定：拿不准就多跑。
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .store.models import PRIORITY_RANK, ApiStep, Priority, Scenario

LEDGER_FILE = "deps.yaml"
MODE_MODULE = "module_map"      # 按变更影响面选中的子集
MODE_FULL = "full_suite"        # 保守：整个场景库
UNMAPPED = "__unmapped__"

#: 保守包含的理由标记。gate 与提示文案都复用它，避免"字符串当枚举"各写一份。
REASON_UNOBSERVED = "无观测条目（从未真跑过）→ 保守包含"

_VAR_SEG = re.compile(r"\$\{[^}]*\}")
_NUM_SEG = re.compile(r"^\d+$")


def normalize_endpoint(call: str) -> str:
    """`GET /api/orders?orderNo=${orderNo}` → `GET /api/orders?orderNo=*`。

    折叠规则决定了这张表能不能匹配上：`${var}`、纯数字路径段、查询串里的**值**都会
    随场景不同而变，但**方法、路径形状与参数名**不会，所以折值留名。
    """
    parts = str(call).split()
    if len(parts) != 2:
        return str(call).strip()
    method, target = parts[0].upper(), parts[1]
    path, sep, query = target.partition("?")
    prefix = "/" if path.startswith("/") else ""
    body = path[1:] if prefix else path
    segs = ["*" if (not s or _NUM_SEG.match(s) or "$" in s) else _VAR_SEG.sub("*", s)
            for s in (body.split("/") if body else [])]
    path = prefix + "/".join(segs)
    if not sep:
        return f"{method} {path}"
    names = sorted(
        f"{_VAR_SEG.sub('*', kv.split('=', 1)[0])}=*" if "=" in kv
        else _VAR_SEG.sub("*", kv)
        for kv in query.split("&") if kv
    )
    return f"{method} {path}?{'&'.join(names)}" if names else f"{method} {path}"


def touchset(scenario: Scenario) -> set[str]:
    """这条场景会打到哪些端点（静态，不执行）。"""
    out: set[str] = set()
    for st in scenario.steps:
        if isinstance(st, ApiStep):
            out.add(normalize_endpoint(st.call))
    return out


def file_hash(path: str | Path) -> str:
    """场景文件内容指纹。文件不在就返回空串，由调用方按"未观测"保守处理。"""
    p = Path(path)
    if not p.is_file():
        return ""
    return hashlib.sha256(p.read_bytes()).hexdigest()


def ledger_key(path: Path | str) -> str:
    """账本键：与 _key 同一套归一化，供只有文件路径（planned_scenarios）的场合复用。"""
    p = Path(str(path))
    try:
        return Path(os.path.relpath(p)).as_posix()
    except ValueError:
        return p.as_posix()


def stale_planned(ledger: dict[str, dict[str, Any]], planned: list[str],
                  run_id: str) -> list[str]:
    """计划了、但账本显示这条运行记录并未真跑它的场景。

    方向：宁可报"没跑"，也不把"计划与执行不一致"放过去——smoke 会按模块分多轮跑，
    某一轮静默失败时 planned 与真跑过的集合就会脱节。
    """
    out: list[str] = []
    for f in planned or []:
        if not f:
            continue
        entry = ledger.get(ledger_key(f))
        if str((entry or {}).get("last_run") or "") != str(run_id):
            out.append(str(f))
    return out


def ledger_path(root: Path | str = ".") -> Path:
    """账本位置。它是派生件，所以恒在 `.atk/` 下、与新旧布局无关（同 skills/）。"""
    return Path(root) / ".atk" / LEDGER_FILE


def load_ledger(path: Path | str) -> dict[str, dict[str, Any]]:
    """读账本。缺失或损坏都返回 {}：没有账本 = 全部未观测 = 全部要跑，方向是保守的。"""
    import yaml

    p = Path(path)
    if not p.is_file():
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): (v or {}) for k, v in data.items() if isinstance(v, (dict, type(None)))}


def _key(scenario: Scenario) -> str:
    """账本键。用相对路径，换机器/换 cwd 不至于把账本变成一堆查不到的条目。"""
    f = scenario.file or scenario.scenario
    try:
        return Path(os.path.relpath(f)).as_posix()
    except ValueError:
        return Path(f).as_posix()


def record_execution(
    results: Any,
    *,
    run_id: str,
    env: str = "",
    path: Path | str,
) -> int:
    """把真跑过的场景写进账本。返回写入条数。

    只记真跑过的：一条从没执行过的场景在账本里没有条目，下次 plan 会因为
    "未观测"被包含进来——这正是想要的行为。
    """
    import yaml

    ledger = load_ledger(path)
    n = 0
    for r in results:
        sc = r.scenario
        ledger[_key(sc)] = {
            "hash": file_hash(sc.file),
            "env": getattr(r, "env", "") or env,
            "endpoints": sorted(touchset(sc)),
            "last_run": run_id,
            "verdict": "pass" if r.passed else str(r.error_class),
        }
        n += 1
    if n:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(yaml.safe_dump(ledger, allow_unicode=True, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, p)
    return n


@dataclass
class DoctorReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def doctor(module_map_path: Path | str, scenarios: list[Scenario],
           ledger_dir: Path | str = ".") -> DoctorReport:
    """双向检查 modules.yaml 与场景库是否对得上。

    这张表是人手维护的，本仓库就出过两个错：`examples/shop_pro.py`（12 个场景的
    被测系统）完全没进表；表里的 `demo` 键对不上任何场景的 module。两类错误都
    只会让 plan 少选场景、gate 照样放行，所以必须有个作者侧的探测器。
    """
    from .diff_analyzer.modules import (
        MAP_MISSING,
        MAP_UNPARSABLE,
        module_map_status,
    )

    rep = DoctorReport()
    status = module_map_status(module_map_path)
    if status == MAP_UNPARSABLE:
        rep.errors.append(f"{module_map_path} 无法解析，plan 会按全量执行")
        return rep
    if status == MAP_MISSING:
        rep.warnings.append(f"{module_map_path} 不存在：plan 无法按影响面子集选择，会跑全量")
        return rep

    mmap = load_map(module_map_path)
    used = {sc.module for sc in scenarios}
    # 方向不对称是有意的：场景模块没有规则 = 改代码永远选不到它（静默漏测）→ error；
    # 规则多出来指向本项目还没有的模块 = 顶多少跑，且常见于从大仓库拷子集来跑 → warning。
    for mod in sorted(used - set(mmap)):
        rep.errors.append(
            f"模块 '{mod}' 有 {sum(1 for s in scenarios if s.module == mod)} 个场景，"
            f"但 modules.yaml 没有任何规则命中它 → 改这类代码永远不会选到这些场景")
    for mod in sorted(set(mmap) - used):
        rep.warnings.append(f"modules.yaml 有规则 '{mod}'，但场景库里没有该模块的场景")
    for mod, pats in mmap.items():
        if not pats:
            rep.warnings.append(f"模块 '{mod}' 的 files 为空，等于没有规则")

    ledger = load_ledger(ledger_path(ledger_dir))
    untried = [sc.file or sc.scenario for sc in scenarios if _key(sc) not in ledger]
    if untried:
        shown = ", ".join(untried[:5]) + (" …" if len(untried) > 5 else "")
        rep.warnings.append(
            f"{len(untried)} 个场景从未被任何运行记录执行过（账本无条目）: {shown}")
    return rep


def load_map(path: Path | str) -> dict[str, list[str]]:
    from .diff_analyzer.modules import load_module_map

    try:
        return load_module_map(path)
    except Exception:
        return {}


@dataclass
class Selection:
    """一次 plan 的选择结果。mode 与 unmapped_files 都要落进运行记录，gate 才有依据。"""

    mode: str = MODE_MODULE
    scenarios: list[Scenario] = field(default_factory=list)
    unmapped_files: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


#: 改了不太可能影响运行结果的尾部。判定用**排除表**而不是白名单：扩展名没见过
#: 就算相关（→ 全量），因为漏跑回归的代价远高于多跑几个场景。
INERT_SUFFIXES = frozenset({
    ".md", ".rst", ".txt", ".adoc", ".license", ".png", ".jpg", ".jpeg", ".gif",
    ".svg", ".ico", ".webp", ".mp4", ".gitignore", ".editorconfig", ".DS_Store",
})
INERT_NAMES = frozenset({"LICENSE", "CODEOWNERS", "NOTICE", "README"})


def is_behavior_relevant(path: str) -> bool:
    name = Path(str(path)).name
    if name.split(".")[0].upper() in INERT_NAMES or name.upper() in INERT_NAMES:
        return False
    return Path(name).suffix.lower() not in INERT_SUFFIXES


def _fits(sc: Scenario, tag_list: Optional[list[str]], rank_max: Optional[int]) -> bool:
    if tag_list and not set(tag_list) <= set(sc.tags):
        return False
    if rank_max is not None and PRIORITY_RANK[sc.priority] > rank_max:
        return False
    return True


def endpoints_of(modules: set[str], scenarios: list[Scenario]) -> set[str]:
    """某些模块会碰到的端点全集（静态）。"""
    out: set[str] = set()
    for sc in scenarios:
        if sc.module in modules:
            out |= touchset(sc)
    return out


def blind_to_full_suite(unmapped_files: list[str]) -> bool:
    """未映射文件里是否有**可能影响行为**的。

    只改 README 不必惊动全库；有一个没映射的 .py 就必须承认"影响面算不清"。
    注意方向：过滤只发生在"要不要因此跑全量"这一步，未映射清单本身照常上报 gate。
    """
    return any(is_behavior_relevant(f) for f in unmapped_files)


def select_for_changes(
    *,
    scenarios: list[Scenario],
    affected: set[str],
    unmapped_files: list[str],
    ledger: dict[str, dict[str, Any]],
    tag_list: Optional[list[str]] = None,
    priority: Optional[Priority] = None,
) -> Selection:
    """按变更影响面选场景，不确定就多跑。

    入选理由四条，命中任一即入选：
    1. 模块被映射命中；
    2. 端点与受影响模块的端点集合相交（共享接口的模块互相牵连）；
    3. 场景文件自身内容变了（上次跑它的时候不是这个内容）；
    4. **账本里没有它的观测条目**——这条是 pytest-testmon 的兜底：没有数据就没有资格
       说"这个场景与本次改动无关"。它是一次性的而非永久：被选中的场景跑完后
       `record_execution` 会补上条目，此后才谈得上"按影响面排除"。
    另有前置兜底：有**行为相关**文件落不进任何模块 → 影响面未知 → 全库。
    """
    rank_max = None if priority is None else PRIORITY_RANK[priority]
    cand = [sc for sc in scenarios if _fits(sc, tag_list, rank_max)]
    if blind_to_full_suite(unmapped_files):
        return Selection(
            mode=MODE_FULL,
            scenarios=list(cand),
            unmapped_files=list(unmapped_files),
            reasons={_key(sc): "有未映射的代码变更，影响面未知 → 全量" for sc in cand},
            warnings=[f"{len(unmapped_files)} 个变更文件未映射到任何模块，已改为执行全量场景库"],
        )

    module_eps = endpoints_of(set(affected), scenarios)
    selected: list[Scenario] = []
    reasons: dict[str, str] = {}
    for sc in cand:
        key = _key(sc)
        entry = ledger.get(key)
        if sc.module in affected:
            reason = "模块命中"
        elif touchset(sc) & module_eps:
            reason = "端点命中"
        elif entry is not None and str(entry.get("hash") or "") != file_hash(sc.file):
            reason = "场景内容已变"
        elif entry is None:
            reason = REASON_UNOBSERVED
        else:
            reason = ""
        if reason:
            selected.append(sc)
            reasons[key] = reason
    warnings: list[str] = []
    n_unobserved = sum(1 for v in reasons.values() if v == REASON_UNOBSERVED)
    if n_unobserved:
        warnings.append(
            f"{n_unobserved} 个场景没有依赖账本条目（从未真跑过），无法判断其与本次变更是否"
            "无关，已保守包含；跑完建立条目后即按影响面子集选择")
    return Selection(mode=MODE_MODULE, scenarios=selected,
                     unmapped_files=list(unmapped_files), reasons=reasons,
                     warnings=warnings)
