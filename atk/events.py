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

from . import keystore

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


def _event_payload(run_id: str, ev: dict, prev_sig: str) -> str:
    """签名覆盖序号、时间、类型、对象与全部附加字段，并链住上一条的签名。

    链式是必需的：只签最后一条的话，改完 run.yaml 再重写那一条就能自圆其说。
    """
    body = keystore.canonical(
        [str(ev.get("seq")), str(ev.get("at")), str(ev.get("kind")), run_id,
         str(ev.get("actor")), str(ev.get("target")),
         ";".join(f"{k}={ev[k]}" for k in sorted(ev)
                  if k not in ("seq", "at", "kind", "actor", "target", "esig"))]
        + [prev_sig])
    return body


def append(runs_dir: Path | str, run_id: str, *, kind: str, actor: str = "",
           target: str = "", **fields: Any) -> Event:
    """追加一条事件（带链式签名）。写失败绝不静默：流水断了审计就没意义。"""
    ev = Event(at=now(), kind=kind, run_id=run_id, actor=actor, target=target)
    for k, v in fields.items():
        if v is not None:
            ev[k] = v
    p = events_path(runs_dir, run_id)
    items = read(p)
    ev["seq"] = len(items) + 1
    prev = str(items[-1].get("esig") or "") if items else ""
    ev["esig"] = keystore.sign(_event_payload(run_id, dict(ev), prev))
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


def verify_chain(runs_dir: Path | str, run_id: str) -> tuple[bool, str]:
    """整条流水是否可信：链上每一枚签名都要对得上，且必须有一把可用密钥。

    返回 (是否有效, 说明)。无密钥时判无效——那等于从没有人（或有钥匙的进程）写过流水，
    而 gate 要的就是这个区分。空流水也判无效：新记录还没落过盘，不该被当成已核验。
    """
    items = read(events_path(runs_dir, run_id))
    if not keystore.available():
        return (False, keystore.missing_key_hint())
    if not items:
        return (False, "事件流水为空，无法核验")
    prev = ""
    for i, ev in enumerate(items, start=1):
        if int(ev.get("seq") or 0) != i:
            return (False, f"第 {i} 条事件序号异常（{ev.get('seq')}），流水被增删过")
        if ev.get("run_id") != run_id:
            return (False, f"第 {i} 条事件的 run_id 与目录不符")
        body = _event_payload(run_id, {k: v for k, v in ev.items()}, prev)
        if not keystore.verify(body, str(ev.get("esig") or "")):
            return (False, f"第 {i} 条事件签名无效，流水在落盘后被改写过")
        prev = str(ev.get("esig") or "")
    return (True, "")


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
