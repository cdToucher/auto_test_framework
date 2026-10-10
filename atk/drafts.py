"""AI 草稿评审：approve 记日志，reject 移走留档。

草稿 = scenarios 下 tags 含 `ai-generated` 的场景文件。tag 只是**溯源标记**：
它本就是 AI 能写的字符串，删掉它从不等于转正。转正的判据是审批账本里一条以
断言面指纹为键、带有效签名的记录（见 atk/approvals.py），因此 approve 不再
改动场景文件——之前那套逐行去 tag 的文本手术（50 行 + YAML 重写兜底，只为
保留注释排版）连同类风险一起删除。
"""
import datetime as dt
import shutil
from pathlib import Path

import yaml
from pydantic import BaseModel

DRAFT_TAG = "ai-generated"
REVIEW_LOG = "draft-reviews.yaml"
REJECTED_DIR = "rejected"


class DraftReview(BaseModel):
    file: str
    by: str = ""
    verdict: str = "approve"
    note: str = ""
    at: str = ""


def is_draft(path: Path | str) -> bool:
    """文件存在且为含 ai-generated tag 的场景映射时为 True；其余一律 False。"""
    p = Path(path)
    if not p.is_file():
        return False
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(raw, dict):
        return False
    tags = raw.get("tags") or []
    return isinstance(tags, list) and DRAFT_TAG in tags


def _append_log(entry: DraftReview, reports_dir: Path | str) -> None:
    log = Path(reports_dir) / REVIEW_LOG
    items: list = []
    if log.exists():
        try:
            loaded = yaml.safe_load(log.read_text(encoding="utf-8")) or []
            items = loaded if isinstance(loaded, list) else []
        except Exception:
            items = []
    items.append(entry.model_dump())
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        yaml.safe_dump(items, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _unique(dest_dir: Path, name: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    cand = dest_dir / name
    n, stem, suffix = 2, Path(name).stem, Path(name).suffix
    while cand.exists():
        cand = dest_dir / f"{stem}-{n}{suffix}"
        n += 1
    return cand


def review_draft(
    path: Path | str,
    by: str,
    verdict: str,
    note: str = "",
    reports_dir: Path | str = "reports",
) -> str:
    """评审单份草稿。approve 只记日志（落账由 CLI 侧凭凭证完成），reject 移到 reports/rejected。

    approve **不碰场景文件**：tag 永久保留作溯源，转正只发生在审批账本里
    （atk/approvals.py 的 expect_hash 为键、带有效签名）。之前"去 tag 就地转正"
    的文本手术已删除——它改不了"tag 是 AI 可写字符串"这个根本问题，却带来
    误伤嵌套 tags 字段、注释排版丢失一整类风险。

    返回人类可读结论。verdict 非法或非草稿抛 ValueError（防 API 直调
    误传 verdict 走 reject 分支移走文件）。
    """
    if verdict not in ("approve", "reject"):
        raise ValueError(f"verdict 必须是 approve|reject， got: {verdict!r}")
    p = Path(path)
    if not is_draft(p):
        raise ValueError(f"不是AI草稿（含{ DRAFT_TAG } tag 的场景文件）: {p}")
    now = dt.datetime.now().isoformat(timespec="seconds")
    if verdict == "approve":
        _append_log(
            DraftReview(file=str(p), by=by, verdict=verdict, note=note, at=now),
            reports_dir,
        )
        return (f"已评审通过：{p}（转正判据是审批账本里的内容签名，"
                f"{DRAFT_TAG} tag 保留作溯源）")
    dest = _unique(Path(reports_dir) / REJECTED_DIR, p.name)
    shutil.move(str(p), str(dest))
    _append_log(
        DraftReview(file=str(p), by=by, verdict=verdict, note=note, at=now),
        reports_dir,
    )
    return f"已移走：{p} -> {dest}"
