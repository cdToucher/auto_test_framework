"""console.schedules 测试：yaml 往返、next_run、校验。"""
import pytest

from atk.console.schedules import (
    ScheduleError,
    load_tasks,
    next_run_of,
    save_tasks,
    validate_task,
)

TASKS = [
    {"name": "夜间回归", "env": "xinfei", "module": "seal", "daily_at": "02:00", "enabled": True},
    {"name": "全量", "env": "demoapp", "cron": "0 */6 * * *", "enabled": False},
]


def test_roundtrip(tmp_path):
    save_tasks(tmp_path, TASKS)
    loaded = load_tasks(tmp_path)
    assert loaded == TASKS


def test_load_missing_file(tmp_path):
    assert load_tasks(tmp_path) == []


def test_next_run_daily():
    t = {"name": "x", "env": "e", "daily_at": "02:00", "enabled": True}
    n = next_run_of(t)
    assert n and "T02:00" in n


def test_next_run_cron():
    t = {"name": "x", "env": "e", "cron": "0 */6 * * *", "enabled": True}
    assert next_run_of(t)


def test_next_run_disabled(tmp_path):
    t = {"name": "x", "env": "e", "daily_at": "02:00", "enabled": False}
    assert next_run_of(t) is None


def test_validate_bad_task():
    assert validate_task({"name": "", "env": "", "enabled": True})
    assert validate_task({"name": "x", "env": "e", "cron": "bad", "enabled": True})
    assert validate_task({"name": "x", "env": "e", "daily_at": "25:00", "enabled": True})
    # 合法：无错误
    assert not validate_task({"name": "x", "env": "e", "daily_at": "02:00", "enabled": True})
    assert not validate_task({"name": "x", "env": "e", "cron": "0 2 * * *", "enabled": True})


def test_save_rejects_invalid(tmp_path):
    with pytest.raises(ScheduleError):
        save_tasks(tmp_path, [{"name": "", "env": "e", "enabled": True}])
