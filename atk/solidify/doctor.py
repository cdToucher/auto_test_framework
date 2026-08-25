"""固化脚本诊断：跑 pytest 并把失败分类，驱动自修复回路。

分类语义（对应规格 §5 自修复流程）：
- healthy   全部通过
- pending   存在未固化 UI 步骤（NotImplementedError），待 Agent 填充
- repairable 选择器/元素类失效（Playwright 超时等），可自动重译
- suspect_bug 业务断言失败，疑似产品 bug，禁止自动修，交人工定性
- broken    其他无法归类的失败（收集失败摘要供排查）
"""
import re
import subprocess
import sys
from pathlib import Path

_PENDING = re.compile(r"NotImplementedError")
_REPAIRABLE = re.compile(
    r"(TimeoutError|Timeout\s+\d+ms|waiting for|locator|selector|playwright|ElementIsNotVisible)",
    re.IGNORECASE,
)


def diagnose(path: Path | str, timeout: float = 120.0) -> dict:
    path = Path(path)
    if not path.exists():
        return {"status": "missing", "file": str(path), "failures": []}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(path)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        return {"status": "healthy", "file": str(path), "failures": []}
    failures = re.findall(r"^(?:FAILED|ERROR)\s+(\S+)\s+-\s+(.*)$", out, re.MULTILINE)
    if not failures:
        failures = [("unknown", out.strip().splitlines()[-1] if out.strip() else "pytest failed")]
    joined = "\n".join(msg for _, msg in failures)
    if _PENDING.search(joined) or "NotImplementedError" in out:
        status = "pending"
    elif _REPAIRABLE.search(joined):
        status = "repairable"
    elif "AssertionError" in joined:
        status = "suspect_bug"
    else:
        status = "broken"
    return {"status": status, "file": str(path), "failures": [f"{n}: {m}" for n, m in failures]}
