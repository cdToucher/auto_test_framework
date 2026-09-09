"""定时任务：config/schedules.yaml 读写 + 触发时间计算 + 校验。

已知限制（规格 §5）：控制台未运行则不触发，不做错过补偿。
"""
from pathlib import Path
from typing import Any

import yaml
from apscheduler.triggers.cron import CronTrigger

FILE = Path("config") / "schedules.yaml"


class ScheduleError(Exception):
    pass


def load_tasks(root: Path) -> list[dict[str, Any]]:
    f = root / FILE
    if not f.exists():
        return []
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return list(data.get("tasks") or [])


def save_tasks(root: Path, tasks: list[dict[str, Any]]) -> None:
    errs = [e for t in tasks for e in validate_task(t)]
    if errs:
        raise ScheduleError("; ".join(errs))
    f = root / FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        yaml.dump({"tasks": tasks}, allow_unicode=True, sort_keys=False,
                  default_flow_style=False),
        encoding="utf-8",
    )


def validate_task(t: dict) -> list[str]:
    errs: list[str] = []
    name = str(t.get("name") or "").strip()
    env = str(t.get("env") or "").strip()
    if not name:
        errs.append("任务缺少 name")
    if not env:
        errs.append(f"任务 {name or '?'} 缺少 env")
    cron = t.get("cron")
    daily = t.get("daily_at")
    if not cron and not daily:
        errs.append(f"任务 {name} 需要 cron 或 daily_at")
    if cron:
        try:
            CronTrigger.from_crontab(str(cron))
        except ValueError as e:
            errs.append(f"任务 {name} cron 非法: {e}")
    if daily:
        parts = str(daily).split(":")
        try:
            h, m = int(parts[0]), int(parts[1])
            assert 0 <= h < 24 and 0 <= m < 60
        except Exception:
            errs.append(f"任务 {name} daily_at 应为 HH:MM（24小时制）")
    return errs


def next_run_of(t: dict) -> str | None:
    """返回 ISO 时间串；disabled 或表达式非法返回 None。"""
    if not t.get("enabled"):
        return None
    try:
        if t.get("daily_at"):
            parts = str(t["daily_at"]).split(":")
            if len(parts) != 2:
                return None
            h, m = int(parts[0]), int(parts[1])
            if not (0 <= h < 24 and 0 <= m < 60):
                return None
            trigger = CronTrigger(hour=h, minute=m)
        else:
            trigger = CronTrigger.from_crontab(str(t["cron"]))
    except (ValueError, KeyError, AttributeError, TypeError):
        return None
    from datetime import datetime, timezone

    dt = trigger.get_next_fire_time(None, datetime.now(timezone.utc))
    return dt.astimezone().isoformat(timespec="minutes") if dt else None


def cron_trigger_of(t: dict) -> CronTrigger | None:
    if not t.get("enabled"):
        return None
    if t.get("daily_at"):
        try:
            parts = str(t["daily_at"]).split(":")
            if len(parts) != 2:
                return None
            h, m = int(parts[0]), int(parts[1])
            if not (0 <= h < 24 and 0 <= m < 60):
                return None
            return CronTrigger(hour=h, minute=m)
        except (ValueError, KeyError, AttributeError, TypeError):
            return None
    try:
        return CronTrigger.from_crontab(str(t["cron"]))
    except (ValueError, KeyError, AttributeError, TypeError):
        return None
