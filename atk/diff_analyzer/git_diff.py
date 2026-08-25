"""git 变更文件提取。"""
import subprocess


def changed_files(base: str, head: str = "HEAD", repo: str = ".") -> list[str]:
    """返回 base...head 三点区间的变更文件（相对路径）。"""
    proc = subprocess.run(
        ["git", "-C", repo, "diff", "--name-only", f"{base}...{head}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git diff 失败: {proc.stderr.strip()}")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
