"""执行任务管理：subprocess 跑 atk run，stdout 行入队供 SSE 消费；全局单槽。"""
import subprocess
import threading
import uuid
from collections import deque
from collections.abc import Iterator


class BusyError(Exception):
    pass


class _Job:
    def __init__(self, argv: list[str], cwd: str | None, timeout: float = 300.0, max_events: int = 1000):
        self.id = uuid.uuid4().hex[:12]
        self.events: deque[dict] = deque(maxlen=max_events)
        self.done = False
        self.exit_code: int | None = None
        self.timeout = timeout
        self.proc = subprocess.Popen(
            argv, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._watchdog, daemon=True).start()

    def _watchdog(self):
        if threading.Event().wait(self.timeout):
            return
        if not self.done:
            try:
                self.proc.kill()
            except Exception:
                pass
            try:
                self.events.append({"type": "log", "line": f"[job] 超时 {self.timeout}s 已终止"})
            except Exception:
                pass

    def _pump(self):
        assert self.proc.stdout
        for line in self.proc.stdout:
            self.events.append({"type": "log", "line": line.rstrip("\n")})
        code = self.proc.wait()
        self.exit_code = code
        self.done = True
        self.events.append({"type": "done", "exit_code": code})


class JobManager:
    """单槽：同时只允许一个执行任务（本机单人足够）。"""

    def __init__(self, timeout: float = 300.0, max_events: int = 1000):
        self._current: _Job | None = None
        self.timeout = timeout
        self.max_events = max_events

    def start(self, argv: list[str], cwd: str | None = None) -> str:
        if self._current is not None and not self._current.done:
            raise BusyError("已有任务在执行，请等待完成")
        job = _Job(argv, cwd, timeout=self.timeout, max_events=self.max_events)
        self._current = job
        return job.id

    def stream(self, job_id: str) -> Iterator[dict]:
        job = self._current
        if job is None or job.id != job_id:
            raise KeyError(job_id)
        while True:
            if job.events:
                yield job.events.popleft()
            elif job.done:
                return
            else:
                threading.Event().wait(0.05)

    def status(self, job_id: str) -> dict:
        job = self._current
        if job is None or job.id != job_id:
            raise KeyError(job_id)
        return {"id": job.id, "done": job.done, "exit_code": job.exit_code}
