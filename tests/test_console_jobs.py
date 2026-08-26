"""console.jobs 测试：子进程执行、SSE 流、单槽占用。"""
import pytest

from atk.console.jobs import BusyError, JobManager
import sys
import time


def test_run_and_stream():
    jm = JobManager()
    jid = jm.start([sys.executable, "-c", "print('hi from job')"])
    lines = []
    for ev in jm.stream(jid):
        lines.append(ev)
        if ev["type"] == "done":
            break
    assert any(ev["type"] == "log" and "hi from job" in ev["line"] for ev in lines)
    assert lines[-1] == {"type": "done", "exit_code": 0}


def test_busy_single_slot():
    jm = JobManager()
    jid = jm.start([sys.executable, "-c", "import time; time.sleep(0.5)"])
    with pytest.raises(BusyError):
        jm.start([sys.executable, "-c", "pass"])
    for _ in jm.stream(jid):
        pass
    jid2 = jm.start([sys.executable, "-c", "pass"])  # 结束后可再启
    list(jm.stream(jid2))


def test_nonzero_exit():
    jm = JobManager()
    jid = jm.start([sys.executable, "-c", "raise SystemExit(3)"])
    last = None
    for ev in jm.stream(jid):
        last = ev
    assert last == {"type": "done", "exit_code": 3}
