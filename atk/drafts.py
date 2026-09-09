"""AI 草稿评审转正：approve 去 tag，reject 移走留档，全程记日志。

草稿 = scenarios 下 tags 含 `ai-generated` 的场景文件。转正即去掉该 tag；
未评审草稿参与执行时 gate 拦截（见 cli._do_gate）。
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
    return isinstance(raw, dict) and DRAFT_TAG in (raw.get("tags") or [])


def _append_log(entry: DraftReview, reports_dir: Path | str) -> None:
    log = Path(reports_dir) / REVIEW_LOG
    items = []
    if log.exists():
        try:
            items = yaml.safe_load(log.read_text(encoding="utf-8")) or []
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
    """评审单份草稿。approve 去 tag 就地转正；reject 移到 reports/rejected。

    返回人类可读结论。非草稿抛 ValueError。
    """
    p = Path(path)
    if not is_draft(p):
        raise ValueError(f"不是AI草稿（含{ DRAFT_TAG } tag 的场景文件）: {p}")
    now = dt.datetime.now().isoformat(timespec="seconds")
    if verdict == "approve":
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        tags = [t for t in (raw.get("tags") or []) if t != DRAFT_TAG]
        if tags:
            raw["tags"] = tags
        else:
            raw.pop("tags", None)
        p.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        _append_log(
            DraftReview(file=str(p), by=by, verdict=verdict, note=note, at=now),
            reports_dir,
        )
        return f"已转正：{p}（去掉 {DRAFT_TAG} tag）"
    dest = _unique(Path(reports_dir) / REJECTED_DIR, p.name)
    shutil.move(str(p), str(dest))
    _append_log(
        DraftReview(file=str(p), by=by, verdict=verdict, note=note, at=now),
        reports_dir,
    )
    return f"已移走：{p} -> {dest}"
