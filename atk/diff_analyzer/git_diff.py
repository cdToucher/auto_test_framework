"""git 变更文件提取。"""
import subprocess


def is_git_repository(repo: str = ".") -> bool:
    """目标目录是否位于 Git 工作树中。"""
    proc = subprocess.run(
        ["git", "-C", repo, "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and proc.stdout.strip() == "true"


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


def rev_parse(ref: str, repo: str = ".") -> str:
    """解析引用为 commit SHA；失败抛 RuntimeError。"""
    proc = subprocess.run(
        ["git", "-C", repo, "rev-parse", ref],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git rev-parse {ref} 失败: {proc.stderr.strip()}")
    return proc.stdout.strip()


def uncommitted_files(repo: str = ".") -> list[str]:
    """工作区未提交（含未跟踪）文件；三点 diff 看不见它们，gate 需提示。"""
    proc = subprocess.run(
        ["git", "-C", repo, "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    return [ln[3:] for ln in proc.stdout.splitlines() if ln.strip()]
