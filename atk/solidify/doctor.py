"""固化脚本诊断：跑 pytest 并把失败分类，驱动自修复回路。

分类语义（对应规格 §5 自修复流程）：
- healthy    全部通过
- pending    存在未固化 UI 步骤（NotImplementedError），待 Agent 填充
- repairable 选择器/元素类失效（Playwright 超时等），可自动重译
- suspect_bug 业务断言失败，疑似产品 bug，禁止自动修，交人工定性
- broken     其他无法归类的失败（收集 E 行摘要供排查）
"""
import re
import subprocess
import sys
from pathlib import Path

_REPAIRABLE = re.compile(
    r"(TimeoutError|Timeout\s+[\d.]+s|waiting for|locator\.|playwright|ElementIsNotVisible"
    r"|net::|Target closed)",
    re.IGNORECASE,
)
_E_LINE = re.compile(r"^(E\s+.+)$", re.MULTILINE)


def diagnose(path: Path | str, timeout: float = 120.0) -> dict:
    path = Path(path)
    if not path.exists():
        return {"status": "missing", "file": str(path), "failures": []}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(path)],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(path.parent),  # 隔离项目根的 pytest 配置干扰
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        return {"status": "healthy", "file": str(path), "failures": []}
    e_lines = [m.group(1).strip() for m in _E_LINE.finditer(out)]
    joined = "\n".join(e_lines) or out[-2000:]

    def has(pat: str) -> bool:
        return bool(re.search(pat, joined, re.IGNORECASE)) or bool(re.search(pat, out, re.IGNORECASE))

    if "NotImplementedError" in joined or "NotImplementedError" in out:
        status = "pending"
    elif has(_REPAIRABLE.pattern):
        status = "repairable"
    elif "AssertionError" in joined or "AssertionError" in out:
        status = "suspect_bug"
    else:
        status = "broken"
    return {"status": status, "file": str(path), "failures": e_lines[:10]}
