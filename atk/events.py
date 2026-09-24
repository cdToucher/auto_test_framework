"""运行记录的事件流水：`run.yaml` 是当前态，这里追加的是"怎么变成这样的"。

为什么需要：`run.yaml` 整份重写、意图就地覆盖，于是同一条意图可以被
`pass → fail → pass` 反复改写而**不留任何痕迹**，事后谁也说不清那次 pass 是
什么时候、由谁、凭什么下的。把 `run.yaml` 改成不可变的追加日志又会破坏
"文件即事实源、可直接读改"的初衷，所以拆成两处：

- `run.yaml`：当前态，仍然整份重写（原子写），是执行与 gate 读的对象；
- `events.yaml`：只追加的迁移流水，带时间戳与操作者，是审计与将来超期判定的依据。

一致性由 gate 复检（`ledger_consistency`）：最后一次落盘的 sha256 必须等于磁盘上的
`run.yaml`，手改或倒填日期的记录会被拦下来，而不是被无条件相信。
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml

EVENTS_FILE = "events.yaml"


class Event(dict):
    """一条事件。用 dict 而不是 dataclass：字段按 kind 不同，且要能原样落 YAML。"""

    @property
    def at(self) -> str:
        return str(self.get("at") or "")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def events_path(runs_dir: Path | str, run_id: str) -> Path:
    return Path(runs_dir) / run_id / EVENTS_FILE


def sha_of(path: Path | str) -> str:
    p = Path(path)
    if not p.is_file():
        return ""
    return hashlib.sha256(p.read_bytes()).hexdigest()


def append(runs_dir: Path | str, run_id: str, *, kind: str, actor: str = "",
           target: str = "", **fields: Any) -> Event:
    """追加一条事件。写失败绝不静默：流水断了审计就没意义。"""
    ev = Event(at=now(), kind=kind, run_id=run_id, actor=actor, target=target)
    for k, v in fields.items():
        if v is not None:
            ev[k] = v
    p = events_path(runs_dir, run_id)
    items = read(p)
    ev["seq"] = len(items) + 1
    items.append(dict(ev))
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(items, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    os.replace(tmp, p)
    return ev


def read(path: Path | str) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        return []
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    except Exception:
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def latest(paths: list[dict], *, kind: str, target: Optional[str] = None) -> Optional[dict]:
    for ev in reversed(paths):
        if ev.get("kind") == kind and (target is None or ev.get("target") == target):
            return ev
    return None


def age_seconds(ev: Optional[dict], *, ref: Optional[datetime] = None) -> Optional[float]:
    """事件距今秒数。时间戳缺失或不可解析时返回 None——调用方必须把它当"已过期"处理，
    不能当"很新"，否则倒填/抹掉时间戳就成了逃避超期拦截的捷径。"""
    if not ev:
        return None
    raw = str(ev.get("at") or "")
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return None
    base = ref or datetime.now()
    return (base - ts).total_seconds()


def record_save(runs_dir: Path | str, run_id: str, *, actor: str = "") -> str:
    """在每次原子落盘后记一条 save 事件，返回该文件的 sha256。"""
    f = Path(runs_dir) / run_id / "run.yaml"
    digest = sha_of(f)
    append(runs_dir, run_id, kind="save", actor=actor, target="run.yaml", sha256=digest)
    return digest


def last_save_sha(runs_dir: Path | str, run_id: str) -> Optional[str]:
    """最后一条 save 事件里的 sha256；没有 save 事件时返回 None（旧记录）。"""
    ev = latest(read(events_path(runs_dir, run_id)), kind="save")
    if ev is None:
        return None
    return str(ev.get("sha256") or "")
